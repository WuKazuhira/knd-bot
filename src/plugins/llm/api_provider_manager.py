from __future__ import annotations

from typing import Optional

from .api_provider import ApiProvider, LlmModel


class ApiProviderManager:
    def __init__(self):
        self.providers: list[ApiProvider] = [
            ApiProvider("futureppo", "fh"),
            ApiProvider("futureppo-b", "fh2"),
            ApiProvider("siliconflow", "sf"),
            ApiProvider("openrouter", "or"),
            ApiProvider("new-api", "na"),
            ApiProvider("ai-yyds", "ay"),
        ]

    def update_models(self):
        for provider in self.providers:
            provider.update_models()

    def find_provider(self, code_or_name: str) -> Optional[ApiProvider]:
        self.update_models()
        for provider in self.providers:
            if provider.code == code_or_name or provider.name == code_or_name:
                return provider
        return None

    def find_model(self, name: str) -> LlmModel:
        self.update_models()
        if ":" in name:
            code, model_name = name.split(":", 1)
            provider = self.find_provider(code)
            if not provider:
                raise Exception(f"找不到 LLM 供应方: {code}")
            for model in provider.models:
                if model.name == model_name or model.get_model_id() == model_name:
                    return model
            raise Exception(f"供应方 {code} 找不到模型: {model_name}")
        for provider in self.providers:
            for model in provider.models:
                if model.name == name or model.get_model_id() == name:
                    return model
        raise Exception(f"找不到 LLM 模型: {name}")

    def all_models(self) -> list[LlmModel]:
        self.update_models()
        return [m for p in self.providers for m in p.models]


api_provider_mgr = ApiProviderManager()
