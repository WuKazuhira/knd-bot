"""卡牌一览功能插件。"""
import asyncio
import hashlib
import json
import math
import time
from collections import OrderedDict
from datetime import datetime, timezone
from io import BytesIO
from typing import Any, Dict, List, Optional, Tuple

from nonebot import on_command
from nonebot.adapters.onebot.v11 import Message, MessageEvent
from nonebot.exception import FinishedException
from nonebot.internal.matcher import Matcher
from nonebot.params import Command, CommandArg
from PIL import Image

from services.log import logger
from utils.imageutils import pic2b64
from utils.message_builder import image

from .._card_utils import (
    ATTR_ORDER,
    RARITY_WEIGHT,
    UNIT_KEY_TO_INTERNAL,
    UNIT_MAIN_CHARS,
    cardtype,
    get_unit_vs_chars,
    is_fes_card,
)
from .._config import SERVER_MAP, data_path, suite_path
from .._errors import apiCallError, maintenanceIn, pjskError, userIdBan
from .._haruki_remote import render_cardbox
from .._models import UserProfile
from .._utils import async_load_master_data, get_pjsk_type, get_userid_preprocess
from ._draw import compose_cardbox_image

__plugin_name__ = "卡牌一览/cardbox"
__plugin_type__ = "烧烤相关&uni移植"
__plugin_version__ = 0.2
__plugin_usage__ = """
usage：
    查看卡牌一览，支持角色/团体查询
    私聊可用，限制每人1分钟只能查询3次
    指令：
        卡牌一览                        : 查看全部角色卡牌
        卡牌一览 [角色名]              : 查看指定角色卡牌
        卡牌一览 [团体名]              : 查看指定团体卡牌
        卡牌一览 [筛选条件]            : 查看符合条件的卡牌
        卡牌一览 [角色名] box          : 查看个人持有的该角色卡牌
    筛选条件（可组合使用）：
        稀有度：一星/1  二星/2  三星/3  四星/4  生日
        属性：  cool  cute  happy  mysterious  pure
        限定：  限定  常驻  fes
        年份：  2022  2023  2024  ...
        活动卡：活动  event
        box：   仅显示持有的卡牌
    数据来源：
        pjsekai.moe
        unipjsk.com
""".strip()
__plugin_settings__ = {
    "default_status": False,
    "cmd": ["卡牌一览", "cardbox", "烧烤相关", "卡面一览", "卡一览"],
}
__plugin_cd_limit__ = {"cd": 60, "count_limit": 3, "rst": "别急，等[cd]秒后再用！", "limit_type": "user"}
__plugin_block_limit__ = {"rst": "别急，还在查！"}

_CARD_BOX_RESULT_CACHE: OrderedDict[tuple, str] = OrderedDict()
_CARD_BOX_RESULT_CACHE_LIMIT = 8


# 筛选维度映射表

# 稀有度（所有 key 均为小写）
RARITY_MAP: Dict[str, str] = {
    '一星': 'rarity_1', '1星': 'rarity_1', '1': 'rarity_1',
    '二星': 'rarity_2', '2星': 'rarity_2', '2': 'rarity_2',
    '三星': 'rarity_3', '3星': 'rarity_3', '3': 'rarity_3',
    '四星': 'rarity_4', '4星': 'rarity_4', '4': 'rarity_4',
    '生日': 'rarity_birthday', 'birthday': 'rarity_birthday',
}

# 属性（key 均为小写）
ATTR_MAP: Dict[str, str] = {
    'cool': 'cool', 'cute': 'cute', 'happy': 'happy',
    'mysterious': 'mysterious', 'pure': 'pure',
    '酷': 'cool', '可爱': 'cute', '快乐': 'happy', '神秘': 'mysterious', '纯洁': 'pure',
    '蓝': 'cool', '蓝星': 'cool',
    '橙': 'happy', '橙心': 'happy', '黄': 'happy',
    '紫': 'mysterious', '紫月': 'mysterious',
    '粉': 'cute', '粉花': 'cute',
    '绿': 'pure', '绿草': 'pure',
}

