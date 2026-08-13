from __future__ import annotations

import asyncio
import base64
import io
import os
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

import aiohttp
from PIL import Image

from services.log import logger
from .config import Config, parse_cfg_num
from .storage import get_file_db


file_db = get_file_db("data/llm/db.json")


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

    async def chat_completions(self, model: LlmModel, messages: list[dict], max_tokens: int | None = None, extra_body: dict | None = None) -> dict:
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
        body.update(extra_body or {})
        body.update(model.client_kwargs or {})
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=body, headers=headers, timeout=360) as resp:
                text = await resp.text()
                if resp.status != 200:
                    raise Exception(f"HTTP {resp.status}: {text[:500]}")
                try:
                    return await resp.json()
                except Exception:
                    import json
                    return json.loads(text)

    async def embeddings(self, model_name: str, texts: list[str]) -> list[list[float]]:
        base_url = self.get_base_url()
        api_key = self.get_api_key()
        url = f"{base_url}/embeddings"
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        body = {"model": model_name, "input": texts}
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=body, headers=headers, timeout=120) as resp:
                text = await resp.text()
                if resp.status != 200:
                    raise Exception(f"Embedding HTTP {resp.status}: {text[:500]}")
                data = await resp.json()
        return [item["embedding"] for item in data.get("data", [])]


def image_to_b64(img: Image.Image) -> str:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def b64_to_image(data: str) -> Image.Image:
    if data.startswith("data:"):
        data = data.split(",", 1)[1]
    return Image.open(io.BytesIO(base64.b64decode(data))).convert("RGBA")
