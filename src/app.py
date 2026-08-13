"""NoneBot 应用工厂与 ASGI 应用。"""

from __future__ import annotations

import os
import pkgutil
from pathlib import Path

import nonebot
from dotenv import load_dotenv
from nonebot.adapters.onebot.v11 import Adapter

from config.path_config import PROJECT_ROOT

load_dotenv(os.getenv("ENV_FILE", PROJECT_ROOT / ".env"))

from services.db_context import disconnect, init  # noqa: E402

nonebot.init()
driver = nonebot.get_driver()
driver.register_adapter(Adapter)
driver.on_startup(init)
driver.on_shutdown(disconnect)

nonebot.load_plugin("nonebot_plugin_htmlrender")
nonebot.load_plugin("nonebot_plugin_apscheduler")


def _plugin_modules(package: str) -> list[str]:
    """枚举顶层插件，并统一使用项目内的绝对模块命名空间。"""
    path = PROJECT_ROOT / "src" / package
    return [
        f"{package}.{module.name}"
        for module in pkgutil.iter_modules([str(path)])
        if not module.name.startswith("_")
    ]


nonebot.load_all_plugins(_plugin_modules("basic_plugins"), [])
nonebot.load_all_plugins(_plugin_modules("plugins"), [])

app = nonebot.get_asgi()
