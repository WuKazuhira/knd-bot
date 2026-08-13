"""运行状态。

Adapted from AiriCore plugins/airi_status (MIT License):
https://github.com/Tenma-Saki/AiriCore/tree/main/plugins/airi_status
"""

import base64
import asyncio

from nonebot import on_fullmatch
from nonebot.adapters.onebot.v11 import MessageEvent, MessageSegment
from services.log import logger

from .drawer import draw

__plugin_name__ = "运行状态"
__plugin_type__ = "信息查询"
__plugin_usage__ = """
usage：
    /status
    /状态
说明：查看 kndbot 运行状态图。
""".strip()
__plugin_settings__ = {"default_status": True, "cmd": ["status", "状态", "运行状态"]}
__plugin_cd_limit__ = {"cd": 10, "rst": "状态图生成中，稍等一下捏"}
__plugin_block_limit__ = {"rst": "状态图生成中，稍等一下捏"}

status = on_fullmatch(("status", "状态"), priority=5, block=True)


@status.handle()
async def _(event: MessageEvent):
    try:
        data = await asyncio.to_thread(draw)
    except Exception as e:
        logger.exception(f"生成状态图失败: {e}")
        await status.finish("生成状态图失败，请查看后台日志")
    await status.finish(MessageSegment.image("base64://" + base64.b64encode(data).decode()))
