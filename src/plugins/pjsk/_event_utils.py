import asyncio
import datetime
import json
import re
import time
from typing import Dict, List, Optional, Set, Tuple, Union

import pytz
import yaml
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter

from ._autoask import pjsk_update_manager
from ._card_utils import cardthumnail
from ._common_utils import PJSK_WATERMARK_TEXT, union
from ._config import SERVER_MAP, data_path
from ._utils import (
    async_load_master_data,
    get_chara_alias_map,
    get_pjsk_font,
    load_master_data,
    open_pjsk_image,
    run_pjsk_thread,
    vertical_gradient,
)

# 角色默认缩写（fallback，优先仍使用 character_nicknames.yaml）
DEFAULT_CHARA_ALIAS_MAP: Dict[str, int] = {
    'ick': 1, 'saki': 2, 'hnm': 3, 'shiho': 4,
    'mnr': 5, 'hrk': 6, 'airi': 7, 'szk': 8,
    'khn': 9, 'an': 10, 'akt': 11, 'toya': 12,
    'tks': 13, 'emu': 14, 'nene': 15, 'rui': 16,
    'knd': 17, 'mfy': 18, 'ena': 19, 'mzk': 20,
    'miku': 21, 'rin': 22, 'len': 23, 'luka': 24, 'meiko': 25, 'kaito': 26,
}
CHARA_ICON_FILES: Dict[int, str] = {
    1: 'ick.png', 2: 'saki.png', 3: 'hnm.png', 4: 'shiho.png',
    5: 'mnr.png', 6: 'hrk.png', 7: 'airi.png', 8: 'szk.png',
    9: 'khn.png', 10: 'an.png', 11: 'akt.png', 12: 'toya.png',
    13: 'tks.png', 14: 'emu.png', 15: 'nene.png', 16: 'rui.png',
    17: 'knd.png', 18: 'mfy.png', 19: 'ena.png', 20: 'mzk.png',
    21: 'miku.png', 22: 'rin.png', 23: 'len.png', 24: 'luka.png', 25: 'meiko.png', 26: 'kaito.png',
}
CHARA_SHORT_NAMES: Dict[int, str] = {v: k for k, v in DEFAULT_CHARA_ALIAS_MAP.items()}
UNIT_COLORS = {
    'piapro': (110, 110, 120),
    'light_sound': (68, 85, 221),
    'idol': (136, 221, 68),
    'street': (238, 17, 102),
    'theme_park': (255, 153, 0),
    'school_refusal': (136, 68, 153),
}
EVENT_STYLE_TEXT = (44, 36, 58)
EVENT_STYLE_MUTED = (118, 112, 132)
EVENT_STYLE_ACCENT = (0, 204, 187)
EVENT_STYLE_PANEL = (255, 255, 255, 226)
EVENT_STYLE_LINE = (255, 255, 255, 245)


def _event_soft_shadow(size: Tuple[int, int], radius: int = 24, alpha: int = 46) -> Image.Image:
    shadow = Image.new('RGBA', size, (0, 0, 0, 0))
    d = ImageDraw.Draw(shadow)
    d.rounded_rectangle((0, 0, size[0] - 1, size[1] - 1), radius=radius, fill=(70, 55, 90, alpha))
    return shadow.filter(ImageFilter.GaussianBlur(10))


