import asyncio
import io
import json
from typing import Any, Literal

import aiohttp

from services.log import logger

from ._config import GAMEAPI_AUTH_KEYWORDS, GAMEAPI_TOKEN, SERVER_CONFIG, SERVER_MAP
from ._errors import apiCallError, maintenanceIn, userIdBan

_GAMEAPI_TIMEOUT = aiohttp.ClientTimeout(
    total=30,
    connect=10,
    sock_connect=10,
    sock_read=30,
)
_session: aiohttp.ClientSession | None = None
_session_lock: asyncio.Lock | None = None


class HttpError(Exception):
    def __init__(self, status: int, detail: Any):
        self.status = status
        self.detail = detail
        super().__init__(f"HTTP {status}: {detail}")


class GameApiConfig:
    def __init__(self, pjsk_type: int):
        self.server_name = SERVER_MAP.get(pjsk_type, "jp")
        config = SERVER_CONFIG.get(self.server_name, {}).get("api", {})
        self.profile_api_url = config.get("profile_api_url")
        self.suite_api_url = config.get("suite_api_url")
        self.mysekai_api_url = config.get("mysekai_api_url")
        self.mysekai_photo_api_url = config.get("mysekai_photo_api_url")
        self.mysekai_upload_time_api_url = config.get("mysekai_upload_time_api_url")
        self.update_msr_sub_api_url = config.get("update_msr_sub_api_url")
        self.ranking_api_url = config.get("ranking_api_url")
        self.ranking_border_api_url = config.get("ranking_border_api_url")
        self.ranking_top100_api_url = config.get("ranking_top100_api_url")
        self.ranking_top100_new_api_url = config.get("ranking_top100_new_api_url")
        self.send_boost_api_url = config.get("send_boost_api_url")


def _get_session_lock() -> asyncio.Lock:
    global _session_lock
    if _session_lock is None:
        # 在首次异步调用中创建，避免导入时绑定错误的事件循环。
        _session_lock = asyncio.Lock()
    return _session_lock


async def _get_session() -> aiohttp.ClientSession:
    global _session

    if _session is not None and not _session.closed:
        return _session

    async with _get_session_lock():
        if _session is None or _session.closed:
            # aiohttp 默认校验证书；这里同时在 connector 和单次请求中显式开启。
            connector = aiohttp.TCPConnector(ssl=True)
            _session = aiohttp.ClientSession(
                connector=connector,
                timeout=_GAMEAPI_TIMEOUT,
            )
        return _session


async def close_gameapi_session() -> None:
    """关闭共享的游戏 API HTTP 会话；测试清理时也可显式调用。"""
    global _session

    async with _get_session_lock():
        session, _session = _session, None
        if session is not None and not session.closed:
            await session.close()


def _requires_auth(url: str) -> bool:
    is_public_api = "/api/public/" in url
    is_mysekai_api = "/mysekai/" in url or "get_upload_time" in url
    lowered_url = url.lower()
    return not (is_public_api or is_mysekai_api) and any(
        keyword in lowered_url for keyword in GAMEAPI_AUTH_KEYWORDS
    )


async def _read_error_detail(response: aiohttp.ClientResponse) -> Any:
    raw_text = await response.text()
    try:
        return json.loads(raw_text)
    except json.JSONDecodeError:
        return raw_text


def _raise_business_error(status: int, detail: Any) -> None:
    if isinstance(detail, dict):
        message = detail.get("message")
        if status == 404 and message == "account binding not found":
            raise apiCallError("未在工具箱绑定QQ和游戏账号")
        if status == 403 and message == "you are not allowed to access this player data.":
            raise apiCallError("未在 Haruki 工具箱中游戏账号设置里勾选“允许公开 API 访问”")
        if detail.get("status") == "maintenance_in":
            raise maintenanceIn("服务器正在维护中")
        if detail.get("status") == "user_id_ban":
            raise userIdBan("账号已被封禁")

    raise apiCallError(f"接口请求失败: HTTP {status}")


async def _read_response(
    response: aiohttp.ClientResponse,
    data_type: Literal["json", "bytes", "text"],
) -> Any:
    if data_type == "bytes":
        return await response.read()
    if data_type == "text":
        return await response.text()

    if "text/plain" in response.content_type:
        return json.loads(await response.text())
    if "application/octet-stream" in response.content_type:
        return json.loads(io.BytesIO(await response.read()).read())
    return await response.json()


async def request_gameapi(
    url: str,
    method: str = "GET",
    data_type: Literal["json", "bytes", "text"] = "json",
    **kwargs: Any,
) -> Any:
    """请求游戏 API，并在响应上下文内读取为 JSON、字节或文本。"""
    if data_type not in {"json", "bytes", "text"}:
        raise ValueError(f"不支持的数据类型: {data_type}")

    headers = dict(kwargs.pop("headers", {}) or {})
    requires_auth = _requires_auth(url)
    if requires_auth:
        if not GAMEAPI_TOKEN:
            raise apiCallError(
                "游戏 API Token 未配置，请设置 GAMEAPI_TOKEN 环境变量"
            )
        headers["X-Haruki-Sekai-Token"] = GAMEAPI_TOKEN
        headers["Authorization"] = f"Bearer {GAMEAPI_TOKEN}"
        logger.debug(f"请求游戏API后端: {method} {url} (使用Token)")
    else:
        logger.debug(f"请求游戏API后端: {method} {url} (无Token)")

    # 不允许调用方关闭 TLS 证书校验。
    kwargs["ssl"] = True

    try:
        session = await _get_session()
        async with session.request(method, url, headers=headers, **kwargs) as response:
            if response.status != 200:
                detail = await _read_error_detail(response)
                logger.error(
                    f"请求游戏API后端 {url} 失败: {response.status} {detail}"
                )
                _raise_business_error(response.status, detail)
            return await _read_response(response, data_type)
    except asyncio.TimeoutError as exc:
        raise apiCallError("请求游戏API超时，请稍后再试") from exc
    except aiohttp.ClientConnectionError as exc:
        raise apiCallError("连接游戏API失败，请稍后再试") from exc
    except aiohttp.ClientError as exc:
        raise apiCallError("请求游戏API失败，请稍后再试") from exc


def _register_shutdown_hook() -> None:
    try:
        from nonebot import get_driver

        get_driver().on_shutdown(close_gameapi_session)
    except (ImportError, RuntimeError, ValueError):
        # 允许在未初始化 NoneBot 的隔离测试中导入本模块。
        return


_register_shutdown_hook()
