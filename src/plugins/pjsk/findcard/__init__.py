import asyncio
import os
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from nonebot import logger, on_command
from nonebot.adapters.onebot.v11 import Message, MessageEvent
from nonebot.exception import FinishedException
from nonebot.internal.matcher import Matcher
from nonebot.params import Command, CommandArg

from services import logger
from services.pjsk_draw import render
from services.pjsk_draw.renderers.cardinfo import CardInfoView
from utils.message_builder import image

from .._card_utils import (
    UNIT_KEY_TO_INTERNAL,
    UNIT_MAIN_CHARS,
    cardidtopic,
    cardtype,
    get_unit_vs_chars,
    is_fes_card,
)
from .._config import SERVER_MAP, data_path
from .._event_utils import extract_ban_event_arg, get_event_card_ids
from .._models import CardInfo
from .._utils import async_load_master_data, get_pjsk_type, load_master_data

try:
    from plugins.image_management.pjsk_images.pjsk_db_source import PjskAlias
    search_flag = True
except:
    search_flag = False
    pass
import json

__plugin_name__ = "卡面查询/findcard"
__plugin_type__ = "烧烤相关&uni移植"
__plugin_version__ = 0.1
__plugin_usage__ = f"""
    帮助：knd findcard help；原有命令入口保留兼容。

usage：
    查询烧烤卡面信息
    若群内已有unibot请勿开启此bot该功能
    私聊可用，限制每人1分钟只能查询4次
    指令：
        findcard [角色名] [筛选条件...]         : 查看角色名对应卡面概览（按属性分行）
        查卡/cardinfo [卡面id]                  : 查看卡面详细信息
        card     [卡面id]                       : 查看卡面id对应的特训前后卡面大图
    筛选条件（可组合使用）：
        稀有度：一星/1  二星/2  三星/3  四星/4  生日
        属性：  cool  cute  happy  mysterious  pure
        技能：  判分  回血  复合  完美
        限定：  限定  常驻  fes
        团：    leo  mmj  vbs  ws  25ji  vs
        年份：  2022  2023  2024  ...
        活动卡：活动  event
    数据来源：
        pjsekai.moe
        unipjsk.com
""".strip()
__plugin_settings__ = {
    "default_status": False,
    "cmd": ['findcard', '烧烤相关', 'uni移植', '卡面查询', 'card', 'cardinfo', 'cn_cardinfo', 'cncard', 'cnfindcard', 'cn查卡', 'cn查询卡面', 'tw_cardinfo', 'twcard', 'twfindcard', 'tw查卡', 'tw查询卡面', '查卡', '查询卡面'],
}
__plugin_cd_limit__ = {"cd": 60, "count_limit": 4, "rst": "别急，等[cd]秒后再用！", "limit_type": "user"}
__plugin_block_limit__ = {"rst": "别急，还在查！"}


RARITY_MAP: Dict[str, str] = {
    '一星': 'rarity_1', '1星': 'rarity_1', '1': 'rarity_1',
    '二星': 'rarity_2', '2星': 'rarity_2', '2': 'rarity_2',
    '三星': 'rarity_3', '3星': 'rarity_3', '3': 'rarity_3',
    '四星': 'rarity_4', '4星': 'rarity_4', '4': 'rarity_4',
    '生日': 'rarity_birthday', 'birthday': 'rarity_birthday',
}

ATTR_MAP: Dict[str, str] = {
    'cool': 'cool', 'cute': 'cute', 'happy': 'happy',
    'mysterious': 'mysterious', 'pure': 'pure',
    '酷': 'cool', '可爱': 'cute', '快乐': 'happy', '神秘': 'mysterious', '纯洁': 'pure',
    '蓝': 'cool',  '蓝星': 'cool',
    '橙': 'happy', '橙心': 'happy', '黄': 'happy',
    '紫': 'mysterious', '紫月': 'mysterious',
    '粉': 'cute',  '粉花': 'cute',
    '绿': 'pure',  '绿草': 'pure',
}

