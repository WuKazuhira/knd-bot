"""PJSK 绘图原语：字体、图片缓存、渐变、资源合并下载。

原先散落在 plugins/pjsk/_utils.py，绘图相关部分统一收到绘图服务里。
"""

from __future__ import annotations

import asyncio
import os
import time
from collections import OrderedDict
from functools import partial
from pathlib import Path
from threading import RLock
from typing import Dict, Hashable, Optional, Tuple

from PIL import Image, ImageFont

from config.path_config import FONT_PATH
from utils.imageutils.utils import encode_image_bytes

from .context import get_context

# 所有出图右下角的统一署名
PJSK_WATERMARK_TEXT = 'DESIGNED by KNDBOT in California'

_CACHE_LOCK = RLock()
_FONT_CACHE: Dict[Tuple[str, int], ImageFont.FreeTypeFont] = {}
_IMAGE_CACHE: "OrderedDict[Tuple[str, Optional[str], int, int, Optional[Tuple[int, int]]], Image.Image]" = OrderedDict()
_IMAGE_CACHE_LIMIT = 768
_RENDER_IMAGE_CACHE: "OrderedDict[Hashable, Image.Image]" = OrderedDict()
_RENDER_IMAGE_CACHE_LIMIT = 256
_RENDER_BYTES_CACHE: "OrderedDict[Hashable, bytes]" = OrderedDict()
_RENDER_BYTES_CACHE_LIMIT = 128
_ASSET_FLIGHTS: Dict[Hashable, "asyncio.Task"] = {}
_ASSET_FAILURES: Dict[Hashable, float] = {}
_ASSET_FAILURE_TTL = 30.0

_ASSET_FLIGHT_LOCK: Optional[asyncio.Lock] = None
_PJSK_THREAD_SEMAPHORE: Optional[asyncio.Semaphore] = None
_PJSK_THREAD_LIMIT = max(2, min(8, (os.cpu_count() or 2)))


async def run_pjsk_thread(func, *args, **kwargs):
    """在线程池中限流执行 pjsk 的同步 I/O / 图片处理任务。"""
    global _PJSK_THREAD_SEMAPHORE
    if _PJSK_THREAD_SEMAPHORE is None:
        _PJSK_THREAD_SEMAPHORE = asyncio.Semaphore(_PJSK_THREAD_LIMIT)
    async with _PJSK_THREAD_SEMAPHORE:
        return await asyncio.to_thread(partial(func, *args, **kwargs))


def vertical_gradient(width: int, height: int, top: tuple, bottom: tuple) -> Image.Image:
    """垂直渐变背景：1x2 渐变条 + 双线性放大，比逐行画线快约两个数量级。"""
    strip = Image.new('RGB', (1, 2))
    strip.putpixel((0, 0), tuple(top[:3]))
    strip.putpixel((0, 1), tuple(bottom[:3]))
    return strip.resize((max(1, width), max(1, height)), Image.Resampling.BILINEAR)


def get_pjsk_font(name: str, size: int) -> ImageFont.FreeTypeFont:
    key = (name, size)
    with _CACHE_LOCK:
        font = _FONT_CACHE.get(key)
        if font is None:
            font = ImageFont.truetype(str(FONT_PATH / name), size)
            _FONT_CACHE[key] = font
    return font


def get_cached_render_image(key: Hashable, copy: bool = True) -> Optional[Image.Image]:
    """读取跨模块共享的渲染结果缓存。"""
    with _CACHE_LOCK:
        image = _RENDER_IMAGE_CACHE.get(key)
        if image is None:
            return None
        _RENDER_IMAGE_CACHE.move_to_end(key)
        return image.copy() if copy else image


def put_cached_render_image(key: Hashable, image: Image.Image) -> None:
    """写入跨模块共享的渲染结果缓存，并限制内存条目数量。"""
    with _CACHE_LOCK:
        _RENDER_IMAGE_CACHE[key] = image.copy()
        _RENDER_IMAGE_CACHE.move_to_end(key)
        while len(_RENDER_IMAGE_CACHE) > _RENDER_IMAGE_CACHE_LIMIT:
            _RENDER_IMAGE_CACHE.popitem(last=False)


def get_cached_render_bytes(key: Hashable) -> Optional[bytes]:
    with _CACHE_LOCK:
        data = _RENDER_BYTES_CACHE.get(key)
        if data is not None:
            _RENDER_BYTES_CACHE.move_to_end(key)
        return data


