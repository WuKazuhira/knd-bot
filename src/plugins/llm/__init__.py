from __future__ import annotations

import asyncio
import base64
import io
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Union, Any

import aiohttp
from PIL import Image

from services.log import logger
from .api_provider import ApiProvider, LlmModel, image_to_b64, b64_to_image
from .api_provider_manager import api_provider_mgr
from .config import Config, ConfigItem, get_cfg_or_value
from .storage import get_file_db


config = Config("llm.llm")
file_db = get_file_db("data/llm/db.json")

CHAT_TIMEOUT_CFG = config.item("chat_timeout")
CHAT_MODEL_SWITCH_INTERVAL_CFG = config.item("chat_model_switch_interval")
CHAT_MAX_TOKENS_CFG = config.item("chat_max_tokens")
_session_id_top = 0


def get_model_preset(key: str) -> Union[str, list[str], dict, None]:
    ret = Config("llm.model_preset").get(key)

    def parse_ref(v):
        return get_model_preset(v[1:]) if isinstance(v, str) and v.startswith("&") else v

    if isinstance(ret, str):
        return parse_ref(ret)
    if isinstance(ret, list):
        return [parse_ref(v) for v in ret]
    if isinstance(ret, dict):
        return {k: parse_ref(v) for k, v in ret.items()}
    return ret


@dataclass
class ChatSessionResponse:
    result: str
    provider: ApiProvider
    model: LlmModel
    prompt_tokens: int
    completion_tokens: int
    cost: float
    quota: float
    reasoning: Optional[str] = None
    images: list[Image.Image] = field(default_factory=list)
    result_list: list[Union[str, Image.Image]] = field(default_factory=list)


class ChatSession:
    @staticmethod
    def check_model_name(model_name: Union[str, list[str]], mode="text"):
        names = model_name if isinstance(model_name, list) else [model_name]
        for name in names:
            model = api_provider_mgr.find_model(name)
            if mode == "mm" and not model.is_multimodal:
                raise Exception(f"模型 {name} 不支持多模态输入")
            if mode == "image" and not model.image_response:
                raise Exception(f"模型 {name} 不支持图片回复")

    def __init__(self, system_prompt: str | None = None):
        global _session_id_top
        _session_id_top += 1
        self.id = _session_id_top
        self.content: list[dict] = []
        self.has_image = False
        self.update_time = datetime.now()
        if system_prompt:
            self.append_system_content(system_prompt, verbose=False)

    def append_content(self, role: str, text: str, imgs: list[str | Image.Image] | None = None, verbose: bool = True):
        imgs = imgs or []
        if not text and not imgs:
            return
        normalized_imgs = []
        for img in imgs:
            normalized_imgs.append(image_to_b64(img) if isinstance(img, Image.Image) else img)
        if normalized_imgs:
            content: Any = [{"type": "text", "text": text or ""}]
            for img in normalized_imgs:
                content.append({"type": "image_url", "image_url": {"url": img}})
            self.has_image = True
        else:
            content = text
        self.content.append({"role": role, "content": content})
        self.update_time = datetime.now()
        if verbose:
            logger.info(f"LLM会话{self.id}添加 {role}: {(text or '')[:120]}")

    def append_system_content(self, text: str, verbose: bool = True):
        self.append_content("system", text, verbose=verbose)

    def append_user_content(self, text: str, imgs: list[str | Image.Image] | None = None, verbose: bool = True):
        self.append_content("user", text, imgs, verbose=verbose)

    def append_bot_content(self, text: str, imgs: list[str | Image.Image] | None = None, verbose: bool = True):
        self.append_content("assistant", text, imgs, verbose=verbose)

    def limit_length(self, limit: int, drop: str = "oldest"):
        if len(self.content) <= limit:
            return
        system = self.content[0] if self.content and self.content[0].get("role") == "system" else None
        body = self.content[1:] if system else self.content
        body = body[-limit:] if drop == "oldest" else body[:limit]
        self.content = ([system] if system else []) + body

    def clear_content(self):
        self.content = []
        self.has_image = False
        self.update_time = datetime.now()

    async def get_response(
        self,
        model_name: Union[str, list[str]],
        process_func=None,
        image_response: bool = False,
        timeout: Union[int, ConfigItem] = CHAT_TIMEOUT_CFG,
        model_switch_interval: Union[int, ConfigItem] = CHAT_MODEL_SWITCH_INTERVAL_CFG,
        max_tokens: Union[int, ConfigItem] = CHAT_MAX_TOKENS_CFG,
        provider_extra_body: dict[str, dict[str, Any]] | None = None,
    ) -> ChatSessionResponse:
        names = model_name if isinstance(model_name, list) else [model_name]
        errs: list[str] = []
        for idx, name in enumerate(names):
            try:
                model = api_provider_mgr.find_model(name)
                provider = model.provider
                if self.has_image and not model.is_multimodal:
                    raise Exception(f"模型 {name} 不支持多模态输入")
                provider.check_qps_limit()
                extra_body = dict(model.extra_body or {})
                if provider_extra_body:
                    provider_overrides = (
                        provider_extra_body.get(provider.code)
                        or provider_extra_body.get(provider.name)
                        or {}
                    )
                    extra_body.update(provider_overrides)
                if model.image_response:
                    extra_body["image_response"] = image_response
                    extra_body["modalities"] = ["image", "text"]
                resp = await asyncio.wait_for(
                    provider.chat_completions(
                        model,
                        self.content,
                        max_tokens=int(get_cfg_or_value(max_tokens) or 2048),
                        extra_body=extra_body,
                    ),
                    timeout=int(get_cfg_or_value(timeout) or 60),
                )
                if resp.get("error"):
                    error = resp["error"]
                    if isinstance(error, dict):
                        error = error.get("message") or error.get("type") or repr(error)
                    raise Exception(f"供应方返回错误: {error}")
                message = resp["choices"][0]["message"]
                usage = resp.get("usage") or {}
                result = message.get("content") or ""
                reasoning = message.get("reasoning_content") or message.get("reasoning")
                images: list[Image.Image] = []
                result_list: list[Union[str, Image.Image]] = [result]
                for item in message.get("images", []) or []:
                    img = b64_to_image(item["image_url"]["url"])
                    images.append(img)
                    result_list.append(img)
                prompt_tokens = int(usage.get("prompt_tokens") or 0)
                completion_tokens = int(usage.get("completion_tokens") or 0)
                cost = model.calc_price(prompt_tokens, completion_tokens)
                quota = await provider.aupdate_quota(-cost)
                ret = ChatSessionResponse(result, provider, model, prompt_tokens, completion_tokens, cost, quota, reasoning, images, result_list)
                processed = process_func(ret) if process_func else result
                if isinstance(processed, str):
                    result = processed
                    ret.result = processed
                self.append_bot_content(result, imgs=[image_to_b64(img) for img in images], verbose=False)
                return ret
            except asyncio.TimeoutError:
                error = f"请求超时（超过 {int(get_cfg_or_value(timeout) or 60)} 秒）"
                errs.append(f"{name}: {error}")
                logger.exception(f"调用模型 {name} 超时")
                if idx + 1 < len(names):
                    await asyncio.sleep(int(get_cfg_or_value(model_switch_interval) or 1))
            except Exception as e:
                error = f"{type(e).__name__}: {e!r}"
                errs.append(f"{name}: {error}")
                logger.exception(f"调用模型 {name} 失败")
                if idx + 1 < len(names):
                    await asyncio.sleep(int(get_cfg_or_value(model_switch_interval) or 1))
        raise Exception("; ".join(errs))