SKILL_MAP: Dict[str, str] = {
    '判分': 'score_up', '加分': 'score_up',
    '回血': 'life_recovery', '回复': 'life_recovery',
    '复合': 'score_up_condition_life_recovery',
    '完美': 'score_up_keep',
}

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

LIMITED_KEYWORDS = {'限定', 'limited'}
PERMANENT_KEYWORDS = {'常驻', 'permanent'}
FES_KEYWORDS = {'fes', 'colorful', 'cf'}

EVENT_KEYWORDS = {'活动', 'event', '活动卡'}


findcard = on_command('findcard', aliases={"查卡", "查询卡面"}, priority=5, block=True)
cn_findcard = on_command('cnfindcard', aliases={"cn查卡", "cn查询卡面"}, priority=5, block=True)
tw_findcard = on_command('twfindcard', aliases={"tw查卡", "tw查询卡面"}, priority=5, block=True)

card = on_command('card', priority=5, block=True)
cn_card = on_command('cncard', priority=5, block=True)
tw_card = on_command('twcard', priority=5, block=True)

cardinfo = on_command('cardinfo', priority=4, block=True)
cn_cardinfo = on_command('cn_cardinfo', priority=4, block=True)
tw_cardinfo = on_command('tw_cardinfo', priority=4, block=True)


async def alias2id(alias: str, group_id: Optional[int] = None) -> int:
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
    if _id == 0 and search_flag:
        name = await PjskAlias.query_name(alias, group_id=group_id)
        return dic.get(name, 0)
    else:
        return _id


class CardFilter:
    def __init__(self):
        self.rarity: Optional[str] = None
        self.attr: Optional[str] = None
        self.skill: Optional[str] = None
        self.is_limited: Optional[bool] = None
        self.is_fes: Optional[bool] = None
        self.unit: Optional[range] = None
        self.year: Optional[int] = None
        self.event_only: bool = False
        self.event_id: Optional[int] = None
        self.event_label: Optional[str] = None
        self.show_leak: bool = False

    @classmethod
    def parse(cls, tokens: List[str]) -> Tuple['CardFilter', List[str]]:
        f = cls()
        remaining = []
        for token in tokens:
            t = token.lower().strip()
            if not t:
                continue
            if t in RARITY_MAP:
                f.rarity = RARITY_MAP[t]
            elif t in ATTR_MAP:
                f.attr = ATTR_MAP[t]
            elif t in SKILL_MAP:
                f.skill = SKILL_MAP[t]
            elif t in {k.lower() for k in FES_KEYWORDS}:
                f.is_fes = True
                f.is_limited = True
            elif t in {k.lower() for k in LIMITED_KEYWORDS}:
                if f.is_fes is None:
                    f.is_limited = True
            elif t in {k.lower() for k in PERMANENT_KEYWORDS}:
                f.is_limited = False
            elif t in {k.lower() for k in EVENT_KEYWORDS}:
                f.event_only = True
            elif t in UNIT_CHAR_RANGE:
                f.unit = UNIT_CHAR_RANGE[t]
            elif t == 'leak':
                f.show_leak = True
            elif t.isdigit() and len(t) == 4:
                f.year = int(t)
            else:
                remaining.append(token)
        return f, remaining


async def _parse_args(raw: str, pjsk_type: int = 0, group_id: Optional[int] = None) -> Tuple[str, CardFilter, Optional[str]]:
    card_filter = CardFilter()
    ban_event, rest, ban_error = await extract_ban_event_arg(raw, pjsk_type=pjsk_type, group_id=group_id)
    if ban_error:
        return '', card_filter, ban_error
    if ban_event:
        card_filter.event_id = ban_event['id']
        card_filter.event_label = ban_event.get('name') or f"Event {ban_event['id']}"
        raw = rest

    tokens = raw.split()
    parsed_filter, remaining = CardFilter.parse(tokens)
    parsed_filter.event_id = card_filter.event_id
    parsed_filter.event_label = card_filter.event_label
    alias = ' '.join(remaining).strip()
    return alias, parsed_filter, None


