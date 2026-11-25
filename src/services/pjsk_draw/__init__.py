from __future__ import annotations

import asyncio
import os
from functools import partial
from pathlib import Path
from typing import Any, Callable, TypeVar

from PIL import Image

from .client import is_remote, render, render_multi, render_with_meta
from .context import PjskDrawContext, get_context, has_context, set_context


def open_pjsk_image(path: str | Path, mode: str | None = None) -> Image.Image:
    with Image.open(path) as im:
        return im.convert(mode) if mode else im.copy()

_T = TypeVar("_T")
_PJSK_THREAD_LIMIT = max(1, int(os.environ.get("PJSK_THREAD_CONCURRENCY", "16")))
_PJSK_THREAD_SEM: asyncio.Semaphore | None = None
_PJSK_THREAD_LOOP: asyncio.AbstractEventLoop | None = None


def _get_thread_semaphore() -> asyncio.Semaphore:
    global _PJSK_THREAD_SEM, _PJSK_THREAD_LOOP
    loop = asyncio.get_running_loop()
    if _PJSK_THREAD_SEM is None or _PJSK_THREAD_LOOP is not loop:
        _PJSK_THREAD_SEM = asyncio.Semaphore(_PJSK_THREAD_LIMIT)
        _PJSK_THREAD_LOOP = loop
    return _PJSK_THREAD_SEM


async def run_pjsk_thread(func: Callable[..., _T], /, *args: Any, **kwargs: Any) -> _T:
    async with _get_thread_semaphore():
        call = partial(func, *args, **kwargs) if (args or kwargs) else func
        return await asyncio.to_thread(call)


__all__ = [
    "PjskDrawContext",
    "get_context",
    "has_context",
    "is_remote",
    "open_pjsk_image",
    "render",
    "render_multi",
    "render_with_meta",
    "run_pjsk_thread",
    "set_context",
]
