import asyncio
import random

from nonebot import get_bots, on_command
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, Message, MessageEvent
from nonebot.params import CommandArg
from nonebot.permission import SUPERUSER

from manager import Config, group_manager
from services.log import logger
from utils.message_builder import image
from utils.utils import get_message_img

__plugin_name__ = "超管广播 [Superuser]"
__plugin_type__ = "消息播报"
__plugin_version__ = 0.1
__plugin_superuser_usage__ = """
usage：
    仅超级用户可用；会向启用了广播被动开关的群发送消息，不是当前群单独播报。
    指令：
        .广播 ?[消息] ?[图片]（/广播 兼容；消息与图片至少提供一项）
    示例：
        .广播 你们好！
    帮助：knd 广播 help（仅超级用户可见）；查询帮助不会执行群发。
""".strip()
__plugin_settings__ = {
    "cmd": ["超管广播", "广播", ".广播", "/广播"]
}
__plugin_task__ = {"broadcast": "超管广播"}

Config.add_plugin_config(
    "_task",
    "DEFAULT_BROADCAST",
    True,
    help_="被动 广播 进群默认开关状态",
    default_value=True,
)

broadcast = on_command(".广播", aliases={'/广播'}, priority=1, permission=SUPERUSER, block=True)


_broadcast_tasks: set[asyncio.Task] = set()


@broadcast.handle()
async def _(bot: Bot, event: MessageEvent, arg: Message = CommandArg()):
    msg = arg.extract_plain_text().strip()
    img_list = get_message_img(event.json())
    rst = ""
    for img in img_list:
        rst += image(img)
    if not msg and not rst:
        await broadcast.finish("你没有要说的话嘛！", at_sender=True)
    seen_groups: set[int] = set()
    recipients: list[tuple[Bot, int]] = []
    for target_bot in get_bots().values():
        for group in await target_bot.get_group_list():
            group_id = int(group["group_id"])
            if group_id in seen_groups:
                continue
            if await group_manager.check_group_task_status(group_id, "broadcast"):
                seen_groups.add(group_id)
                recipients.append((target_bot, group_id))
    if not recipients:
        await broadcast.finish("没有开启广播的群聊。")

    group_id = event.group_id if isinstance(event, GroupMessageEvent) else None
    task = asyncio.create_task(_broadcast_send(bot, event.user_id, group_id, recipients, msg + rst))
    _broadcast_tasks.add(task)
    task.add_done_callback(_broadcast_tasks.discard)


async def _broadcast_send(
    sender_bot: Bot, user_id: int, group_id: int | None, recipients: list[tuple[Bot, int]], msg: str
):
    async def report(message: str):
        try:
            if group_id is None:
                await sender_bot.send_private_msg(user_id=user_id, message=message)
            else:
                await sender_bot.send_group_msg(group_id=group_id, message=message)
        except Exception:
            logger.exception("广播进度汇报失败")

    errors = []
    next_progress = 25
    total = len(recipients)
    for count, (target_bot, target_group) in enumerate(recipients, start=1):
        try:
            await target_bot.send_group_msg(group_id=target_group, message="来自master的广播消息\n" + msg)
            logger.info(f"GROUP {target_group} 投递广播成功")
        except Exception as e:
            logger.exception(f"GROUP {target_group} 投递广播失败")
            errors.append(f"GROUP {target_group} 投递广播失败：{type(e).__name__}")
        progress = count * 100 // total
        if progress >= next_progress:
            await report(f"广播进度 {progress}%")
            next_progress = (progress // 25 + 1) * 25
        if count < total:
            await asyncio.sleep(random.randint(4, 8))
    if errors:
        await report("播报时错误：\n" + "\n".join(errors))