def _apply_filter(
    cards: List[Dict[str, Any]],
    card_filter: CardFilter,
    cardCostume3ds: List[Dict],
    costume3ds: List[Dict],
    skills_data: List[Dict],
    event_card_ids: Optional[set],
    card_supplies: List[Dict] = None,
    now_ts: int = 0,
    pjsk_type: int = 0,
) -> List[Dict[str, Any]]:
    result = []
    for card in cards:
        if not isinstance(card, dict):
            continue

        if not card_filter.show_leak and now_ts > 0:
            if card.get('releaseAt', 0) > now_ts:
                continue

        if card_filter.rarity and card.get('cardRarityType') != card_filter.rarity:
            continue

        if card_filter.attr and card.get('attr') != card_filter.attr:
            continue

        if card_filter.skill:
            skill_id = card.get('skillId')
            matched_skill = False
            for sk in skills_data:
                if not isinstance(sk, dict):
                    continue
                if sk.get('id') == skill_id:
                    if sk.get('descriptionSpriteName') == card_filter.skill:
                        matched_skill = True
                    break
            if not matched_skill:
                continue

        if card_filter.is_limited is not None or card_filter.is_fes is not None:
            is_lim = (cardtype(card['id'], cardCostume3ds, costume3ds) == 1
                      or card.get('cardRarityType') == 'rarity_birthday')
            
            if card_filter.is_fes is not None:
                is_fes = is_fes_card(card, card_supplies, pjsk_type)
                if card_filter.is_fes:
                    if not is_fes:
                        continue
                else:
                    if is_fes:
                        continue
            elif card_filter.is_limited is not None:
                if is_lim != card_filter.is_limited:
                    continue

        if card_filter.unit is not None:
            if card.get('characterId') not in card_filter.unit:
                continue

        if card_filter.year is not None:
            release_ts = card.get('releaseAt', 0)
            release_year = datetime.fromtimestamp(release_ts / 1000, tz=timezone.utc).year
            if release_year != card_filter.year:
                continue

        if (card_filter.event_only or card_filter.event_id is not None) and event_card_ids is not None:
            if card['id'] not in event_card_ids:
                continue

        result.append(card)
    return result


def _make_cache_key(charaid: int, card_filter: CardFilter) -> str:
    # 布局改动时递增版本号清缓存
    parts = ['layout_v3', str(charaid)]
    parts.append(card_filter.rarity or 'all')
    parts.append(card_filter.attr or 'allattr')
    parts.append(card_filter.skill or 'allskill')
    if card_filter.is_fes is True:
        parts.append('fes')
    elif card_filter.is_limited is True:
        parts.append('limited')
    elif card_filter.is_limited is False:
        parts.append('permanent')
    else:
        parts.append('alllimit')
    if card_filter.unit is not None:
        parts.append(f'unit{card_filter.unit.start}')
    else:
        parts.append('allunit')
    parts.append(str(card_filter.year) if card_filter.year else 'allyear')
    parts.append(f"eventid{card_filter.event_id}" if card_filter.event_id is not None else ('event' if card_filter.event_only else 'allevent'))
    parts.append('leak' if card_filter.show_leak else 'noleak')
    return '_'.join(parts)


