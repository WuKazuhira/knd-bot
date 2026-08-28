"""组卡后端选择的持久化状态。

kndbot 有两个组卡后端：
  * http   —— Rust 写的 deck-service，跑在独立容器里，走 HTTP
  * allium —— allium-sekai-deck（内嵌 C++ 引擎），在 bot 进程内直接调

写法照搬同目录风格的 sk/_api_state.py：原子写入、非法值回落默认、
读失败不抛异常。默认值取配置里的 DECK_RECOMMEND_BACKENDS，
所以没切过的部署行为完全不变。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import List, Literal

from services.log import logger

from .._config import DECK_RECOMMEND_BACKENDS
from .._paths import PJSK_DATA_PATH

DeckBackendMode = Literal["http", "allium", "both"]
STATE_FILE = PJSK_DATA_PATH / "deck_backend_state.json"
_VALID_MODES = {"http", "allium", "both"}

# 模式 -> 实际启用的后端列表。both 里 allium 在前，与 do_recommend 的执行顺序一致。
_MODE_TO_BACKENDS: dict[str, List[str]] = {
    "http": ["http"],
    "allium": ["allium"],
    "both": ["allium", "http"],
}

MODE_LABELS = {
    "http": "deck-service（独立容器，Rust）",
    "allium": "allium（进程内，C++）",
    "both": "两个都跑（结果合并去重）",
}


def _default_mode() -> DeckBackendMode:
    """没切换过时沿用配置：既支持只配 http，也支持配了两个。"""
    configured = {b for b in DECK_RECOMMEND_BACKENDS if b in {"http", "allium"}}
    if configured == {"http", "allium"}:
        return "both"
    if configured == {"allium"}:
        return "allium"
    return "http"


def load_backend_mode(path: Path = STATE_FILE) -> DeckBackendMode:
    if not path.exists():
        return _default_mode()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        mode = data.get("mode") if isinstance(data, dict) else None
        if mode in _VALID_MODES:
            return mode  # type: ignore[return-value]
    except Exception as exc:
        logger.warning(f"[deck] 读取后端切换状态失败，回落默认：{exc}")
    return _default_mode()


def save_backend_mode(mode: str, path: Path = STATE_FILE) -> DeckBackendMode:
    normalized = mode.strip().lower()
    if normalized not in _VALID_MODES:
        raise ValueError(f"不支持的组卡后端: {mode}")

    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temp_path.write_text(
        json.dumps({"mode": normalized}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temp_path.replace(path)
    return normalized  # type: ignore[return-value]


def active_backends(path: Path = STATE_FILE) -> List[str]:
    """给 do_recommend 用：当前该启用哪些后端。"""
    return list(_MODE_TO_BACKENDS[load_backend_mode(path)])
