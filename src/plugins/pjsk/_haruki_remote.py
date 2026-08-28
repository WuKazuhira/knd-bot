from __future__ import annotations

import base64
from typing import Any, Iterable, Optional

from services.log import logger
from utils.http_utils import AsyncHttpx

from ._config import HARUKI_DECK_SERVICE_SERVERS

_DRAWING_IMAGE_TYPES = {"image/png", "image/jpeg", "image/jpg", "image/webp", "application/octet-stream"}


def _iter_server_urls(servers: Optional[Iterable[Any]]) -> list[str]:
    urls: list[str] = []
    if not servers:
        return urls
    for server in servers:
        if isinstance(server, dict):
            url = str(server.get("url", "")).strip()
        else:
            url = str(server).strip()
        if url:
            urls.append(url.rstrip("/"))
    return urls


async def _post_json_image(urls: Iterable[str], path: str, payload: dict, timeout: float = 60) -> Optional[bytes]:
    last_error: Optional[Exception] = None
    for base_url in urls:
        full_url = f"{base_url}/{path.lstrip('/')}"
        try:
            logger.info(f"[haruki-remote] 请求 {full_url}")
            resp = await AsyncHttpx.post(full_url, json=payload, timeout=timeout, use_proxy=False)
            content_type = (resp.headers.get("content-type") or "").split(";", 1)[0].strip().lower()
            if resp.status_code != 200:
                logger.warning(f"[haruki-remote] {full_url} 返回 {resp.status_code}")
                continue

            if content_type == "application/json":
                try:
                    data = resp.json()
                except Exception:
                    data = None
                if isinstance(data, dict):
                    for key in ("image", "image_base64", "base64", "b64", "data"):
                        value = data.get(key)
                        if isinstance(value, str) and value:
                            try:
                                return base64.b64decode(value)
                            except Exception:
                                logger.warning(f"[haruki-remote] {full_url} 的 {key} 不是有效 base64")
                                break
                    if isinstance(data.get("image"), dict):
                        nested = data["image"]
                        for key in ("base64", "b64", "data"):
                            value = nested.get(key)
                            if isinstance(value, str) and value:
                                try:
                                    return base64.b64decode(value)
                                except Exception:
                                    logger.warning(f"[haruki-remote] {full_url} 的嵌套 {key} 不是有效 base64")
                                    break
                continue

            content = resp.content
            is_image_magic = content.startswith((b"\x89PNG\r\n\x1a\n", b"\xff\xd8", b"RIFF"))
            if content_type in _DRAWING_IMAGE_TYPES or is_image_magic:
                return content
            logger.warning(f"[haruki-remote] {full_url} 返回非图片内容类型: {content_type or 'unknown'}")
        except Exception as e:
            last_error = e
            logger.warning(f"[haruki-remote] 请求 {full_url} 失败: {e}")
            continue

    if last_error:
        logger.debug(f"[haruki-remote] 所有候选地址都失败，最后错误: {last_error}")
    return None


async def render_profile(payload: dict) -> Optional[bytes]:
    return None


async def render_cardbox(payload: dict) -> Optional[bytes]:
    return None


async def render_deck(payload: dict) -> Optional[bytes]:
    return None


async def render_mysekai(payload: dict) -> Optional[bytes]:
    return None


async def choose_deck_service_payload(payload: dict) -> Optional[dict]:
    """优先尝试 deck-service 远端推荐服务；失败则返回 None 交给本地旧实现。"""
    urls = _iter_server_urls(HARUKI_DECK_SERVICE_SERVERS)
    if not urls:
        return None
    return payload
