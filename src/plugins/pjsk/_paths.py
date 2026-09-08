"""PJSK 统一数据路径。"""

from __future__ import annotations

from pathlib import Path

try:
    from config.path_config import DATA_PATH
except ModuleNotFoundError:
    # 供迁移/去重 CLI 在仓库根目录直接运行，不依赖 NoneBot 配置加载。
    DATA_PATH = Path(__file__).resolve().parents[3] / "data"

PJSK_DATA_PATH = DATA_PATH / "pjsk"
STATIC_PATH = PJSK_DATA_PATH / "static"
ONDEMAND_PATH = PJSK_DATA_PATH / "ondemand"

# 兼容旧模块名：主数据已归入 ondemand，不再创建 masterdata 父目录。
MASTERDATA_PATH = ONDEMAND_PATH
ASSETS_PATH = ONDEMAND_PATH / "assets"
PROFILE_PATH = ONDEMAND_PATH / "profile"
DECKREC_PATH = ONDEMAND_PATH / "deckrec"
FORECAST_PATH = ONDEMAND_PATH / "forecast"
REMOTE_PATH = ONDEMAND_PATH / "remote"
DATABASE_PATH = ONDEMAND_PATH / "database"
TEMP_PATH = ONDEMAND_PATH / "temp"
SUITE_PATH = ONDEMAND_PATH / "suite"


def ensure_pjsk_directories() -> None:
    for path in (
        STATIC_PATH,
        ONDEMAND_PATH,
        ASSETS_PATH,
        PROFILE_PATH,
        DECKREC_PATH,
        FORECAST_PATH,
        REMOTE_PATH,
        DATABASE_PATH,
        TEMP_PATH,
        SUITE_PATH,
    ):
        try:
            path.mkdir(parents=True, exist_ok=True)
        except PermissionError:
            # 只读诊断/去重 CLI 不应因已有 root-owned 目录而无法启动。
            continue


ensure_pjsk_directories()
