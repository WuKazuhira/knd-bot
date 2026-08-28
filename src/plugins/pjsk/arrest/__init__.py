import json
import random
from typing import Tuple

from nonebot import on_command
from nonebot.adapters.onebot.v11 import ActionFailed, Message, MessageEvent
from nonebot.internal.matcher import Matcher
from nonebot.params import Command, CommandArg

from services import logger
from utils.imageutils import pic2b64, text2image
from utils.message_builder import image

from .._config import BUG_ERROR, NOT_PLAYER_ERROR, SERVER_CONFIG, SERVER_MAP, api_base_url_list, rankmatchgrades
from .._errors import apiCallError, maintenanceIn, pjskError, userIdBan
from .._models import UserProfile
from .._utils import callapi, currentrankmatch, get_pjsk_type, get_userid_preprocess

__plugin_name__ = "逮捕"
__plugin_type__ = "烧烤相关&uni移植"
__plugin_version__ = 0.1
__plugin_usage__ = f"""
usage：
    查询烧烤收歌情况
    若群内已有unibot请勿开启此bot该功能
    私聊可用，限制每人1分钟只能查询2次
    指令：
        逮捕              :查看自己的收歌情况
        逮捕 @qq          :查看艾特用户的收歌情况(对方必须已绑定烧烤账户)
        逮捕 烧烤id        :查看对应烧烤账号的收歌情况
        逮捕 活动排名       :查看当前活动排名对应烧烤用户的收歌情况
    数据来源：
        pjsekai.moe
        unipjsk.com
""".strip()
__plugin_settings__ = {
    "default_status": False,
    "cmd": ["逮捕", "烧烤相关", "uni移植"],
}
__plugin_cd_limit__ = {"cd": 60, "count_limit": 2, "rst": "别急，等[cd]秒后再用！", "limit_type": "user"}
__plugin_block_limit__ = {"rst": "别急，还在查！"}

# pjsk逮捕
pjsk_assest = on_command('逮捕', priority=5, block=True)
cn_pjsk_assest = on_command('cn逮捕', priority=5, block=True)
tw_pjsk_assest = on_command('tw逮捕', priority=5, block=True)


@pjsk_assest.handle()
@cn_pjsk_assest.handle()
@tw_pjsk_assest.handle()
async def _(matcher: Matcher, event: MessageEvent, msg: Message = CommandArg(), cmd: Tuple[str, ...] = Command()):
    pjsk_type = get_pjsk_type(cmd[0])
    
    server_name = SERVER_MAP.get(pjsk_type, 'jp')

    state = await get_userid_preprocess(event, msg, pjsk_type=pjsk_type)
    if reply := state['error']:
        await matcher.finish(reply, at_sender=True)
    userid = state['userid']
    isprivate = state['private']
    profile = UserProfile()
    try:
        await profile.getprofile(userid, 'arrest', pjsk_type=pjsk_type)
    except (json.decoder.JSONDecodeError, IndexError):
        await matcher.finish(NOT_PLAYER_ERROR)
    except pjskError as e:
        await matcher.finish(str(e))
    except:
        await matcher.finish(BUG_ERROR)
    text = f"{profile.name} - {userid}\n" if not isprivate else f"{profile.name}\n"
    text += f"expert进度:FC {profile.full_combo[3]}/{profile.clear[3]}" \
            f" AP{profile.full_perfect[3]}/{profile.clear[3]}\n" \
            f"master进度:FC {profile.full_combo[4]}/{profile.clear[4]}" \
            f" AP{profile.full_perfect[4]}/{profile.clear[4]}\n"
    ap33plus = profile.masterscore[33][0] + profile.masterscore[34][0] + profile.masterscore[35][0] + \
               profile.masterscore[36][0] + profile.masterscore[37][0]
    fc33plus = profile.masterscore[33][1] + profile.masterscore[34][1] + profile.masterscore[35][1] + \
               profile.masterscore[36][1] + profile.masterscore[37][1]
    if ap33plus != 0:
        text = text + f"\nLv.33及以上AP进度：{ap33plus}/{profile.masterscore[33][3] + profile.masterscore[34][3] + profile.masterscore[35][3] + profile.masterscore[36][3] + profile.masterscore[37][3]}"
    if fc33plus != 0:
        text = text + f"\nLv.33及以上FC进度：{fc33plus}/{profile.masterscore[33][3] + profile.masterscore[34][3] + profile.masterscore[35][3] + profile.masterscore[36][3] + profile.masterscore[37][3]}"
    if profile.masterscore[32][0] != 0:
        text = text + f"\nLv.32AP进度：{profile.masterscore[32][0]}/{profile.masterscore[32][3]}"
    if profile.masterscore[32][1] != 0:
        text = text + f"\nLv.32FC进度：{profile.masterscore[32][1]}/{profile.masterscore[32][3]}"

    # 排位数据
    rankmatchid = currentrankmatch(pjsk_type=pjsk_type)
    try:
        try:
            url = SERVER_CONFIG[server_name]['api']['ranking_api_url'].format(event_id=rankmatchid) + f'?targetUserId={userid}'
        except KeyError:
             url = random.choice(api_base_url_list) + \
                f'/user/%7Buser_id%7D/rank-match-season/{rankmatchid}/ranking?targetUserId={userid}'
        
        data = await callapi(url, pjsk_type=pjsk_type)
        ranking = data['rankings'][0]['userRankMatchSeason']
        grade = int((ranking['rankMatchTierId'] - 1) / 4) + 1
        rktext = ''
        if grade > 7:
            grade = 7
        gradename = rankmatchgrades[grade]
        kurasu = ranking['rankMatchTierId'] - 4 * (grade - 1)
        if not kurasu:
            kurasu = 4
        winrate = ranking['winCount'] / (ranking['winCount'] + ranking['loseCount'])
        # 大师、其他段位荣誉称号
        if grade == 7:
            rktext += f"{gradename}🎵×{ranking['tierPoint']}\n排名：{data['rankings'][0]['rank']}\n"
        else:
            rktext += f"{gradename}Class {kurasu}({ranking['tierPoint']}/5)\n排名：{data['rankings'][0]['rank']}\n"
        # 胜负数据
        rktext += f"Win {ranking['winCount']} | Draw {ranking['drawCount']} | "
        if ranking['penaltyCount'] == 0:
            rktext += f"Lose {ranking['loseCount']}\n"
        else:
            rktext += f"Lose {ranking['loseCount'] - ranking['penaltyCount']}+{ranking['penaltyCount']}\n"
        rktext += f'胜率(除去平局)：{round(winrate * 100, 2)}%\n'
        rktext += f"最高连胜：{ranking['maxConsecutiveWinCount']}\n"
    except IndexError:
        rktext = '未参加当期排位赛'
    except (maintenanceIn, apiCallError, userIdBan) as e:
        rktext = ''
    except Exception as e:
        rktext = ''
        logger.warning(f"pjsk逮捕查排位失败。Error：{e}")
    text = text + ('\n\n' + rktext if rktext else '')
    try:
        await matcher.finish(text)
    except ActionFailed:
        await matcher.finish(image(b64=pic2b64(text2image(text))))