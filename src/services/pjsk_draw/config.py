from __future__ import annotations

import os
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, List

import yaml

try:
    from config.path_config import CONFIG_PATH
except ModuleNotFoundError:
    CONFIG_PATH = Path(os.getenv("KNDBOT_CONFIG_DIR", "config"))


def _load_yaml(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as file:
        return yaml.safe_load(file) or {}


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    result = deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


_config_dir = CONFIG_PATH / "pjsk"
_settings = _load_yaml(_config_dir / "settings.yaml")
SERVER_CONFIG = _load_yaml(_config_dir / "servers.yaml")
_local = _load_yaml(Path(os.getenv("PJSK_LOCAL_CONFIG", _config_dir / "local.yaml")))
if _local:
    _settings = _deep_merge(_settings, _local.get("settings", {}) or {})
    SERVER_CONFIG = _deep_merge(SERVER_CONFIG, _local.get("servers", {}) or {})

_draw = _settings.get("draw") or {}

SERVER_MAP: Dict[int, str] = {
    int(key): value
    for key, value in (_settings.get("server_map") or {0: "jp", 1: "tw", 2: "cn"}).items()
}


def _split_urls(value: str | None, default: List[str] | None = None) -> List[str]:
    urls = [item.strip().rstrip("/") for item in (value or "").split(",") if item.strip()]
    if not urls:
        urls = [str(item).strip().rstrip("/") for item in (default or []) if str(item).strip()]
    return urls


_endpoints = _settings.get("endpoints", {})

DRAW_CHART_PREVIEW_BASE_URL: str = str(_endpoints.get("chart_preview_base_url") or "").rstrip("/")

DRAW_SERVICE_URLS: List[str] = _split_urls(
    os.getenv("PJSK_DRAW_SERVICE_URLS"),
    _draw.get("service_urls", []),
)

DRAW_SERVICE_TIMEOUT: float = float(os.getenv("PJSK_DRAW_SERVICE_TIMEOUT") or _draw.get("timeout", 120))

DRAW_SERVICE_FALLBACK_LOCAL: bool = str(
    os.getenv("PJSK_DRAW_SERVICE_FALLBACK_LOCAL") or _draw.get("fallback_local", True)
).lower() not in ("0", "false", "no")
