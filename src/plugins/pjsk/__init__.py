import os

if os.getenv("KNDBOT_SKIP_PJSK_PLUGIN_AUTOLOAD") != "1":
    from utils.plugin_loader import load_sub_plugins

    from ._draw_context import install_draw_context

    install_draw_context()
    load_sub_plugins(__name__, __file__)

    # go 侧接管的指令跳过 Python 处理，防止双回复
    from nonebot.adapters.onebot.v11 import MessageEvent
    from nonebot.consts import CMD_KEY, PREFIX_KEY
    from nonebot.exception import IgnoredException
    from nonebot.matcher import Matcher
    from nonebot.message import run_preprocessor
    from nonebot.typing import T_State

    from services.go_ownership import (
        pjsk_command_owned_by_go,
        pjsk_text_owned_by_go,
    )

    @run_preprocessor
    async def _pjsk_go_ownership(matcher: Matcher, event: MessageEvent, state: T_State):
        module = getattr(matcher, "module_name", "") or ""
        if "plugins.pjsk." not in module:
            return
        prefix = state.get(PREFIX_KEY) or {}
        cmd = prefix.get(CMD_KEY)
        if cmd:
            trigger = cmd[0] if isinstance(cmd, tuple) else cmd
            if pjsk_command_owned_by_go(str(trigger)):
                raise IgnoredException("pjsk 指令已由 go-pjsk-bot 接管")
            return
        # 正则指令没有命令元组，直接拿原文判断
        if pjsk_text_owned_by_go(event.get_plaintext()):
            raise IgnoredException("pjsk 正则指令已由 go-pjsk-bot 接管")


