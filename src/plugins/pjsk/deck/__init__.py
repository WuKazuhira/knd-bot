"""组卡功能插件，支持活动、挑战、长草和加成组卡。"""
import asyncio
import time
from io import BytesIO
from typing import Tuple

from nonebot import on_command
from nonebot.adapters.onebot.v11 import Message, MessageEvent
from nonebot.exception import FinishedException
from nonebot.internal.matcher import Matcher
from nonebot.params import Command, CommandArg
from nonebot.permission import SUPERUSER
from PIL import Image

from services.log import logger
from utils.imageutils import pic2b64_fast
from utils.message_builder import image

from .._autoask import pjsk_update_manager
from .._common_utils import callapi
from .._config import (
    BUG_ERROR,
    DECK_RECOMMEND_DEFAULT_ALGS,
    DECK_RECOMMEND_SERVERS,
    HARUKI_DECK_SERVICE_SERVERS,
    NOT_IMAGE_ERROR,
    SERVER_MAP,
    data_path,
    suite_path,
)
from .._errors import apiCallError, maintenanceIn, pjskError, userIdBan
from .._haruki_remote import render_deck
from .._models import UserProfile
from .._utils import async_load_master_data, get_pjsk_type, get_userid_preprocess
from ._allium_backend import get_allium_unavailable_reason, is_allium_available
from ._backend_state import MODE_LABELS, load_backend_mode, save_backend_mode
from ._draw import compose_deck_image
from ._options import (
    BOOST_BONUS_DICT,
    build_bonus_options,
    build_challenge_options,
    build_event_options,
    build_no_event_options,
)
from ._recommender import do_recommend

_AREA_ITEM_LEVELS_CACHE = {}

import json

__plugin_name__ = "烧烤组卡/pjsk deck"
__plugin_type__ = "烧烤相关&uni移植"
__plugin_version__ = 0.1
__plugin_usage__ = f"""
usage：
    查询烧烤最优卡组推荐
    私聊可用，限制每人1分钟只能查询2次
    指令：
        活动组卡/组卡                :当前活动最优卡组
        活动组卡 活动123             :指定活动最优卡组
        活动组卡 mfy/wl2/wlmfy       :WL活动指定角色/章节组卡
        活动组卡 ln蓝               :模拟ln+蓝属性活动组卡
        挑战组卡                    :每日挑战最优卡组
        长草组卡/最强卡组            :无活动时最强卡组
        加成组卡 120                :指定120%加成的最优卡组
    参数：
        多人/单人/auto              :指定live类型
        满技能/满破/已读             :指定卡牌配置
        顶配/满配                  :使用全卡满级/满突破/满技能/区域道具满级的顶配数据
        次顶配/中配                 :同上但区域道具限制在15级
        当前/目前                  :使用当前实际卡组（固定当前5张卡）
        #123 456                   :固定指定卡牌
        #emu miku                  :固定指定角色，第一个角色排首位
        纯ln/纯蓝                  :限制仅某团/某属性上场
        3火/5火                    :指定火数
    数据来源：
        pjsekai.moe / unipjsk.com
    超级用户：
        组卡后端 [http|allium|both]   :查看或切换组卡后端（重启后保持）
""".strip()
__plugin_settings__ = {
    "default_status": False,
    "cmd": ["pjsk组卡", "烧烤相关", "组卡", "活动组卡", "挑战组卡", "长草组卡", "加成组卡", "pjsk deck"],
}
__plugin_cd_limit__ = {"cd": 60, "count_limit": 2, "rst": "别急，等[cd]秒后再用！", "limit_type": "user"}
__plugin_block_limit__ = {"rst": "别急，还在组卡！"}


# 指令注册

