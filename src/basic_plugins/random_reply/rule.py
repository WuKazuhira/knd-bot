import time

from nonebot.adapters.onebot.v11 import GroupMessageEvent
from nonebot.adapters.onebot.v11.event import NoticeEvent
from nonebot.params import Command
from nonebot.typing import T_RuleChecker, T_State

from services import logger

from .models import UserInfo, retry_manager


# 不需要用户消息的规则
def normal_rule(command: Command) -> T_RuleChecker:
    async def check_args(
            event: GroupMessageEvent, state: T_State
    ) -> bool:
        if (not command.need_at) or (command.need_at and event.is_tome()):
            user = UserInfo(qq=event.user_id, group=event.group_id)
            state["users"] = user
            return True
        else:
            return False

    return check_args


# 需要用户消息的规则
def check_rule(command: Command) -> T_RuleChecker:
    async def check_args(
            event: GroupMessageEvent, state: T_State
    ) -> bool:
        if event.reply:
            return False
        if (not command.need_at) or (command.need_at and event.is_tome()):
            msg = event.get_plaintext()
            user = UserInfo(qq=event.user_id, group=event.group_id, text=msg)
            if not user:
                return False
            state["users"] = user
            return True
        else:
            return False

    return check_args


# 戳一戳的特殊规则
def poke_rule(command: Command) -> T_RuleChecker:
    async def check_args(
            event: NoticeEvent, state: T_State
    ) -> bool:
        try:
            if getattr(event, 'notice_type', None) != 'notify' or getattr(event, 'sub_type', None) != 'poke':
                return False
            group_id = getattr(event, 'group_id', None)
            user_id = getattr(event, 'user_id', None)
            target_id = getattr(event, 'target_id', None)
            self_id = getattr(event, 'self_id', None)
            if group_id is None or user_id is None or target_id is None or self_id is None:
                return False
            if target_id != self_id:
                return False
            if command.need_at:
                try:
                    if not event.is_tome():
                        return False
                except Exception:
                    return False
            user = UserInfo(qq=user_id, group=group_id)
            state["users"] = user
            return True
        except Exception as e:
            logger.warning(f"戳一戳规则检查失败: {e}")
            return False

    return check_args


# 多轮对话触发规则
def retry_rule() -> T_RuleChecker:
    async def check_args(event: GroupMessageEvent) -> bool:
        retry_info = retry_manager.get(event.user_id, event.group_id)
        if retry_info:
            if retry_info["time"] + 60 < time.time():
                retry_manager.remove(event.user_id, event.group_id)
                return False
            return True
        return False
    return check_args