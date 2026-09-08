"""PJSK 绘图服务。

pjsk 各指令的「出图」全部收在这里，指令侧只负责收集数据，然后：

    from services.pjsk_draw import render
    png = await render("pjskinfo", {"music_id": 74, "pjsk_type": 0})

`render` 会在配置了 `PJSK_DRAW_SERVICE_URLS` 时走 HTTP 调独立的绘图服务进程
（services/pjsk_draw/serve.py），否则在当前进程内执行同一个注册表里的渲染器。
服务自身不 import plugins 层：资源下载与主数据读取由插件在加载时通过
:func:`set_context` 注入，独立进程则用 local_data 的只读实现。
"""

from __future__ import annotations

from .client import is_remote, render, render_multi, render_with_meta
from .context import PjskDrawContext, get_context, has_context, set_context
from .honor import bondsbackground, generatehonor
from .primitives import (
    PJSK_WATERMARK_TEXT,
    get_cached_render_bytes,
    get_cached_render_image,
    get_pjsk_asset_cached,
    get_pjsk_font,
    image_to_bytes,
    image_to_jpeg,
    image_to_png,
    open_pjsk_image,
    put_cached_render_bytes,
    put_cached_render_image,
    run_pjsk_thread,
    vertical_gradient,
)
from .registry import register, renderer_names

__all__ = [
    "PJSK_WATERMARK_TEXT",
    "PjskDrawContext",
    "bondsbackground",
    "generatehonor",
    "get_cached_render_bytes",
    "get_cached_render_image",
    "get_context",
    "get_pjsk_asset_cached",
    "get_pjsk_font",
    "has_context",
    "image_to_bytes",
    "image_to_jpeg",
    "image_to_png",
    "is_remote",
    "open_pjsk_image",
    "put_cached_render_bytes",
    "put_cached_render_image",
    "register",
    "render",
    "render_multi",
    "render_with_meta",
    "renderer_names",
    "run_pjsk_thread",
    "set_context",
    "vertical_gradient",
]
