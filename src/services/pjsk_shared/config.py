"""PJSK 共享路径和最小配置。

此模块只供非 PJSK Python 插件和独立脚本使用，不能反向 import plugins.pjsk，
避免为了读取路径/服务器映射而重新加载完整 Python PJSK 包。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from config.path_config import CONFIG_PATH
from utils.pjsk_paths import ONDEMAND_PATH, STATIC_PATH


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    with path.open("r", encoding="utf-8") as file:
        value = yaml.safe_load(file) or {}
    return value if isinstance(value, dict) else {}


_settings = _load_yaml(CONFIG_PATH / "pjsk" / "settings.yaml")
SERVER_MAP = {
    int(key): str(value)
    for key, value in (_settings.get("server_map") or {0: "jp", 1: "tw", 2: "cn"}).items()
}

# 与旧 plugins.pjsk._config 保持兼容的命名，供迁移中的独立脚本使用。
data_path = ONDEMAND_PATH
static_path = STATIC_PATH
PY