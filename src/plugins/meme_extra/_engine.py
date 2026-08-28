"""meme-generator（Rust 实现）的薄封装。

上游 nonebot-plugin-memes 从 v0.8 起把表情实现全部搬到了 meme-generator，
本模块只取它的渲染引擎，指令分发、权限、CD 仍走 kndbot 自己那一套。

注意两个反直觉的 API 行为，本模块负责挡住：
  * ``Meme.generate()`` 失败时**返回**错误对象，不抛异常；
  * ``get_meme()`` 遇到未知 key 返回 ``None``，也不抛异常。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Optional

from services.log import logger

try:
    import meme_generator
    from meme_generator import Image as MemeImage

    MEME_GENERATOR_AVAILABLE = True
    IMPORT_ERROR = ""
except Exception as e:  # pragma: no cover - 依赖缺失时整个插件降级
    meme_generator = None  # type: ignore[assignment]
    MemeImage = None  # type: ignore[assignment]
    MEME_GENERATOR_AVAILABLE = False
    IMPORT_ERROR = f"{type(e).__name__}: {e}"


# 渲染是 CPU 密集的同步调用，限流后丢线程池，避免拖住事件循环。
_RENDER_LIMIT = 4
_semaphore: Optional[asyncio.Semaphore] = None

# 错误对象 -> 给用户看的话。键是 meme_generator 的异常类型名。
_ERROR_MESSAGES = {
    "ImageNumberMismatch": "图片数量不对",
    "TextNumberMismatch": "文字数量不对",
    "TextOverLength": "文字太长了，短一点吧",
    "ImageDecodeError": "图片读取失败，换一张试试",
    "ImageEncodeError": "图片生成失败",
    "ImageAssetMissing": "表情素材缺失，请检查 meme-generator 素材是否下载完整",
    "DeserializeError": "参数解析失败",
    "MemeFeedback": "",  # 引擎自带提示，直接透传
}


@dataclass
class MemeSpec:
    """一个上游表情的元信息，供上层生成指令用。"""

    key: str
    keywords: list[str]
    min_images: int
    max_images: int
    min_texts: int
    max_texts: int
    default_texts: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)


def _iter_raw_memes():
    if not MEME_GENERATOR_AVAILABLE:
        return []
    try:
        return meme_generator.get_memes()
    except Exception as e:
        logger.warning(f"[meme_extra] 读取上游表情列表失败: {e}")
        return []


def load_specs(excluded_keys: set[str]) -> list[MemeSpec]:
    """列出要注册的表情，按关键词排序保证帮助图稳定。"""
    specs: list[MemeSpec] = []
    for meme in _iter_raw_memes():
        if meme.key in excluded_keys:
            continue
        info = meme.info
        keywords = [k for k in info.keywords if k]
        if not keywords:
            continue
        params = info.params
        specs.append(
            MemeSpec(
                key=meme.key,
                keywords=keywords,
                min_images=params.min_images,
                max_images=params.max_images,
                min_texts=params.min_texts,
                max_texts=params.max_texts,
                default_texts=list(params.default_texts or []),
                tags=list(info.tags or []),
            )
        )
    specs.sort(key=lambda s: s.keywords[0])
    return specs


def _describe_error(result: Any) -> str:
    name = type(result).__name__
    if name in _ERROR_MESSAGES:
        message = _ERROR_MESSAGES[name]
        detail = str(result).strip()
        # MemeFeedback 之类自带可读内容时优先用它的。
        if not message:
            return detail or "表情生成失败"
        return message
    return f"表情生成失败（{name}）"


def _generate_sync(key: str, images: list[tuple[str, bytes]], texts: list[str],
                   options: dict[str, Any]) -> tuple[Optional[bytes], str]:
    meme = meme_generator.get_meme(key)
    if meme is None:
        return None, f"表情 {key} 在当前 meme-generator 版本里不存在"
    result = meme.generate(
        [MemeImage(name=name, data=data) for name, data in images], texts, options
    )
    # 关键：失败时返回的是错误对象而不是抛异常，不判类型会把它当图片发出去。
    if isinstance(result, (bytes, bytearray)):
        return bytes(result), ""
    return None, _describe_error(result)


async def generate(
    key: str,
    images: list[tuple[str, bytes]],
    texts: list[str],
    options: Optional[dict[str, Any]] = None,
) -> tuple[Optional[bytes], str]:
    """渲染一个表情，返回 (图片字节, 错误提示)，两者必有其一。"""
    if not MEME_GENERATOR_AVAILABLE:
        return None, f"meme-generator 未安装或加载失败（{IMPORT_ERROR}）"

    global _semaphore
    if _semaphore is None:
        _semaphore = asyncio.Semaphore(_RENDER_LIMIT)
    async with _semaphore:
        try:
            return await asyncio.to_thread(
                _generate_sync, key, images, texts, options or {}
            )
        except Exception as e:
            logger.warning(f"[meme_extra] 生成表情 {key} 异常: {e}", exc_info=True)
            return None, f"表情生成异常：{type(e).__name__}"


def version() -> str:
    if not MEME_GENERATOR_AVAILABLE:
        return "unavailable"
    try:
        return meme_generator.get_version()
    except Exception:
        return "unknown"
