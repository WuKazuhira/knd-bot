"""PJSK 统一数据路径。

实际定义在 utils.pjsk_paths，这里只做再导出，保证插件与绘图服务
（services.pjsk_draw）看到的是同一套目录。
"""

from __future__ import annotations

from utils.pjsk_paths import (
    ASSETS_PATH,
    DATABASE_PATH,
    DECKREC_PATH,
    FORECAST_PATH,
    MASTERDATA_PATH,
    ONDEMAND_PATH,
    PJSK_DATA_PATH,
    PROFILE_PATH,
    REMOTE_PATH,
    STATIC_PATH,
    SUITE_PATH,
    TEMP_PATH,
    ensure_pjsk_directories,
)

__all__ = [
    "ASSETS_PATH",
    "DATABASE_PATH",
    "DECKREC_PATH",
    "FORECAST_PATH",
    "MASTERDATA_PATH",
    "ONDEMAND_PATH",
    "PJSK_DATA_PATH",
    "PROFILE_PATH",
    "REMOTE_PATH",
    "STATIC_PATH",
    "SUITE_PATH",
    "TEMP_PATH",
    "ensure_pjsk_directories",
]
