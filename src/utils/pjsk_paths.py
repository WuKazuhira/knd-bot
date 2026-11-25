from __future__ import annotations

from pathlib import Path

try:
    from config.path_config import DATA_PATH
except ModuleNotFoundError:
    # 独立脚本直接跑时没配 NoneBot 路径
    DATA_PATH = Path(__file__).resolve().parents[2] / "data"

PJSK_DATA_PATH = DATA_PATH / "pjsk"
STATIC_PATH = PJSK_DATA_PATH / "static"
ONDEMAND_PATH = PJSK_DATA_PATH / "ondemand"

# 兼容旧名字
MASTERDATA_PATH = ONDEMAND_PATH
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
            continue


ensure_pjsk_directories()
