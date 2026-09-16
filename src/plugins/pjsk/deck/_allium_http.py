"""Allium PR #39 HTTP 服务客户端。"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Iterable

import aiohttp

from services.log import logger

from .._config import DECK_RECOMMEND_HTTP_API, DECK_RECOMMEND_HTTP_TIMEOUT
from ._allium_http_contract import normalize_http_decks, translate_options_for_http

_SHARED_SESSION: aiohttp.ClientSession | None = None
_SHARED_SESSION_LOCK = asyncio.Lock()
_SERVER_CURSOR = 0


class AlliumHttpError(RuntimeError):
    """HTTP 服务返回非成功状态或无效响应。"""

    def __init__(self, status: int, message: str, code: str | None = None):
        self.status = status
        self.code = code
        self.message = message
        super().__init__(f"HTTP {status}{f' [{code}]' if code else ''}: {message}")


async def _get_shared_session() -> aiohttp.ClientSession:
    global _SHARED_SESSION
    if _SHARED_SESSION is not None and not _SHARED_SESSION.closed:
        return _SHARED_SESSION
    async with _SHARED_SESSION_LOCK:
        if _SHARED_SESSION is None or _SHARED_SESSION.closed:
            _SHARED_SESSION = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=max(5.0, DECK_RECOMMEND_HTTP_TIMEOUT)),
                connector=aiohttp.TCPConnector(limit=64, limit_per_host=16, ttl_dns_cache=300),
            )
        return _SHARED_SESSION


async def close_http_session() -> None:
    """关闭共享客户端，供测试或进程退出钩子调用。"""
    global _SHARED_SESSION
    if _SHARED_SESSION is not None and not _SHARED_SESSION.closed:
        await _SHARED_SESSION.close()
    _SHARED_SESSION = None


def _register_shutdown_hook() -> None:
    try:
        from nonebot import get_driver

        get_driver().on_shutdown(close_http_session)
    except (ImportError, RuntimeError, ValueError):
        # 允许在未初始化 NoneBot 的隔离测试中导入本模块。
        return


_register_shutdown_hook()


def _ordered_servers(server_urls: Iterable[str], server_weights: Iterable[int] | None) -> list[str]:
    global _SERVER_CURSOR
    urls = [str(url).strip().rstrip("/") for url in server_urls if str(url).strip()]
    weights = list(server_weights or [])
    if weights and len(weights) == len(urls):
        urls = [url for url, weight in zip(urls, weights) if int(weight) > 0]
    if not urls:
        return []
    start = _SERVER_CURSOR % len(urls)
    _SERVER_CURSOR += 1
    return urls[start:] + urls[:start]


async def _post_json(
    session: aiohttp.ClientSession,
    url: str,
    payload: dict[str, Any],
    timeout: float,
) -> dict[str, Any]:
    try:
        async with session.post(
            url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            timeout=aiohttp.ClientTimeout(total=timeout),
        ) as response:
            raw = await response.text()
            try:
                body = json.loads(raw) if raw else {}
            except json.JSONDecodeError:
                body = {}
            if response.status < 200 or response.status >= 300:
                error = body.get("error") if isinstance(body, dict) else None
                if isinstance(error, dict):
                    code = str(error.get("code") or "") or None
                    message = str(error.get("message") or raw[:500] or response.reason)
                else:
                    code = None
                    detail = body.get("detail") if isinstance(body, dict) else None
                    message = str(detail or raw[:500] or response.reason)
                raise AlliumHttpError(response.status, message, code)
            if not isinstance(body, dict):
                raise AlliumHttpError(response.status, "响应不是 JSON 对象")
            return body
    except asyncio.TimeoutError as exc:
        raise AlliumHttpError(504, "HTTP 组卡请求超时") from exc
    except aiohttp.ClientError as exc:
        raise AlliumHttpError(503, f"HTTP 组卡连接失败：{exc}") from exc


def _can_fallback_to_legacy(error: AlliumHttpError) -> bool:
    """仅在 v1 路由不存在时回退旧 JSON /recommend。"""
    return error.status in {404, 405, 501} and error.code not in {"unknown_region", "invalid_request"}


async def _post_one_server(
    session: aiohttp.ClientSession,
    base_url: str,
    region: str,
    options: dict[str, Any],
    userdata_bytes: bytes,
    timeout: float,
) -> dict[str, Any]:
    try:
        userdata_text = userdata_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"用户数据不是 UTF-8 JSON：{exc}") from exc

    api = DECK_RECOMMEND_HTTP_API
    translated = translate_options_for_http(options)
    v1_payload = {
        "region": region,
        "user": userdata_text,
        "params": translated,
    }
    legacy_payload = dict(options)
    legacy_payload.update({
        "region": region,
        "user_data_str": userdata_text,
        "algorithm": options.get("algorithm", "dfs"),
        "timeout_ms": options.get("timeout_ms", 15000),
    })

    if api in {"v1", "auto"}:
        try:
            return await _post_json(session, f"{base_url}/v1/recommend", v1_payload, timeout)
        except AlliumHttpError as exc:
            if api != "auto" or not _can_fallback_to_legacy(exc):
                raise
            logger.info(f"[deck] Allium HTTP v1 不存在，回退旧 /recommend: {exc}")

    return await _post_json(session, f"{base_url}/recommend", legacy_payload, timeout)


async def recommend_with_allium_http(
    server_urls: list[str],
    server_weights: list[int],
    region: str,
    options: dict[str, Any],
    userdata_bytes: bytes,
) -> tuple[list[dict[str, Any]], float, str]:
    """调用 Allium HTTP 服务，返回旧绘图结构、耗时和实际地址。"""
    servers = _ordered_servers(server_urls, server_weights)
    if not servers:
        raise AlliumHttpError(503, "未配置可用的 Allium HTTP 服务")

    timeout = max(
        5.0,
        float(DECK_RECOMMEND_HTTP_TIMEOUT),
        float(options.get("timeout_ms") or 0) / 1000.0 + 5.0,
    )
    session = await _get_shared_session()
    errors: list[str] = []
    started = asyncio.get_running_loop().time()
    for base_url in servers:
        try:
            logger.info(f"[deck] 发送 Allium HTTP 组卡请求: url={base_url} region={region}")
            response = await _post_one_server(session, base_url, region, options, userdata_bytes, timeout)
            decks = normalize_http_decks(response)
            if not decks:
                raise AlliumHttpError(502, "HTTP 服务返回空卡组")
            elapsed = asyncio.get_running_loop().time() - started
            return decks, elapsed, base_url
        except Exception as exc:  # noqa: BLE001 - 尝试下一台配置的服务
            errors.append(f"{base_url}: {exc}")
            logger.warning(f"[deck] Allium HTTP 服务失败: {errors[-1]}")

    raise AlliumHttpError(503, "请求所有 Allium HTTP 服务失败：" + " | ".join(errors))


async def recommend_challenge_all_with_allium_http(
    server_urls: list[str],
    server_weights: list[int],
    region: str,
    options: dict[str, Any],
    userdata_bytes: bytes,
) -> tuple[list[dict[str, Any]], float, str]:
    """调用 PR #39 challenge-all，兼容地展平为旧绘图结构。"""
    if DECK_RECOMMEND_HTTP_API == "legacy":
        raise AlliumHttpError(501, "legacy HTTP 服务不支持 challenge-all")
    servers = _ordered_servers(server_urls, server_weights)
    if not servers:
        raise AlliumHttpError(503, "未配置可用的 Allium HTTP 服务")
    try:
        userdata_text = userdata_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"用户数据不是 UTF-8 JSON：{exc}") from exc

    params = translate_options_for_http(options)
    params.pop("challengeLiveCharacterId", None)
    params.setdefault("liveType", "challenge")
    params.setdefault("musicId", 104)
    params.setdefault("musicDiff", "master")
    params["limit"] = 1
    payload = {"region": region, "user": userdata_text, "params": params}
    timeout = max(
        5.0,
        float(DECK_RECOMMEND_HTTP_TIMEOUT),
        float(options.get("timeout_ms") or 0) / 1000.0 + 5.0,
    )
    session = await _get_shared_session()
    errors: list[str] = []
    started = asyncio.get_running_loop().time()
    for base_url in servers:
        try:
            response = await _post_json(
                session,
                f"{base_url}/v1/recommend/challenge-all",
                payload,
                timeout,
            )
            characters = response.get("characters") if isinstance(response, dict) else None
            if not isinstance(characters, list):
                raise AlliumHttpError(502, "challenge-all 响应缺少 characters")
            raw_decks = [
                item.get("deck")
                for item in characters
                if isinstance(item, dict) and isinstance(item.get("deck"), dict)
            ]
            decks = normalize_http_decks({"decks": raw_decks})
            if not decks:
                raise AlliumHttpError(502, "challenge-all 未返回可用卡组")
            elapsed = asyncio.get_running_loop().time() - started
            return decks, elapsed, base_url
        except Exception as exc:  # noqa: BLE001 - 尝试下一台配置的服务
            errors.append(f"{base_url}: {exc}")
            logger.warning(f"[deck] Allium HTTP challenge-all 服务失败: {errors[-1]}")
    raise AlliumHttpError(503, "请求所有 Allium HTTP challenge-all 服务失败：" + " | ".join(errors))


async def probe_allium_http(server_urls: list[str], timeout: float = 3.0) -> tuple[bool, str]:
    """探测 PR #39 服务 readyz/healthz，供后端状态指令显示。"""
    servers = _ordered_servers(server_urls, None)
    if not servers:
        return False, "未配置地址"
    session = await _get_shared_session()
    errors: list[str] = []
    for base_url in servers:
        for path in ("/readyz", "/healthz"):
            try:
                async with session.get(
                    f"{base_url}{path}",
                    timeout=aiohttp.ClientTimeout(total=timeout),
                ) as response:
                    if 200 <= response.status < 300:
                        return True, base_url
                    errors.append(f"{base_url}{path}: HTTP {response.status}")
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                errors.append(f"{base_url}{path}: {exc}")
    return False, "; ".join(errors)
