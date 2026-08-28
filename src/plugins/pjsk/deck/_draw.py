"""组卡结果绘图，统一使用玩家信息头部和结果表格样式。"""
import asyncio
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from config.path_config import FONT_PATH
from services.log import logger

from .._autoask import pjsk_update_manager
from .._card_utils import cardthumnail, paste_card_thumbnail_tile
from .._config import data_path
from .._profile_header import PjskHeaderData, draw_pjsk_profile_header
from .._utils import async_load_master_data, get_pjsk_asset_cached, get_pjsk_font, open_pjsk_image, vertical_gradient

# 常量

BG_COLOR = (248, 246, 252)
HEADER_BG = (255, 255, 255, 218)
CARD_BG = (255, 255, 255)
PANEL_COLOR = (255, 255, 255, 232)
TABLE_HEADER_BG = (88, 92, 118)
TABLE_ROW_BG_A = (255, 255, 255)
TABLE_ROW_BG_B = (250, 247, 252)
ACCENT_COLOR = (88, 92, 118)
SCORE_COLOR = (61, 74, 162)
BONUS_COLOR = (200, 120, 50)
POWER_COLOR = (50, 130, 80)
SKILL_COLOR = (160, 80, 160)
WARN_COLOR = (255, 50, 50)
TIP_COLOR = (0, 204, 187)
READ_COLOR = (0, 180, 0)
UNREAD_COLOR = (220, 50, 50)

# 布局常量
CANVAS_WIDTH = 1100
PADDING = 30
HEADER_HEIGHT = 280
CARD_THUMB_SIZE = 80
CARD_GAP = 8
ROW_HEIGHT = 160
TABLE_HEADER_H = 40

# 右侧数据列 X 偏移（相对于 info_x）
# 布局：[PT/分数/加成] [综合力] [加成%] [实效%] [歌曲分]
COL0 = 0    # PT / 分数 / 加成
COL1 = 110  # 综合力
COL2 = 220  # 加成（活动组卡时）
COL3 = 330  # 实效
COL4 = 430  # 歌曲分（after_live_score）

DIFF_COLORS = {
    'master': (187, 51, 238),
    'expert': (238, 67, 102),
    'hard': (254, 170, 0),
    'normal': (51, 187, 238),
    'easy': (102, 221, 17),
    'append': (255, 75, 75),
}

_FONT_CACHE: Dict[Tuple[str, int], ImageFont.FreeTypeFont] = {}
_IMAGE_CACHE: Dict[Tuple[str, Optional[Tuple[int, int]], str], Image.Image] = {}
_MASTER_RANK_ICON_CACHE: Dict[int, Image.Image] = {}
_SHADOW_CACHE: Dict[Tuple[int, int], Tuple[Image.Image, Image.Image]] = {}
DECK_ROW_LIMIT = max(4, min(8, (os.cpu_count() or 4)))


def _font(name: str, size: int) -> ImageFont.FreeTypeFont:
    return get_pjsk_font(name, size)


def _bold(size: int) -> ImageFont.FreeTypeFont:
    return _font("SourceHanSansCN-Bold.otf", size)


def _medium(size: int) -> ImageFont.FreeTypeFont:
    return _font("SourceHanSansCN-Medium.otf", size)


def _rodin(size: int) -> ImageFont.FreeTypeFont:
    return _font("FOT-RodinNTLGPro-DB.ttf", size)


def _format_relative_time(timestamp: int) -> str:
    """将时间戳转换为相对时间，如"5分钟前"。"""
    if not timestamp:
        return "未知"
    now = int(time.time())
    diff = now - timestamp
    if diff < 0:
        return "未来"
    elif diff < 60:
        return "刚刚"
    elif diff < 3600:
        return f"{diff // 60}分钟前"
    elif diff < 86400:
        return f"{diff // 3600}小时前"
    elif diff < 2592000:
        return f"{diff // 86400}天前"
    else:
        return f"{diff // 2592000}个月前"


def _format_absolute_time(timestamp: int) -> str:
    """将时间戳转换为绝对时间，如"05-01 07:23"。"""
    if not timestamp:
        return "未知"
    return datetime.fromtimestamp(timestamp).strftime("%m-%d %H:%M")


def _draw_rounded_rect(draw: ImageDraw.Draw, xy, fill, radius=8):
    draw.rounded_rectangle(xy, radius=radius, fill=fill)


