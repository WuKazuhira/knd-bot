"""SK 榜线 API 模式的持久化状态。"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Literal

from services.log import logger

from .._paths import PJSK_DATA_PATH

SkApiMode = Literal["new", "old"]
DEFAULT_MODE: SkApiMode = "new"
STATE_FILE = PJSK_DATA_PATH / "sk_api_state.json"
_VALID_MODES = {"new", "old"}


def load_api_mode(path: Path = STATE_FILE) -> SkApiMode:
    if not path.exists():
        return DEFAULT_MODE
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        mode = data.get("mode") if isinstance(data, dict) else None
        if mode in _VALID_MODES:
            return mode
    except Exception as exc:
        logger.warning(f"[SK API] 读取切换状态失败，使用新 API：{exc}")
    return DEFAULT_MODE


def save_api_mode(mode: str, path: Path = STATE_FILE) -> SkApiMode:
    normalized = mode.strip().lower()
    if normalized not in _VALID_MODES:
        raise ValueError(f"不支持的 SK API 模式: {mode}")

    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temp_path.write_text(
        json.dumps({"mode": normalized}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temp_path.replace(path)
    return normalized  # type: ignore[return-value]
