"""核心运行配置。

公开默认值可以进入版本库；账号、超级用户、数据库口令等仅从环境变量读取。
"""

from __future__ import annotations

import os
from typing import Optional

NICKNAME: str = os.getenv("BOT_DISPLAY_NAME", "小七")
BOT_URL: str = os.getenv("BOT_URL", "")


def _env_int(name: str) -> int:
    value = (os.getenv(name) or "").strip()
    return int(value) if value else 0


# 可选的多机器人账号；0 表示未配置。
MAIN_BOT: int = _env_int("MAIN_BOT_QQ")
SUB_BOT: int = _env_int("SUB_BOT_QQ")
AUX_BOT: int = _env_int("AUX_BOT_QQ")
EXT_BOT: int = _env_int("EXT_BOT_QQ")
FIF_BOT: int = _env_int("FIF_BOT_QQ")

sql_name: str = os.getenv("DB_TYPE", "postgresql")
user: str = os.getenv("DB_USER", "kndbot")
password: str = os.getenv("DB_PASSWORD", "")
address: str = os.getenv("DB_HOST", "127.0.0.1")
port: str = os.getenv("DB_PORT", "5432")
database: str = os.getenv("DB_NAME", "kndbot")

bind: str = os.getenv("DATABASE_URL", "")
if not bind and password:
    bind = f"{sql_name}://{user}:{password}@{address}:{port}/{database}"

SYSTEM_PROXY: Optional[str] = os.getenv("SYSTEM_PROXY") or None
