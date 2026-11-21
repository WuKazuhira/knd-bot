import random

import nonebot
from asyncpg.exceptions import ConnectionDoesNotExistError, UndefinedColumnError

from config.config import AUX_BOT, MAIN_BOT, SUB_BOT
from manager import Config
from models.friend_user import FriendUser
from models.group_info import GroupInfo
from services.log import logger
from utils.utils import get_bot, scheduler

from ._data_source import update_member_info

Config.add_plugin_config(
    "_backup",
    "BACKUP_FLAG",
    False,
    help_="是否开启文件备份（已弃用，由 scripts/ops/backup.sh 接管）",
    default_value=False
)

Config.add_plugin_config(
    "_backup",
    "BACKUP_DIR_OR_FILE",
    ['data/config', 'data/statistics', 'data/word_bank', 'data/manager'],
    name="文件备份",
    help_="备份的文件夹或文件",
    default_value=[]
)

__plugin_name__ = "定时任务相关 [Hidden]"


# 每天定时更新所有群的群员信息
@scheduler.scheduled_job(
    "cron",
    hour=3,
    minute=30,
)
async def _():
    bots = list(nonebot.get_bots().values())
    for bot in bots:
        try:
            gl = await bot.get_group_list()
        except Exception as e:
            logger.warning(f"[定时任务]:Bot({bot.self_id}) 获取群列表失败 e:{e}")
            continue
        gl = [g["group_id"] for g in gl]
        for g in gl:
            try:
                await update_member_info(bot, g)
                logger.info(f"[定时任务]:更新群组 g:{g} 成功")
            except Exception as e:
                logger.error(f"[定时任务]:更新群组错误 g:{g} e:{e}")


# 快速更新未保存的群的群员信息
@scheduler.scheduled_job(
    "interval",
    minutes=5,
)
async def _():
    try:
        bots = list(nonebot.get_bots().values())
        if not bots:
            return
        all_group = {x.group_id for x in await GroupInfo.get_all_group()}
    except (IndexError, ConnectionDoesNotExistError, UndefinedColumnError):
        return
    for bot in bots:
        try:
            gl = [g["group_id"] for g in await bot.get_group_list()]
        except Exception as e:
            logger.warning(f"[定时任务]:Bot({bot.self_id}) 获取群列表失败 e:{e}")
            continue
        for g in gl:
            if g not in all_group:
                try:
                    await update_member_info(bot, g)
                    all_group.add(g)
                    logger.info(f"[定时任务]:快速更新未保存的群信息以及权限：{g}")
                except (IndexError, ConnectionDoesNotExistError, UndefinedColumnError):
                    continue
                except Exception as e:
                    logger.warning(f"[定时任务]:快速更新群组失败 bot={bot.self_id} g:{g} e:{e}")
                    continue


# 每天定时更新所有群的群信息
@scheduler.scheduled_job(
    "cron",
    hour=3,
    minute=1,
)
async def _():
    try:
        bot = get_bot()
        gl = await bot.get_group_list()
        gl = [g["group_id"] for g in gl]
        for g in gl:
            group_info = await bot.get_group_info(group_id=g)
            await GroupInfo.add_group_info(
                group_info["group_id"],
                group_info["group_name"],
                group_info["max_member_count"],
                group_info["member_count"],
            )
            logger.info(f"[定时任务]:自动更新群组 {g} 信息成功")
    except Exception as e:
        logger.error(f"[定时任务]:自动更新群组信息错误 e:{e}")


# 定时更新好友信息
@scheduler.scheduled_job(
    "cron",
    hour=3,
    minute=1,
)
async def _():
    try:
        bot = get_bot()
        fl = await bot.get_friend_list()
        for f in fl:
            if await FriendUser.add_friend_info(f["user_id"], f["nickname"]):
                logger.info(f'[定时任务]:自动更新好友 {f["user_id"]} 信息成功')
            else:
                logger.warning(f'[定时任务]:自动更新好友 {f["user_id"]} 信息失败')
    except Exception as e:
        logger.error(f"[定时任务]:自动更新群组信息错误 e:{e}")


# 每天定时检查群是否已有认证
@scheduler.scheduled_job(
    "cron",
    hour=3,
    minute=30,
)
async def _():
    groupinfo = await GroupInfo.query.gino.all()
    illegal_groups = [g.group_id for g in groupinfo if not g.group_flag]
    bot_ls = [MAIN_BOT, SUB_BOT, AUX_BOT]
    if illegal_groups:
        while True:
            if not bot_ls:
                break
            botid = random.choice(bot_ls)
            bot_ls.remove(botid)
            bot = nonebot.get_bot(str(botid))
            try:
                await bot.send_private_msg(
                    user_id=int(list(bot.config.superusers)[0]),
                    message=f"*****风险提醒*****\n"
                            f"以下群未通过群认证：\n"
                            f"{','.join(illegal_groups)}",
                )
                break
            except:
                pass
