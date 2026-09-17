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
from urllib.parse import quote, urlsplit, urlunsplit

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


def _proxy_candidates(proxy_port: int | None = None) -> list[str]:
    if proxy_port is None and not _http_bool("proxy_fallback", True):
        return []
    raw: list[Any] = []
    if proxy_port is None:
        configured = _http_config("proxy_urls", [])
        raw.extend(configured if isinstance(configured, list) else [configured])
        raw.extend([
            os.getenv("LLM_PROXY_URL"),
            os.getenv("LLM_HTTP_PROXY"),
            os.getenv("LLM_HTTPS_PROXY"),
        ])

    port = proxy_port or _http_int("proxy_port", 7890, minimum=1)
    hosts = _http_config(
        "proxy_hosts",
        ["172.22.0.1", "host.docker.internal", "127.0.0.1"],
    )
    if isinstance(hosts, str):
        hosts = [host.strip() for host in hosts.split(",") if host.strip()]
    if not isinstance(hosts, list):
        hosts = ["172.22.0.1", "host.docker.internal", "127.0.0.1"]
    raw.extend(f"http://{host}:{port}" for host in hosts if str(host).strip())
    if proxy_port is None:
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
    force_proxy: bool = False,
    proxy_port: int | None = None,
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
    proxy_routes = _proxy_candidates(proxy_port)
    routes: list[str | None] = proxy_routes if force_proxy else [None, *proxy_routes]
    if force_proxy and not routes:
        raise LlmHttpError("该请求要求代理，但没有配置可用的代理线路", route="proxy")
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


def _is_gemini_model_id(model_id: str) -> bool:
    return model_id.removeprefix("models/").strip().lower().startswith("gemini-")


def _gemini_parts(content: Any) -> list[dict[str, Any]]:
    items = content if isinstance(content, list) else [content]
    parts: list[dict[str, Any]] = []
    for item in items:
        if isinstance(item, str):
            if item:
                parts.append({"text": item})
            continue
        if not isinstance(item, dict):
            continue
        item_type = item.get("type")
        if item_type == "text" or "text" in item:
            text = item.get("text") or ""
            if text:
                parts.append({"text": str(text)})
            continue
        if item_type != "image_url":
            continue
        image_value = item.get("image_url")
        image_url = image_value.get("url") if isinstance(image_value, dict) else image_value
        if not isinstance(image_url, str) or not image_url:
            continue
        if image_url.startswith("data:") and "," in image_url:
            metadata, encoded = image_url.split(",", 1)
            mime_type = metadata[5:].split(";", 1)[0] or "application/octet-stream"
            parts.append({"inlineData": {"mimeType": mime_type, "data": encoded}})
        else:
            mime_type = (
                image_value.get("mime_type", "image/*")
                if isinstance(image_value, dict)
                else "image/*"
            )
            parts.append({"fileData": {"mimeType": mime_type, "fileUri": image_url}})
    return parts


_GEMINI_CONFIG_KEY_MAP = {
    "max_output_tokens": "maxOutputTokens",
    "stop_sequences": "stopSequences",
    "response_mime_type": "responseMimeType",
    "response_schema": "responseSchema",
    "response_modalities": "responseModalities",
    "thinking_config": "thinkingConfig",
    "top_p": "topP",
    "top_k": "topK",
    "candidate_count": "candidateCount",
    "presence_penalty": "presencePenalty",
    "frequency_penalty": "frequencyPenalty",
}

_GEMINI_THINKING_KEY_MAP = {
    "include_thoughts": "includeThoughts",
    "thinking_budget": "thinkingBudget",
    "thinking_level": "thinkingLevel",
}


