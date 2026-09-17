from __future__ import annotations

import asyncio
import base64
import io
import os
import random
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional
from urllib.parse import urlsplit

import aiohttp
from PIL import Image

from services.log import logger

from .config import Config, parse_cfg_num
from .storage import get_file_db

file_db = get_file_db("data/llm/db.json")
_llm_config = Config("llm.llm")
_http_session: aiohttp.ClientSession | None = None
_http_session_loop = None
_http_session_lock = asyncio.Lock()
_http_shutdown_registered = False
_NETWORK_ERRORS = (
    asyncio.TimeoutError,
    aiohttp.ClientConnectionError,
    ConnectionError,
    OSError,
)
_RETRYABLE_HTTP_STATUS = {408, 425, 429, 500, 502, 503, 504}


class LlmHttpError(RuntimeError):
    def __init__(self, message: str, *, status: int | None = None, route: str = "direct"):
        self.status = status
        self.route = route
        super().__init__(message)


def _http_config(key: str, default: Any) -> Any:
    return _llm_config.get(f"http.{key}", default)


def _http_int(key: str, default: int, minimum: int = 0) -> int:
    try:
        return max(minimum, int(_http_config(key, default)))
    except (TypeError, ValueError):
        return default


def _http_float(key: str, default: float, minimum: float = 0.0) -> float:
    try:
        return max(minimum, float(_http_config(key, default)))
    except (TypeError, ValueError):
        return default


def _http_bool(key: str, default: bool) -> bool:
    value = _http_config(key, default)
    if isinstance(value, str):
        return value.strip().lower() not in {"0", "false", "no", "off", ""}
    return bool(value)


