"""NoneBot 应用工厂与 ASGI 应用。"""

from __future__ import annotations

import os
import pkgutil
from pathlib import Path

import nonebot
from dotenv import load_dotenv
from nonebot.adapters.onebot.v11 import Adapter
from starlette.responses import JSONResponse, PlainTextResponse

from config.path_config import PROJECT_ROOT
from pjsk_runtime import pjsk_plugin_load_plan, resolve_pjsk_runtime

load_dotenv(os.getenv("ENV_FILE", PROJECT_ROOT / ".env"))

# Go 常驻模式下，Python 仍保留组卡 deck 子插件；此内部变量供
# plugins.pjsk 被其它共享代码偶然 import 时阻止其自动注册其它 PJSK 子插件。
PJSK_RUNTIME = resolve_pjsk_runtime(
    os.getenv("KNDBOT_PJSK_RUNTIME"),
    os.getenv("PJSKBOT_STANDALONE"),
)
LOAD_FULL_PYTHON_PJSK, LOAD_DECK_ONLY = pjsk_plugin_load_plan(PJSK_RUNTIME)
if PJSK_RUNTIME == "go":
    os.environ["KNDBOT_SKIP_PJSK_PLUGIN_AUTOLOAD"] = "1"
else:
    os.environ.pop("KNDBOT_SKIP_PJSK_PLUGIN_AUTOLOAD", None)

from services.db_context import disconnect, init  # noqa: E402

nonebot.init()
driver = nonebot.get_driver()
driver.register_adapter(Adapter)
_app_ready = False


async def _startup() -> None:
    global _app_ready
    await init()
    _app_ready = True


async def _shutdown() -> None:
    global _app_ready
    _app_ready = False
    await disconnect()


driver.on_startup(_startup)
driver.on_shutdown(_shutdown)

nonebot.load_plugin("nonebot_plugin_htmlrender")
nonebot.load_plugin("nonebot_plugin_apscheduler")


def _plugin_modules(package: str) -> list[str]:
    """枚举顶层插件，并统一使用项目内的绝对模块命名空间。"""
    path = PROJECT_ROOT / "src" / package
    modules = [
        module.name
        for module in pkgutil.iter_modules([str(path)])
        if not module.name.startswith("_")
    ]
    if package == "plugins" and not LOAD_FULL_PYTHON_PJSK:
        modules = [module for module in modules if module != "pjsk"]
    return [f"{package}.{module}" for module in modules]


nonebot.load_all_plugins(_plugin_modules("basic_plugins"), [])
nonebot.load_all_plugins(_plugin_modules("plugins"), [])

if LOAD_DECK_ONLY:
    # Go 不再注册组卡；仅恢复 Python deck 子插件，并安装本地绘图回退所需的数据上下文。
    from plugins.pjsk._draw_context import install_draw_context

    install_draw_context()
    nonebot.load_plugin("plugins.pjsk.deck")

app = nonebot.get_asgi()


async def _healthz(_request):
    return PlainTextResponse("ok")


async def _readyz(_request):
    return JSONResponse(
        {"ready": _app_ready, "pjsk_runtime": PJSK_RUNTIME},
        status_code=200 if _app_ready else 503,
    )


app.add_route("/healthz", _healthz, methods=["GET"])
app.add_route("/readyz", _readyz, methods=["GET"])