# 限定关键词
LIMITED_KEYWORDS = {'限定', 'limited'}
PERMANENT_KEYWORDS = {'常驻', 'permanent'}
FES_KEYWORDS = {'fes', 'colorful', 'cf', 'bloom', 'bf'}

# 活动卡关键词
EVENT_KEYWORDS = {'活动', 'event', '活动卡'}

# 团体关键词（characterId 所属范围）
UNIT_CHAR_RANGE: Dict[str, range] = {
    'ln': range(1, 5), 'leo': range(1, 5), 'leoneed': range(1, 5),
    'light_sound': range(1, 5),
    'mmj': range(5, 9), 'moremorejump': range(5, 9), 'idol': range(5, 9),
    'vbs': range(9, 13), 'vivid': range(9, 13), 'street': range(9, 13),
    'ws': range(13, 17), 'wonderlands': range(13, 17), 'theme_park': range(13, 17),
    '25h': range(17, 21), '25ji': range(17, 21), '25': range(17, 21),
    '25时': range(17, 21), 'nightcord': range(17, 21), 'school_refusal': range(17, 21),
    'vs': range(21, 27), 'virtual': range(21, 27), 'piapro': range(21, 27),
    'v': range(21, 27),
}


# 角色别名解析

async def alias2id(alias: str, group_id: Optional[int] = None) -> int:
    """将角色别名转换为角色ID"""
    from .._utils import get_chara_alias_map
    dic = get_chara_alias_map()
    if not dic:
        dic = {
            'ick': 1, 'saki': 2, 'hnm': 3, 'shiho': 4,
            'mnr': 5, 'hrk': 6, 'airi': 7, 'szk': 8,
            'khn': 9, 'an': 10, 'akt': 11, 'toya': 12,
            'tks': 13, 'emu': 14, 'nene': 15, 'rui': 16,
            'knd': 17, 'mfy': 18, 'ena': 19, 'mzk': 20,
            'miku': 21, 'rin': 22, 'len': 23, 'luka': 24, 'meiko': 25, 'kaito': 26
        }
    _id = dic.get(alias, 0)
    if _id == 0:
        try:
            from plugins.image_management.pjsk_images.pjsk_db_source import PjskAlias
            name = await PjskAlias.query_name(alias, group_id=group_id)
            return dic.get(name, 0)
        except:
            pass
    return _id


# 参数解析

class CardFilter:
    """卡牌筛选条件"""

    def __init__(self):
        self.rarity: Optional[str] = None       # 'rarity_4' 等
        self.attr: Optional[str] = None         # 'cool' 等
        self.is_limited: Optional[bool] = None  # True=限定 False=常驻 None=不限
        self.is_fes: Optional[bool] = None      # True=仅fes限定 False=排除fes限定 None=不限
        self.year: Optional[int] = None         # 发布年份
        self.event_only: bool = False           # 仅活动卡
        self.show_leak: bool = False            # 显示未发布卡面
        self.show_box: bool = False             # 仅显示持有的卡牌
        self.unit: Optional[range] = None       # 团体范围

    @classmethod
    def parse(cls, tokens: List[str]) -> Tuple['CardFilter', List[str]]:
        """从 tokens 中识别筛选关键词，返回 (CardFilter, 未消费的剩余tokens)"""
        f = cls()
        remaining = []
        for token in tokens:
            t = token.lower().strip()
            if not t:
                continue
            # box 模式
            if t == 'box':
                f.show_box = True
            # 稀有度
            elif t in RARITY_MAP:
                f.rarity = RARITY_MAP[t]
            # 属性
            elif t in ATTR_MAP:
                f.attr = ATTR_MAP[t]
            # fes限定（优先级高于普通限定）
            elif t in {k.lower() for k in FES_KEYWORDS}:
                f.is_fes = True
                f.is_limited = True
            # 限定/常驻
            elif t in {k.lower() for k in LIMITED_KEYWORDS}:
                if f.is_fes is None:
                    f.is_limited = True
            elif t in {k.lower() for k in PERMANENT_KEYWORDS}:
                f.is_limited = False
            # 活动卡
            elif t in {k.lower() for k in EVENT_KEYWORDS}:
                f.event_only = True
            # leak 模式
            elif t == 'leak':
                f.show_leak = True
            # 团体
            elif t in UNIT_CHAR_RANGE:
                f.unit = UNIT_CHAR_RANGE[t]
            # 年份（4位数字）
            elif t.isdigit() and len(t) == 4:
                f.year = int(t)
            else:
                remaining.append(token)
        return f, remaining


