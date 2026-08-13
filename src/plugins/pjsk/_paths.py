"""PJSK 统一数据路径。"""

from __future__ import annotations

from config.path_config import DATA_PATH

PJSK_DATA_PATH = DATA_PATH / "pjsk"
STATIC_PATH = PJSK_DATA_PATH / "static"
MASTERDATA_PATH = PJSK_DATA_PATH / "masterdata"
ASSETS_PATH = PJSK_DATA_PATH / "assets"
PROFILE_PATH = PJSK_DATA_PATH / "profile"
DECKREC_PATH = PJSK_DATA_PATH / "deckrec"
FORECAST_PATH = PJSK_DATA_PATH / "forecast"
REMOTE_PATH = PJSK_DATA_PATH / "remote"
DATABASE_PATH = PJSK_DATA_PATH / "database"
TEMP_PATH = PJSK_DATA_PATH / "temp"
SUITE_PATH = PROFILE_PATH / "suite"


def ensure_pjsk_directories() -> None:
    for path in (
        STATIC_PATH,
        MASTERDATA_PATH,
        ASSETS_PATH,
        PROFILE_PATH,
        DECKREC_PATH,
        FORECAST_PATH,
        REMOTE_PATH,
        DATABASE_PATH,
        TEMP_PATH,
        SUITE_PATH,
    ):
        path.mkdir(parents=True, exist_ok=True)


ensure_pjsk_directories()
