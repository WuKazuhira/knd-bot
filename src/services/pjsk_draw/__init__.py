"""PJSK 绘图服务客户端（独立 Rust pjsk-draw HTTP 代理）。

指令侧收集完数据后调用 :func:`render`，请求独立的 `pjsk-draw` 服务（`PJSK_DRAW_SERVICE_URLS`）。
旧版进程内 Python 绘图渲染器已归档至 `data/pjsk/archive/legacy_pjsk_draw_20261121.tar.gz`。
"""

from __future__ import annotations

from .client import is_remote, render, render_multi, render_with_meta
from .context import PjskDrawContext, get_context, has_context, set_context

__all__ = [
    "PjskDrawContext",
    "get_context",
    "has_context",
    "is_remote",
    "render",
    "render_multi",
    "render_with_meta",
    "set_context",
]
