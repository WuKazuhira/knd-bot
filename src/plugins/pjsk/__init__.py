import os

if os.getenv("KNDBOT_SKIP_PJSK_PLUGIN_AUTOLOAD") != "1":
    from utils.plugin_loader import load_sub_plugins

    from ._draw_context import install_draw_context

    # 指令模块加载前先给绘图服务注入数据上下文。
    install_draw_context()
    load_sub_plugins(__name__, __file__)

    # 命令所有权互斥：当某 pjsk 指令已由 go-pjsk-bot 接管（列在
    # KND_GO_OWNED_COMMANDS）时，让 Python 侧对应 matcher 安静退场，避免双回复。
    # 默认 owned 列表为空 -> 全部命令仍由 Python 处理，本钩子零影响。
    from nonebot.consts import CMD_KEY, PREFIX_KEY
    from nonebot.exception import IgnoredException
    from nonebot.matcher import Matcher
    from nonebot.message import run_preprocessor
    from nonebot.typing import T_State

    from services.go_ownership import pjsk_command_owned_by_go

    @run_preprocessor
    async def _pjsk_go_ownership(matcher: Matcher, state: T_State):
        # 仅作用于 pjsk 子插件的 matcher，不影响其它插件。
        module = getattr(matcher, "module_name", "") or ""
        if "plugins.pjsk." not in module:
            return
        # 取 nonebot 自身匹配到的命令元组（含别名/cn·tw 前缀），无需重复解析。
        prefix = state.get(PREFIX_KEY) or {}
        cmd = prefix.get(CMD_KEY)
        if not cmd:
            return
        trigger = cmd[0] if isinstance(cmd, tuple) else cmd
        if pjsk_command_owned_by_go(str(trigger)):
            raise IgnoredException("pjsk 指令已由 go-pjsk-bot 接管")