def _event_round_panel(base: Image.Image, xy: Tuple[int, int, int, int], radius: int = 24, fill=EVENT_STYLE_PANEL, outline=EVENT_STYLE_LINE, shadow: bool = True):
    x1, y1, x2, y2 = xy
    w, h = x2 - x1, y2 - y1
    if shadow:
        sh = _event_soft_shadow((w, h), radius=radius)
        base.paste(sh, (x1 + 4, y1 + 7), sh.split()[-1])
    panel = Image.new('RGBA', (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(panel)
    d.rounded_rectangle((0, 0, w - 1, h - 1), radius=radius, fill=fill, outline=outline, width=1 if outline else 0)
    base.paste(panel, (x1, y1), panel.split()[-1])


def _event_gradient_background(width: int, height: int) -> Image.Image:
    top = (255, 246, 250)
    bottom = (236, 244, 255)
    img = vertical_gradient(width, height, top, bottom)
    glow = Image.new('RGBA', (width, height), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    gd.ellipse((-width // 5, -height // 6, width // 2, height // 4), fill=(255, 190, 220, 72))
    gd.ellipse((width // 2, height // 3, width + width // 4, height + height // 6), fill=(170, 210, 255, 64))
    img.paste(glow, (0, 0), glow.split()[-1])
    return img.convert('RGBA')


def _event_rounded_image(img: Image.Image, radius: int = 16) -> Image.Image:
    img = img.convert('RGBA')
    mask = Image.new('L', img.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, img.width - 1, img.height - 1), radius=radius, fill=255)
    out = Image.new('RGBA', img.size, (0, 0, 0, 0))
    out.paste(img, (0, 0), mask)
    return out


def _event_truncate(draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> str:
    text = str(text or '')
    if draw.textlength(text, font=font) <= max_width:
        return text
    while text and draw.textlength(text + '…', font=font) > max_width:
        text = text[:-1]
    return text + '…' if text else '…'


def _load_event_chara_icon(chara_id: Optional[int], unit: Optional[str] = None, size: int = 34) -> Image.Image:
    if chara_id == 21 and unit and unit != 'piapro':
        candidates = [f'miku_{unit}.png', 'miku.png']
    else:
        candidates = [CHARA_ICON_FILES.get(int(chara_id or 21), 'miku.png')]
    for filename in candidates:
        path = data_path / 'chara/chara_icon' / filename
        if path.exists():
            icon = open_pjsk_image(path, mode='RGBA').resize((size, size), Image.Resampling.LANCZOS)
            return _event_rounded_image(icon, radius=size // 2)
    return Image.new('RGBA', (size, size), (255, 255, 255, 0))


async def resolve_chara_alias(alias: str, group_id: Optional[int] = None) -> int:
    """解析角色别名为 characterId。"""
    alias = (alias or '').strip().lower()
    if not alias:
        return 0

    alias_map = get_chara_alias_map() or {}
    chara_id = alias_map.get(alias) or DEFAULT_CHARA_ALIAS_MAP.get(alias, 0)
    if chara_id:
        return int(chara_id)

    try:
        from plugins.image_management.pjsk_images.pjsk_db_source import PjskAlias
        official_alias = await PjskAlias.query_name(alias, group_id=group_id)
        if official_alias:
            return int(alias_map.get(official_alias) or DEFAULT_CHARA_ALIAS_MAP.get(official_alias, 0))
    except Exception:
        pass
    return 0


def _normalize_master_list(data):
    if isinstance(data, dict):
        return list(data.values())
    return data or []


def get_event_card_ids(event_id: int, pjsk_type: int = 0) -> Set[int]:
    """获取活动卡牌 ID 集合。"""
    event_cards = _normalize_master_list(load_master_data('eventCards.json', pjsk_type))
    return {
        int(ec['cardId']) for ec in event_cards
        if isinstance(ec, dict) and ec.get('eventId') == event_id and ec.get('cardId')
    }


def get_event_music_ids(event_id: int, pjsk_type: int = 0) -> List[int]:
    """获取活动歌曲 ID 列表，按 seq 排序。"""
    event_musics = _normalize_master_list(load_master_data('eventMusics.json', pjsk_type))
    matched = [
        em for em in event_musics
        if isinstance(em, dict) and em.get('eventId') == event_id and em.get('musicId')
    ]
    matched.sort(key=lambda x: x.get('seq', 0))
    return [int(em['musicId']) for em in matched]


def get_ban_events_id_set(pjsk_type: int = 0) -> Set[int]:
    """获取箱活活动 ID 集合。已上线活动按 eventMusics 判断，并保留 nnmbot 的 SDL3 特判。"""
    events = _normalize_master_list(load_master_data('events.json', pjsk_type))
    event_music_ids = {
        int(em['eventId']) for em in _normalize_master_list(load_master_data('eventMusics.json', pjsk_type))
        if isinstance(em, dict) and em.get('eventId')
    }
    normal_event_ids = {
        int(event['id']) for event in events
        if isinstance(event, dict)
        and event.get('eventType') in ('marathon', 'cheerful_carnival')
        and event.get('id') in event_music_ids
    }
    normal_event_ids.add(74)
    return normal_event_ids


def _is_fes_card_for_ban(card: Dict, card_supply_by_id: Dict[int, str]) -> bool:
    supply_type = card_supply_by_id.get(card.get('cardSupplyId'), '')
    return 'festival_limited' in supply_type


def get_event_banner_chara_id(event_id: int, pjsk_type: int = 0) -> Optional[int]:
    """通过活动新卡中非 FES 的最小 cardId 推定箱活主角。"""
    cards = _normalize_master_list(load_master_data('cards.json', pjsk_type))
    card_by_id = {int(c['id']): c for c in cards if isinstance(c, dict) and c.get('id')}
    card_supply_by_id = {
        int(cs['id']): cs.get('cardSupplyType', '')
        for cs in _normalize_master_list(load_master_data('cardSupplies.json', pjsk_type))
        if isinstance(cs, dict) and cs.get('id')
    }

    candidate_ids = []
    for card_id in get_event_card_ids(event_id, pjsk_type):
        card = card_by_id.get(card_id)
        if not card:
            continue
        if _is_fes_card_for_ban(card, card_supply_by_id):
            continue
        candidate_ids.append(card_id)
    if not candidate_ids:
        return None
    banner_card = card_by_id.get(min(candidate_ids))
    if not banner_card:
        return None
    return banner_card.get('characterId')


def get_chara_ban_events(chara_id: int, pjsk_type: int = 0) -> List[Dict]:
    """获取某角色所有箱活，按开始时间升序。"""
    if not chara_id:
        return []
    events = _normalize_master_list(load_master_data('events.json', pjsk_type))
    ban_ids = get_ban_events_id_set(pjsk_type)
    result = []
    for event in events:
        if not isinstance(event, dict) or event.get('id') not in ban_ids:
            continue
        if get_event_banner_chara_id(event['id'], pjsk_type) == chara_id:
            result.append(dict(event))
    result.sort(key=lambda x: x.get('startAt', 0))
    for index, event in enumerate(result, 1):
        event['ban_index'] = index
    return result


async def extract_ban_event_arg(
    text: str,
    pjsk_type: int = 0,
    group_id: Optional[int] = None,
) -> Tuple[Optional[Dict], str, Optional[str]]:
    """从文本中提取 ena7 这类箱活短写，返回 (活动, 剩余文本, 错误提示)。"""
    raw_text = text or ''
    for match in re.finditer(r'(?<!\w)([\w\u3040-\u30ff\u3400-\u9fff]+?)(\d+)(?!\w)', raw_text, flags=re.I):
        alias = match.group(1).strip().lower()
        seq = int(match.group(2))
        if seq <= 0:
            continue
        chara_id = await resolve_chara_alias(alias, group_id=group_id)
        if not chara_id:
            continue
        ban_events = get_chara_ban_events(chara_id, pjsk_type)
        if seq > len(ban_events):
            return None, raw_text, f"角色{alias}只有{len(ban_events)}次箱活"
        event = ban_events[seq - 1]
        rest = (raw_text[:match.start()] + raw_text[match.end():]).strip()
        rest = re.sub(r'\s+', ' ', rest)
        return event, rest, None
    return None, raw_text, None


# 解析组合id
def analysisunitid(unitid, gameCharacterUnits=None, pjsk_type: int = 0):
    if gameCharacterUnits is None:
        gameCharacterUnits = load_master_data('gameCharacterUnits.json', pjsk_type)
    for units in gameCharacterUnits:
        if not isinstance(units, dict):
            continue
        if units['id'] == unitid:
            if unitid <= 20:
                return unitid, units['unit'], f'chr_ts_90_{unitid}.png'
            elif units['gameCharacterId'] == 21:
                if unitid != 21:
                    return 21, units['unit'], f'chr_ts_90_21_{unitid - 25}.png'
                else:
                    return 21, 'piapro', f'chr_ts_90_21.png'
            else:
                return units['gameCharacterId'], units['unit'], f'chr_ts_90_{units["gameCharacterId"]}_2.png'


# 加成角色图
async def _charabonuspic(unitid, attr, cards, gameCharacterUnits, endtime, pjsk_type: int = 0):
    try:
        charaid, unit, charapicname = analysisunitid(unitid, gameCharacterUnits, pjsk_type)
        img = Image.new('RGBA', (2000, 125), color=(0, 0, 0, 0))

        charapic_path = data_path / f'chara/{charapicname}'
        if not charapic_path.exists():
            return None
        charapic = open_pjsk_image(charapic_path)
        charapic = charapic.resize((80, 80))
        r, g, b, mask = charapic.split()
        img.paste(charapic, (0, 0), mask)

        attrpic_path = data_path / f'chara/icon_attribute_{attr}.png'
        if not attrpic_path.exists():
            return None
        attrpic = open_pjsk_image(attrpic_path)
        attrpic = attrpic.resize((80, 80))
        r, g, b, mask = attrpic.split()
        img.paste(attrpic, (84, 0), mask)
        count = 0
        pos = 172
        for card in cards:
            if not isinstance(card, dict):
                continue
            if (
                card['characterId'] == charaid
                and card['attr'] == attr
                and ((card['supportUnit'] == unit) if card['supportUnit'] != 'none' else True)
                and card['releaseAt'] < endtime
            ):
                count += 1
                cardpic = await cardthumnail(card['id'], True, cards, pjsk_type=pjsk_type)
                cardpic = cardpic.resize((125, 125))
                r, g, b, mask = cardpic.split()
                img.paste(cardpic, (pos, 0), mask)
                pos += 130
        if count == 0:
            return None
        img = img.crop((0, 0, pos, 125))
        return img
    except Exception as e:
        return None


# 活动图片
def _prepare_event_background(pic: Image.Image) -> Image.Image:
    """活动详情底图：统一到输出尺寸，轻微模糊压暗，避免和前景卡片抢焦点。"""
    pic = pic.convert('RGBA')
    # 活动详情底图固定为最终输出尺寸，方便按 PS 坐标直接调整各图层位置
    pic = pic.resize((1980, 1210), Image.LANCZOS)
    # 对背景进行轻微模糊，保留场景氛围但避免和前景卡片抢焦点
    pic = pic.filter(ImageFilter.GaussianBlur(radius=8))
    # 只略微压暗背景，整体观感更接近官方活动图
    return ImageEnhance.Brightness(pic).enhance(0.92)


async def drawevent(event, pjsk_type: int = 0):
    try:
        # 优先使用您找到的无角色背景路径
        pic = await pjsk_update_manager.get_asset(
            f'ondemand/event_story/{event.assetbundleName}/screen_image', 'story_bg.png',
            pjsk_type=pjsk_type
        )
        if pic is None:
            # 次优选择
            pic = await pjsk_update_manager.get_asset(
                f'ondemand/event/{event.assetbundleName}/screen', 'bg.png',
                pjsk_type=pjsk_type
            )
    except:
        pic = None
    
    if pic is None:
        # 如果背景图获取失败，创建一个默认背景
        pic = Image.new('RGB', (1980, 1210), color=(200, 200, 200))
    else:
        # 1980x1210 的 LANCZOS 缩放 + 半径 8 的高斯模糊是本函数最重的一段，
        # 留在事件循环上会让整个 bot 卡住数百毫秒，丢到线程池里做。
        pic = await run_pjsk_thread(_prepare_event_background, pic)

    draw = ImageDraw.Draw(pic)
    
    # 1. 绘制背景人物
    try:
        # 优先使用独立的角色无背景图片
        chara = await pjsk_update_manager.get_asset(
            f'ondemand/event/{event.assetbundleName}/screen', 'character.png',
            pjsk_type=pjsk_type
        )
        if chara is None:
            chara = await pjsk_update_manager.get_asset(
                f'ondemand/event_story/{event.assetbundleName}/screen_image', 'story_title.png',
                pjsk_type=pjsk_type
            )
        if chara is not None:
            # 角色立绘不缩放，但先裁掉四周透明区域，再让角色主体左边缘对齐画布并垂直居中
            chara = chara.convert('RGBA')
            alpha_bbox = chara.getchannel('A').getbbox()
            if alpha_bbox is not None:
                chara = chara.crop(alpha_bbox)
            chara_y = (1210 - chara.height) // 2
            pic.paste(chara, (0, chara_y), chara)
    except:
        pass

    # 2. 准备字体
    font_id = get_pjsk_font("SourceHanSansCN-Bold.otf", 24)
    pill_font = get_pjsk_font("SourceHanSansCN-Bold.otf", 34)
    label_font = get_pjsk_font("SourceHanSansCN-Bold.otf", 30)

    # 3. 绘制左下角时间药丸
    def draw_time_pill(y, label, time_str):
        pill_color = (59, 67, 97, 225)
        draw.rounded_rectangle((60, y, 650, y + 58), radius=29, fill=pill_color)
        draw.text((125, y + 11), label, fill=(255, 255, 255), font=label_font)
        draw.text((290, y + 8), time_str, fill=(255, 255, 255), font=pill_font)

    # 绘制 Logo (位于开始时间上方)
    try:
        logo_asset = await pjsk_update_manager.get_asset(
            f'ondemand/event/{event.assetbundleName}/logo', 'logo.png', 
            pjsk_type=pjsk_type
        )
        if logo_asset is not None:
            logo_asset = logo_asset.convert('RGBA')
            # 活动 logo 不缩放，放在开始时间胶囊上方并与胶囊左侧对齐
            logo_x = 60
            logo_y = 1010 - 20 - logo_asset.height
            pic.paste(logo_asset, (logo_x, logo_y), logo_asset)
    except:
        pass

    draw_time_pill(1010, "开始时间", event.startAt)
    draw_time_pill(1090, "结束时间", event.aggregateAt)

    # 4. 右侧内容区域：参考官方构图，留出左侧角色视觉，内容从中右部开始
    header_color = (59, 67, 97, 245)
    right_x = 710
    max_x = 1900
    header_h = 48

    def draw_section_header(x, y, text, width):
        draw.rounded_rectangle((x, y, x + width, y + header_h), radius=24, fill=header_color)
        text_w = draw.textbbox((0, 0), text, font=label_font)[2]
        draw.text((x + (width - text_w) // 2, y + 4), text, fill=(255, 255, 255), font=label_font)

    # Event ID 保留在左上角，避免右侧标题挤占卡片区域
    draw.text((18, 12), f"Event ID: {event.id}", font=font_id, fill=(20, 20, 35, 230))

    cards = await async_load_master_data('cards.json', pjsk_type)
    gameCharacterUnits = await async_load_master_data('gameCharacterUnits.json', pjsk_type)

    # 活动新卡：更靠上、更紧凑，和参考图一致
    new_header_y = 54
    draw_section_header(right_x + 20, new_header_y, "活动新卡", 300)

    new_card_size = 136
    new_card_gap = 18
    card_x = right_x + 35
    card_y = new_header_y + header_h + 20
    for card_id in event.cards:
        try:
            cardimg = await cardthumnail(card_id, True, cards, pjsk_type=pjsk_type)
            cardimg = cardimg.resize((new_card_size, new_card_size))
            pic.paste(cardimg, (card_x, card_y), cardimg)
            card_x += new_card_size + new_card_gap
            if card_x + new_card_size > max_x:
                card_x = right_x + 25
                card_y += new_card_size + new_card_gap
        except:
            continue

    # 同属性加成卡片：紧贴新卡下方，统一行高，减少图2里的大块空白
    bonus_header_y = card_y + new_card_size + 36
    draw_section_header(right_x + 20, bonus_header_y, "同属性加成卡片", 350)

    current_y = bonus_header_y + header_h + 24
    icon_size = 50
    attr_size = 64
    bonus_card_size = 84
    bonus_card_gap = 10
    row_gap = 20
    row_card_start_x = right_x + 135

    # 详情图只展示主加成角色：过滤 50% 加成并把同团 VS 合并成一行，避免箱活画出十几行。
    display_bonus_rows = []
    try:
        eventDeckBonuses = await async_load_master_data('eventDeckBonuses.json', pjsk_type)
        raw_unit_ids = []
        for bonus in eventDeckBonuses:
            if not isinstance(bonus, dict):
                continue
            if bonus.get('eventId') == event.id and bonus.get('bonusRate') == 50 and bonus.get('gameCharacterUnitId'):
                uid = bonus.get('gameCharacterUnitId')
                if uid not in raw_unit_ids:
                    raw_unit_ids.append(uid)
        if not raw_unit_ids:
            raw_unit_ids = list(dict.fromkeys(event.bonusechara))

        analyzed_rows = []
        for uid in raw_unit_ids:
            charaid, unit, charapicname = analysisunitid(uid, gameCharacterUnits, pjsk_type)
            analyzed_rows.append({'kind': 'chara', 'id': charaid, 'unit': unit, 'asset': charapicname})

        units = {row['unit'] for row in analyzed_rows}
        if event.id >= 37 and len(units) == 1 and any(row['id'] > 20 for row in analyzed_rows):
            display_bonus_rows = [row for row in analyzed_rows if row['id'] <= 20]
            display_bonus_rows.append({'kind': 'vs', 'id': None, 'unit': next(iter(units)), 'asset': 'vs_90.png'})
        else:
            display_bonus_rows = analyzed_rows
    except Exception:
        display_bonus_rows = []
        for uid in dict.fromkeys(event.bonusechara):
            try:
                charaid, unit, charapicname = analysisunitid(uid, gameCharacterUnits, pjsk_type)
                display_bonus_rows.append({'kind': 'chara', 'id': charaid, 'unit': unit, 'asset': charapicname})
            except Exception:
                continue

    for bonus_row in display_bonus_rows:
        try:
            charaid = bonus_row.get('id')
            unit = bonus_row.get('unit')
            charapicname = bonus_row.get('asset')

            chara_pic_path = data_path / f'chara/{charapicname}'
            if chara_pic_path.exists():
                chara_icon = open_pjsk_image(chara_pic_path).convert('RGBA').resize((icon_size, icon_size))
                pic.paste(chara_icon, (right_x, current_y + 17), chara_icon)

            attr_pic_path = data_path / f'chara/icon_attribute_{event.bonuseattr}.png'
            if attr_pic_path.exists():
                attr_icon = open_pjsk_image(attr_pic_path).convert('RGBA').resize((attr_size, attr_size))
                pic.paste(attr_icon, (right_x + 60, current_y + 10), attr_icon)

            bonus_card_x = row_card_start_x
            max_row_h = bonus_card_size
            for card in cards:
                if not isinstance(card, dict):
                    continue
                is_target_chara = (
                    card.get('characterId') == charaid
                    if bonus_row.get('kind') != 'vs'
                    else card.get('characterId', 0) > 20 and card.get('supportUnit') == unit
                )
                if (
                    is_target_chara
                    and card['attr'] == event.bonuseattr
                    and ((card['supportUnit'] == unit) if card['supportUnit'] != 'none' else True)
                    and card['releaseAt'] < event.aggregateAtorin
                ):
                    try:
                        cardimg = await cardthumnail(card['id'], True, cards, pjsk_type=pjsk_type)
                        cardimg = cardimg.resize((bonus_card_size, bonus_card_size))
                        if bonus_card_x + bonus_card_size > max_x:
                            bonus_card_x = row_card_start_x
                            current_y += bonus_card_size + 8
                            max_row_h += bonus_card_size + 8
                        pic.paste(cardimg, (bonus_card_x, current_y), cardimg)
                        bonus_card_x += bonus_card_size + bonus_card_gap
                    except:
                        continue

            current_y += max_row_h + row_gap

        except:
            continue

    pic = pic.convert('RGB')
    return pic


# 活动图鉴
async def draweventall(
    event_type: Optional[str] = None,
    event_attr: Optional[str] = None,
    event_units_name: Optional[List] = None,
    event_charas_id: Optional[List[Union[int, Tuple[int, str]]]] = None,
    isEqualAllUnits: bool = True,
    isContainAllCharasId: bool = True,
    isTeamEvent: Optional[bool] = None,
    events: Optional[Dict] = None,
    pjsk_type: int = 0,
    display_limit: Optional[int] = None,
    *args, **kwargs
):
    """
    生成活动图鉴
    :param event_type: 筛选的活动类型
    :param event_attr: 筛选的活动属性
    :param event_units_name: 筛选的活动组合
    :param event_charas_id: 筛选的活动出卡角色
    :param isEqualAllUnits: 筛选的活动组合是否需要完全等同所有组合名称，针对event_units_name参数
    :param isContainAllCharasId: 筛选的活动出卡是否需要包含所有角色id，针对event_charas_id参数
    :param isTeamEvent: True时只筛选箱活、False时只筛选混活，会无视除event_type、event_attr的筛选条件
    :param events: events.json
    """
    if events is None:
        events = await async_load_master_data('events.json', pjsk_type)
    eventCards = await async_load_master_data('eventCards.json', pjsk_type)
    eventDeckBonuses = await async_load_master_data('eventDeckBonuses.json', pjsk_type)
    game_character_units = await async_load_master_data('gameCharacterUnits.json', pjsk_type)
    allcards = await async_load_master_data('cards.json', pjsk_type)
    box_label_by_event: Dict[int, str] = {}
    try:
        ban_ids = get_ban_events_id_set(pjsk_type)
        chara_box_counts: Dict[int, int] = {}
        all_events_for_box = sorted(_normalize_master_list(await async_load_master_data('events.json', pjsk_type)), key=lambda x: x.get('startAt', 0))
        for ev in all_events_for_box:
            event_id = ev.get('id')
            if event_id not in ban_ids:
                continue
            banner_chara_id = get_event_banner_chara_id(event_id, pjsk_type)
            if not banner_chara_id:
                continue
            chara_box_counts[banner_chara_id] = chara_box_counts.get(banner_chara_id, 0) + 1
            short_name = CHARA_SHORT_NAMES.get(banner_chara_id, str(banner_chara_id))
            box_label_by_event[int(event_id)] = f'{short_name}{chara_box_counts[banner_chara_id]}箱'
    except Exception:
        box_label_by_event = {}
    if event_type != 'marathon':
        allteams = await async_load_master_data('cheerfulCarnivalTeams.json', pjsk_type)
        server_name = SERVER_MAP.get(pjsk_type, 'jp')
        trans_path = data_path / server_name / 'translate.yaml'
        if trans_path.exists():
            with open(trans_path, encoding='utf-8') as f:
                trans = yaml.load(f, Loader=yaml.FullLoader) or {}
        else:
             trans = {}
    font30 = get_pjsk_font("SourceHanSansCN-Medium.otf", 30)
    font20 = get_pjsk_font("SourceHanSansCN-Medium.otf", 20)
    font10 = get_pjsk_font("SourceHanSansCN-Medium.otf", 10)
    # 筛选：指定活动图鉴显示的活动类型
    if event_type is not None:
        events = list(filter(lambda x: x['eventType'] == event_type, events))
    events = sorted(events, key=lambda x: x.get('startAt', 0), reverse=True)
    
    limit_count = 10  # 单列活动缩略图的个数
    event_size = (835, 250)  # 每张活动图的尺寸
    event_interval = 20  # 每张活动图的行间距
    event_pad = (40, 20, 25, 25)  # 每张活动图的pad
    handbook_interval = 50  # 每列活动概要的列间距
    handbook_pad = (180, 180, 50, 50)  # 整张活动概要的pad
    light_grey = '#dbdbdb'
    dark_grey = '#929292'
    col_event_imgs = []     # 存放每列活动概要
    _tmp_event_imgs = []

    # 进行预筛选并准备生成任务
    async def process_single_event(each):
        # ********************************获取活动信息******************************** #
        is_world_bloom = each.get('eventType') == 'world_bloom'
        # 获取活动出卡情况
        event_cards = [i['cardId'] for i in filter(lambda x: isinstance(x, dict) and x['eventId'] == each['id'], eventCards)]
        current_cards = list(filter(lambda x: isinstance(x, dict) and x['id'] in event_cards, allcards))
        # 获取活动加成角色，属性
        event_bonusecharas = []
        current_bonuse = list(filter(lambda x: isinstance(x, dict) and x['eventId'] == each['id'], eventDeckBonuses))
        # 筛选：指定活动图鉴显示的对应角色。
        # 现在角色筛选同时匹配“当期出卡角色”和“50% 活动加成角色”，例如 /查活动 tks 会列出司相关活动。
        if event_charas_id:
            matched_count = 0 if isContainAllCharasId else len(event_charas_id) - 1
            if is_world_bloom:
                bonus_unit_ids = [
                    bonuse.get("gameCharacterUnitId") for bonuse in current_bonuse
                    if bonuse.get("gameCharacterUnitId")
                ]
            else:
                bonus_unit_ids = [
                    bonuse.get("gameCharacterUnitId") for bonuse in current_bonuse
                    if bonuse.get('bonusRate') == 50 and bonuse.get("gameCharacterUnitId")
                ]
            bonus_charas = []
            for unitid in bonus_unit_ids:
                try:
                    charaid, unit, _ = analysisunitid(unitid, game_character_units, pjsk_type)
                    bonus_charas.append((charaid, unit))
                except Exception:
                    continue
            for each_id in event_charas_id:
                card_matched = False
                bonus_matched = False
                if isinstance(each_id, tuple):
                    card_matched = any(
                        each_id == (card.get('characterId'), card.get('supportUnit'))
                        for card in current_cards
                    )
                    bonus_matched = each_id in bonus_charas
                else:
                    card_matched = any(each_id == card.get('characterId') for card in current_cards)
                    bonus_matched = any(each_id == charaid for charaid, _unit in bonus_charas)
                if card_matched or bonus_matched:
                    matched_count += 1
            if matched_count < len(event_charas_id):
                return None
        if is_world_bloom:
            event_bonusecharas.extend(
                bonuse["gameCharacterUnitId"] for bonuse in current_bonuse
                if bonuse.get('gameCharacterUnitId')
            )
        else:
            event_bonusecharas.extend(
                bonuse["gameCharacterUnitId"] for bonuse in current_bonuse
                if bonuse['bonusRate'] == 50 and bonuse.get('gameCharacterUnitId')
            )
        try:
            event_bonuseattr = next(filter(lambda x: isinstance(x, dict) and x.get('cardAttr'), current_bonuse))['cardAttr']
        except StopIteration:
            event_bonuseattr = next((card.get('attr') for card in current_cards if isinstance(card, dict) and card.get('attr')), None)
            if not event_bonuseattr:
                return None
        if event_attr is not None:
            if is_world_bloom:
                card_attrs = {card.get('attr') for card in current_cards if isinstance(card, dict) and card.get('attr')}
                if event_attr != event_bonuseattr and event_attr not in card_attrs:
                    return None
            elif event_bonuseattr != event_attr:
                return None
        tmp_bonuse_charas = []
        for unitid in event_bonusecharas:
            charaid, unit, charapicname = analysisunitid(unitid, game_character_units, pjsk_type)
            tmp_bonuse_charas.append({
                'id': charaid,
                'unit': unit,
                'asset': charapicname
            })
        # 对箱活加成角色作额外处理，只对杏二箱(id:37)后箱活作处理，之前的箱活加成角色不用变
        if each['id'] >= 37 and len(set(i['unit'] for i in tmp_bonuse_charas)) == 1:
            for bonuse_chara in tmp_bonuse_charas.copy():
                if bonuse_chara['id'] > 20:
                    tmp_bonuse_charas.remove(bonuse_chara)
            tmp_bonuse_charas.append({
                'unit': tmp_bonuse_charas[0]['unit'],
                'asset': 'vs_90.png'
            })
        event_bonusecharas = tmp_bonuse_charas
        # 加成角色的所属团体
        belong_units = set(map(lambda x: x['unit'], event_bonusecharas))
        if isTeamEvent is True and len(belong_units) != 1:
            return None
        if isTeamEvent is False and len(belong_units) == 1:
            return None
        if is_world_bloom:
            event_scope_label = 'WL'
        else:
            event_scope_label = box_label_by_event.get(each['id']) if len(belong_units) == 1 else '混活'
            if not event_scope_label:
                event_scope_label = '箱活'

        if event_units_name:
            # 当期加成只能是筛选团体（筛选团体单数时即为筛选箱活）
            if isEqualAllUnits and belong_units != set(event_units_name):
                return None
            # 筛选团体存在当期加成即可（但排除箱活），可以是复数
            if not isEqualAllUnits and not (
                len(set(belong_units)) > 1 and
                set(event_units_name).issubset(belong_units)
            ):
                return None
        # ********************************生成活动图片******************************** #
        event_img = Image.new('RGBA', event_size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(event_img)
        _event_round_panel(event_img, (0, 0, event_size[0] - 8, event_size[1] - 8), radius=24, fill=(255, 255, 255, 232), outline=(255, 255, 255, 245), shadow=True)

        banner_w, banner_h = 286, 112
        banner_x, banner_y = 22, 52
        card_size = 54
        card_gap = 8
        team_size = 46

        # 并行准备资源
        resource_tasks = []
        resource_tasks.append(pjsk_update_manager.get_asset(f'ondemand/event_story/{each["assetbundleName"]}/screen_image', 'banner_event_story.png', pjsk_type=pjsk_type))
        if each['eventType'] == 'cheerful_carnival':
            event_teams = list(filter(lambda x: isinstance(x, dict) and x['eventId'] == each['id'], allteams))
            for team_info in event_teams:
                resource_tasks.append(pjsk_update_manager.get_asset(f'ondemand/event/{each["assetbundleName"]}/team_image', f'{team_info["assetbundleName"]}.png', pjsk_type=pjsk_type))
        else:
            event_teams = []
        for cardid in event_cards:
            resource_tasks.append(cardthumnail(cardid, False, allcards, pjsk_type=pjsk_type))

        resources = await asyncio.gather(*resource_tasks)
        res_idx = 0

        bannerpic = resources[res_idx]
        res_idx += 1
        if bannerpic is not None:
            bannerpic = bannerpic.convert('RGBA')
            ratio = max(banner_w / bannerpic.width, banner_h / bannerpic.height)
            bannerpic = bannerpic.resize((int(bannerpic.width * ratio), int(bannerpic.height * ratio)), Image.Resampling.LANCZOS)
            bannerpic = bannerpic.crop(((bannerpic.width - banner_w) // 2, (bannerpic.height - banner_h) // 2, (bannerpic.width + banner_w) // 2, (bannerpic.height + banner_h) // 2))
        else:
            bannerpic = Image.new('RGBA', (banner_w, banner_h), (232, 232, 238, 255))
        bannerpic = _event_rounded_image(bannerpic, radius=18)
        event_img.paste(bannerpic, (banner_x, banner_y), bannerpic)

        eventtype = {"marathon": "马拉松", "cheerful_carnival": "欢乐嘉年华", "world_bloom": "World Link"}.get(each['eventType'], "活动")
        startAt = datetime.datetime.fromtimestamp(each['startAt'] / 1000, pytz.timezone('Asia/Shanghai')).strftime('%Y/%m/%d')
        aggregateAt = datetime.datetime.fromtimestamp(each['aggregateAt'] / 1000 + 1, pytz.timezone('Asia/Shanghai')).strftime('%Y/%m/%d')
        title = each.get('name') or each.get('eventName') or each.get('assetbundleName') or f"Event {each['id']}"

        draw.rounded_rectangle((22, 18, 106, 42), radius=12, fill=(88, 92, 118, 230))
        draw.text((64, 30), f"#{each['id']}", fill=(255, 255, 255), font=get_pjsk_font('FOT-RodinNTLGPro-DB.ttf', 16), anchor='mm')
        draw.text((118, 30), _event_truncate(draw, title, font20, 360), fill=EVENT_STYLE_TEXT, font=font20, anchor='lm')

        info_x = 326
        draw.rounded_rectangle((info_x, 54, info_x + 120, 82), radius=14, fill=(244, 238, 255), outline=(224, 214, 246))
        draw.text((info_x + 60, 68), eventtype, fill=EVENT_STYLE_TEXT, font=get_pjsk_font('SourceHanSansCN-Medium.otf', 15), anchor='mm')
        label_font = get_pjsk_font('SourceHanSansCN-Medium.otf', 15)
        label_x = info_x + 132
        label_w = max(70, int(draw.textlength(event_scope_label, font=label_font)) + 28)
        label_fill = (255, 246, 251) if event_scope_label != '混活' else (238, 248, 255)
        label_outline = (245, 218, 232) if event_scope_label != '混活' else (215, 232, 248)
        draw.rounded_rectangle((label_x, 54, label_x + label_w, 82), radius=14, fill=label_fill, outline=label_outline)
        draw.text((label_x + label_w // 2, 68), event_scope_label, fill=EVENT_STYLE_TEXT, font=label_font, anchor='mm')
        draw.text((info_x, 104), f"开始 {startAt}", fill=EVENT_STYLE_MUTED, font=get_pjsk_font('SourceHanSansCN-Medium.otf', 15), anchor='la')
        draw.text((info_x, 130), f"结束 {aggregateAt}", fill=EVENT_STYLE_MUTED, font=get_pjsk_font('SourceHanSansCN-Medium.otf', 15), anchor='la')

        attrpic = open_pjsk_image(data_path / f'chara/icon_attribute_{event_bonuseattr}.png', mode='RGBA').resize((28, 28), Image.Resampling.LANCZOS)
        attr_x = label_x + label_w + 14
        attr_y = 54
        event_img.paste(attrpic, (attr_x, attr_y), attrpic)

        icon_x = attr_x + 38
        icon_step = 34
        max_icons = max(0, min(7, (event_size[0] - 32 - icon_x) // icon_step))
        for bonusechara in event_bonusecharas[:max_icons]:
            unit = bonusechara.get('unit', 'piapro')
            color = UNIT_COLORS.get(unit, (110, 110, 120))
            draw.ellipse((icon_x - 2, 52, icon_x + 30, 84), fill=color)
            icon = _load_event_chara_icon(bonusechara.get('id'), unit=unit, size=28)
            event_img.paste(icon, (icon_x, 54), icon)
            icon_x += icon_step

        if each['eventType'] == 'cheerful_carnival':
            team_x = info_x
            for _i, team_info in enumerate(event_teams[:2]):
                team_img = resources[res_idx]
                res_idx += 1
                if team_img is None:
                    continue
                team_img = _event_rounded_image(team_img.convert('RGBA').resize((team_size, team_size), Image.Resampling.LANCZOS), radius=12)
                event_img.paste(team_img, (team_x, 152), team_img)
                team_name = ((trans or {}).get('cheerful_carnival_teams', {}).get(team_info['id']) or team_info.get('teamName') or str(team_info.get('id', '')))
                draw.text((team_x + team_size // 2, 204), _event_truncate(draw, team_name, font10, team_size + 20), fill=EVENT_STYLE_MUTED, font=font10, anchor='mm')
                if _i == 0:
                    draw.text((team_x + team_size + 18, 175), 'VS', fill=EVENT_STYLE_TEXT, font=get_pjsk_font('FOT-RodinNTLGPro-DB.ttf', 16), anchor='mm')
                    team_x += team_size + 36
                else:
                    team_x += team_size + 12
        else:
            res_idx += len(event_teams)

        card_y = 174
        for index, cardid in enumerate(event_cards[:5]):
            _c = resources[res_idx]
            res_idx += 1
            card_x = banner_x + index * (card_size + card_gap)
            if _c is not None:
                _c = _event_rounded_image(_c.resize((card_size, card_size), Image.Resampling.LANCZOS), radius=12)
                event_img.paste(_c, (card_x, card_y), _c)
            else:
                draw.rounded_rectangle((card_x, card_y, card_x + card_size, card_y + card_size), radius=12, fill=(232, 232, 238))
            draw.text((card_x + card_size // 2, card_y + card_size + 10), str(cardid), fill=EVENT_STYLE_MUTED, font=font10, anchor='mm')
        for _ in event_cards[5:]:
            res_idx += 1

        return event_img

    # 准备所有活动的处理任务
    import asyncio
    event_tasks = [process_single_event(each) for each in events]
    processed_events = await asyncio.gather(*event_tasks)
    
    event_imgs = [event_img.copy() for event_img in processed_events if event_img is not None]
    if display_limit is not None:
        event_imgs = event_imgs[:display_limit]

    # 若没有任何活动满足需求
    if not event_imgs:
        return None

    # 只改分列：每列最多 limit_count 个活动，不改新版背景/页眉/页脚风格
    per_column = limit_count
    columns = max(1, (len(event_imgs) + per_column - 1) // per_column)
    card_gap_x = 24
    card_gap_y = 18
    pad_x = 34
    header_h = 132
    footer_h = 82
    rows = min(per_column, len(event_imgs))
    canvas_w = pad_x * 2 + columns * event_size[0] + (columns - 1) * card_gap_x
    canvas_h = header_h + rows * event_size[1] + max(0, rows - 1) * card_gap_y + footer_h
    handbook_img = _event_gradient_background(canvas_w, canvas_h)
    draw = ImageDraw.Draw(handbook_img)

    _event_round_panel(handbook_img, (pad_x, 24, canvas_w - pad_x, header_h - 18), radius=30, fill=(255, 255, 255, 218), outline=EVENT_STYLE_LINE, shadow=True)
    server_name = SERVER_MAP.get(pjsk_type, 'jp').upper()
    scope_text = '近 50 个活动' if display_limit is not None else '全部活动'
    draw.text((pad_x + 28, 54), 'EVENT LIST', fill=EVENT_STYLE_MUTED, font=get_pjsk_font('FOT-RodinNTLGPro-DB.ttf', 24), anchor='la')
    draw.text((pad_x + 28, 90), f'{server_name} 活动列表 · {scope_text}', fill=EVENT_STYLE_TEXT, font=get_pjsk_font('SourceHanSansCN-Bold.otf', 32), anchor='la')
    draw.rounded_rectangle((canvas_w - pad_x - 148, 54, canvas_w - pad_x - 28, 92), radius=19, fill=(88, 92, 118, 220))
    draw.text((canvas_w - pad_x - 88, 73), server_name, fill=(255, 255, 255), font=get_pjsk_font('FOT-RodinNTLGPro-DB.ttf', 18), anchor='mm')

    start_y = header_h
    for idx, event_img in enumerate(event_imgs):
        col = idx // per_column
        row = idx % per_column
        x = pad_x + col * (event_size[0] + card_gap_x)
        y = start_y + row * (event_size[1] + card_gap_y)
        handbook_img.paste(event_img, (x, y), event_img.split()[-1])

    footer_y = canvas_h - footer_h + 10
    _event_round_panel(handbook_img, (pad_x, footer_y, canvas_w - pad_x, canvas_h - 24), radius=22, fill=(255, 255, 255, 205), outline=EVENT_STYLE_LINE, shadow=True)
    draw.text((pad_x + 24, footer_y + 26), '查活动 + [活动ID] 查询活动详情   /   查卡 + [卡面ID] 查询卡面详情', fill=EVENT_STYLE_ACCENT, font=get_pjsk_font('SourceHanSansCN-Medium.otf', 18), anchor='lm')
    draw.text((canvas_w - pad_x - 24, canvas_h - 32), PJSK_WATERMARK_TEXT, fill=EVENT_STYLE_MUTED, font=get_pjsk_font('SourceHanSansCN-Medium.otf', 16), anchor='ra')
    return handbook_img
