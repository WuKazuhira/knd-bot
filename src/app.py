from __future__ import annotations

import os
import pkgutil

import nonebot
from dotenv import load_dotenv
from nonebot.adapters.onebot.v11 import Adapter
from starlette.responses import JSONResponse, PlainTextResponse

from config.path_config import PROJECT_ROOT

load_dotenv(os.getenv("ENV_FILE", PROJECT_ROOT / ".env"))

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
    path = PROJECT_ROOT / "src" / package
    return [
        f"{package}.{module.name}"
        for module in pkgutil.iter_modules([str(path)])
        if not module.name.startswith("_")
    ]


nonebot.load_all_plugins(_plugin_modules("basic_plugins"), [])
nonebot.load_all_plugins(_plugin_modules("plugins"), [])

app = nonebot.get_asgi()


async def _healthz(_request):
    return PlainTextResponse("ok")


async def _readyz(_request):
    return JSONResponse(
        {"ready": _app_ready},
        status_code=200 if _app_ready else 503,
    )


app.add_route("/healthz", _healthz, methods=["GET"])
app.add_route("/readyz", _readyz, methods=["GET"])
