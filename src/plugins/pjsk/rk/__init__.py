import json
import random
import re
from typing import Any, Tuple

from nonebot import get_driver, on_command
from nonebot.adapters.onebot.v11 import ActionFailed, Message, MessageEvent
from nonebot.internal.matcher import Matcher
from nonebot.params import Command, CommandArg

from services import logger
from utils.imageutils import pic2b64, text2image
from utils.message_builder import image
from utils.utils import get_message_at, scheduler

from .._config import *
from .._errors import apiCallError, maintenanceIn, userIdBan
from .._models import PjskBind
from .._utils import callapi, currentrankmatch, get_pjsk_type

driver = get_driver()

__plugin_name__ = "排位查询/rk"
__plugin_type__ = "烧烤相关&uni移植"
__plugin_version__ = 0.1
__plugin_usage__ = f"""
usage：
    pjsk排位查询，仅限日服
    若群内已有unibot请勿开启此bot该功能
    私聊可用，限制每人1分钟只能查询2次
    因为sbga的原因，今后只能查前百的成绩
    指令：
        rk [排名]          查询此排名玩家的排位成绩，仅限前百
        rk [id]           查询此id玩家的排位成绩，仅限前百
        rk @qq            查看艾特用户的排位成绩(对方必须已绑定烧烤账户且排名前百)
        rk                查询自己的排位成绩，仅限前百
    数据来源：
        pjsekai.moe
        unipjsk.com
""".strip()
__plugin_settings__ = {
    "default_status": False,
    "cmd": ["rk", "排位查询", "烧烤相关"],
}
__plugin_cd_limit__ = {"cd": 15, "rst": "别急，你才刚查完呢", "limit_type": "group"}
__plugin_block_limit__ = {"rst": "别急，还在查！"}


# pjsk查排位
pjsk_rk = on_command('rk', priority=5, block=True)
cn_rk = on_command('cnrk', priority=5, block=True)
tw_rk = on_command('twrk', priority=5, block=True)


@pjsk_rk.handle()
@cn_rk.handle()
@tw_rk.handle()
async def _(matcher: Matcher, event: MessageEvent, msg: Message = CommandArg(), cmd: Tuple[str, ...] = Command()):
    pjsk_type = get_pjsk_type(cmd[0])
    server_name = SERVER_MAP.get(pjsk_type, 'jp')

    rankmatchid = currentrankmatch(pjsk_type)
    
    # 构建 Rank Match URL
    if not RANK_MATCH_API_BASE_URL:
        await matcher.finish("排位查询 API 未配置")
    url = f"{RANK_MATCH_API_BASE_URL}/{server_name}/rank-match-season/{rankmatchid}/ranking"
    
    arg = re.sub(r'\D', "", msg.extract_plain_text().strip())
    # 若无参数，尝试获取用户绑定的id
    if not arg:
        qq_ls = get_message_at(event.raw_message)
        qid = qq_ls[0] if qq_ls and qq_ls[0] != event.self_id else event.user_id
        arg, isprivate = await PjskBind.get_user_bind(qid, pjsk_type)
        if not arg:
            server_display = "日服" if pjsk_type == 0 else ("台服" if pjsk_type == 1 else "国服")
            await matcher.finish(
                f"{'你' if event.user_id == qid else '用户'}还没有绑定{server_display}哦，国服/台服指令请加cn/tw前缀，日服无需前缀",
                at_sender=True
            )
        if isprivate and qid != event.user_id:
            await matcher.finish(REFUSED_ERROR, at_sender=True)
        param = {'targetUserId': arg}
    # 若有参数，区别处理
    # 输入的是用户id或者排名
    elif arg.isdigit():
        search_type = 'targetUserId' if len(arg) > 8 else 'targetRank'
        param = {search_type: arg}
    # 若获取玩家信息失败
    else:
        await matcher.finish(ID_ERROR, at_sender=True)
        return
    try:
        data = await callapi(url, param, pjsk_type=pjsk_type)
    except IndexError:
        await matcher.finish('查不到数据捏，可能是没打排位', at_sender=True)
        return
    except (maintenanceIn, apiCallError, userIdBan) as e:
        await matcher.finish(str(e), at_sender=True)
        return
    except Exception as e:
        await matcher.finish(BUG_ERROR, at_sender=True)
        logger.warning(f"pjsk查排位失败。Error：{e}")
        return
    try:
        ranking = data['rankings'][0]['userRankMatchSeason']
        grade = int((ranking['rankMatchTierId'] - 1) / 4) + 1
    except IndexError:
        await matcher.finish('未参加当期排位赛', at_sender=True)
        return

    if grade > 7:
        grade = 7
    gradename = rankmatchgrades[grade]
    kurasu = ranking['rankMatchTierId'] - 4 * (grade - 1)
    if not kurasu:
        kurasu = 4
    winrate = ranking['winCount'] / (ranking['winCount'] + ranking['loseCount'])
    text = ''
    if grade == 7:
        text += f"{gradename}🎵×{ranking['tierPoint']}\n排名：{data['rankings'][0]['rank']}\n"
    else:
        text += f"{gradename}Class {kurasu}({ranking['tierPoint']}/5)\n排名：{data['rankings'][0]['rank']}\n"
    text += f"Win {ranking['winCount']} | Draw {ranking['drawCount']} | "
    if ranking['penaltyCount'] == 0:
        text += f"Lose {ranking['loseCount']}\n"
    else:
        text += f"Lose {ranking['loseCount'] - ranking['penaltyCount']}+{ranking['penaltyCount']}\n"
    text += f'胜率(除去平局)：{round(winrate * 100, 2)}%\n'
    text += f"最高连胜：{ranking['maxConsecutiveWinCount']}\n"
    text += f"更新时间：{data['updateTime']}\n"
    try:
        await matcher.finish(text)
    except ActionFailed:
        await matcher.finish(image(b64=pic2b64(text2image(text))))


# 自动更新前百分数
@scheduler.scheduled_job(
    "interval",
    minutes=25
)
async def _():
    for pjsk_type in SERVER_MAP.keys():
        server_name = SERVER_MAP[pjsk_type]
        try:
            rankmatchid = currentrankmatch(pjsk_type)
            if not rankmatchid:
                continue
            
            if not RANK_MATCH_API_BASE_URL:
                continue
            api_url = f"{RANK_MATCH_API_BASE_URL}/{server_name}/rank-match-season/{rankmatchid}/ranking-top100"
            
            ranking = await callapi(api_url, pjsk_type=pjsk_type, is_force_update=True)
            
            server_data_path = data_path / server_name
            if not server_data_path.exists():
                server_data_path.mkdir(parents=True, exist_ok=True)
                
            with open(server_data_path / 'rktop100.json', 'w', encoding='utf-8') as f:
                json.dump(ranking, f, sort_keys=True, indent=4)
            logger.info(f"[定时任务]:pjsk {server_name} 更新前百排位分数成功！")
        except Exception as e:
            logger.warning(f"[定时任务]:pjsk {server_name} 更新前百排位分数失败！Error:{e}")