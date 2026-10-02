import asyncio
import os

from nonebot import Driver, get_driver, on_regex
from nonebot.adapters.onebot.v11 import Bot, MessageEvent

from config.config import BOT_URL
from config.path_config import DATA_PATH
from models.level_user import LevelUser
from services import logger
from services.go_ownership import go_owns
from utils.help_index import HELP_PATTERN, delegate_world_bloom_help, parse_help_request
from utils.message_builder import image

from .data_source import create_help_img, get_plugin_help, get_plugin_help_text

driver: Driver = get_driver()

__plugin_name__ = "帮助 [Hidden]"
__plugin_version__ = 0.1


group_help_path = DATA_PATH / "group_help"
group_help_path.mkdir(exist_ok=True, parents=True)
for x in os.listdir(group_help_path):
    group_help_image_path = group_help_path / x
    group_help_image_path.unlink()

def _help_raw_text(event: MessageEvent) -> str:
    # OneBot 的 to_me 预处理可能删除昵称；保留原文才能区分昵称入口与 Go 直达。
    original = getattr(event, "original_message", None)
    extract = getattr(original, "extract_plain_text", None)
    return extract() if callable(extract) else event.get_plaintext()


async def _help_rule(event: MessageEvent) -> bool:
    raw = _help_raw_text(event)
    if delegate_world_bloom_help(raw, go_owns("wl边框"), driver.config.command_start):
        return False
    return parse_help_request(raw, driver.config.command_start, driver.config.nickname) is not None


# 保留单个 matcher；阻断后续 Python 业务 matcher，避免把 help 当业务参数。
simple_help = on_regex(HELP_PATTERN, rule=_help_rule, priority=3, block=True)


@simple_help.handle()
async def _(bot: Bot, event: MessageEvent):
    msg = parse_help_request(_help_raw_text(event), driver.config.command_start, driver.config.nickname)
    if msg is None:
        return
    group_id = getattr(event, "group_id", None)
    user_type = 0
    if str(event.user_id) in bot.config.superusers:
        user_type = 2
    elif group_id is not None and await LevelUser.get_user_level(event.user_id, group_id) > 0:
        user_type = 1
    if msg:
        try:
            res = await asyncio.to_thread(get_plugin_help, msg, user_type)
        except Exception as exc:
            logger.warning(f"帮助图片生成失败，回退文本：{type(exc).__name__}")
            res = None
            text = await asyncio.to_thread(get_plugin_help_text, msg, user_type)
            await simple_help.finish(text or "没有此功能的帮助信息...")
        if res:
            await simple_help.finish(image(b64=res))
        await simple_help.finish("没有此功能的帮助信息...")
    if group_id is None:
        await simple_help.finish(f"帮助文档👉https://{BOT_URL}/docs\n具体功能：knd <指令> help")
    _image_path = group_help_path / f"{group_id}.png"
    if not _image_path.exists():
        await create_help_img(group_id, _image_path)
    await simple_help.finish(f"帮助文档👉https://{BOT_URL}/docs\n" + image(file=_image_path.read_bytes()))


# 每个bot启动时生成bot所在的每个群的帮助图片
@driver.on_bot_connect
async def _(bot: Bot):
    group_list = []
    gl = [g["group_id"] for g in await bot.get_group_list()]
    for g in gl:    # [int]
        if g not in group_list:
            group_list.append(g)
            _image_path = group_help_path / f"{g}.png"
            if not _image_path.exists():
                await create_help_img(g, _image_path)
                logger.info(f'生成群组({g})的简易帮助图片')
                await asyncio.sleep(5)