def _normalize_gemini_thinking_config(value: Any) -> Any:
    if not isinstance(value, dict):
        return value
    normalized: dict[str, Any] = {}
    for key, item in value.items():
        normalized_key = _GEMINI_THINKING_KEY_MAP.get(key, key)
        if normalized_key == "thinkingBudget" and isinstance(item, str):
            stripped = item.strip()
            if stripped.lower() in {"minimal", "low", "medium", "high"}:
                normalized_key = "thinkingLevel"
                item = stripped.lower()
            else:
                try:
                    item = int(stripped)
                except ValueError:
                    pass
        normalized[normalized_key] = item
    return normalized


def _normalize_gemini_generation_config(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    normalized: dict[str, Any] = {}
    for key, item in value.items():
        normalized_key = _GEMINI_CONFIG_KEY_MAP.get(key, key)
        if normalized_key == "thinkingConfig":
            item = _normalize_gemini_thinking_config(item)
        elif normalized_key == "responseModalities" and isinstance(item, list):
            item = [str(modality).upper() for modality in item]
        normalized[normalized_key] = item
    return normalized


def _gemini_request_body(
    messages: list[dict],
    max_tokens: int | None,
    model_kwargs: dict[str, Any] | None,
    extra_body: dict[str, Any] | None,
    *,
    image_response: bool = False,
    strict: bool = False,
) -> dict[str, Any]:
    system_parts: list[dict[str, Any]] = []
    contents: list[dict[str, Any]] = []
    for message in messages:
        if not isinstance(message, dict):
            continue
        parts = _gemini_parts(message.get("content"))
        if not parts:
            continue
        role = str(message.get("role") or "user")
        if role == "system":
            system_parts.extend(parts)
            continue
        role = "model" if role == "assistant" else "user"
        if contents and contents[-1]["role"] == role:
            contents[-1]["parts"].extend(parts)
        else:
            contents.append({"role": role, "parts": parts})

    body: dict[str, Any] = {"contents": contents}
    if system_parts:
        body["systemInstruction"] = {"parts": system_parts}

    generation_config: dict[str, Any] = {}
    if max_tokens:
        generation_config["maxOutputTokens"] = max_tokens

    client_kwargs = dict(model_kwargs or {})
    request_extra = dict(extra_body or {})
    for source in (client_kwargs, request_extra):
        for key in ("generationConfig", "generation_config"):
            value = source.pop(key, None)
            if isinstance(value, dict):
                generation_config.update(_normalize_gemini_generation_config(value))
        value = source.pop("thinkingConfig", source.pop("thinking_config", None))
        if isinstance(value, dict):
            thinking_config = generation_config.setdefault("thinkingConfig", {})
            thinking_config.update(_normalize_gemini_thinking_config(value))

    requested_image = bool(request_extra.pop("image_response", False))
    request_extra.pop("modalities", None)
    if requested_image:
        if not image_response:
            raise ValueError("Google Gemini 模型未配置图片回复能力")
        generation_config.setdefault("responseModalities", ["TEXT", "IMAGE"])

    allowed_top_level = {
        "cachedContent",
        "safetySettings",
        "tools",
        "toolConfig",
        "systemInstruction",
    }
    for source in (client_kwargs, request_extra):
        for key, value in source.items():
            normalized_key = {
                "cached_content": "cachedContent",
                "safety_settings": "safetySettings",
                "tool_config": "toolConfig",
                "system_instruction": "systemInstruction",
            }.get(key, key)
            if not strict or normalized_key in allowed_top_level:
                body[normalized_key] = value

    if generation_config:
        body["generationConfig"] = generation_config
    return body


def _futureppo_gemini_url(base_url: str, model_id: str) -> str:
    parsed = urlsplit(base_url)
    if not parsed.scheme or not parsed.netloc:
        raise ValueError(f"供应方 base_url 无效: {base_url}")
    model_path = quote(model_id.removeprefix("models/"), safe="")
    origin = urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))
    return f"{origin}/v1beta/models/{model_path}:generateContent"


