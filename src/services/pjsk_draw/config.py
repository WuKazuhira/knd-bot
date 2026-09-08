"""绘图服务配置。

独立进程运行时不能依赖 plugins 层，所以这里自己读一遍
config/pjsk/settings.yaml，只取绘图服务需要的几项。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List

import yaml

try:
    from config.path_config import CONFIG_PATH
except ModuleNotFoundError:  # 独立进程 / 容器内可能没有 config 包
    CONFIG_PATH = Path(os.getenv("KNDBOT_CONFIG_DIR", "config"))


def _load_yaml(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as file:
        return yaml.safe_load(file) or {}


_settings = _load_yaml(CONFIG_PATH / "pjsk" / "settings.yaml")
_local = _load_yaml(Path(os.getenv("PJSK_LOCAL_CONFIG", CONFIG_PATH / "pjsk" / "local.yaml")))
if _local:
    _settings.update(_local.get("settings", {}) or {})

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

# 谱面预览外部图源（与插件 _config.CHART_PREVIEW_BASE_URL 同源）
DRAW_CHART_PREVIEW_BASE_URL: str = str(_endpoints.get("chart_preview_base_url") or "").rstrip("/")

# 绘图服务地址列表。留空 = 不走 HTTP，直接在 bot 进程内渲染。
DRAW_SERVICE_URLS: List[str] = _split_urls(
    os.getenv("PJSK_DRAW_SERVICE_URLS"),
    _draw.get("service_urls", []),
)

# 单次渲染请求超时（秒）
DRAW_SERVICE_TIMEOUT: float = float(os.getenv("PJSK_DRAW_SERVICE_TIMEOUT") or _draw.get("timeout", 120))

# HTTP 渲染失败时是否回退到进程内渲染
DRAW_SERVICE_FALLBACK_LOCAL: bool = str(
    os.getenv("PJSK_DRAW_SERVICE_FALLBACK_LOCAL") or _draw.get("fallback_local", True)
).lower() not in ("0", "false", "no")