def _soft_shadow(size: Tuple[int, int], radius: int = 14, alpha: int = 60) -> Image.Image:
    shadow = Image.new('RGBA', size, (0, 0, 0, 0))
    d = ImageDraw.Draw(shadow)
    d.rounded_rectangle((0, 0, size[0] - 1, size[1] - 1), radius=radius, fill=(70, 55, 90, alpha))
    return shadow.filter(ImageFilter.GaussianBlur(10))


def _draw_round_panel(base: Image.Image, xy: Tuple[int, int, int, int], radius: int, fill, outline=None, shadow: bool = True):
    x1, y1, x2, y2 = xy
    w, h = x2 - x1, y2 - y1
    if shadow:
        sh = _soft_shadow((w, h), radius=radius, alpha=48)
        base.paste(sh, (x1 + 4, y1 + 6), sh.split()[3])
    panel = Image.new('RGBA', (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(panel)
    d.rounded_rectangle((0, 0, w - 1, h - 1), radius=radius, fill=fill, outline=outline, width=1 if outline else 0)
    base.paste(panel, (x1, y1), panel.split()[3])


def _make_gradient_background(width: int, height: int) -> Image.Image:
    top = (255, 246, 250)
    bottom = (236, 244, 255)
    img = vertical_gradient(width, height, top, bottom)
    glow = Image.new('RGBA', (width, height), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    gd.ellipse((-width // 6, -height // 5, width // 2, height // 3), fill=(255, 190, 220, 72))
    gd.ellipse((width // 2, height // 3, width + width // 4, height + height // 6), fill=(170, 210, 255, 64))
    img.paste(glow, (0, 0), glow.split()[3])
    return img


def _paste_rgba(base: Image.Image, overlay: Image.Image, pos: Tuple[int, int]):
    overlay = overlay.convert('RGBA')
    base.paste(overlay, pos, overlay.split()[-1])


def _text_width(font, text):
    try:
        bbox = font.getbbox(text)
        return bbox[2] - bbox[0]
    except AttributeError:
        return font.getsize(text)[0]


def _load_cached_image(path: Path, mode: str = 'RGBA', size: Optional[Tuple[int, int]] = None) -> Image.Image:
    return open_pjsk_image(path, mode=mode, size=size)


def _get_master_rank_icon(master_rank: int) -> Optional[Image.Image]:
    if master_rank <= 0 or master_rank > 5:
        return None
    if master_rank not in _MASTER_RANK_ICON_CACHE:
        rank_icon_path = data_path / 'chara' / f'train_rank_{master_rank}.png'
        if not rank_icon_path.exists():
            return None
        _MASTER_RANK_ICON_CACHE[master_rank] = open_pjsk_image(rank_icon_path, mode='RGBA', size=(20, 20))
    return _MASTER_RANK_ICON_CACHE[master_rank].copy()


def _get_chara_icon(cid: int, size: int = 42) -> Optional[Image.Image]:
    for path in (
        data_path / 'chara' / f'chr_ts_90_{cid}.png',
        data_path / 'chara' / f'chr_ts_90_{cid}_2.png',
    ):
        if path.exists():
            try:
                return open_pjsk_image(path, mode='RGBA', size=(size, size))
            except Exception:
                pass
    return None


def _get_shadow(size: Tuple[int, int]) -> Tuple[Image.Image, Image.Image]:
    if size not in _SHADOW_CACHE:
        shadow = Image.new('RGBA', (size[0] + 6, size[1] + 6), (0, 0, 0, 0))
        shadow.paste(Image.new('RGBA', size, (0, 0, 0, 30)), (3, 3))
        shadow = shadow.filter(ImageFilter.GaussianBlur(2))
        _SHADOW_CACHE[size] = (shadow, shadow.split()[-1])
    cached_shadow, cached_mask = _SHADOW_CACHE[size]
    return cached_shadow.copy(), cached_mask.copy()


async def _load_music_cover(music_id: int, pjsk_type: int = 0, size: int = 56) -> Optional[Image.Image]:
    """加载歌曲封面缩略图，失败时返回 None。"""
    try:
        asset_name = f'jacket_s_{str(music_id).zfill(3)}'
        cover = await get_pjsk_asset_cached(
            f'startapp/music/jacket/{asset_name}', f'{asset_name}.png',
            pjsk_type=pjsk_type, mode='RGBA', size=(size, size),
        )
        if cover is None:
            cover = await get_pjsk_asset_cached(
                'startapp/thumbnail/music_jacket', f'{asset_name}.png',
                pjsk_type=pjsk_type, mode='RGBA', size=(size, size),
            )
        return cover
    except Exception as e:
        logger.debug(f"[deck] 加载歌曲封面失败 music_id={music_id}: {e}")
        return None


def _paste_music_cover_with_border(
    pic: Image.Image,
    cover: Image.Image,
    pos: Tuple[int, int],
    diff_color: Tuple[int, int, int],
    size: int = 56,
    border: int = 3,
):
    """在指定位置贴歌曲封面，并套上对应难度色的圆角边框。"""
    x, y = pos
    # 难度色外框底板
    frame = Image.new('RGBA', (size + border * 2, size + border * 2), (0, 0, 0, 0))
    fd = ImageDraw.Draw(frame)
    fd.rounded_rectangle(
        (0, 0, size + border * 2 - 1, size + border * 2 - 1),
        radius=12, fill=(*diff_color, 255),
    )
    pic.paste(frame, (x, y), frame.split()[3])
    # 圆角封面
    mask = Image.new('L', (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, size - 1, size - 1), radius=9, fill=255)
    pic.paste(cover, (x + border, y + border), mask)


def _draw_card_tile(
    base: Image.Image,
    thumb: Image.Image,
    pos: Tuple[int, int],
    master_rank: int = 0,
    size: int = CARD_THUMB_SIZE,
):
    paste_card_thumbnail_tile(
        base,
        thumb,
        pos,
        size=size,
        master_rank=master_rank,
    )


# 玩家信息区

async def _draw_player_header(
    pic: Image.Image,
    draw: ImageDraw.Draw,
    profile_data: dict,
    is_private: bool,
    card_asset_map: Optional[Dict[int, str]] = None,
    pjsk_type: int = 0,
):
    await draw_pjsk_profile_header(
        pic,
        (PADDING, 12, CANVAS_WIDTH - PADDING, HEADER_HEIGHT - 12),
        PjskHeaderData(
            userid=str(profile_data.get('userid', '')),
            name=profile_data.get('name', '???'),
            rank=profile_data.get('rank', 0),
            is_private=is_private,
            user_decks=profile_data.get('userDecks', []) or [],
            special_training=profile_data.get('special_training', []) or [],
            user_profile_honors=profile_data.get('userProfileHonors', []) or [],
            user_honor_missions=profile_data.get('userHonorMissions', []) or [],
            suite_update_time=profile_data.get('suite_update_time'),
        ),
        module_label="DECK RECOMMEND",
        pjsk_type=pjsk_type,
        card_asset_map=card_asset_map,
    )


# 结果表格

async def _draw_deck_row(
    deck: dict,
    row_index: int,
    cards_data: list,
    recommend_type: str,
    pjsk_type: int = 0,
) -> Image.Image:
    """
    绘制单行组卡结果。
    左侧：5张卡牌缩略图 + 卡面ID（右上角）+ 详细信息（SLv / +技能值 / 前篇 后篇）
    右侧：多列纵向对齐数据（PT | 综合力 | 加成 | 实效 | 歌曲分）
    """
    row_w = CANVAS_WIDTH - PADDING * 2
    bg_color = TABLE_ROW_BG_A if row_index % 2 == 0 else TABLE_ROW_BG_B
    row = Image.new("RGBA", (row_w, ROW_HEIGHT), (0, 0, 0, 0))
    row_draw = ImageDraw.Draw(row)
    row_draw.rounded_rectangle((0, 4, row_w - 1, ROW_HEIGHT - 5), radius=18, fill=(*bg_color, 226), outline=(255, 255, 255, 230), width=1)

    deck_cards = deck.get('cards', [])

    # ---- 卡牌缩略图 ----
    card_x = 15
    card_y = 8
    thumb_tasks = [
        cardthumnail(
            dc.get('card_id', 0),
            istrained=(dc.get('default_image', 'original') == 'special_training'),
            cards=cards_data,
            pjsk_type=pjsk_type,
        )
        for dc in deck_cards[:5]
    ]
    thumbs = await asyncio.gather(*thumb_tasks, return_exceptions=True)

    for i, thumb in enumerate(thumbs):
        px = card_x + i * (CARD_THUMB_SIZE + CARD_GAP)
        master_rank = deck_cards[i].get('master_rank', 0) if i < len(deck_cards) else 0
        if isinstance(thumb, Exception):
            placeholder = Image.new("RGBA", (CARD_THUMB_SIZE, CARD_THUMB_SIZE), (215, 215, 225, 180))
            _draw_card_tile(row, placeholder, (px, card_y), master_rank=0)
        else:
            _draw_card_tile(row, thumb, (px, card_y), master_rank=master_rank)

    # ---- 卡面详细信息 ----
    font_id_bg = _medium(11)   # 卡面ID
    font_detail = _medium(12)  # SLv / +技能值 / 前后篇

    for i, dc in enumerate(deck_cards[:5]):
        px = card_x + i * (CARD_THUMB_SIZE + CARD_GAP)
        card_id = dc.get('card_id', 0)
        slv = dc.get('skill_level', 0)
        sup = dc.get('skill_score_up', 0)
        # 前后篇阅读状态：直接从后端返回的字段读取
        front_read = dc.get('episode1_read')
        back_read = dc.get('episode2_read')

        # 卡面ID：右上角白色半透明底，黑色文字
        id_text = str(card_id)
        id_bbox = font_id_bg.getbbox(id_text)
        id_w = id_bbox[2] - id_bbox[0] + 4
        id_h = id_bbox[3] - id_bbox[1] + 2
        id_bg = Image.new("RGBA", (id_w, id_h), (255, 255, 255, 180))
        row.paste(id_bg, (px + CARD_THUMB_SIZE - id_w, card_y), id_bg.split()[3])
        row_draw.text(
            (px + CARD_THUMB_SIZE - id_w + 2, card_y),
            id_text, fill=(0, 0, 0), font=font_id_bg
        )

        # 缩略图下方第一行：左 SLv.X  右 +技能值
        detail_y = card_y + CARD_THUMB_SIZE + 4
        if slv > 0:
            row_draw.text((px, detail_y), f"SLv.{slv}", fill=(60, 60, 60), font=font_detail)
        if sup > 0:
            sup_text = f"+{sup}"
            sup_w = _text_width(font_detail, sup_text)
            row_draw.text(
                (px + CARD_THUMB_SIZE - sup_w, detail_y),
                sup_text, fill=SKILL_COLOR, font=font_detail
            )

        # 缩略图下方第二行：前篇  后篇（绿=已读，红=未读，None=不显示）
        ep_y = detail_y + 18
        # None 表示该卡没有剧情，显示为灰色
        if front_read is None:
            front_color = (150, 150, 150)
        else:
            front_color = READ_COLOR if front_read else UNREAD_COLOR
        
        if back_read is None:
            back_color = (150, 150, 150)
        else:
            back_color = READ_COLOR if back_read else UNREAD_COLOR
        
        row_draw.text((px, ep_y), "前篇", fill=front_color, font=font_detail)
        row_draw.text((px + CARD_THUMB_SIZE // 2, ep_y), "后篇", fill=back_color, font=font_detail)

    # ---- 右侧数值区（多列纵向对齐） ----
    info_x = card_x + 5 * (CARD_THUMB_SIZE + CARD_GAP) + 20
    info_y = 10

    score = deck.get('score', 0)
    total_power = deck.get('total_power', 0)
    event_bonus = deck.get('event_bonus_rate', 0)
    multi_score_up = deck.get('multi_live_score_up', 0)
    # 后端返回的是 live_score，不是 after_live_score
    live_score = deck.get('live_score', 0)

    font_label = _medium(13)
    font_value = _bold(20)

    def _draw_col(col_offset, label, value, color):
        cx = info_x + col_offset
        card_w = 96
        chip = Image.new('RGBA', (card_w, 58), (0, 0, 0, 0))
        cd = ImageDraw.Draw(chip)
        soft = tuple(min(255, int(v * 0.16 + 255 * 0.84)) for v in color)
        cd.rounded_rectangle((0, 0, card_w - 1, 57), radius=14, fill=(*soft, 185), outline=(*color, 82), width=1)
        cd.text((10, 9), label, fill=(96, 90, 108), font=font_label)
        cd.text((10, 27), value, fill=color, font=font_value)
        row.paste(chip, (cx, info_y), chip.split()[3])

    # COL0：PT / 分数 / 加成
    if recommend_type in ['bonus', 'wl_bonus']:
        _draw_col(COL0, '加成', f"{event_bonus:.1f}%", BONUS_COLOR)
    elif recommend_type in ['challenge', 'challenge_all', 'no_event']:
        _draw_col(COL0, '分数', str(score), SCORE_COLOR)
    else:
        _draw_col(COL0, 'PT', str(score), SCORE_COLOR)

    # COL1：综合力（非加成组卡）
    if recommend_type not in ['bonus', 'wl_bonus']:
        _draw_col(COL1, '综合力', str(total_power), POWER_COLOR)

    # COL2：加成%（活动/WL组卡时）
    if recommend_type not in ['challenge', 'challenge_all', 'no_event', 'bonus', 'wl_bonus']:
        if event_bonus > 0:
            _draw_col(COL2, '加成', f"{event_bonus:.1f}%", BONUS_COLOR)

    # COL3：实效%（多人live时）
    if multi_score_up > 0:
        _draw_col(COL3, '实效', f"{multi_score_up:.1f}%", SKILL_COLOR)

    # COL4：歌曲分（所有组卡都显示）
    if live_score > 0:
        _draw_col(COL4, '歌曲分', str(live_score), (100, 100, 200))

    return row


# 主绘图函数

async def _resolve_event_banner(options: dict, pjsk_type: int = 0) -> tuple[Optional[Image.Image], Optional[dict]]:
    """解析活动组卡标题区展示用的活动 banner。"""
    event_id = options.get('event_id')
    if not event_id:
        return None, None
    try:
        event_id = int(event_id)
        events = await async_load_master_data('events.json', pjsk_type)
        event_info = next((e for e in events if isinstance(e, dict) and e.get('id') == event_id), None)
        if not event_info:
            return None, None
        assetbundle_name = event_info.get('assetbundleName')
        if not assetbundle_name:
            return None, event_info
        banner = await pjsk_update_manager.get_asset(
            f'ondemand/event_story/{assetbundle_name}/screen_image',
            'banner_event_story.png',
            pjsk_type=pjsk_type,
        )
        if banner is None:
            banner = await pjsk_update_manager.get_asset(
                f'ondemand/event/{assetbundle_name}/screen',
                'banner.png',
                pjsk_type=pjsk_type,
            )
        if banner is None:
            banner = await pjsk_update_manager.get_asset(
                f'ondemand/event_story/{assetbundle_name}/screen_image',
                'story_title.png',
                pjsk_type=pjsk_type,
            )
        if banner is not None:
            return banner.convert('RGBA'), event_info
        return None, event_info
    except Exception as e:
        logger.debug(f"[deck] 加载活动 banner 失败: {e}")
        return None, None


async def _resolve_event_unit_attr_icons(options: dict, additional: dict, pjsk_type: int = 0) -> tuple[Optional[Image.Image], Optional[Image.Image]]:
    """解析活动/筛选条件对应的组合与属性图标。"""
    unit = (options.get('event_unit') or options.get('unit_filter') or additional.get('unit_filter') or '').strip()
    attr = (options.get('event_attr') or options.get('attr_filter') or additional.get('attr_filter') or '').strip()

    event_id = options.get('event_id')
    if event_id and (not unit or not attr):
        try:
            event_id = int(event_id)
            bonuses = await async_load_master_data('eventDeckBonuses.json', pjsk_type)
            event_bonuses = [b for b in bonuses if isinstance(b, dict) and b.get('eventId') == event_id]
            if event_bonuses:
                if not attr:
                    attrs = {str(b.get('cardAttr', '')).strip() for b in event_bonuses if b.get('cardAttr')}
                    if len(attrs) == 1:
                        attr = next(iter(attrs))
                if not unit:
                    game_character_units = await async_load_master_data('gameCharacterUnits.json', pjsk_type)
                    unit_by_id = {
                        int(row['id']): str(row.get('unit', '')).strip()
                        for row in game_character_units
                        if isinstance(row, dict) and row.get('id') is not None
                    }
                    units = {
                        unit_by_id.get(int(b.get('gameCharacterUnitId')))
                        for b in event_bonuses
                        if b.get('gameCharacterUnitId') is not None and unit_by_id.get(int(b.get('gameCharacterUnitId')))
                    }
                    if len(units) == 1:
                        unit = next(iter(units))
        except Exception as e:
            logger.debug(f"[deck] 解析活动图标失败: {e}")

    unit_icon = None
    attr_icon = None
    try:
        if unit:
            unit_path = data_path / 'pics' / f'logo_{unit}.png'
            if unit_path.exists():
                unit_icon = open_pjsk_image(unit_path).convert('RGBA')
        if attr:
            attr_path = data_path / 'chara' / f'icon_attribute_{attr}.png'
            if attr_path.exists():
                attr_icon = open_pjsk_image(attr_path).convert('RGBA')
    except Exception as e:
        logger.debug(f"[deck] 加载活动图标失败: {e}")
    return unit_icon, attr_icon


async def compose_deck_image(
    profile_data: dict,
    is_private: bool,
    result_decks: List[dict],
    result_algs: List[str],
    cost_times: dict,
    wait_times: dict,
    recommend_type: str,
    options: dict,
    additional: dict,
    pjsk_type: int = 0,
) -> Image.Image:
    """
    合成组卡结果图片。
    布局：顶部玩家信息区 → 标题/歌曲信息 → 结果表格 → 底部算法信息
    """
    cards_data = await async_load_master_data('cards.json', pjsk_type)
    card_asset_map = {
        card['id']: card['assetbundleName']
        for card in cards_data
        if isinstance(card, dict) and 'id' in card and 'assetbundleName' in card
    }
    music_title_map: Dict[int, str] = {}

    # 计算画布高度
    title_height = 116
    num_decks = len(result_decks)
    table_height = TABLE_HEADER_H + num_decks * ROW_HEIGHT if num_decks > 0 else 60
    footer_height = 60
    total_height = HEADER_HEIGHT + title_height + table_height + footer_height + PADDING * 2

    # 创建画布：柔和渐变背景
    pic = _make_gradient_background(CANVAS_WIDTH, total_height)
    draw = ImageDraw.Draw(pic)

    # 玩家信息区
    await _draw_player_header(pic, draw, profile_data, is_private, card_asset_map=card_asset_map, pjsk_type=pjsk_type)

    # 标题区
    title_y = HEADER_HEIGHT
    title_map = {
        'event': '活动组卡',
        'wl': 'WL活动组卡',
        'wl_fake': 'WL模拟组卡',
        'unit_attr': '模拟活动组卡',
        'custom_event': '自定义混活组卡',
        'no_event': '长草组卡',
        'challenge': '挑战组卡',
        'challenge_all': '挑战组卡(全角色)',
        'bonus': '加成组卡',
        'wl_bonus': 'WL加成组卡',
    }
    title_text = title_map.get(recommend_type, '组卡')

    event_id = options.get('event_id')
    if event_id and recommend_type in ['event', 'wl', 'bonus', 'wl_bonus']:
        title_text += f" #{event_id}"

    live_type = options.get('live_type', 'multi')
    if live_type == 'multi':
        title_text += " (多人)"
    elif live_type == 'solo':
        title_text += " (单人)"
    elif live_type == 'auto':
        title_text += " (AUTO)"

    font_title = _bold(26)
    title_x = PADDING + 10
    title_text_y = title_y + 10
    draw.text((title_x, title_text_y), title_text, fill=ACCENT_COLOR, font=font_title)

    # WL 当前组卡角色徽章
    wl_cid = options.get('world_bloom_character_id')
    wl_chapter_no = options.get('world_bloom_chapter_no')
    if wl_cid:
        try:
            badge_x = title_x + _text_width(font_title, title_text) + 14
            badge_y = title_y + 4
            badge_w = 178
            badge_h = 46
            icon = _get_chara_icon(int(wl_cid), size=36)
            _draw_round_panel(pic, (badge_x, badge_y, badge_x + badge_w, badge_y + badge_h), 18, (255, 246, 251, 230), outline=(245, 205, 225), shadow=False)
            if icon:
                pic.paste(icon, (badge_x + 8, badge_y + 5), icon)
            badge_text = f"当前组卡角色"
            chapter_text = f"第{wl_chapter_no}章" if wl_chapter_no else "WL单榜"
            draw.text((badge_x + 52, badge_y + 12), badge_text, fill=(150, 70, 110), font=_bold(13), anchor="lm")
            draw.text((badge_x + 52, badge_y + 31), chapter_text, fill=(90, 80, 100), font=_medium(12), anchor="lm")
        except Exception as e:
            logger.debug(f"[deck] 绘制 WL 角色徽章失败: {e}")

    # 活动 banner 牌子：用于活动组卡时更明显地显示当前/预告活动
    banner_badge_left = None
    try:
        if event_id and recommend_type in ['event', 'wl']:
            banner, event_info = await _resolve_event_banner(options, pjsk_type)
            if banner is not None:
                badge_w = 360
                badge_h = 72
                banner.thumbnail((210, 60))
                badge_x = CANVAS_WIDTH - PADDING - badge_w
                badge_y = title_y + 4
                banner_badge_left = badge_x

                shadow, shadow_mask = _get_shadow((badge_w, badge_h))
                pic.paste(shadow, (badge_x - 3, badge_y - 1), shadow_mask)
                badge = Image.new('RGBA', (badge_w, badge_h), (255, 255, 255, 225))
                badge_draw = ImageDraw.Draw(badge)
                badge_draw.rounded_rectangle((0, 0, badge_w - 1, badge_h - 1), radius=12, fill=(255, 255, 255, 225))
                badge.paste(banner, (8, (badge_h - banner.height) // 2), banner)

                font_event_id = _bold(18)
                font_event_name = _medium(13)
                text_x = 226
                badge_draw.text((text_x, 12), f"Event #{event_id}", fill=ACCENT_COLOR, font=font_event_id)
                if event_info:
                    event_name = str(event_info.get('name') or '')
                    while event_name and _text_width(font_event_name, event_name) > badge_w - text_x - 12:
                        event_name = event_name[:-1]
                    if event_name and event_name != str(event_info.get('name') or ''):
                        event_name = event_name[:-1] + '…' if len(event_name) > 1 else '…'
                    if event_name:
                        badge_draw.text((text_x, 40), event_name, fill=(80, 80, 80), font=font_event_name)
                pic.paste(badge, (badge_x, badge_y), badge)
    except Exception as e:
        logger.debug(f"[deck] 绘制活动 banner 失败: {e}")

    # 活动组合/属性图标
    try:
        unit_icon, attr_icon = await _resolve_event_unit_attr_icons(options, additional, pjsk_type)
        icon_x = title_x + _text_width(font_title, title_text) + 14
        if wl_cid:
            icon_x += 190
        icon_y = title_y + 8
        icon_max_x = banner_badge_left - 12 if banner_badge_left is not None else CANVAS_WIDTH - PADDING
        if unit_icon is not None:
            unit_icon.thumbnail((96, 42))
            if icon_x + unit_icon.width <= icon_max_x:
                pic.paste(unit_icon, (icon_x, icon_y), unit_icon)
                icon_x += unit_icon.width + 8
        if attr_icon is not None:
            attr_icon = attr_icon.resize((42, 42))
            if icon_x + attr_icon.width <= icon_max_x:
                pic.paste(attr_icon, (icon_x, icon_y), attr_icon)
    except Exception as e:
        logger.debug(f"[deck] 绘制活动图标失败: {e}")

    # 歌曲信息（标题文字下方独立一行：封面 + 歌名 + 配置信息）
    music_id = options.get('music_id', 10000)
    music_diff = options.get('music_diff', 'master')
    diff_color = DIFF_COLORS.get(music_diff, (100, 100, 100))

    if music_id != 10000:
        musics = await async_load_master_data('musics.json', pjsk_type)
        music_title_map = {
            music['id']: music.get('title', f"ID:{music['id']}")
            for music in musics
            if isinstance(music, dict) and 'id' in music
        }
        music_title = music_title_map.get(music_id, f"ID:{music_id}")
        music_text = f"{music_title} ({music_diff.upper()})"
    else:
        music_text = f"おまかせ ({music_diff.upper()})"

    font_music = _medium(16)
    # 歌曲封面（独立一行，套对应难度色边框；omakase 用 rt.png 替代位）
    cover_size = 52
    cover_x = PADDING + 10
    cover_y = title_y + 50
    cover_drawn = False
    if music_id != 10000:
        cover = await _load_music_cover(music_id, pjsk_type, size=cover_size)
        if cover is not None:
            _paste_music_cover_with_border(pic, cover, (cover_x, cover_y), diff_color, size=cover_size)
            cover_drawn = True
    else:
        cover_path = data_path / 'pics' / 'rt.png'
        if cover_path.exists():
            try:
                cover = open_pjsk_image(cover_path, mode='RGBA', size=(cover_size, cover_size))
                _paste_music_cover_with_border(pic, cover, (cover_x, cover_y), diff_color, size=cover_size)
                cover_drawn = True
            except Exception as e:
                logger.warning(f"[deck] 加载歌曲封面失败: {e}")

    text_x = cover_x + cover_size + 16 if cover_drawn else cover_x
    text_center_y = cover_y + cover_size // 2
    draw.text((text_x, text_center_y), music_text, fill=diff_color, font=font_music, anchor="lm")
    text_w = _text_width(font_music, music_text)

    # 难度缺失提示（歌名右侧）
    if options.get('music_diff_missing'):
        warn_text = f"⚠无{options['music_diff_missing'].upper()}难度"
        draw.text((text_x + text_w + 12, text_center_y), warn_text, fill=WARN_COLOR, font=_medium(13), anchor="lm")

    # 配置信息（歌曲行右侧，垂直居中）
    settings = []
    if additional.get('unit_filter'):
        settings.append(f"仅{additional['unit_filter']}")
    if additional.get('attr_filter'):
        settings.append(f"仅{additional['attr_filter']}")
    if additional.get('boost') is not None:
        boost = additional['boost']
        settings.append(f"{boost}火(x{options.get('boost_bonus', boost)})")
    if settings:
        font_setting = _medium(14)
        settings_text = "  ".join(settings)
        settings_w = _text_width(font_setting, settings_text)
        settings_x = max(text_x + text_w + 160, CANVAS_WIDTH - PADDING - 10 - settings_w)
        draw.text((settings_x, text_center_y), settings_text, fill=(120, 110, 130), font=font_setting, anchor="lm")

    # 结果表格
    table_y = title_y + title_height

    if num_decks > 0:
        # 表头背景
        _draw_rounded_rect(
            draw,
            (PADDING, table_y, CANVAS_WIDTH - PADDING, table_y + TABLE_HEADER_H),
            TABLE_HEADER_BG, radius=6
        )
        font_th = _bold(15)
        draw.text((PADDING + 15, table_y + 12), "卡组", fill=(255, 255, 255), font=font_th)

        # 表头列标签（与 _draw_deck_row 中的 COL 偏移严格对齐）
        info_x_abs = PADDING + 15 + 5 * (CARD_THUMB_SIZE + CARD_GAP) + 20

        if recommend_type in ['bonus', 'wl_bonus']:
            th_labels = [(COL0, '加成')]
        elif recommend_type in ['challenge', 'challenge_all']:
            th_labels = [(COL0, '分数'), (COL1, '综合力'), (COL3, '实效')]
        elif recommend_type == 'no_event':
            th_labels = [(COL0, '分数'), (COL1, '综合力'), (COL3, '实效'), (COL4, '歌曲分')]
        else:
            th_labels = [(COL0, 'PT'), (COL1, '综合力'), (COL2, '加成'), (COL3, '实效'), (COL4, '歌曲分')]

        for col_off, label in th_labels:
            draw.text((info_x_abs + col_off, table_y + 12), label, fill=(255, 255, 255), font=font_th)

        # 并行绘制所有行
        row_tasks = [
            _draw_deck_row(deck, i, cards_data, recommend_type, pjsk_type)
            for i, deck in enumerate(result_decks)
        ]
        sem = asyncio.Semaphore(DECK_ROW_LIMIT)

        async def _limited_row(coro):
            async with sem:
                return await coro

        rows = await asyncio.gather(*[_limited_row(task) for task in row_tasks], return_exceptions=True)

        for i, row_result in enumerate(rows):
            row_y = table_y + TABLE_HEADER_H + i * ROW_HEIGHT
            if isinstance(row_result, Exception):
                logger.error(f"绘制组卡行 {i} 失败: {row_result}")
                continue
            if row_result.mode != 'RGBA':
                row_result = row_result.convert('RGBA')
            pic.paste(row_result, (PADDING, row_y), row_result.split()[3])
    else:
        font_no_result = _bold(22)
        draw.text((PADDING + 10, table_y + 15), "未找到符合条件的卡组", fill=WARN_COLOR, font=font_no_result)

    # 底部信息
    footer_y = table_y + table_height + 10
    font_tip = _medium(13)
    alg_names = {'dfs': '暴力搜索', 'sa': '模拟退火', 'ga': '遗传算法'}
    alg_parts = []
    for alg, cost in cost_times.items():
        name = alg_names.get(alg, alg.upper())
        if cost < 1:
            alg_parts.append(f"{name}({cost * 1000:.0f}ms)")
        else:
            alg_parts.append(f"{name}({cost:.2f}s)")
    draw.text((PADDING + 5, footer_y), "算法: " + "  ".join(alg_parts), fill=TIP_COLOR, font=font_tip)
    draw.text((PADDING + 5, footer_y + 20), "结果仅供参考，请以实际游戏内数据为准", fill=TIP_COLOR, font=font_tip)

    return pic