def _normalize_proxy_url(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    if not value:
        return None
    return value if "://" in value else f"http://{value}"


def _proxy_candidates() -> list[str]:
    if not _http_bool("proxy_fallback", True):
        return []
    raw: list[Any] = []
    configured = _http_config("proxy_urls", [])
    raw.extend(configured if isinstance(configured, list) else [configured])
    raw.extend([
        os.getenv("LLM_PROXY_URL"),
        os.getenv("LLM_HTTP_PROXY"),
        os.getenv("LLM_HTTPS_PROXY"),
    ])
    port = _http_int("proxy_port", 7890, minimum=1)
    hosts = _http_config("proxy_hosts", ["host.docker.internal", "127.0.0.1"])
    if isinstance(hosts, str):
        hosts = [host.strip() for host in hosts.split(",") if host.strip()]
    if not isinstance(hosts, list):
        hosts = ["host.docker.internal", "127.0.0.1"]
    raw.extend(f"http://{host}:{port}" for host in hosts if str(host).strip())
    # 系统代理作为最后的兼容线路，确保本地 7890 优先被尝试。
    raw.extend([os.getenv("HTTPS_PROXY"), os.getenv("HTTP_PROXY")])

    result: list[str] = []
    seen: set[str] = set()
    for item in raw:
        proxy = _normalize_proxy_url(item)
        if proxy and proxy not in seen:
            seen.add(proxy)
            result.append(proxy)
    return result


def _route_label(proxy: str | None) -> str:
    if not proxy:
        return "direct"
    try:
        parsed = urlsplit(proxy)
        host = parsed.hostname or "?"
        port = f":{parsed.port}" if parsed.port else ""
    except ValueError:
        return "proxy://invalid"
    return f"proxy://{host}{port}"


def _is_retryable_network_error(exc: BaseException) -> bool:
    if isinstance(exc, (aiohttp.InvalidURL, aiohttp.TooManyRedirects)):
        return False
    return isinstance(exc, _NETWORK_ERRORS)


async def close_http_session() -> None:
    global _http_session, _http_session_loop
    session = _http_session
    _http_session = None
    _http_session_loop = None
    if session is not None and not session.closed:
        await session.close()


def _register_http_shutdown_hook() -> None:
    global _http_shutdown_registered
    if _http_shutdown_registered:
        return
    try:
        from nonebot import get_driver

        get_driver().on_shutdown(close_http_session)
        _http_shutdown_registered = True
    except (ImportError, RuntimeError, ValueError):
        return


async def _get_http_session() -> aiohttp.ClientSession:
    global _http_session, _http_session_loop
    loop = asyncio.get_running_loop()
    if _http_session is not None and not _http_session.closed and _http_session_loop is loop:
        return _http_session
    async with _http_session_lock:
        if _http_session is not None and not _http_session.closed and _http_session_loop is loop:
            return _http_session
        if _http_session is not None and not _http_session.closed:
            try:
                await _http_session.close()
            except Exception:
                logger.debug("关闭旧 LLM HTTP 会话失败", exc_info=True)
        _http_session = aiohttp.ClientSession(
            connector=aiohttp.TCPConnector(
                limit=_http_int("max_connections", 64, minimum=1),
                limit_per_host=_http_int("max_connections_per_host", 16, minimum=1),
                ttl_dns_cache=_http_int("dns_cache_ttl", 300, minimum=0),
                keepalive_timeout=_http_float("keepalive_timeout", 30.0, minimum=1.0),
                enable_cleanup_closed=True,
            ),
            raise_for_status=False,
            trust_env=False,
        )
        _http_session_loop = loop
        _register_http_shutdown_hook()
        return _http_session


async def _request_bytes(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    json_body: dict[str, Any] | None = None,
    timeout: float | None = None,
    expected_status: set[int] | None = None,
) -> bytes:
    session = await _get_http_session()
    expected_status = expected_status or {200}
    request_timeout = max(1.0, float(timeout or _http_config("total_timeout", 180)))
    connect_timeout = min(
        request_timeout,
        _http_float("connect_timeout", 12.0, minimum=0.5),
    )
    sock_read_timeout = min(
        request_timeout,
        _http_float("sock_read_timeout", 180.0, minimum=1.0),
    )
    attempts = _http_int("retries", 2, minimum=0) + 1
    backoff = _http_float("retry_backoff", 0.8, minimum=0.0)
    routes: list[str | None] = [None]
    routes.extend(_proxy_candidates())
    errors: list[str] = []

    for proxy in routes:
        route = _route_label(proxy)
        for attempt in range(attempts):
            client_timeout = aiohttp.ClientTimeout(
                total=request_timeout,
                connect=connect_timeout,
                sock_connect=connect_timeout,
                sock_read=sock_read_timeout,
            )
            try:
                async with session.request(
                    method,
                    url,
                    json=json_body,
                    headers=headers,
                    proxy=proxy,
                    timeout=client_timeout,
                ) as response:
                    body = await response.read()
                    if response.status not in expected_status:
                        text = body.decode(response.charset or "utf-8", errors="replace")
                        if response.status in _RETRYABLE_HTTP_STATUS and attempt + 1 < attempts:
                            delay = min(8.0, backoff * (2**attempt) + random.uniform(0, 0.25))
                            logger.warning(
                                f"LLM 请求 HTTP {response.status}，{delay:.2f}s 后重试: "
                                f"route={route} attempt={attempt + 1}/{attempts}"
                            )
                            await asyncio.sleep(delay)
                            continue
                        raise LlmHttpError(
                            f"HTTP {response.status}: {text[:500]}",
                            status=response.status,
                            route=route,
                        )
                    logger.debug(
                        f"LLM 请求成功: route={route} attempt={attempt + 1}/{attempts} "
                        f"status={response.status}"
                    )
                    return body
            except LlmHttpError:
                raise
            except Exception as exc:
                if not _is_retryable_network_error(exc):
                    raise
                errors.append(f"{route} {type(exc).__name__}")
                if attempt + 1 < attempts:
                    delay = min(8.0, backoff * (2**attempt) + random.uniform(0, 0.25))
                    logger.warning(
                        f"LLM {route} 网络请求失败，{delay:.2f}s 后重试: "
                        f"{type(exc).__name__} attempt={attempt + 1}/{attempts}"
                    )
                    await asyncio.sleep(delay)
                    continue
                logger.warning(
                    f"LLM {route} 已耗尽网络重试，将尝试下一条线路（若有）: "
                    f"{type(exc).__name__}"
                )
                break

    detail = "; ".join(errors[-8:]) or "没有可用的请求线路"
    raise LlmHttpError(f"LLM 网络请求失败: {detail}", route="fallback")


@dataclass
class LlmModel:
    name: str
    input_pricing: float = 0.0
    output_pricing: float = 0.0
    max_token: int = 128000
    is_multimodal: bool = False
    model_id: Optional[str] = None
    image_response: bool = False
    allow_online: bool = False
    provider: "ApiProvider" = None
    data: dict = field(default_factory=dict)
    client_kwargs: dict = field(default_factory=dict)
    extra_body: dict = field(default_factory=dict)

    def calc_price(self, input_tokens: int, output_tokens: int) -> float:
        return input_tokens * self.input_pricing + output_tokens * self.output_pricing

    def get_model_id(self) -> str:
        return self.model_id or self.name

    def get_full_name(self) -> str:
        return f"{self.provider.code}:{self.name}"


class ApiProvider:
    def __init__(self, name: str, code: str):
        self.name = name
        self.code = code
        self.config = Config(f"llm.providers.{name}")
        self.models: list[LlmModel] = []
        self.models_mtime = None
        self.cur_query_ts = 0
        self.cur_sec_query_count = 0
        self.local_quota_key = f"api_provider_{name}_local_quota"
        self.last_quota_sync_time = datetime.now()

    def get_qps_limit(self) -> int:
        return int(self.config.get("qps_limit", 5) or 5)

    def get_price_unit(self) -> str:
        return self.config.get("price_unit", "$")

    def get_quota_sync_interval_sec(self) -> int:
        return int(parse_cfg_num(self.config.get("quota_sync_interval_sec", 3600 * 6)) or 3600 * 6)

    def get_api_key(self) -> str:
        env_key = f"LLM_{self.name.upper().replace('-', '_')}_API_KEY"
        return os.getenv(env_key) or self.config.get("api_key", "") or ""

    def get_base_url(self) -> str:
        return (self.config.get("base_url", "") or "").rstrip("/")

    def update_models(self):
        mtime = self.config.mtime()
        if self.models_mtime == mtime and self.models:
            return

        def parse_price(d: dict, key: str):
            val = d.get(key)
            if isinstance(val, str):
                if "/" in val:
                    a, b = val.split("/", 1)
                    d[key] = float(a) / float(b)
                else:
                    d[key] = float(val)

        self.models = []
        for model_config in self.config.get("models", []) or []:
            model_config = dict(model_config)
            parse_price(model_config, "input_pricing")
            parse_price(model_config, "output_pricing")
            model = LlmModel(**model_config)
            model.provider = self
            self.models.append(model)
        self.models_mtime = mtime
        logger.info(f"LLM API供应方 {self.name} 模型列表更新成功，共 {len(self.models)} 个")

    def check_qps_limit(self):
        now_ts = int(time.time())
        if now_ts > self.cur_query_ts:
            self.cur_query_ts = now_ts
            self.cur_sec_query_count = 0
        if self.cur_sec_query_count >= self.get_qps_limit():
            raise Exception(f"API供应方 {self.name} QPS限制已超出")
        self.cur_sec_query_count += 1

    async def aupdate_quota(self, delta: float) -> float:
        quota = file_db.get(self.local_quota_key, 0.0)
        if not isinstance(quota, (int, float)):
            quota = 0.0
        quota += delta
        file_db.set(self.local_quota_key, quota)
        return quota

    async def chat_completions(
        self,
        model: LlmModel,
        messages: list[dict],
        max_tokens: int | None = None,
        extra_body: dict | None = None,
        timeout: float | None = None,
    ) -> dict:
        base_url = self.get_base_url()
        api_key = self.get_api_key()
        if not base_url:
            raise Exception(f"供应方 {self.name} 未配置 base_url")
        if not api_key:
            raise Exception(f"供应方 {self.name} 未配置 api_key")
        url = f"{base_url}/chat/completions"
        body: dict[str, Any] = {
            "model": model.get_model_id(),
            "messages": messages,
        }
        if max_tokens:
            body["max_tokens"] = max_tokens
        body.update(model.client_kwargs or {})
        body.update(extra_body or {})
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        raw = await _request_bytes(
            "POST",
            url,
            json_body=body,
            headers=headers,
            timeout=timeout,
            expected_status={200},
        )
        text = raw.decode("utf-8", errors="replace")
        try:
            import json

            return json.loads(text)
        except Exception as exc:
            raise Exception(
                f"供应方返回了无效 JSON: {type(exc).__name__}: {exc}; "
                f"响应内容: {text[:500]!r}"
            ) from exc

    async def embeddings(self, model_name: str, texts: list[str], timeout: float | None = None) -> list[list[float]]:
        base_url = self.get_base_url()
        api_key = self.get_api_key()
        if not base_url:
            raise Exception(f"供应方 {self.name} 未配置 base_url")
        if not api_key:
            raise Exception(f"供应方 {self.name} 未配置 api_key")
        url = f"{base_url}/embeddings"
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        body = {"model": model_name, "input": texts}
        raw = await _request_bytes(
            "POST",
            url,
            json_body=body,
            headers=headers,
            timeout=timeout or _http_config("embedding_timeout", 120),
            expected_status={200},
        )
        import json

        data = json.loads(raw.decode("utf-8", errors="replace"))
        return [item["embedding"] for item in data.get("data", [])]


def image_to_b64(img: Image.Image) -> str:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def b64_to_image(data: str) -> Image.Image:
    if data.startswith("data:"):
        data = data.split(",", 1)[1]
    return Image.open(io.BytesIO(base64.b64decode(data))).convert("RGBA")
