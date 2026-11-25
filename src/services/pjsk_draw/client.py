from __future__ import annotations

import asyncio
import base64
from typing import Any, Dict, List, Optional, Tuple

from services.log import logger
from utils.fast_json import dumps_bytes, loads_bytes

from .config import DRAW_SERVICE_TIMEOUT, DRAW_SERVICE_URLS

_SESSION = None
_SESSION_LOCK = asyncio.Lock()
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


def _ordered_urls() -> list[str]:
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
    body = dumps_bytes(payload or {}, default=str)
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
                    envelope = loads_bytes(await resp.read()) or {}
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
    if not DRAW_SERVICE_URLS:
        raise RuntimeError("未配置 PJSK_DRAW_SERVICE_URLS（独立 pjsk-draw 服务）")
    return await _render_remote(name, payload or {}, timeout)


async def render_multi(
    name: str, payload: Optional[Dict[str, Any]] = None, timeout: Optional[float] = None
) -> List[bytes]:
    images, _ = await render_with_meta(name, payload, timeout)
    return images


async def render(name: str, payload: Optional[Dict[str, Any]] = None, timeout: Optional[float] = None) -> bytes:
    images, _ = await render_with_meta(name, payload, timeout)
    return images[0]


def is_remote() -> bool:
    return bool(DRAW_SERVICE_URLS)