def _parse_args(raw: str) -> Tuple[str, CardFilter]:
    """将原始参数字符串拆分为 (角色别名, CardFilter)"""
    tokens = raw.split()
    card_filter, remaining = CardFilter.parse(tokens)
    alias = ' '.join(remaining).strip()
    return alias, card_filter


def _extract_user_cards(suite_data: Any) -> List[Dict[str, Any]]:
    """从 Suite API 的不同返回结构中提取 userCards。"""
    if not isinstance(suite_data, dict):
        return []

    candidates = [suite_data]
    for key in ('data', 'result', 'user', 'userGamedata'):
        block = suite_data.get(key)
        if isinstance(block, dict):
            candidates.append(block)

    for block in candidates:
        cards = block.get('userCards')
        if isinstance(cards, list):
            return [card for card in cards if isinstance(card, dict) and card.get('cardId') is not None]

    return []


def _apply_filter(
    cards: List[Dict[str, Any]],
    card_filter: CardFilter,
    cardCostume3ds: List[Dict],
    costume3ds: List[Dict],
    event_card_ids: Optional[set],
    card_supplies: List[Dict] = None,
    now_ts: int = 0,
    pjsk_type: int = 0,
) -> List[Dict[str, Any]]:
    """根据 CardFilter 对卡面列表进行筛选"""
    result = []
    for card in cards:
        if not isinstance(card, dict):
            continue

        # 时间过滤：releaseAt > now_ts 的卡视为未发布
        if not card_filter.show_leak and now_ts > 0:
            if card.get('releaseAt', 0) > now_ts:
                continue

        # 稀有度
        if card_filter.rarity and card.get('cardRarityType') != card_filter.rarity:
            continue

        # 属性
        if card_filter.attr and card.get('attr') != card_filter.attr:
            continue

        # 限定/常驻/fes筛选
        if card_filter.is_limited is not None or card_filter.is_fes is not None:
            is_lim = (cardtype(card['id'], cardCostume3ds, costume3ds) == 1
                      or card.get('cardRarityType') == 'rarity_birthday')
            
            # 如果指定了fes筛选
            if card_filter.is_fes is not None:
                is_fes = is_fes_card(card, card_supplies, pjsk_type)
                if card_filter.is_fes:
                    if not is_fes:
                        continue
                else:
                    if is_fes:
                        continue
            # 如果只指定了限定/常驻
            elif card_filter.is_limited is not None:
                if is_lim != card_filter.is_limited:
                    continue

        # 年份
        if card_filter.year is not None:
            release_ts = card.get('releaseAt', 0)
            release_year = datetime.fromtimestamp(release_ts / 1000, tz=timezone.utc).year
            if release_year != card_filter.year:
                continue

        # 活动卡
        if card_filter.event_only and event_card_ids is not None:
            if card['id'] not in event_card_ids:
                continue

        result.append(card)
    return result


# 指令注册

cardbox = on_command('卡牌一览', aliases={'卡面一览', '卡一览', 'cardbox'}, priority=5, block=True)
cn_cardbox = on_command('cn卡牌一览', aliases={'cn卡面一览', 'cn卡一览'}, priority=5, block=True)
tw_cardbox = on_command('tw卡牌一览', aliases={'tw卡面一览', 'tw卡一览'}, priority=5, block=True)


