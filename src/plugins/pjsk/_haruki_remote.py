from __future__ import annotations

from typing import Any, Iterable, Optional

from ._config import HARUKI_DECK_SERVICE_SERVERS


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


async def choose_deck_service_payload(payload: dict) -> Optional[dict]:
    """优先尝试 deck-service 远端推荐服务；失败则返回 None 交给本地旧实现。"""
    urls = _iter_server_urls(HARUKI_DECK_SERVICE_SERVERS)
    if not urls:
        return None
    return payload
