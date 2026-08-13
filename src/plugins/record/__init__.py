from __future__ import annotations

from typing import Awaitable, Callable

from nonebot import on_message
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, MessageEvent
from services.log import logger

from .sql import insert_msg


__plugin_name__ = "聊天记录记录器"
__plugin_type__ = "工具功能"
__plugin_version__ = 0.1
__plugin_usage__ = "为 chat/autochat 提供群聊记录检索。"
__plugin_settings__ = {"default_status": False, "cmd": ["record"]}

before_record_hooks: list[Callable[[Bot, MessageEvent], Awaitable[None]]] = []


def before_record_hook(func: Callable[[Bot, MessageEvent], Awaitable[None]]):
    before_record_hooks.append(func)
    return func


def _msg_to_serializable(event: MessageEvent) -> list[dict]:
    return [{"type": seg.type, "data": dict(seg.data)} for seg in event.message]


record_matcher = on_message(priority=99, block=False)


@record_matcher.handle()
async def _(bot: Bot, event: MessageEvent):
    if not isinstance(event, GroupMessageEvent):
        return
    for hook in list(before_record_hooks):
        try:
            await hook(bot, event)
        except Exception as e:
            logger.warning(f"before_record_hook 执行失败: {e}")
    try:
        sender = getattr(event, "sender", None)
        nickname = getattr(sender, "nickname", "") if sender else ""
        await insert_msg(
            event.message_id,
            event.time,
            event.user_id,
            event.group_id,
            nickname,
            _msg_to_serializable(event),
        )
    except Exception as e:
        logger.warning(f"记录群聊消息失败: {e}")