def put_cached_render_bytes(key: Hashable, data: bytes) -> None:
    with _CACHE_LOCK:
        _RENDER_BYTES_CACHE[key] = data
        _RENDER_BYTES_CACHE.move_to_end(key)
        while len(_RENDER_BYTES_CACHE) > _RENDER_BYTES_CACHE_LIMIT:
            _RENDER_BYTES_CACHE.popitem(last=False)


async def get_pjsk_asset_cached(
    category: str,
    filename: str,
    pjsk_type: int = 0,
    mode: Optional[str] = "RGBA",
    size: Optional[Tuple[int, int]] = None,
) -> Optional[Image.Image]:
    """合并相同资源的并发下载，并缓存转换/缩放后的图片。"""
    global _ASSET_FLIGHT_LOCK
    key = (pjsk_type, category, filename, mode, size)
    cached = get_cached_render_image(("asset", key))
    if cached is not None:
        return cached

    now = time.monotonic()
    with _CACHE_LOCK:
        failed_at = _ASSET_FAILURES.get(key)
    if failed_at is not None and now - failed_at < _ASSET_FAILURE_TTL:
        return None

    if _ASSET_FLIGHT_LOCK is None:
        _ASSET_FLIGHT_LOCK = asyncio.Lock()
    async with _ASSET_FLIGHT_LOCK:
        task = _ASSET_FLIGHTS.get(key)
        if task is None:
            task = asyncio.create_task(
                get_context().get_asset(category, filename, pjsk_type=pjsk_type)
            )
            _ASSET_FLIGHTS[key] = task
    try:
        image = await asyncio.shield(task)
        if image is None:
            with _CACHE_LOCK:
                _ASSET_FAILURES[key] = time.monotonic()
            return None
        image = image.convert(mode) if mode else image.copy()
        if size is not None and image.size != size:
            image = image.resize(size, Image.Resampling.LANCZOS)
        put_cached_render_image(("asset", key), image)
        with _CACHE_LOCK:
            _ASSET_FAILURES.pop(key, None)
        return image.copy()
    except Exception:
        with _CACHE_LOCK:
            _ASSET_FAILURES[key] = time.monotonic()
        raise
    finally:
        async with _ASSET_FLIGHT_LOCK:
            if _ASSET_FLIGHTS.get(key) is task:
                _ASSET_FLIGHTS.pop(key, None)


def image_to_bytes(
    image: Image.Image,
    image_format: str = 'PNG',
    quality: int = 88,
    watermark: bool = True,
) -> bytes:
    """渲染结果统一编码成图片字节（含 kndbot 水印），方便走 HTTP 返回。"""
    return encode_image_bytes(
        image, image_format=image_format, quality=quality, watermark=watermark
    )


def image_to_png(image: Image.Image, watermark: bool = True) -> bytes:
    return image_to_bytes(image, image_format='PNG', watermark=watermark)


def image_to_jpeg(image: Image.Image, quality: int = 88, watermark: bool = True) -> bytes:
    """大尺寸无透明出图用 JPEG，和原先 pic2b64_fast 行为一致。"""
    return image_to_bytes(image, image_format='JPEG', quality=quality, watermark=watermark)


def guess_image_media_type(data: bytes) -> str:
    """按魔数判断渲染结果的 MIME，供 HTTP 响应用。"""
    if data[:8] == b'\x89PNG\r\n\x1a\n':
        return 'image/png'
    if data[:3] == b'\xff\xd8\xff':
        return 'image/jpeg'
    if data[:6] in (b'GIF87a', b'GIF89a'):
        return 'image/gif'
    if data[:4] == b'RIFF' and data[8:12] == b'WEBP':
        return 'image/webp'
    return 'application/octet-stream'


def open_pjsk_image(
    path: Path,
    mode: Optional[str] = None,
    copy: bool = True,
    size: Optional[Tuple[int, int]] = None,
) -> Image.Image:
    stat = path.stat()
    key = (str(path), mode, stat.st_mtime_ns, stat.st_size, size)
    with _CACHE_LOCK:
        img = _IMAGE_CACHE.get(key)
        if img is None:
            with Image.open(path) as source:
                img = source.convert(mode) if mode else source.copy()
            if size is not None:
                img = img.resize(size, Image.Resampling.LANCZOS)
            _IMAGE_CACHE[key] = img
            while len(_IMAGE_CACHE) > _IMAGE_CACHE_LIMIT:
                _IMAGE_CACHE.popitem(last=False)
        else:
            _IMAGE_CACHE.move_to_end(key)
        return img.copy() if copy else img
