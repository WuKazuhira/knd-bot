"""Go 迁移所有权兼容层。

当前部署中 remote/sk 指令仍由 Python 插件处理；当 Go 服务接管某项
功能时，可通过环境变量列出对应 ownership key，使 Python matcher 安静退出。
"""

from __future__ import annotations

import json
import os
from functools import lru_cache


@lru_cache(maxsize=1)
def _owned() -> frozenset[str]:
    raw = (os.getenv("KND_GO_OWNED_COMMANDS") or "").strip()
    if not raw:
        return frozenset()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        data = [item.strip() for item in raw.split(",") if item.strip()]
    if not isinstance(data, list):
        return frozenset()
    return frozenset(str(item).strip() for item in data if str(item).strip())


def go_owns(key: str) -> bool:
    """返回指定功能是否由 Go 接管；未配置时默认由 Python 处理。"""
    return key in _owned()
