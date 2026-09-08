"""绘图服务客户端：pjsk 指令出图的唯一入口。

指令侧收集完数据后调用 :func:`render`，得到 PNG 字节：

    from services.pjsk_draw import render
    png = await render("pjskinfo", {"music_id": 74, "pjsk_type": 0})

配了 `PJSK_DRAW_SERVICE_URLS` 就走 HTTP 调独立的绘图服务进程，
没配就在当前进程内执行同一个注册表里的渲染器，指令侧代码完全一样。
"""

from __future__ import annotations

import asyncio
import base64
import json
from typing import Any, Dict, List, Optional, Tuple

from services.log import logger

from .config import DRAW_SERVICE_FALLBACK_LOCAL, DRAW_SERVICE_TIMEOUT, DRAW_SERVICE_URLS
from .registry import dispatch, load_all_renderers

_SESSION = None
_SESSION_LOCK = asyncio.Lock()
_RENDERERS_LOADED = False
_ROUND_ROBIN = 0


async def _get_session():
    global _SESSION
    import aiohttp

    if _SESSION is not None and not _SESSION.closed:
        return _SESSION
    async with _SESSION_LOCK:
        if _SESSION is None or _SESSION.closed:
            _SESSION = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=DRAW_SERVICE_TIMEOUT),
                connector=aiohttp.TCPConnector(limit=64, limit_per_host=16, ttl_dns_cache=300),
            )
        return _SESSION


def _ensure_renderers() -> None:
    global _RENDERERS_LOADED
    if not _RENDERERS_LOADED:
        load_all_renderers()
        _RENDERERS_LOADED = True


def _ordered_urls() -> list[str]:
    """轮转起点，让多个绘图服务实例分摊请求。"""
    global _ROUND_ROBIN
    if len(DRAW_SERVICE_URLS) <= 1:
        return list(DRAW_SERVICE_URLS)
    _ROUND_ROBIN = (_ROUND_ROBIN + 1) % len(DRAW_SERVICE_URLS)
    return DRAW_SERVICE_URLS[_ROUND_ROBIN:] + DRAW_SERVICE_URLS[: _ROUND_ROBIN]


async def _render_remote(
    name: str, payload: Dict[str, Any], timeout: Optional[float]
) -> Tuple[List[bytes], Dict[str, Any]]:
    import aiohttp

    session = await _get_session()
    body = json.dumps(payload or {}, ensure_ascii=False, default=str).encode("utf-8")
    errors = []
    for base_url in _ordered_urls():
        url = f"{base_url}/render/{name}"
        try:
            async with session.post(
                url,
                data=body,
                headers={"Content-Type": "application/json"},
                timeout=aiohttp.ClientTimeout(total=timeout or DRAW_SERVICE_TIMEOUT),
            ) as resp:
                if resp.status != 200:
                    detail = (await resp.text())[:500]
                    raise RuntimeError(f"{resp.status}: {detail}")
                content_type = (resp.headers.get("content-type") or "").split(";", 1)[0].strip().lower()
                if content_type == "application/json":
                    # 多图 / 带元信息的任务用 JSON 信封返回
                    envelope = await resp.json() or {}
                    # images 允许为空：例如谱面预览在没有可用图源时只回 meta
                    images = [base64.b64decode(item) for item in envelope.get("images") or []]
                    return images, envelope.get("meta") or {}
                data = await resp.read()
                if not data:
                    raise RuntimeError("绘图服务返回空响应")
                return [data], {}
        except Exception as exc:
            logger.warning(f"[pjsk_draw] 调用绘图服务失败 {url}: {exc}")
            errors.append(f"{base_url}: {exc}")
    raise RuntimeError("所有绘图服务实例均失败:\n" + "\n".join(errors))


async def render_with_meta(
    name: str, payload: Optional[Dict[str, Any]] = None, timeout: Optional[float] = None
) -> Tuple[List[bytes], Dict[str, Any]]:
    """渲染指定任务，返回 (图片字节列表, 元信息)。"""
    payload = payload or {}
    if DRAW_SERVICE_URLS:
        try:
            return await _render_remote(name, payload, timeout)
        except Exception:
            if not DRAW_SERVICE_FALLBACK_LOCAL:
                raise
            logger.warning(f"[pjsk_draw] 任务 {name} 回退到进程内渲染")
    _ensure_renderers()
    return await dispatch(name, payload)


async def render_multi(
    name: str, payload: Optional[Dict[str, Any]] = None, timeout: Optional[float] = None
) -> List[bytes]:
    """渲染指定任务，返回图片字节列表（多图任务用）。"""
    images, _ = await render_with_meta(name, payload, timeout)
    return images


async def render(name: str, payload: Optional[Dict[str, Any]] = None, timeout: Optional[float] = None) -> bytes:
    """渲染指定任务，返回单张图片字节。"""
    images, _ = await render_with_meta(name, payload, timeout)
    return images[0]


def is_remote() -> bool:
    """当前是否配置了独立的绘图服务进程。"""
    return bool(DRAW_SERVICE_URLS)
