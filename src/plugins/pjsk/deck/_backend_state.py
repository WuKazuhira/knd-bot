"""组卡后端状态。

组卡计算固定使用 Python 进程内的 allium-sekai-deck，不再支持 HTTP
或 Rust deck-service。旧状态文件中的 http/both 会安全回落到 allium。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import List, Literal

from services.log import logger

from .._paths import ONDEMAND_PATH

DeckBackendMode = Literal["allium"]
STATE_FILE = ONDEMAND_PATH / "deck_backend_state.json"
_VALID_MODES = {"allium"}

MODE_LABELS = {
    "allium": "allium（进程内，C++）",
}


def load_backend_mode(path: Path = STATE_FILE) -> DeckBackendMode:
    """读取后端状态；历史 HTTP 状态一律回落为 allium。"""
    if not path.exists():
        return "allium"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        mode = data.get("mode") if isinstance(data, dict) else None
        if mode == "allium":
            return "allium"
        if mode in {"http", "both"}:
            logger.warning("[deck] 检测到已停用的 HTTP 组卡后端状态，回落 allium")
        elif mode is not None:
            logger.warning(f"[deck] 组卡后端状态无效 {mode!r}，回落 allium")
    except Exception as exc:
        logger.warning(f"[deck] 读取后端状态失败，回落 allium：{exc}")
    return "allium"


def save_backend_mode(mode: str, path: Path = STATE_FILE) -> DeckBackendMode:
    normalized = mode.strip().lower()
    if normalized not in _VALID_MODES:
        raise ValueError("组卡后端已固定为 allium，不支持 HTTP/deck-service")

    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temp_path.write_text(
        json.dumps({"mode": normalized}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temp_path.replace(path)
    return "allium"


def active_backends(path: Path = STATE_FILE) -> List[str]:
    """给推荐器用：始终只启用 allium。"""
    load_backend_mode(path)
    return ["allium"]