# 指令处理

@cardbox.handle()
@cn_cardbox.handle()
@tw_cardbox.handle()
async def _(matcher: Matcher, event: MessageEvent, arg: Message = CommandArg(), cmd: Tuple[str, ...] = Command()):
    try:
        pjsk_type = get_pjsk_type(cmd[0])
        server_name = SERVER_MAP.get(pjsk_type, 'jp')

        raw = arg.extract_plain_text().strip()
        alias, card_filter = _parse_args(raw)

        # 获取用户数据：
        # - 普通卡牌一览需要展示「持有/未持有」状态，未持有灰显；
        # - box 模式在此基础上只保留持有卡牌。
        user_card_ids = None
        profile_data = None
        need_user_suite = True
        if need_user_suite:
            # 注意：不要传入 arg，因为 arg 可能包含筛选条件（如 "box 4"）
            # get_userid_preprocess 会从消息中提取数字作为用户ID，这会导致错误
            # 应该传入空消息，让它从绑定或@中获取用户ID
            from nonebot.adapters.onebot.v11 import Message as EmptyMessage
            state = await get_userid_preprocess(event, EmptyMessage(), pjsk_type=pjsk_type)
            if reply := state['error']:
                await matcher.finish(reply, at_sender=True)
            userid = state['userid']
            is_private = state['private']
            
            # 获取用户数据
            profile = UserProfile()
            try:
                suite_data = await profile.getsuite(userid, pjsk_type=pjsk_type)
            except pjskError as e:
                await matcher.finish(str(e))
            except Exception as e:
                logger.error(f"[cardbox] 获取用户数据失败: {e}")
                await matcher.finish("获取用户数据失败，请稍后再试")
            
            # 获取用户持有的卡牌ID
            user_cards = _extract_user_cards(suite_data)
            if user_cards:
                user_card_ids = {uc['cardId']: uc for uc in user_cards}
            else:
                logger.warning(f"[cardbox] Suite 数据中没有可用 userCards: uid={userid}, server={server_name}")
            
            if card_filter.show_box and not user_card_ids:
                await matcher.finish('没有获取到你的持卡数据，请确认 Suite 数据已上传或稍后再试')
            
            # 构建 profile_data
            profile_data = {
                'name': profile.name,
                'rank': profile.rank,
                'userid': userid if not is_private else None,
                'userDecks': profile.userDecks,
                'special_training': profile.special_training,
                'userProfileHonors': profile.userProfileHonors,
                'userHonorMissions': profile.userHonorMissions,
                'suite_update_time': suite_data.get('upload_time') if suite_data else None,
            }

        # 解析角色或团体
        charaid = 0
        unit_internal = None
        ordered_chars = []

        # 团体别名已在 CardFilter.parse 中消费进 card_filter.unit，alias 此时为空
        if card_filter.unit:
            unit_internal = next(
                (internal for key, internal in UNIT_KEY_TO_INTERNAL.items()
                 if UNIT_CHAR_RANGE.get(key) == card_filter.unit),
                None
            )

        if alias:
            # 先尝试解析为角色
            group_id = None
            if hasattr(event, 'group_id'):
                group_id = event.group_id
            charaid = await alias2id(alias, group_id)

            if charaid == 0 and not unit_internal:
                # 既不是角色也不是团体
                if not card_filter.show_box:
                    await matcher.finish('找不到你说的角色或团体哦')
                # box 模式下，alias 无效时忽略，显示全部持有卡牌

        # 加载数据
        allcards = await async_load_master_data('cards.json', pjsk_type)
        cardCostume3ds = await async_load_master_data('cardCostume3ds.json', pjsk_type)
        costume3ds = await async_load_master_data('costume3ds.json', pjsk_type)
        card_supplies = await async_load_master_data('cardSupplies.json', pjsk_type)
        gameCharacters = await async_load_master_data('gameCharacters.json', pjsk_type)

        # 活动卡 ID 集合
        event_card_ids: Optional[set] = None
        if card_filter.event_only:
            event_cards_raw = await async_load_master_data('eventCards.json', pjsk_type)
            event_card_ids = {
                ec['cardId'] for ec in event_cards_raw
                if isinstance(ec, dict) and 'cardId' in ec
            }

        # 当前时间毫秒时间戳
        now_ts = int(time.time() * 1000)

        # 筛选卡牌
        if unit_internal:
            # 团体查询
            gameCharacterUnits_data = await async_load_master_data('gameCharacterUnits.json', pjsk_type)
            main_chars = UNIT_MAIN_CHARS.get(unit_internal, [])
            vs_chars = get_unit_vs_chars(unit_internal, gameCharacterUnits_data)
            ordered_chars = main_chars + vs_chars
            
            main_char_set = set(main_chars)
            vs_char_set = set(vs_chars)
            base_cards = []
            for c in allcards:
                if not isinstance(c, dict):
                    continue
                cid = c.get('characterId')
                if cid in main_char_set:
                    base_cards.append(c)
                elif cid in vs_char_set:
                    if c.get('supportUnit') == unit_internal:
                        base_cards.append(c)
        elif charaid:
            # 单角色查询
            base_cards = [c for c in allcards if isinstance(c, dict) and c.get('characterId') == charaid]
            ordered_chars = [charaid]
        else:
            # 全部角色
            base_cards = allcards
            ordered_chars = list(range(1, 27))

        target_cards = _apply_filter(
            base_cards, card_filter, cardCostume3ds, costume3ds, event_card_ids, card_supplies,
            now_ts=now_ts, pjsk_type=pjsk_type
        )

        if not target_cards:
            await matcher.finish('没有找到符合条件的卡面哦')

        # 使用本地绘图，确保卡牌一览使用动态单元格布局。
        # 远端绘图服务可能仍是旧布局，暂不优先调用。
        user_state = []
        if user_card_ids:
            user_state = sorted(
                (int(card_id), int((card or {}).get('masterRank') or (card or {}).get('master_rank') or 0))
                for card_id, card in user_card_ids.items()
            )
        cache_payload = {
            'target': [int(card.get('id', 0)) for card in target_cards if isinstance(card, dict)],
            'chars': ordered_chars,
            'user': user_state,
            'profile': profile_data,
            'show_box': card_filter.show_box,
            'server': pjsk_type,
        }
        cache_digest = hashlib.blake2b(
            json.dumps(cache_payload, sort_keys=True, ensure_ascii=False, default=str).encode('utf-8'),
            digest_size=16,
        ).hexdigest()
        cardbox_cache_key = ('cardbox-v4', pjsk_type, cache_digest)
        cached_pic = _CARD_BOX_RESULT_CACHE.get(cardbox_cache_key)
        if cached_pic is not None:
            _CARD_BOX_RESULT_CACHE.move_to_end(cardbox_cache_key)
            await matcher.finish(image(b64=cached_pic))

        # 生成图片
        pic = await compose_cardbox_image(
            cards=target_cards,
            ordered_chars=ordered_chars,
            user_card_ids=user_card_ids,
            profile_data=profile_data,
            show_box=card_filter.show_box,
            allcards=allcards,
            cardCostume3ds=cardCostume3ds,
            costume3ds=costume3ds,
            card_supplies=card_supplies,
            gameCharacters=gameCharacters,
            pjsk_type=pjsk_type,
        )

        _CARD_BOX_RESULT_CACHE[cardbox_cache_key] = pic
        _CARD_BOX_RESULT_CACHE.move_to_end(cardbox_cache_key)
        while len(_CARD_BOX_RESULT_CACHE) > _CARD_BOX_RESULT_CACHE_LIMIT:
            _CARD_BOX_RESULT_CACHE.popitem(last=False)
        await matcher.finish(image(b64=pic))

    except FinishedException:
        raise
    except Exception as e:
        logger.exception(f"cardbox handler failed: {e}")
        await matcher.finish(f"出错了: {e}")