@findcard.handle()
@cn_findcard.handle()
@tw_findcard.handle()
async def _(matcher: Matcher, event: MessageEvent, arg: Message = CommandArg(), cmd: Tuple[str, ...] = Command()):
    try:
        pjsk_type = get_pjsk_type(cmd[0])
        server_name = SERVER_MAP.get(pjsk_type, 'jp')

        raw = arg.extract_plain_text().strip()

        if raw.isdigit():
            await _cardinfo(matcher, event, arg, cmd=cmd)
            return

        group_id = None
        if hasattr(event, 'group_id'):
            group_id = event.group_id

        alias, card_filter, parse_error = await _parse_args(raw, pjsk_type=pjsk_type, group_id=group_id)
        if parse_error:
            await matcher.finish(parse_error)

        charaid = 0
        if alias:
            charaid = await alias2id(alias, group_id)
            if charaid == 0:
                await matcher.finish('找不到你说的角色哦')
        elif card_filter.unit is None:
            has_filter = any([
                card_filter.rarity is not None,
                card_filter.attr is not None,
                card_filter.skill is not None,
                card_filter.is_limited is not None,
                card_filter.is_fes is not None,
                card_filter.year is not None,
                card_filter.event_only,
                card_filter.event_id is not None,
                card_filter.show_leak,
            ])
            if not has_filter:
                await matcher.finish('请输入角色名/团队名或筛选条件（如：fes、限定、四星等）')

        allcards = await async_load_master_data('cards.json', pjsk_type)
        cardCostume3ds = await async_load_master_data('cardCostume3ds.json', pjsk_type)
        costume3ds = await async_load_master_data('costume3ds.json', pjsk_type)
        skills_data = await async_load_master_data('skills.json', pjsk_type)
        card_supplies = await async_load_master_data('cardSupplies.json', pjsk_type)

        event_card_ids: Optional[set] = None
        if card_filter.event_id is not None:
            event_card_ids = get_event_card_ids(card_filter.event_id, pjsk_type=pjsk_type)
        elif card_filter.event_only:
            event_cards_raw = await async_load_master_data('eventCards.json', pjsk_type)
            event_card_ids = {
                ec['cardId'] for ec in event_cards_raw
                if isinstance(ec, dict) and 'cardId' in ec
            }

        now_ts = int(time.time() * 1000)

        allcards.sort(key=lambda x: x.get("releaseAt", 0), reverse=True)

        is_unit_query = (not charaid and card_filter.unit is not None)
        is_global_query = (not charaid and card_filter.unit is None)

        unit_internal: Optional[str] = None
        ordered_chars: List[int] = []

        if is_unit_query:
            unit_internal = next(
                (internal for key, internal in UNIT_KEY_TO_INTERNAL.items()
                 if UNIT_CHAR_RANGE.get(key) == card_filter.unit),
                None
            )
            if unit_internal is None:
                await matcher.finish('无法识别团体名，请重试')

            main_chars = UNIT_MAIN_CHARS.get(unit_internal, [])

            gameCharacterUnits_data = await async_load_master_data('gameCharacterUnits.json', pjsk_type)
            vs_chars = get_unit_vs_chars(unit_internal, gameCharacterUnits_data)

            ordered_chars = main_chars + vs_chars

            # 虚拟歌手需要额外按 supportUnit 过滤
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

            vs_filter = CardFilter()
            vs_filter.rarity = card_filter.rarity
            vs_filter.attr = card_filter.attr
            vs_filter.skill = card_filter.skill
            vs_filter.is_limited = card_filter.is_limited
            vs_filter.is_fes = card_filter.is_fes
            vs_filter.year = card_filter.year
            vs_filter.event_only = card_filter.event_only
            vs_filter.event_id = card_filter.event_id
            vs_filter.event_label = card_filter.event_label
            vs_filter.show_leak = card_filter.show_leak
            vs_filter.unit = None
            target_cards = _apply_filter(
                base_cards, vs_filter, cardCostume3ds, costume3ds, skills_data, event_card_ids, card_supplies,
                now_ts=now_ts, pjsk_type=pjsk_type
            )
        elif is_global_query:
            target_cards = _apply_filter(
                allcards, card_filter, cardCostume3ds, costume3ds, skills_data, event_card_ids, card_supplies,
                now_ts=now_ts, pjsk_type=pjsk_type
            )
            ordered_chars = list(range(1, 27))
        else:
            ordered_chars = [charaid]
            base_cards = [c for c in allcards if isinstance(c, dict) and c.get('characterId') == charaid]
            target_cards = _apply_filter(
                base_cards, card_filter, cardCostume3ds, costume3ds, skills_data, event_card_ids, card_supplies,
                now_ts=now_ts, pjsk_type=pjsk_type
            )

        if not target_cards:
            await matcher.finish('没有找到符合条件的卡面哦')

        if card_filter.show_leak:
            leak_cards = [c for c in target_cards if c.get('releaseAt', 0) > now_ts]
            if not leak_cards:
                await matcher.finish('没有找到未发布的卡面哦')
            if len(leak_cards) == 1:
                from nonebot.params import Command as _Cmd
                leak_arg = Message(str(leak_cards[0]['id']))
                await _cardinfo(matcher, event, leak_arg, cmd=cmd)
                return
            target_cards = leak_cards

        count = len(target_cards)

        if count > 300:
            await matcher.finish(f'查询结果共 {count} 张卡面，范围过大，请添加更多筛选条件（如角色名、属性、稀有度等）缩小范围')

        cache_key = _make_cache_key(charaid, card_filter)

        path = data_path / server_name / 'cardinfo'
        path.mkdir(parents=True, exist_ok=True)
        savepath = path / f'{cache_key}_{count}.jpg'

        if savepath.exists():
            await matcher.finish(image(savepath))

        for fname in os.listdir(path):
            if fname.startswith(f'{cache_key}_') and fname.endswith('.jpg'):
                os.remove(path / fname)

        pic = await render('findcard', {
            'card_ids': [int(c.get('id', 0)) for c in target_cards if isinstance(c, dict)],
            'ordered_chars': ordered_chars,
            'unit_internal': unit_internal or 'all',
            'pjsk_type': pjsk_type,
        })
        savepath.write_bytes(pic)
        await matcher.finish(image(savepath))

    except FinishedException:
        raise
    except Exception as e:
        logger.exception(f"findcard handler failed: {e}")
        await matcher.finish(f"出错了: {e}")