# 活动组卡
pjsk_event_deck = on_command(
    '活动组卡',
    aliases={'组卡', '活动卡组', '活动组队', '活动配队', '配队', '组队', '模拟组卡', 'pjsk deck', 'pjsk event deck'},
    priority=5, block=True
)
cn_event_deck = on_command(
    'cn活动组卡',
    aliases={'cn组卡', 'cn活动卡组', 'cn活动组队', 'cn活动配队', 'cn配队', 'cn组队', 'cn模拟组卡', 'cnpjsk deck'},
    priority=5, block=True
)
tw_event_deck = on_command(
    'tw活动组卡',
    aliases={'tw组卡', 'tw活动卡组', 'tw活动组队', 'tw活动配队', 'tw配队', 'tw组队', 'tw模拟组卡', 'twpjsk deck'},
    priority=5, block=True
)

# 挑战组卡
pjsk_challenge_deck = on_command(
    '挑战组卡',
    aliases={'挑战卡组', '挑战组队', '挑战配队', 'pjsk challenge deck'},
    priority=5, block=True
)
cn_challenge_deck = on_command(
    'cn挑战组卡',
    aliases={'cn挑战卡组', 'cn挑战组队', 'cn挑战配队', 'cnpjsk challenge deck'},
    priority=5, block=True
)
tw_challenge_deck = on_command(
    'tw挑战组卡',
    aliases={'tw挑战卡组', 'tw挑战组队', 'tw挑战配队', 'twpjsk challenge deck'},
    priority=5, block=True
)

# 长草组卡
pjsk_no_event_deck = on_command(
    '长草组卡',
    aliases={'最强卡组', '最强组卡', '长草卡组', '长草组队', 'pjsk best deck', 'pjsk no event deck'},
    priority=5, block=True
)
cn_no_event_deck = on_command(
    'cn长草组卡',
    aliases={'cn最强卡组', 'cn最强组卡', 'cn长草卡组', 'cnpjsk best deck'},
    priority=5, block=True
)
tw_no_event_deck = on_command(
    'tw长草组卡',
    aliases={'tw最强卡组', 'tw最强组卡', 'tw长草卡组', 'twpjsk best deck'},
    priority=5, block=True
)

# 加成组卡
pjsk_bonus_deck = on_command(
    '加成组卡',
    aliases={'控分组卡', '加成卡组', '控分卡组', 'pjsk bonus deck'},
    priority=5, block=True
)
cn_bonus_deck = on_command(
    'cn加成组卡',
    aliases={'cn控分组卡', 'cn加成卡组', 'cn控分卡组', 'cnpjsk bonus deck'},
    priority=5, block=True
)
tw_bonus_deck = on_command(
    'tw加成组卡',
    aliases={'tw控分组卡', 'tw加成卡组', 'tw控分卡组', 'twpjsk bonus deck'},
    priority=5, block=True
)


# 核心处理逻辑

def _determine_recommend_type(options: dict) -> str:
    """根据 options 判断组卡类型"""
    live_type = options.get('live_type', 'multi')
    target = options.get('target', 'score')
    event_id = options.get('event_id')
    event_unit = options.get('event_unit')
    wl_cid = options.get('world_bloom_character_id')
    challenge_cid = options.get('challenge_live_character_id')

    if target == 'bonus':
        if wl_cid:
            return 'wl_bonus'
        return 'bonus'
    elif live_type in ['challenge', 'challenge_auto']:
        if challenge_cid:
            return 'challenge'
        return 'challenge_all'
    elif event_id:
        if wl_cid:
            return 'wl'
        return 'event'
    elif event_unit:
        return 'unit_attr'
    else:
        return 'no_event'


async def _get_area_item_level_map(pjsk_type: int, area_item_level: int) -> dict:
    cache_key = (pjsk_type, area_item_level)
    if cache_key not in _AREA_ITEM_LEVELS_CACHE:
        area_item_levels_data = await async_load_master_data('areaItemLevels.json', pjsk_type)
        levels = {}
        for item in area_item_levels_data:
            if not isinstance(item, dict):
                continue
            item_id = item.get('areaItemId')
            lv = item.get('level', 0)
            if lv > area_item_level:
                continue
            levels[item_id] = max(levels.get(item_id, 0), lv)
        _AREA_ITEM_LEVELS_CACHE[cache_key] = levels
    return _AREA_ITEM_LEVELS_CACHE[cache_key].copy()


