"""项目统一路径定义。

所有路径都基于仓库根目录解析，不依赖启动时的当前工作目录。
"""

from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = Path(os.getenv("KNDBOT_CONFIG_DIR", PROJECT_ROOT / "config")).resolve()
DATA_PATH = Path(os.getenv("KNDBOT_DATA_DIR", PROJECT_ROOT / "data")).resolve()
RESOURCE_PATH = Path(os.getenv("KNDBOT_RESOURCE_DIR", DATA_PATH / "resources")).resolve()

IMAGE_PATH = RESOURCE_PATH / "image"
RECORD_PATH = RESOURCE_PATH / "record"
TEXT_PATH = RESOURCE_PATH / "text"
FONT_PATH = RESOURCE_PATH / "font"
LOG_PATH = Path(os.getenv("KNDBOT_LOG_DIR", DATA_PATH / "log")).resolve()
TEMP_PATH = Path(os.getenv("KNDBOT_TEMP_DIR", DATA_PATH / "temp")).resolve()
RUNTIME_CONFIG_PATH = DATA_PATH / "config"


def load_path() -> None:
    """创建运行时可写目录；公开配置与静态资源保持只读。"""
    for path in (DATA_PATH, LOG_PATH, TEMP_PATH, RUNTIME_CONFIG_PATH):
        path.mkdir(parents=True, exist_ok=True)


load_path()
