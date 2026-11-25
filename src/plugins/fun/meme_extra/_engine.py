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


# 同步渲染丢线程池并限并发
_RENDER_LIMIT = 4
_semaphore: Optional[asyncio.Semaphore] = None

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
    # 失败时不抛异常而是返回错误对象，先判类型
    if isinstance(result, (bytes, bytearray)):
        return bytes(result), ""
    return None, _describe_error(result)


async def generate(
    key: str,
    images: list[tuple[str, bytes]],
    texts: list[str],
    options: Optional[dict[str, Any]] = None,
) -> tuple[Optional[bytes], str]:
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
