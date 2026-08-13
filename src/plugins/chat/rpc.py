from __future__ import annotations

import asyncio
from typing import Awaitable, Callable

from nonebot import get_driver
from services.log import logger

_rpc_service_tokens: dict[str, str] = {}
_rpc_handlers: dict[str, Callable] = {}
_rpc_started: set[str] = set()
_rpc_tasks: dict[str, asyncio.Task] = {}


def rpc_method(service_name: str, method_name: str):
    def decorator(func):
        _rpc_handlers[f"{service_name}.{method_name}"] = func
        return func
    return decorator


class RpcSession:  # fallback type hint placeholder
    id: str


def start_rpc_service(host: str, port: int, name: str, token: str, on_connect: Callable | None = None, on_disconnect: Callable | None = None):
    _rpc_service_tokens[name] = token

    async def _run():
        try:
            import aiorpcx
        except Exception as e:
            logger.warning(f"{name} RPC 服务无法启动，缺少 aiorpcx: {e}")
            return

        class Session(aiorpcx.RPCSession):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self.id = str(self.remote_address())
                self.processing_timeout = 300.0
                self.sent_request_timeout = 60.0
                if on_connect:
                    on_connect(self)
                logger.info(f"{name} RPC 客户端 {self.id} 连接成功")

            async def connection_lost(self):
                await super().connection_lost()
                if on_disconnect:
                    on_disconnect(self)
                logger.info(f"{name} RPC 客户端 {self.id} 断开连接")

            async def handle_request(self, request):
                handler = _rpc_handlers.get(f"{name}.{request.method}")
                if not handler:
                    raise aiorpcx.RPCError(-32601, f"Unknown method {request.method}")
                args = list(request.args or [])
                if not args or args[0] != _rpc_service_tokens.get(name):
                    await asyncio.sleep(1)
                    raise aiorpcx.RPCError(-32000, "Invalid or missing token")
                request.args = [self.id] + args[1:]
                return await aiorpcx.handler_invocation(handler, request)()

        try:
            async with aiorpcx.serve_ws(Session, host, int(port)):
                logger.info(f"{name} RPC 服务已启动 ws://{host}:{port}")
                await asyncio.Future()
        except asyncio.CancelledError:
            logger.info(f"{name} RPC 服务已关闭")
        except Exception as e:
            logger.error(f"{name} RPC 服务启动失败: {e}", exc_info=True)

    def _launch_rpc_service():
        if name in _rpc_started:
            return
        _rpc_started.add(name)
        _rpc_tasks[name] = asyncio.create_task(_run())

    async def _stop_rpc_service():
        task = _rpc_tasks.pop(name, None)
        if not task:
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        _rpc_started.discard(name)

    driver = get_driver()

    @driver.on_startup
    async def _startup_rpc_service():
        _launch_rpc_service()

    @driver.on_shutdown
    async def _shutdown_rpc_service():
        await _stop_rpc_service()
