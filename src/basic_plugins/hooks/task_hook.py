import re
from typing import Any, Dict

from nonebot.adapters.onebot.v11 import Bot, Message
from nonebot.exception import MockApiException

from manager import group_manager

_TASK_PREFIX_RE = re.compile(
    r"^(?:\[\[_task\|(?P<plain>[^\]]+)]]|&#91;&#91;_task\|(?P<escaped>.*?)&#93;&#93;)"
)


def _message_text(message: Any) -> str:
    if isinstance(message, str):
        return message.strip()
    return str(message).strip()


# task 被动插件发送消息前的预处理
@Bot.on_calling_api
async def handle_api_call(bot: Bot, api: str, data: Dict[str, Any]):
    is_group_message = api == "send_group_msg" or (
        api == "send_msg" and data.get("message_type") == "group"
    )
    if not is_group_message or "message" not in data or "group_id" not in data:
        return

    message_text = _message_text(data["message"])
    match = _TASK_PREFIX_RE.match(message_text)
    if not match:
        return

    task = match.group("plain") or match.group("escaped")
    group_id = int(data["group_id"])

    # 任务元数据与群开关是惰性初始化的。先初始化，避免空任务表导致
    # 内部控制标记直接泄露到实际发送消息中。
    await group_manager.init_group_task(group_id)
    if task not in group_manager.get_task_data():
        return

    if group_manager.get_group_level(group_id) < 0 or not await group_manager.check_group_task_status(
        group_id, task
    ):
        raise MockApiException(f"被动技能 {task} 处于关闭状态...")

    data["message"] = Message(message_text[match.end():])
