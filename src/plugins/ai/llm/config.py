from __future__ import annotations

import ast
import operator
import os
from pathlib import Path
from typing import Any

import yaml

from config.path_config import CONFIG_PATH, PROJECT_ROOT

PUBLIC_CONFIG_ROOT = PROJECT_ROOT / "example_config"
LOCAL_CONFIG_ROOT = Path(os.getenv("KNDBOT_LOCAL_CONFIG_DIR", CONFIG_PATH))


class ConfigItem:
    def __init__(self, config: "Config", key: str):
        self.config = config
        self.key = key

    def get(self, default: Any = None) -> Any:
        return self.config.get(self.key, default)


class Config:
    def __init__(self, name: str):
        self.name = name
        parts = name.split(".")
        relative_path = Path(*parts).with_suffix(".yaml")
        local_path = LOCAL_CONFIG_ROOT / relative_path
        public_path = PUBLIC_CONFIG_ROOT / relative_path
        self.path = local_path if local_path.exists() else public_path
        self._env_prefix = "LLM_" + "_".join(part.upper().replace("-", "_") for part in parts)
        self._data: dict[str, Any] | None = None
        self._mtime: float | None = None

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {}
        mtime = self.path.stat().st_mtime
        if self._data is None or self._mtime != mtime:
            with self.path.open("r", encoding="utf-8") as f:
                self._data = yaml.safe_load(f) or {}
            self._mtime = mtime
        return self._data

    def mtime(self) -> float | None:
        return self.path.stat().st_mtime if self.path.exists() else None

    def get(self, key: str | None = None, default: Any = None) -> Any:
        data: Any = self._load()
        if not key:
            return data
        env_key = f"{self._env_prefix}_{key.upper().replace('.', '_').replace('-', '_')}"
        env_value = os.getenv(env_key)
        if env_value is not None:
            return env_value
        for part in key.split("."):
            if not isinstance(data, dict) or part not in data:
                return default
            data = data[part]
        return data

    def item(self, key: str) -> ConfigItem:
        return ConfigItem(self, key)


def get_cfg_or_value(value: Any) -> Any:
    return value.get() if isinstance(value, ConfigItem) else value


_UNARY_OPERATORS = {ast.UAdd: operator.pos, ast.USub: operator.neg}
_BINARY_OPERATORS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
}


def _evaluate_number(node: ast.expr) -> int | float:
    if isinstance(node, ast.Constant) and type(node.value) in (int, float):
        return node.value
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY_OPERATORS:
        return _UNARY_OPERATORS[type(node.op)](_evaluate_number(node.operand))
    if isinstance(node, ast.BinOp) and type(node.op) in _BINARY_OPERATORS:
        return _BINARY_OPERATORS[type(node.op)](
            _evaluate_number(node.left), _evaluate_number(node.right)
        )
    raise ValueError("配置数值只支持数字和基本算术运算")


def parse_cfg_num(value: Any) -> int | float:
    value = get_cfg_or_value(value)
    if isinstance(value, str):
        return _evaluate_number(ast.parse(value.strip(), mode="eval").body)
    return value