# 稀有度对应满级等级
_RARITY_MAX_LEVEL = {
    'rarity_1': 20,
    'rarity_2': 30,
    'rarity_3': 50,
    'rarity_4': 60,
    'rarity_birthday': 60,
}


async def construct_max_profile(pjsk_type: int = 0, max_area_item_level: int = 20) -> dict:
    """
    构造一个"顶配"虚拟用户数据，包含当前所有已发布卡片的满级状态。
    - 顶配 (max_area_item_level=20): 所有卡满技能/满突破/已读，区域道具满级
    - 次顶配 (max_area_item_level=15): 同上但区域道具限制在15级
    """
    import time as _time

    now_ms = int(_time.time() * 1000)

    cards_data = await async_load_master_data('cards.json', pjsk_type)
    card_episodes_data = await async_load_master_data('cardEpisodes.json', pjsk_type)

    # 按 cardId 索引 episodes
    episodes_by_card: dict = {}
    for ep in card_episodes_data:
        if not isinstance(ep, dict):
            continue
        cid = ep.get('cardId')
        if cid is not None:
            episodes_by_card.setdefault(cid, []).append(ep)

    user_cards = []
    for card in cards_data:
        if not isinstance(card, dict):
            continue
        # 仅处理已发布的卡
        if card.get('releaseAt', 0) > now_ms:
            continue
        card_id = card.get('id')
        if not card_id:
            continue
        rarity = card.get('cardRarityType', 'rarity_1')
        level = _RARITY_MAX_LEVEL.get(rarity, 20)
        # rarity_3/rarity_4 有特训（specialTrainingCosts 非空则有特训）
        has_training = bool(card.get('specialTrainingCosts'))
        episodes = [
            {'cardEpisodeId': ep['id'], 'scenarioStatus': 'already_read'}
            for ep in episodes_by_card.get(card_id, [])
            if isinstance(ep, dict) and 'id' in ep
        ]
        user_cards.append({
            'cardId': card_id,
            'level': level,
            'skillLevel': 4,
            'masterRank': 5,
            'specialTrainingStatus': 'done' if has_training else 'none',
            'defaultImage': 'special_training' if has_training else 'original',
            'episodes': episodes,
        })

    # 角色等级满级（26名角色 + 虚拟歌手按实际处理，给到120级）
    user_characters = [
        {'characterId': cid, 'characterRank': 120}
        for cid in range(1, 27)
    ]

    # 区域道具
    levels = await _get_area_item_level_map(pjsk_type, max_area_item_level)
    user_areas = [{
        'userAreaStatus': {},
        'areaItems': [
            {'areaItemId': item_id, 'level': lv}
            for item_id, lv in levels.items()
        ]
    }]

    return {
        'userGamedata': {},
        'userDecks': [],
        'userCards': user_cards,
        'userHonors': [],
        'userCharacters': user_characters,
        'userAreas': user_areas,
    }


