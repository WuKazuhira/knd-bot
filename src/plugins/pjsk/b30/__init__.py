import hashlib
import json
import time
from collections import OrderedDict
from typing import Tuple

from nonebot import on_command
from nonebot.adapters.onebot.v11 import Message, MessageEvent
from nonebot.internal.matcher import Matcher
from nonebot.params import Command, CommandArg
from services.log import logger
from services.pjsk_draw import render
from utils.message_builder import image

from .._config import BUG_ERROR, SERVER_MAP, suite_path
from .._errors import apiCallError, maintenanceIn, pjskError, userIdBan
from .._models import UserProfile
from .._profile_header import build_header_payload
from .._utils import (
    async_load_master_data,
    get_pjsk_type,
    get_userid_preprocess,
)

__plugin_name__ = "烧烤b30/pjskb30"
__plugin_type__ = "烧烤相关&uni移植"
__plugin_version__ = 0.1
__plugin_usage__ = f"""
usage：
    查询烧烤b30(仅供娱乐)
    若群内已有unibot请勿开启此bot该功能
    私聊可用，限制每人1分钟只能查询2次
    指令：
        b30/烧烤b30/pjsk b30           :查看自己的b30
        b30/烧烤b30/pjsk b30  @qq      :查看艾特用户的b30(对方必须已绑定烧烤账户)
        b30/烧烤b30/pjsk b30  烧烤id    :查看对应烧烤账号的b30
        b30/烧烤b30/pjsk b30  活动排名   :查看当期活动排名对应烧烤用户的b30
    数据来源：
        pjsekai.moe
        unipjsk.com
""".strip()
__plugin_settings__ = {
    "default_status": False,
    "cmd": ["b30", "pjskb30", "烧烤相关", "uni移植", "烧烤b30"],
}
__plugin_cd_limit__ = {"cd": 60, "count_limit": 2, "rst": "别急，等[cd]秒后再用！", "limit_type": "user"}
__plugin_block_limit__ = {"rst": "别急，还在查！"}

pjsk_b30 = on_command('pjsk b30', aliases={'pjskb30', '烧烤b30', '烧烤 b30', 'b30'}, priority=5, block=True)
cn_b30 = on_command('cnpjsk b30', aliases={'cnpjskb30', 'cn烧烤b30', 'cn烧烤 b30', 'cnb30'}, priority=5, block=True)
tw_b30 = on_command('twpjsk b30', aliases={'twpjskb30', 'tw烧烤b30', 'tw烧烤 b30', 'twb30'}, priority=5, block=True)


_B30_RESULT_CACHE: OrderedDict[tuple, bytes] = OrderedDict()
_B30_RESULT_CACHE_LIMIT = 12





def _build_b30_profile(profile: UserProfile, userid: str, isprivate: bool, suite_data: dict, suite_raw_data: dict = None) -> dict:
    if suite_raw_data is None:
        suite_raw_data = {}
    # userMusicResults 在根层（suite_raw_data），gamedata 层（suite_data）里没有
    music_results = (
        suite_raw_data.get('userMusicResults')
        or suite_data.get('userMusicResults')
        or []
    )
    return {
        'userid': '保密' if isprivate else userid,
        'name': profile.name or suite_data.get('name') or suite_raw_data.get('name') or '???',
        'rank': profile.rank or suite_data.get('rank', 0) or suite_raw_data.get('rank', 0),
        'userDecks': profile.userDecks or suite_data.get('userDecks', []) or suite_raw_data.get('userDecks', []),
        'special_training': profile.special_training or suite_data.get('special_training', []),
        'userProfileHonors': profile.userProfileHonors or suite_data.get('userProfileHonors', []) or suite_raw_data.get('userProfileHonors', []),
        'userHonorMissions': profile.userHonorMissions or suite_data.get('userHonorMissions', []) or suite_raw_data.get('userHonorMissions', []),
        'suite_update_time': suite_data.get('upload_time') or suite_raw_data.get('upload_time') or suite_data.get('updatedAt') or suite_raw_data.get('updatedAt') or getattr(profile, 'updatedAt', 0),
        'music_results': music_results,
    }


def fcrank(playlevel, rank):
    if playlevel <= 32:
        return rank - 1.5
    else:
        return rank - 1