def _normalize_gemini_response(data: dict[str, Any]) -> dict[str, Any]:
    if data.get("error"):
        return data
    candidates = data.get("candidates") or []
    if not candidates:
        raise ValueError("Gemini 返回中没有 candidates")

    candidate = candidates[0] or {}
    content = candidate.get("content") or {}
    text_parts: list[str] = []
    reasoning_parts: list[str] = []
    images: list[dict[str, Any]] = []
    for part in content.get("parts") or []:
        if not isinstance(part, dict):
            continue
        text = part.get("text")
        if text is not None:
            (reasoning_parts if part.get("thought") else text_parts).append(str(text))
        inline_data = part.get("inlineData") or part.get("inline_data")
        if isinstance(inline_data, dict) and inline_data.get("data"):
            mime_type = inline_data.get("mimeType") or inline_data.get("mime_type") or "application/octet-stream"
            images.append({
                "image_url": {
                    "url": f"data:{mime_type};base64,{inline_data['data']}"
                }
            })

    message: dict[str, Any] = {
        "role": "assistant",
        "content": "".join(text_parts),
    }
    if reasoning_parts:
        message["reasoning_content"] = "".join(reasoning_parts)
    if images:
        message["images"] = images

    usage_metadata = data.get("usageMetadata") or {}
    usage = {
        "prompt_tokens": int(usage_metadata.get("promptTokenCount") or 0),
        "completion_tokens": int(usage_metadata.get("candidatesTokenCount") or 0),
        "reasoning_tokens": int(usage_metadata.get("thoughtsTokenCount") or 0),
        "total_tokens": int(usage_metadata.get("totalTokenCount") or 0),
    }
    return {
        "choices": [{
            "index": 0,
            "message": message,
            "finish_reason": candidate.get("finishReason"),
        }],
        "usage": usage,
    }


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
    disabled: bool = False
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
        env_keys = [env_key]
        if self.name == "google":
            env_keys.extend(["GEMINI_API_KEY", "GOOGLE_API_KEY"])
        for key in env_keys:
            value = os.getenv(key)
            if value:
                return value
        return self.config.get("api_key", "") or ""

    def get_base_url(self) -> str:
        base_url = self.config.get("base_url", "")
        if not base_url and self.name == "google":
            base_url = self.config.get("http_options.base_url", "")
        return (base_url or "").rstrip("/")

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
            if model_config.pop("disabled", False):
                continue
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

    def _uses_native_gemini(self, model: LlmModel) -> bool:
        if self.name == "google":
            return True
        return self.name in {"futureppo", "futureppo-b"} and _is_gemini_model_id(model.get_model_id())

    async def _gemini_chat_completions(
        self,
        model: LlmModel,
        messages: list[dict],
        api_key: str,
        base_url: str,
        max_tokens: int | None = None,
        extra_body: dict | None = None,
        timeout: float | None = None,
    ) -> dict:
        import json

        url = _futureppo_gemini_url(base_url, model.get_model_id())
        body = _gemini_request_body(
            messages,
            max_tokens,
            model.client_kwargs,
            extra_body,
            image_response=model.image_response,
            strict=self.name == "google",
        )
        headers = {"x-goog-api-key": api_key, "Content-Type": "application/json"}
        if self.name != "google":
            headers["Authorization"] = f"Bearer {api_key}"
        raw = await _request_bytes(
            "POST",
            url,
            json_body=body,
            headers=headers,
            timeout=timeout,
            expected_status={200},
            force_proxy=True,
            proxy_port=7890 if self.name == "google" else None,
        )
        try:
            data = json.loads(raw.decode("utf-8", errors="replace"))
        except Exception as exc:
            raise Exception(
                f"供应方返回了无效 Gemini JSON: {type(exc).__name__}: "
                f"{exc}; 响应内容: {raw[:500]!r}"
            ) from exc
        return _normalize_gemini_response(data)

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
        if self._uses_native_gemini(model):
            return await self._gemini_chat_completions(
                model,
                messages,
                api_key,
                base_url,
                max_tokens=max_tokens,
                extra_body=extra_body,
                timeout=timeout,
            )
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
