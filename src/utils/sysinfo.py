from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

import psutil

from config.path_config import DATA_PATH


@lru_cache(maxsize=1)
def in_container() -> bool:
    if Path("/.dockerenv").exists():
        return True
    try:
        return "docker" in Path("/proc/1/cgroup").read_text() or os.getenv("KNDBOT_IN_CONTAINER") == "1"
    except OSError:
        return False


def disk_usage_path() -> str:
    # 容器里 "/" 是 overlay，看实际挂载的 data 目录
    if DATA_PATH.exists():
        return str(DATA_PATH)
    return "/"


def disk_usage():
    return psutil.disk_usage(disk_usage_path())


def logged_in_users() -> list:
    # 容器里没有 utmp
    if in_container():
        return []
    try:
        return psutil.users()
    except Exception:
        return []