async def download_image_to_b64(url: str) -> str:
    async with aiohttp.ClientSession() as session:
        async with session.get(url, timeout=30) as resp:
            if resp.status != 200:
                raise Exception(f"下载图片失败 HTTP {resp.status}")
            data = await resp.read()
    return "data:image/png;base64," + base64.b64encode(data).decode()


async def translate_text(text: str, additional_info: str | None = None, dst_lang: str = "中文", timeout: int = 20, default=None, model=None, cache: bool = True):
    if not text:
        return default
    cache_key = f"{dst_lang}:{additional_info or ''}:{text}"
    if cache:
        cached = file_db.get("text_translation:" + cache_key)
        if cached:
            return cached
    prompt = f"请将以下文本翻译成{dst_lang}，只输出翻译结果。"
    if additional_info:
        prompt += f"\n补充说明：{additional_info}"
    session = ChatSession(prompt)
    session.append_user_content(text, verbose=False)
    model_name = model or get_model_preset("translation") or get_model_preset("basic_chat_mm")
    try:
        resp = await session.get_response(model_name, timeout=timeout)
        result = resp.result.strip()
        if cache:
            file_db.set("text_translation:" + cache_key, result)
        return result
    except Exception as e:
        logger.warning(f"翻译失败: {e}")
        return default


async def get_text_embedding(texts: list[str], model_name: str) -> list[list[float]]:
    model = api_provider_mgr.find_model(model_name)
    return await model.provider.embeddings(model.get_model_id(), texts)


__all__ = [
    "ChatSession", "ChatSessionResponse", "get_model_preset", "translate_text",
    "api_provider_mgr", "download_image_to_b64", "get_text_embedding",
]