async def _get_user_data_bytes(profile: UserProfile, suite_data: dict, additional: dict, pjsk_type: int = 0) -> bytes:
    """
    将用户 suite 数据序列化为后端可接受的 JSON bytes。
    后端的 DeckRecommendUserData.load_from_bytes 期望接收包含
    userCards, userDecks, userAreas, userCharacters 等字段的 JSON。
    """
    # 顶配/次顶配模式：使用虚拟满级数据，不需要用户实际数据
    if additional.get('max_profile'):
        logger.info("[deck] 使用顶配模式（全卡满级/满突破/满技能/区域道具满级）")
        data = await construct_max_profile(pjsk_type, max_area_item_level=20)
        return json.dumps(data, ensure_ascii=False).encode('utf-8')
    if additional.get('sub_max_profile'):
        logger.info("[deck] 使用次顶配模式（全卡满级/满突破/满技能/区域道具15级）")
        data = await construct_max_profile(pjsk_type, max_area_item_level=15)
        return json.dumps(data, ensure_ascii=False).encode('utf-8')

    # 优先使用 suite API 返回的原始数据
    if suite_data and isinstance(suite_data, dict):
        data = suite_data.copy()
    else:
        # 尝试从本地缓存读取
        server_name = SERVER_MAP.get(pjsk_type, 'jp')
        user_suite_file = suite_path / server_name / f'{profile.userid}.json'

        if user_suite_file.exists():
            with open(user_suite_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
        else:
            raise Exception("未找到用户数据，请先使用 pjsk b30 或烧烤档案 命令获取数据")
    
    # 处理区域道具等级提升
    area_item_level = additional.get('area_item_level')
    if area_item_level is not None:
        levels = await _get_area_item_level_map(pjsk_type, area_item_level)

        # 确保 userAreas 存在
        if 'userAreas' not in data:
            data['userAreas'] = []
        
        # 已存在的区域道具等级覆盖
        for area in data['userAreas']:
            if 'areaItems' not in area:
                area['areaItems'] = []
            for area_item in area['areaItems']:
                item_id = area_item.get('areaItemId')
                if item_id in levels:
                    area_item['level'] = max(area_item.get('level', 0), levels[item_id])
                    del levels[item_id]
        
        # 不存在的添加
        if levels:
            data['userAreas'].append({
                "userAreaStatus": {},
                "areaItems": [
                    {
                        "areaItemId": item_id,
                        "level": lv,
                    } for item_id, lv in levels.items()
                ]
            })
    
    return json.dumps(data, ensure_ascii=False).encode('utf-8')


async def _handle_deck_recommend(
    matcher: Matcher,
    event: MessageEvent,
    msg: Message,
    cmd: Tuple[str, ...],
    build_options_func,
):
    """通用组卡处理函数"""
    pjsk_type = get_pjsk_type(cmd[0])
    server_name = SERVER_MAP.get(pjsk_type, 'jp')

    # 优先解析参数，以便顶配/次顶配模式下跳过用户数据获取
    arg_text = msg.extract_plain_text().strip()
    full_text = event.get_plaintext().strip()
    cmd_text = cmd[0] if cmd else ''
    if cmd_text and full_text.lower().startswith(cmd_text.lower()):
        args = full_text[len(cmd_text):].strip().lower()
        if arg_text.strip() and '#' not in args:
            args = arg_text.strip().lower()
    else:
        args = arg_text.strip().lower()
    logger.info(f"[deck] 解析参数文本: args='{args}' raw_arg='{arg_text}' full='{full_text}'")
    try:
        parsed = await build_options_func(args, pjsk_type)
        options = parsed['options']
        additional = parsed['additional']
        if options.get('fixed_characters') and options.get('algorithm') == 'all':
            logger.warning(
                f"[deck] fixed_characters={options.get('fixed_characters')} 需要实际队长约束，"
                "将算法从 all 强制切换为 dfs"
            )
            options['algorithm'] = 'dfs'
        logger.info(
            f"[deck] 参数解析完成: algorithm={options.get('algorithm')} "
            f"fixed_characters={options.get('fixed_characters')} "
            f"fixed_cards={options.get('fixed_cards')} "
            f"forced_leader_character_id={options.get('forced_leader_character_id')} "
            f"event_id={options.get('event_id')} music_id={options.get('music_id')}"
        )
    except Exception as e:
        logger.error(f"[deck] 参数解析失败: {e}")
        import traceback
        logger.error(traceback.format_exc())
        await matcher.finish(f"参数解析失败: {e}")

    use_max_profile = additional.get('max_profile', False)
    use_sub_max_profile = additional.get('sub_max_profile', False)

    # 顶配/次顶配模式：不需要用户数据，直接使用虚拟满级数据
    if use_max_profile or use_sub_max_profile:
        userid = None
        is_private = False
        profile = UserProfile()
        suite_data = None
        user_profile_honors = []
        user_honor_missions = []
    else:
        # 获取用户 ID
        # 组卡指令的参数是活动号/歌曲名等，不是 UID，需要传空消息让它走绑定查询逻辑
        state = await get_userid_preprocess(event, Message(""), pjsk_type=pjsk_type)
        if reply := state['error']:
            await matcher.finish(reply, at_sender=True)
        userid = state['userid']
        is_private = state['private']

        # 获取资料数据
        profile = UserProfile()
        suite_data = None
        try:
            suite_data = await profile.getsuite(userid, pjsk_type=pjsk_type)
        except pjskError as e:
            await matcher.finish(str(e))
        except (maintenanceIn, apiCallError, userIdBan) as e:
            await matcher.finish(str(e))
        except Exception as e:
            logger.error(f"[deck] 获取profile失败: {e}")
            await matcher.finish(BUG_ERROR)

        # 优先复用已有 suite/profile 数据中的 honor 信息，缺失时再回源获取
        user_profile_honors = (
            (suite_data.get('userProfileHonors') if isinstance(suite_data, dict) else None)
            or profile.userProfileHonors
        )
        user_honor_missions = (
            (suite_data.get('userHonorMissions') if isinstance(suite_data, dict) else None)
            or profile.userHonorMissions
        )

        if not user_profile_honors and not user_honor_missions:
            profile_full = UserProfile()
            try:
                await profile_full.getprofile(userid, 'profile', is_force_update=True, pjsk_type=pjsk_type)
                user_profile_honors = profile_full.userProfileHonors
                user_honor_missions = profile_full.userHonorMissions
            except Exception:
                user_profile_honors = profile.userProfileHonors
                user_honor_missions = profile.userHonorMissions

    # 确定组卡类型
    recommend_type = _determine_recommend_type(options)

    # 使用当前卡组：从 suite 数据中读取当前 deck 并固定为 fixed_cards
    if additional.get('use_current_deck'):
        if recommend_type == 'challenge_all':
            await matcher.finish('需要指定挑战角色才能使用"当前"参数，例如：挑战组卡 miku 当前')
        current_deck_cards = None
        if suite_data and isinstance(suite_data, dict):
            user_decks = suite_data.get('userDecks', [])
            if user_decks and isinstance(user_decks, list) and len(user_decks) > 0:
                deck = user_decks[0]
                if isinstance(deck, dict):
                    current_deck_cards = [
                        deck[f'member{i}'] for i in range(1, 6)
                        if deck.get(f'member{i}')
                    ]
        if not current_deck_cards or len(current_deck_cards) < 5:
            await matcher.finish("无法获取当前卡组（需要更新抓包数据），请先使用 pjsk b30 或烧烤档案 命令更新数据")
        options['fixed_cards'] = current_deck_cards
        options['fixed_characters'] = None
        options['algorithm'] = 'dfs'
        logger.info(f"[deck] 使用当前卡组: fixed_cards={current_deck_cards}")

    # 获取用户资料
    try:
        userdata_bytes = await _get_user_data_bytes(profile, suite_data, additional, pjsk_type)
    except Exception as e:
        await matcher.finish(str(e))

    # 发送组卡请求
    try:
        merged_servers = HARUKI_DECK_SERVICE_SERVERS + DECK_RECOMMEND_SERVERS
        server_urls = [s['url'] for s in merged_servers]
        server_weights = [s['weight'] for s in merged_servers]

        # 准备批次组卡参数
        options_list = []
        if recommend_type == "challenge_all":
            # 挑战组卡没有指定角色情况下，每角色组1个最强
            for cid in range(1, 27):  # 角色ID 1-26
                opt = options.copy()
                opt['challenge_live_character_id'] = cid
                opt['limit'] = 1
                options_list.append(opt)
        else:
            # 正常组卡
            options_list = [options]

        results = await do_recommend(
            server_urls=server_urls,
            server_weights=server_weights,
            region=server_name,
            options_list=options_list,
            userdata_bytes=userdata_bytes,
            default_algs=DECK_RECOMMEND_DEFAULT_ALGS,
        )

        if not results:
            await matcher.finish("组卡服务返回空结果")

        # 合并所有结果
        all_decks = []
        all_algs = []
        cost_times = {}
        wait_times = {}
        
        for result_decks, result_algs, c_times, w_times in results:
            all_decks.extend(result_decks)
            all_algs.extend(result_algs)
            for alg, cost in c_times.items():
                if alg not in cost_times:
                    cost_times[alg] = []
                cost_times[alg].append(cost)
            for alg, wait in w_times.items():
                if alg not in wait_times:
                    wait_times[alg] = []
                wait_times[alg].append(wait)
        
        # 计算平均时间
        for alg in cost_times:
            cost_times[alg] = sum(cost_times[alg]) / len(cost_times[alg])
        for alg in wait_times:
            wait_times[alg] = sum(wait_times[alg]) / len(wait_times[alg])

        # 限制返回数量
        # challenge_all 模式每个角色已经 limit=1，合并后不再截断
        if recommend_type != 'challenge_all':
            limit = options.get('limit', 3)
            result_decks = all_decks[:limit]
            result_algs = all_algs[:limit]
        else:
            result_decks = all_decks
            result_algs = all_algs

    except Exception as e:
        logger.error(f"[deck] 组卡请求失败: {e}")
        await matcher.finish(f"组卡失败: {e}")

    # 构建 profile_data 用于绘图
    import time
    profile_data = {
        'name': profile.name,
        'rank': profile.rank,
        'userid': userid,
        'userDecks': profile.userDecks,
        'special_training': profile.special_training,
        'userProfileHonors': user_profile_honors,
        'userHonorMissions': user_honor_missions,
        'update_time': int(time.time()),  # 数据更新时间戳
    }
    
    # 读取 suite 数据更新时间，直接取顶层 upload_time
    suite_update_time = None
    if suite_data and isinstance(suite_data, dict):
        ts = suite_data.get('upload_time')
        if ts:
            # upload_time 可能是毫秒级，转换为秒
            suite_update_time = int(ts) // 1000 if int(ts) > 1e10 else int(ts)
        if not suite_update_time:
            suite_update_time = int(time.time())

    profile_data['suite_update_time'] = suite_update_time

    remote_pic = None
    try:
        remote_pic = await render_deck({
            'profile_data': profile_data,
            'is_private': is_private,
            'result_decks': result_decks,
            'result_algs': result_algs,
            'cost_times': cost_times,
            'wait_times': wait_times,
            'recommend_type': recommend_type,
            'options': options,
            'additional': additional,
            'pjsk_type': pjsk_type,
        })
    except Exception as e:
        logger.warning(f"[deck] 远端绘图失败，回退本地实现: {e}")

    if remote_pic:
        try:
            await matcher.finish(image(b64=pic2b64_fast(Image.open(BytesIO(remote_pic)).convert('RGB'))))
        except Exception as e:
            logger.warning(f"[deck] 远端图片转换失败，回退本地实现: {e}")

    # 绘制图片
    try:
        pic = await compose_deck_image(
            profile_data=profile_data,
            is_private=is_private,
            result_decks=result_decks,
            result_algs=result_algs,
            cost_times=cost_times,
            wait_times=wait_times,
            recommend_type=recommend_type,
            options=options,
            additional=additional,
            pjsk_type=pjsk_type,
        )
        pic = pic.convert("RGB")
        # 组卡图 1100x1600 且无透明，PNG 编码 230ms / 载荷 664KB，
        # JPEG 只要 38ms / 300KB——载荷砍半直接缩短 OneBot 端的上传时间。
        # cardbox 与 b30 早已改用 pic2b64_fast，这里当时漏了。
        await matcher.finish(image(b64=pic2b64_fast(pic, quality=88)))
    except FinishedException:
        raise
    except Exception as e:
        logger.error(f"[deck] 绘图失败: {e}")
        import traceback
        logger.error(traceback.format_exc())
        await matcher.finish(f"组卡图片生成失败: {e}")


# 指令处理器

@pjsk_event_deck.handle()
@cn_event_deck.handle()
@tw_event_deck.handle()
async def _(matcher: Matcher, event: MessageEvent, msg: Message = CommandArg(), cmd: Tuple[str, ...] = Command()):
    await _handle_deck_recommend(matcher, event, msg, cmd, build_event_options)


@pjsk_challenge_deck.handle()
@cn_challenge_deck.handle()
@tw_challenge_deck.handle()
async def _(matcher: Matcher, event: MessageEvent, msg: Message = CommandArg(), cmd: Tuple[str, ...] = Command()):
    await _handle_deck_recommend(matcher, event, msg, cmd, build_challenge_options)


@pjsk_no_event_deck.handle()
@cn_no_event_deck.handle()
@tw_no_event_deck.handle()
async def _(matcher: Matcher, event: MessageEvent, msg: Message = CommandArg(), cmd: Tuple[str, ...] = Command()):
    await _handle_deck_recommend(matcher, event, msg, cmd, build_no_event_options)


@pjsk_bonus_deck.handle()
@cn_bonus_deck.handle()
@tw_bonus_deck.handle()
async def _(matcher: Matcher, event: MessageEvent, msg: Message = CommandArg(), cmd: Tuple[str, ...] = Command()):
    await _handle_deck_recommend(matcher, event, msg, cmd, build_bonus_options)


# 组卡后端切换（仅超级用户）

deck_backend_switch = on_command(
    '组卡后端',
    aliases={'组卡后端切换', '切换组卡后端', 'deck backend'},
    permission=SUPERUSER, priority=4, block=True
)

_BACKEND_ALIASES = {
    'http': 'http', 'deck-service': 'http', 'deckservice': 'http', 'rust': 'http', '容器': 'http',
    'allium': 'allium', '本地': 'allium', 'cpp': 'allium', 'c++': 'allium',
    'both': 'both', '两个': 'both', '全部': 'both', 'all': 'both', '都': 'both',
}


def _backend_status_text(mode: str) -> str:
    lines = [f"当前组卡后端：{MODE_LABELS[mode]}"]
    ok = is_allium_available()
    lines.append(f"allium 可用性：{'正常' if ok else '不可用（' + get_allium_unavailable_reason() + '）'}")
    lines.append(f"deck-service 地址：{', '.join(DECK_RECOMMEND_SERVERS) or '未配置'}")
    lines.append("用法：组卡后端 http / allium / both")
    return "\n".join(lines)


@deck_backend_switch.handle()
async def _(matcher: Matcher, msg: Message = CommandArg()):
    raw = msg.extract_plain_text().strip().lower().replace(' ', '')
    current = load_backend_mode()

    if not raw:
        await matcher.finish(_backend_status_text(current))

    mode = _BACKEND_ALIASES.get(raw)
    if mode is None:
        await matcher.finish(f"参数无效：{raw}\n用法：组卡后端 http / allium / both")

    # allium 是进程内引擎，装不上或素材缺失时切过去会每次组卡都失败，先挡住。
    if mode in ('allium', 'both') and not is_allium_available():
        await matcher.finish(f"无法切换：allium 后端不可用（{get_allium_unavailable_reason()}）")

    if mode == current:
        await matcher.finish(f"组卡后端已经是：{MODE_LABELS[mode]}")

    saved = save_backend_mode(mode)
    logger.warning(f"[deck] 组卡后端已由 {current} 切换为 {saved}")
    await matcher.finish(f"已切换组卡后端：{MODE_LABELS[saved]}\n下一次组卡立即生效。")