@pjsk_b30.handle()
@cn_b30.handle()
@tw_b30.handle()
async def _(matcher: Matcher, event: MessageEvent, msg: Message = CommandArg(), cmd: Tuple[str, ...] = Command()):
    pjsk_type = get_pjsk_type(cmd[0])
    
    server_name = SERVER_MAP.get(pjsk_type, 'jp')

    # 获取用户 ID
    state = await get_userid_preprocess(event, msg, pjsk_type=pjsk_type)
    if reply := state['error']:
        await matcher.finish(reply, at_sender=True)
    userid = state['userid']
    isprivate = state['private']
    
    # 获取基础资料和 suite 数据
    profile = UserProfile()
    try:
        await profile.getprofile(userid, 'profile', is_force_update=True, pjsk_type=pjsk_type)
    except pjskError as e:
        await matcher.finish(str(e))
    except (maintenanceIn, apiCallError, userIdBan) as e:
        await matcher.finish(str(e))
    except Exception as e:
        import traceback
        logger.error(f"[b30] 获取profile失败: {e}")
        logger.error(f"[b30] 错误堆栈: {traceback.format_exc()}")
        await matcher.finish(BUG_ERROR)

    try:
        await profile.getsuite(userid, pjsk_type=pjsk_type)
    except Exception as e:
        logger.warning(f"[b30] 获取suite失败，后续将使用profile数据兜底: {e}")

    suite_raw_data = getattr(profile, 'suite_raw_data', None) or {}
    suite_data = getattr(profile, 'suite_data', None) or {}
    # suite_data 是 gamedata 层，suite_raw_data 是根层
    # userMusicResults 在根层，需要从 suite_raw_data 取
    if not isinstance(suite_raw_data, dict):
        suite_raw_data = {}
    if not isinstance(suite_data, dict):
        suite_data = {}

    profile_data = _build_b30_profile(profile, userid, isprivate, suite_data, suite_raw_data)
    music_results_for_key = profile_data.get('music_results') or []
    result_digest = hashlib.blake2b(
        json.dumps(music_results_for_key, sort_keys=True, ensure_ascii=False, default=str).encode('utf-8'),
        digest_size=12,
    ).hexdigest()
    b30_cache_key = (
        'b30-v3', pjsk_type, userid, bool(isprivate),
        profile_data.get('suite_update_time'), profile_data.get('name'), profile_data.get('rank'),
        result_digest,
    )
    cached_b30 = _B30_RESULT_CACHE.get(b30_cache_key)
    if cached_b30 is not None:
        _B30_RESULT_CACHE.move_to_end(b30_cache_key)
        await matcher.finish(image(cached_b30))

    # 获取定数表，缺失时自动下载
    from ..diffrank.data_source import load_constants, update_diff_from_sheet
    constants = load_constants(pjsk_type)
    if not constants:
        logger.info("[b30] realtime/constants.csv 不存在，尝试从 Google Sheets 下载...")
        await update_diff_from_sheet(pjsk_type=pjsk_type)
        constants = load_constants(pjsk_type)
        if not constants:
            logger.warning("[b30] 定数数据获取失败，将使用整数 level 作为定数")

    # 读取原始难度列表（musicDifficulties.json）
    diff = [
        item.copy()
        for item in await async_load_master_data('musicDifficulties.json', pjsk_type)
        if isinstance(item, dict)
    ]
    diff_index = {}
    for diff_item in diff:
        mid = diff_item.get('musicId')
        d = diff_item.get('musicDifficulty')
        play_level = diff_item.get('playLevel', 0)
        # AP 定数：有精确定数则用，否则用整数 level
        ap = constants.get((mid, d), play_level)
        diff_item['result'] = 0
        diff_item['rank'] = 0
        diff_item['has_constant'] = (mid, d) in constants
        diff_item['aplevel+'] = ap
        # FC 定数 = AP 定数 - 1（level≥33）或 - 1.5（level<33）
        diff_item['fclevel+'] = fcrank(play_level, ap)
        diff_index[(mid, d)] = diff_item
    diff.sort(key=lambda x: x["aplevel+"], reverse=True)
    highest = 0
    top_count = min(30, len(diff))
    for i in range(top_count):
        highest = highest + diff[i]['aplevel+']
    highest = round(highest / top_count, 2) if top_count else 0
    # userMusicResults 在根层（suite_raw_data），兼容 suite_data 层
    music_results = (
        suite_raw_data.get('userMusicResults')
        or suite_data.get('userMusicResults')
        or []
    )
    for music in music_results:
        playResult = music.get('playResult')
        musicId = music.get('musicId')
        # Suite API 用 musicDifficultyType，兼容旧格式的 musicDifficulty
        musicDifficulty = music.get('musicDifficultyType') or music.get('musicDifficulty')
        diff_item = diff_index.get((musicId, musicDifficulty))
        if diff_item is None:
            continue
        if playResult == 'full_perfect':
            diff_item['result'] = 2
            diff_item['rank'] = diff_item['aplevel+']
        elif playResult == 'full_combo':
            if diff_item['result'] < 1:
                diff_item['result'] = 1
                diff_item['rank'] = diff_item['fclevel+']
    diff.sort(key=lambda x: x["rank"], reverse=True)

    # 非实时数据时在图上标注抓包上传时间
    data_update_text = None
    try:
        if not profile.isNewData:
            user_suite_file = suite_path / server_name / f'{userid}.json'
            if user_suite_file.exists():
                mtime = user_suite_file.stat().st_mtime
                updatetime = time.localtime(mtime)
                data_update_text = '数据更新于：' + time.strftime("%Y-%m-%d %H:%M:%S", updatetime)
    except Exception as e:
        logger.debug(f"[b30] 读取更新时间失败: {e}")

    # 数据收集完成，出图交给绘图服务。
    encoded = await render('b30', {
        'diff': diff[:30],
        'highest': highest,
        'header': build_header_payload(profile, userid, isprivate, suite_data, suite_raw_data),
        'data_update_text': data_update_text,
        'pjsk_type': pjsk_type,
    })
    _B30_RESULT_CACHE[b30_cache_key] = encoded
    _B30_RESULT_CACHE.move_to_end(b30_cache_key)
    while len(_B30_RESULT_CACHE) > _B30_RESULT_CACHE_LIMIT:
        _B30_RESULT_CACHE.popitem(last=False)

    await matcher.finish(image(encoded))

