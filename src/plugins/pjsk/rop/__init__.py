import json
import time
from typing import Tuple

from nonebot import on_command
from nonebot.adapters.onebot.v11 import Message, MessageEvent
from nonebot.internal.matcher import Matcher
from nonebot.params import Command, CommandArg

from services.log import logger
from services.pjsk_draw import render
from utils.message_builder import image

from .._config import BUG_ERROR, SERVER_MAP, suite_path
from .._errors import pjskError
from .._models import UserProfile
from .._profile_header import build_header_payload
from .._utils import get_pjsk_type, get_userid_preprocess

__plugin_name__ = "烧烤进度/pjsk进度"
__plugin_type__ = "烧烤相关&uni移植"
__plugin_version__ = 0.1
__plugin_usage__ = f"""
usage：
    查询烧烤收歌进度
    若群内已有unibot请勿开启此bot该功能
    私聊可用，限制每人1分钟只能查询2次

    默认难度为master，若带参数ex、expert可以查询expert谱面收歌进度
    指令：
        烧烤进度/pjsk进度/pjskrop       ?[ex,ma]       :查看自己的收歌进度
        烧烤进度/pjsk进度/pjskrop @qq   ?[ex,ma]       :查看艾特用户的收歌进度(对方必须已绑定烧烤账户)
        烧烤进度/pjsk进度/pjskrop 烧烤id ?[ex,ma]       :查看对应烧烤账号的收歌进度
    数据来源：
        pjsekai.moe
        unipjsk.com
""".strip()
__plugin_settings__ = {
    "default_status": False,
    "cmd": ["pjsk进度", "烧烤进度", "烧烤相关"],
}
__plugin_cd_limit__ = {"cd": 60, "count_limit": 2, "rst": "别急，等[cd]秒后再用！", "limit_type": "user"}
__plugin_block_limit__ = {"rst": "别急，还在查！"}

# pjsk进度
pjsk_progress = on_command('pjsk进度', aliases={'pjskrop', "烧烤进度"}, priority=5, block=True)
cn_progress = on_command('cnpjsk进度', aliases={'cnpjskrop', "cn烧烤进度"}, priority=5, block=True)
tw_progress = on_command('twpjsk进度', aliases={'twpjskrop', "tw烧烤进度"}, priority=5, block=True)


@pjsk_progress.handle()
@cn_progress.handle()
@tw_progress.handle()
async def _(matcher: Matcher, event: MessageEvent, msg: Message = CommandArg(), cmd: Tuple[str, ...] = Command()):
    pjsk_type = get_pjsk_type(cmd[0])
    
    server_name = SERVER_MAP.get(pjsk_type, 'jp')

    # 参数解析
    arg = msg.extract_plain_text().strip()
    if 'ex' in arg.lower() or 'expert' in arg.lower():
        diff = 'expert'
    else:
        diff = 'master'
    state = await get_userid_preprocess(event, msg, pjsk_type=pjsk_type)
    if reply := state['error']:
        await matcher.finish(reply, at_sender=True)
    userid = state['userid']
    isprivate = state['private']
    # 用户信息
    profile = UserProfile()
    try:
        await profile.getsuite(userid, pjsk_type=pjsk_type)
    except pjskError as e:
        await matcher.finish(str(e))
    except Exception as e:
        import traceback
        logger.error(f"[rop] 获取profile失败: {e}")
        logger.error(f"[rop] 错误堆栈: {traceback.format_exc()}")
        await matcher.finish(BUG_ERROR)
    
    # 数据收集完成，出图交给绘图服务。
    score = profile.masterscore if diff == 'master' else profile.expertscore

    data_update_text = None
    if not profile.isNewData:
        user_suite_file = suite_path / server_name / f'{userid}.json'
        if user_suite_file.exists():
            mtime = user_suite_file.stat().st_mtime
            updatetime = time.localtime(mtime)
            data_update_text = '数据更新于：' + time.strftime("%Y-%m-%d %H:%M:%S", updatetime)

    pic = await render('rop', {
        'diff': diff,
        'score': {str(level): values for level, values in (score or {}).items()},
        'header': build_header_payload(profile, userid, isprivate),
        'data_update_text': data_update_text,
        'pjsk_type': pjsk_type,
    })
    await matcher.finish(image(pic))
