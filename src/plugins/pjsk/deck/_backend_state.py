"""组卡后端选择的持久化状态。

支持进程内 allium、PR #39 风格的 HTTP 服务，以及两者并行去重。
默认仍由配置决定，未配置时使用进程内 allium。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import List, Literal

from services.log import logger

from .._config import DECK_RECOMMEND_BACKENDS
from .._paths import ONDEMAND_PATH

DeckBackendMode = Literal["http", "allium", "both"]
STATE_FILE = ONDEMAND_PATH / "deck_backend_state.json"
_VALID_MODES = {"http", "allium", "both"}
_MODE_TO_BACKENDS: dict[str, List[str]] = {
    "http": ["http"],
    "allium": ["allium"],
    "both": ["allium", "http"],
}

MODE_LABELS = {
    "http": "allium HTTP（PR39 服务）",
    "allium": "allium（进程内，C++）",
    "both": "allium + HTTP（结果合并）",
}


def _default_mode() -> DeckBackendMode:
    configured = {item for item in DECK_RECOMMEND_BACKENDS if item in {"http", "allium"}}
    if configured == {"http", "allium"}:
        return "both"
    if configured == {"http"}:
        return "http"
    return "allium"


def load_backend_mode(path: Path = STATE_FILE) -> DeckBackendMode:
    """读取后端状态；旧状态和非法状态都安全回到配置默认值。"""
    if not path.exists():
        return _default_mode()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        mode = data.get("mode") if isinstance(data, dict) else None
        if mode in _VALID_MODES:
            return mode  # type: ignore[return-value]
        if mode is not None:
            logger.warning(f"[deck] 组卡后端状态无效 {mode!r}，回落配置默认")
    except Exception as exc:
        logger.warning(f"[deck] 读取后端状态失败，回落配置默认：{exc}")
    return _default_mode()


def save_backend_mode(mode: str, path: Path = STATE_FILE) -> DeckBackendMode:
    normalized = mode.strip().lower()
    if normalized not in _VALID_MODES:
        raise ValueError("不支持的组卡后端：请使用 http、allium 或 both")

    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temp_path.write_text(
        json.dumps({"mode": normalized}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temp_path.replace(path)
    return normalized  # type: ignore[return-value]


def active_backends(path: Path = STATE_FILE) -> List[str]:
    """返回当前模式启用的后端，both 保持 allium 在前。"""
    return list(_MODE_TO_BACKENDS[load_backend_mode(path)])
