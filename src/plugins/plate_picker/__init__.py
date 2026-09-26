"""群聊捡车牌：仅在群功能开关开启后处理完整的五位数字消息。"""

import random
import re

from nonebot import on_message
from nonebot.adapters.onebot.v11 import GROUP, Bot, GroupMessageEvent, Message, MessageEvent, MessageSegment

from config.path_config import DATA_PATH
from manager import group_manager
from services.log import logger

__plugin_name__ = "捡车牌"
__plugin_type__ = "群相关"
__plugin_version__ = 0.1
__plugin_usage__ = "群内使用 knd 开启 捡车牌 / knd 关闭 捡车牌；开启后发送恰好五位数字，更新群名前的车牌编号并随机回复图片与补火提示。"
__plugin_settings__ = {"default_status": False, "cmd": ["捡车牌"]}

_PLATE_RE = re.compile(r"[0-9]{5}")
_PLATE_PREFIX_RE = re.compile(r"^(?:【[0-9]{5}】)+")
_PICK_IMAGES_PATH = DATA_PATH / "pjsk" / "static" / "pics" / "pick"
_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
_MODULE = "plate_picker"


def plate_message(event: MessageEvent) -> bool:
    """只接受单个纯文本消息段，不允许空白、CQ 段或 Unicode 数字。"""
    if not isinstance(event, GroupMessageEvent) or len(event.message) != 1:
        return False
    segment = event.message[0]
    return segment.type == "text" and isinstance(segment.data.get("text"), str) and bool(
        _PLATE_RE.fullmatch(segment.data["text"])
    )


plate_picker = on_message(rule=plate_message, permission=GROUP, priority=5, block=False)


@plate_picker.handle()
async def _(bot: Bot, event: GroupMessageEvent):
    # 超级用户会跳过公共权限预处理，此处仍需遵守群开关。
    if not group_manager.get_plugin_status(_MODULE, event.group_id):
        return
    plate = event.message[0].data["text"]
    try:
        group_info = await bot.get_group_info(group_id=event.group_id, no_cache=True)
        original_name = group_info["group_name"]
        if not original_name:
            raise ValueError("群名称为空")
        # 连续捡到车牌时替换旧前缀；其他群名内容保持原样。
        group_name = f"【{plate}】{_PLATE_PREFIX_RE.sub('', original_name)}"
        await bot.set_group_name(group_id=event.group_id, group_name=group_name)
    except Exception as exc:
        logger.warning(f"捡车牌修改群名称失败 GROUP {event.group_id}: {type(exc).__name__}: {exc}")
        try:
            await plate_picker.send("捡车牌失败：无法修改群名称，请确认机器人拥有群管理权限。")
        except Exception as send_exc:
            logger.warning(f"捡车牌发送失败提示失败 GROUP {event.group_id}: {type(send_exc).__name__}: {send_exc}")
        return

    try:
        reply = Message("请...记得补火")
        images = [
            path for path in _PICK_IMAGES_PATH.iterdir()
            if path.is_file() and path.suffix.lower() in _IMAGE_EXTENSIONS
        ] if _PICK_IMAGES_PATH.is_dir() else []
        if images:
            reply += MessageSegment.image(random.choice(images).read_bytes())
        else:
            logger.warning(f"捡车牌回复图片目录没有可用图片: {_PICK_IMAGES_PATH}")
        await plate_picker.send(reply)
    except Exception as exc:
        logger.warning(f"捡车牌发送补火回复失败 GROUP {event.group_id}: {type(exc).__name__}: {exc}")
