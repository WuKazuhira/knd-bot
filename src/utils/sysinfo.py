"""容器友好的系统指标采集。

容器里 `psutil.disk_usage("/")` 量的是镜像 overlay 层，和用户关心的数据盘
没有关系；`psutil.users()` 读的是 /var/run/utmp，容器里恒为空。
这里统一改成看 data 目录（部署时 bind mount 出去，正好落在真实数据盘上），
并在容器内跳过登录用户统计。
"""

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
    """返回该统计哪个挂载点的磁盘用量。"""
    if DATA_PATH.exists():
        return str(DATA_PATH)
    return "/"


def disk_usage():
    return psutil.disk_usage(disk_usage_path())


def logged_in_users() -> list:
    """容器内没有 utmp，返回空列表而不是让调用方以为“没人登录”。"""
    if in_container():
        return []
    try:
        return psutil.users()
    except Exception:
        return []