@card.handle()
@cn_card.handle()
@tw_card.handle()
async def _card(matcher: Matcher, event: MessageEvent, arg: Message = CommandArg(), cmd: Tuple[str, ...] = Command()):
    pjsk_type = get_pjsk_type(cmd[0])

    card_id = arg.extract_plain_text().strip()
    try:
        card_id = int(card_id)
    except:
        return
    pic_paths = await cardidtopic(card_id, pjsk_type=pjsk_type)
    await matcher.finish(Message([image(i) for i in pic_paths]))


@cardinfo.handle()
@cn_cardinfo.handle()
@tw_cardinfo.handle()
async def _cardinfo(matcher: Matcher, event: MessageEvent, arg: Message = CommandArg(), cmd: Tuple[str, ...] = Command()):
    pjsk_type = get_pjsk_type(cmd[0])
    server_name = SERVER_MAP.get(pjsk_type, 'jp')
    card_id_str = arg.extract_plain_text().strip()

    try:
        try:
            card_id = int(card_id_str)
        except ValueError:
            return

        path = data_path / server_name / 'infocard'
        path.mkdir(parents=True, exist_ok=True)
        file = path / f'id_{card_id_str}.jpg'

        if not file.exists():
            card_obj = CardInfo()
            await card_obj.getinfo(card_id, pjsk_type=pjsk_type)
            pic = await render('cardinfo', CardInfoView.payload_from_card(card_obj))
            file.write_bytes(pic)

        await matcher.finish(image(file))

    except FinishedException:
        raise
    except Exception as e:
        logger.exception(f"cardinfo handler failed: {e}")
        await matcher.finish(f"出错了: {e}" if str(e) else "出错了，可能是没有此id的卡面")
