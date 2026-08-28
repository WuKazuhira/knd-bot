"""卡牌一览绘图，按属性行和角色列展示卡牌。"""
import asyncio
import math
import os
import time
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from config.path_config import FONT_PATH
from services.log import logger
from utils.imageutils import pic2b64, pic2b64_fast

from .._autoask import pjsk_update_manager
from .._card_utils import (
    ATTR_ORDER,
    RARITY_WEIGHT,
    cardthumnail,
    cardtype,
    get_chara_icon_by_chara_id,
    is_fes_card,
    paste_card_thumbnail_tile,
)
from .._config import data_path
from .._utils import async_load_master_data, generatehonor, get_pjsk_font, open_pjsk_image

# 常量

PADDING      = 28          # 画布外边距
HEADER_H     = 230         # 玩家信息区高度
ATTR_COL_W   = 76          # 左侧属性列宽度
CHARA_ICON_H = 76          # 角色头像行高度
CELL_GAP     = 8           # 格子间距
THUMB_SZ     = 84          # 卡牌缩略图卡片大小
ATTR_ICON_SZ = 46          # 属性图标大小
CHARA_ICON_SZ = 64         # 角色头像大小
ROW_PAD      = 8           # 行内上下内边距
COL_PAD      = 8           # 列内左右内边距

BG_COLOR     = (248, 246, 252)
PANEL_COLOR  = (255, 255, 255, 232)
ACCENT_COLOR = (88, 92, 118)
CARD_RENDER_LIMIT = max(4, min(8, (os.cpu_count() or 4)))
_MASTER_RANK_ICON_CACHE: Dict[int, Image.Image] = {}

ATTR_ROW_COLORS = {
    'cool':       (224, 238, 255),
    'cute':       (255, 226, 240),
    'happy':      (255, 242, 217),
    'mysterious': (239, 228, 255),
    'pure':       (222, 247, 228),
}

ATTR_ACCENT_COLORS = {
    'cool':       (78, 145, 255),
    'cute':       (255, 104, 165),
    'happy':      (255, 174, 75),
    'mysterious': (147, 103, 255),
    'pure':       (78, 190, 105),
}

CHARA_ICON_FILES = {
    1: 'ick.png', 2: 'saki.png', 3: 'hnm.png', 4: 'shiho.png',
    5: 'mnr.png', 6: 'hrk.png', 7: 'airi.png', 8: 'szk.png',
    9: 'khn.png', 10: 'an.png', 11: 'akt.png', 12: 'toya.png',
    13: 'tks.png', 14: 'emu.png', 15: 'nene.png', 16: 'rui.png',
    17: 'knd.png', 18: 'mfy.png', 19: 'ena.png', 20: 'mzk.png',
    21: 'miku.png', 22: 'rin.png', 23: 'len.png', 24: 'luka.png',
    25: 'meiko.png', 26: 'kaito.png',
}


def _font(name: str, size: int) -> ImageFont.FreeTypeFont:
    return get_pjsk_font(name, size)

def _bold(size: int):   return _font("SourceHanSansCN-Bold.otf", size)
def _medium(size: int): return _font("SourceHanSansCN-Medium.otf", size)
def _rodin(size: int):  return _font("FOT-RodinNTLGPro-DB.ttf", size)


def _paste_rgba(base: Image.Image, overlay: Image.Image, pos: Tuple[int, int]):
    """安全粘贴 RGBA 图片"""
    try:
        r, g, b, mask = overlay.split()
        base.paste(overlay, pos, mask)
    except ValueError:
        base.paste(overlay, pos)


def _soft_shadow(size: Tuple[int, int], radius: int = 14, alpha: int = 80) -> Image.Image:
    """生成柔和阴影。"""
    shadow = Image.new("RGBA", size, (0, 0, 0, 0))
    d = ImageDraw.Draw(shadow)
    d.rounded_rectangle((0, 0, size[0] - 1, size[1] - 1), radius=radius, fill=(70, 55, 90, alpha))
    return shadow.filter(ImageFilter.GaussianBlur(10))


def _draw_round_panel(base: Image.Image, xy: Tuple[int, int, int, int], radius: int, fill, outline=None, shadow: bool = True):
    """绘制带柔和阴影的圆角面板。"""
    x1, y1, x2, y2 = xy
    w, h = x2 - x1, y2 - y1
    if shadow:
        sh = _soft_shadow((w, h), radius=radius, alpha=55)
        base.paste(sh, (x1 + 4, y1 + 6), sh.split()[3])
    panel = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(panel)
    d.rounded_rectangle((0, 0, w - 1, h - 1), radius=radius, fill=fill, outline=outline, width=1 if outline else 0)
    base.paste(panel, (x1, y1), panel.split()[3])


def _make_gradient_background(width: int, height: int) -> Image.Image:
    """生成柔和粉紫渐变背景。"""
    top = (255, 246, 250)
    bottom = (236, 244, 255)
    img = Image.new("RGB", (width, height), top)
    d = ImageDraw.Draw(img)
    for y in range(height):
        t = y / max(1, height - 1)
        color = tuple(int(top[i] * (1 - t) + bottom[i] * t) for i in range(3))
        d.line((0, y, width, y), fill=color)
    glow = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    gd.ellipse((-width // 5, -height // 4, width // 2, height // 3), fill=(255, 190, 220, 80))
    gd.ellipse((width // 2, height // 4, width + width // 4, height + height // 5), fill=(170, 210, 255, 70))
    img.paste(glow, (0, 0), glow.split()[3])
    return img


def _fit_contain(img: Image.Image, size: Tuple[int, int]) -> Image.Image:
    """保持比例缩放到指定盒子内，自动裁掉透明留白。"""
    img = img.convert("RGBA")
    alpha_bbox = img.split()[-1].getbbox()
    if alpha_bbox:
        img = img.crop(alpha_bbox)
    w, h = img.size
    scale = min(size[0] / max(1, w), size[1] / max(1, h))
    new_size = (max(1, int(w * scale)), max(1, int(h * scale)))
    return img.resize(new_size, Image.Resampling.LANCZOS)


def _circle_avatar(img: Image.Image, size: int, border_color=(255, 255, 255), outer_color=(180, 180, 200)) -> Image.Image:
    """把角色立绘头像裁成圆形头像，并加白边与角色色外圈。"""
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(canvas)
    d.ellipse((0, 0, size - 1, size - 1), fill=outer_color)
    d.ellipse((4, 4, size - 5, size - 5), fill=border_color)

    inner = size - 12
    fitted = _fit_contain(img, (inner, inner))
    layer = Image.new("RGBA", (inner, inner), (255, 255, 255, 0))
    layer.paste(fitted, ((inner - fitted.width) // 2, (inner - fitted.height) // 2), fitted)

    mask = Image.new("L", (inner, inner), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, inner - 1, inner - 1), fill=255)
    canvas.paste(layer, (6, 6), mask)
    return canvas


def _load_chara_icon(cid: int, size: int, border_color=(255, 255, 255), outer_color=(180, 180, 200)) -> Image.Image:
    """优先读取 data/pjsk/masterdata/chara/chara_icon 下的新角色头像。"""
    filename = CHARA_ICON_FILES.get(cid)
    if filename:
        icon_path = data_path / 'chara' / 'chara_icon' / filename
        if icon_path.exists():
            try:
                return _circle_avatar(Image.open(icon_path), size, border_color=border_color, outer_color=outer_color)
            except Exception as e:
                logger.debug(f"新角色头像加载失败 {cid}: {e}")
    icon = get_chara_icon_by_chara_id(cid)
    return _circle_avatar(icon, size, border_color=border_color, outer_color=outer_color)


def _get_master_rank_icon(master_rank: int, size: int = 22) -> Optional[Image.Image]:
    """读取专家等级 / MasterRank 图标。"""
    if master_rank <= 0 or master_rank > 5:
        return None
    cache_key = master_rank * 100 + size
    if cache_key not in _MASTER_RANK_ICON_CACHE:
        rank_icon_path = data_path / 'chara' / f'train_rank_{master_rank}.png'
        if not rank_icon_path.exists():
            return None
        _MASTER_RANK_ICON_CACHE[cache_key] = open_pjsk_image(rank_icon_path, mode='RGBA', size=(size, size))
    return _MASTER_RANK_ICON_CACHE[cache_key].copy()


def _draw_card_tile(
    base: Image.Image,
    thumb: Image.Image,
    pos: Tuple[int, int],
    has_card: bool,
    missing_font: ImageFont.FreeTypeFont,
    master_rank: int = 0,
    card_id: int = 0,
    id_font: Optional[ImageFont.FreeTypeFont] = None,
):
    """绘制统一格式的卡牌缩略图 tile。"""
    paste_card_thumbnail_tile(
        base,
        thumb,
        pos,
        size=THUMB_SZ,
        has_card=has_card,
        missing_font=missing_font,
        master_rank=master_rank,
        card_id=card_id,
        id_font=id_font,
    )


def _gather_limited(coros, limit: int = CARD_RENDER_LIMIT):
    sem = asyncio.Semaphore(limit)

    async def _run(coro):
        async with sem:
            return await coro

    return asyncio.gather(*[_run(coro) for coro in coros], return_exceptions=True)


def _format_relative_time(ts: int) -> str:
    diff = int(time.time()) - ts
    if diff < 60:       return "刚刚"
    if diff < 3600:     return f"{diff // 60}分钟前"
    if diff < 86400:    return f"{diff // 3600}小时前"
    if diff < 2592000:  return f"{diff // 86400}天前"
    return f"{diff // 2592000}个月前"

def _format_abs_time(ts: int) -> str:
    return datetime.fromtimestamp(ts).strftime("%m-%d %H:%M")


def _near_square_thumb_grid(n: int) -> Tuple[int, int]:
    """根据缩略图数量返回尽量接近正方形的列数和行数。"""
    if n <= 0:
        return 0, 0
    best_cols, best_rows = 1, n
    best_score = None
    for cols in range(1, n + 1):
        rows = math.ceil(n / cols)
        width = cols * THUMB_SZ + max(0, cols - 1) * CELL_GAP
        height = rows * THUMB_SZ + max(0, rows - 1) * CELL_GAP
        score = (abs(width - height), width * height, cols)
        if best_score is None or score < best_score:
            best_score = score
            best_cols, best_rows = cols, rows
    return best_cols, best_rows


# 玩家信息区

async def _draw_player_header(
    pic: Image.Image,
    draw: ImageDraw.Draw,
    profile_data: dict,
    canvas_width: int,
    pjsk_type: int = 0,
    card_asset_map: Optional[Dict[int, str]] = None,
):
    name              = profile_data.get('name', '???')
    rank              = profile_data.get('rank', 0)
    userid            = profile_data.get('userid')
    user_decks        = profile_data.get('userDecks', [])
    special_training  = profile_data.get('special_training', [])
    honors            = profile_data.get('userProfileHonors', [])
    honor_missions    = profile_data.get('userHonorMissions', [])
    suite_update_time = profile_data.get('suite_update_time')

    # 玻璃风玩家信息卡
    panel_x1, panel_y1 = PADDING, 12
    panel_x2, panel_y2 = canvas_width - PADDING, HEADER_H - 12
    _draw_round_panel(
        pic,
        (panel_x1, panel_y1, panel_x2, panel_y2),
        radius=24,
        fill=(255, 255, 255, 218),
        outline=(255, 255, 255, 230),
        shadow=True,
    )

    # 装饰色条与标题
    draw.rounded_rectangle((panel_x1 + 18, panel_y1 + 18, panel_x1 + 80, panel_y1 + 24), radius=3, fill=(255, 128, 178))
    draw.text((panel_x2 - 24, panel_y1 + 24), "CARD BOX", fill=(150, 135, 165), font=_rodin(18), anchor="ra")

    # 头像卡面
    avatar_x, avatar_y = panel_x1 + 26, panel_y1 + 34
    avatar_size = 132
    _draw_round_panel(
        pic,
        (avatar_x, avatar_y, avatar_x + avatar_size, avatar_y + avatar_size),
        radius=20,
        fill=(255, 255, 255, 245),
        outline=(255, 255, 255, 255),
        shadow=True,
    )
    if user_decks:
        try:
            if card_asset_map is None:
                cards = await async_load_master_data('cards.json', pjsk_type)
                card_asset_map = {
                    c['id']: c['assetbundleName']
                    for c in cards if isinstance(c, dict) and c.get('id') is not None and c.get('assetbundleName')
                }
            asset_name = card_asset_map.get(user_decks[0], '') if card_asset_map else ''
            if asset_name:
                suffix = 'after_training' if (special_training and special_training[0]) else 'normal'
                cardimg = await pjsk_update_manager.get_asset(
                    'startapp/thumbnail/chara', f'{asset_name}_{suffix}.png', pjsk_type=pjsk_type)
                cardimg = cardimg.convert("RGBA").resize((avatar_size - 14, avatar_size - 14), Image.Resampling.LANCZOS)
                mask = Image.new("L", cardimg.size, 0)
                ImageDraw.Draw(mask).rounded_rectangle((0, 0, cardimg.width - 1, cardimg.height - 1), radius=16, fill=255)
                pic.paste(cardimg, (avatar_x + 7, avatar_y + 7), mask)
        except Exception as e:
            logger.debug(f"加载头像失败: {e}")

    # 玩家基础信息
    info_x = avatar_x + avatar_size + 28
    draw.text((info_x, panel_y1 + 42), name, fill=(44, 36, 58), font=_bold(34), anchor="la")
    rank_text = f"Rank {rank}"
    draw.rounded_rectangle((info_x, panel_y1 + 88, info_x + 132, panel_y1 + 124), radius=18, fill=(244, 238, 255), outline=(224, 214, 246))
    draw.text((info_x + 66, panel_y1 + 106), rank_text, fill=ACCENT_COLOR, font=_rodin(22), anchor="mm")
    if userid:
        id_text = f"ID {userid}"
        draw.rounded_rectangle((info_x + 146, panel_y1 + 88, info_x + 146 + max(132, len(id_text) * 9), panel_y1 + 124), radius=18, fill=(238, 248, 255), outline=(215, 232, 248))
        draw.text((info_x + 162, panel_y1 + 106), id_text, fill=(82, 92, 110), font=_rodin(16), anchor="lm")

    if suite_update_time:
        ts = int(suite_update_time) // 1000 if int(suite_update_time) > 1e10 else int(suite_update_time)
        update_text = f"数据更新 {_format_relative_time(ts)} · {_format_abs_time(ts)}"
        chip_w = min(max(230, len(update_text) * 10), panel_x2 - info_x - 32)
        draw.rounded_rectangle((info_x, panel_y1 + 134, info_x + chip_w, panel_y1 + 162), radius=14, fill=(255, 246, 251), outline=(245, 218, 232))
        draw.text((info_x + 14, panel_y1 + 148), update_text, fill=(132, 92, 116), font=_medium(14), anchor="lm")

    # 荣誉牌子
    honor_tasks = [((('main' if h.get('seq') == 1 else 'sub'), h))
                   for h in honors if isinstance(h, dict) and h.get('seq') in [1, 2, 3]]
    if honor_tasks:
        results = await asyncio.gather(
            *[generatehonor(h, t == 'main', honor_missions, pjsk_type=pjsk_type) for t, h in honor_tasks],
            return_exceptions=True)
        hx, hy = info_x, panel_y1 + 174
        max_x = panel_x2 - 24
        for (htype, _), res in zip(honor_tasks, results):
            if isinstance(res, Exception):
                continue
            try:
                sz = (188, 40) if htype == 'main' else (90, 40)
                if hx + sz[0] > max_x:
                    break
                honor_img = res.resize(sz, Image.Resampling.LANCZOS)
                _paste_rgba(pic, honor_img, (hx, hy))
                hx += sz[0] + 10
            except Exception:
                continue



# 数据整理

def _build_grid(
    cards: List[Dict],
    ordered_chars: List[int],
    user_card_ids: Optional[Dict[int, Dict]],
    show_box: bool,
) -> Tuple[List[int], Dict[int, Dict[str, List[Dict]]]]:
    """
    返回：
      active_chars  — 有卡的角色ID列表（按 ordered_chars 顺序）
      grid          — {chara_id: {attr: [card, ...]}}
                      每个格子内按稀有度倒序排列
    """
    raw: Dict[int, Dict[str, List[Dict]]] = {}
    for card in cards:
        if not isinstance(card, dict):
            continue
        cid  = card.get('characterId')
        attr = card.get('attr', '')
        if attr not in ATTR_ORDER:
            continue

        item = card.copy()
        if user_card_ids is not None:
            pcard = user_card_ids.get(card['id'])
            item['has'] = pcard is not None
            if pcard:
                item['master_rank'] = int(pcard.get('masterRank') or pcard.get('master_rank') or 0)
        else:
            item['has'] = True

        # box 模式只保留持有的卡
        if show_box and not item['has']:
            continue

        raw.setdefault(cid, {}).setdefault(attr, []).append(item)

    # 每个格子内按稀有度倒序排列
    for cid in raw:
        for attr in raw[cid]:
            raw[cid][attr].sort(
                key=lambda c: (
                    -RARITY_WEIGHT.get(c.get('cardRarityType', ''), 0),
                    -c.get('releaseAt', 0),
                ),
            )

    # 只保留有卡的角色，按 ordered_chars 顺序
    active_chars = [cid for cid in ordered_chars if cid in raw and any(raw[cid].values())]
    return active_chars, raw


# 主绘图函数

async def compose_cardbox_image(
    cards: List[Dict],
    ordered_chars: List[int],
    user_card_ids: Optional[Dict[int, Dict]],
    profile_data: Optional[Dict],
    show_box: bool,
    allcards: List[Dict],
    cardCostume3ds: List[Dict],
    costume3ds: List[Dict],
    card_supplies: List[Dict],
    gameCharacters: List[Dict],
    card_asset_map: Optional[Dict[int, str]] = None,
    pjsk_type: int = 0,
) -> str:
    # 1) 整理数据
    active_chars, grid = _build_grid(cards, ordered_chars, user_card_ids, show_box)
    if card_asset_map is None:
        card_asset_map = {
            card['id']: card['assetbundleName']
            for card in allcards
            if isinstance(card, dict) and card.get('id') is not None and card.get('assetbundleName')
        }

    if not active_chars:
        pic = Image.new("RGB", (800, 300), BG_COLOR)
        ImageDraw.Draw(pic).text((400, 150), "没有找到符合条件的卡牌",
                                 fill=(0, 0, 0), font=_bold(30), anchor="mm")
        return pic2b64(pic)



    # 只保留有卡的属性行
    active_attrs = [a for a in ATTR_ORDER if any(grid.get(cid, {}).get(a) for cid in active_chars)]

    # 为每个「角色 × 属性」单元格计算缩略图网格。
    # 单角色/少角色查询时横向展开，避免图片纵向过长。
    # cell_layout[(cid, attr)] = (inner_cols, inner_rows, box_w, box_h)
    cell_layout: Dict[Tuple[int, str], Tuple[int, int, int, int]] = {}
    char_count = len(active_chars)
    for cid in active_chars:
        for attr in active_attrs:
            n = len(grid.get(cid, {}).get(attr, []))
            cols, rows = _near_square_thumb_grid(n)
            if n > 0 and char_count <= 2:
                if char_count == 1:
                    cols = min(n, max(cols, min(8, max(5, math.ceil(math.sqrt(n * 2.4))))))
                else:
                    cols = min(n, max(cols, min(5, max(3, math.ceil(math.sqrt(n * 1.7))))))
                rows = math.ceil(n / cols)
            if n <= 0:
                box_w = THUMB_SZ + COL_PAD * 2
                box_h = THUMB_SZ + ROW_PAD * 2
            else:
                box_w = cols * THUMB_SZ + max(0, cols - 1) * CELL_GAP + COL_PAD * 2
                box_h = rows * THUMB_SZ + max(0, rows - 1) * CELL_GAP + ROW_PAD * 2
            cell_layout[(cid, attr)] = (cols, rows, box_w, box_h)

    col_widths: Dict[int, int] = {
        cid: max(cell_layout[(cid, attr)][2] for attr in active_attrs)
        for cid in active_chars
    }
    attr_heights: Dict[str, int] = {
        attr: max(cell_layout[(cid, attr)][3] for cid in active_chars)
        for attr in active_attrs
    }

    n_rows = len(active_attrs)
    content_w = ATTR_COL_W + sum(col_widths[cid] for cid in active_chars) + (len(active_chars) - 1) * CELL_GAP
    content_h = CHARA_ICON_H + sum(attr_heights[attr] for attr in active_attrs) + (n_rows - 1) * CELL_GAP

    canvas_w = max(800, content_w + PADDING * 2)
    canvas_h = (HEADER_H if profile_data else 0) + content_h + PADDING * 2

    col_x_map: Dict[int, int] = {}
    cur_x = PADDING + ATTR_COL_W
    for cid in active_chars:
        col_x_map[cid] = cur_x
        cur_x += col_widths[cid] + CELL_GAP

    attr_y_map: Dict[str, int] = {}

    # 3) 创建画布：柔和渐变背景
    pic = _make_gradient_background(canvas_w, canvas_h)
    draw = ImageDraw.Draw(pic)

    # 4) 玩家信息区
    table_y = PADDING
    if profile_data:
        await _draw_player_header(pic, draw, profile_data, canvas_w, pjsk_type, card_asset_map=card_asset_map)
        table_y = HEADER_H + PADDING

    # 5) 内容区背景：主圆角面板 + 头像表头浅色区域
    _draw_round_panel(
        pic,
        (PADDING, table_y, PADDING + content_w, table_y + content_h),
        radius=22,
        fill=PANEL_COLOR,
        outline=(255, 255, 255, 220),
        shadow=True,
    )

    # 内容区起始坐标
    cx = PADDING
    cy = table_y
    header_panel = Image.new("RGBA", (content_w, CHARA_ICON_H), (0, 0, 0, 0))
    hd = ImageDraw.Draw(header_panel)
    hd.rounded_rectangle((0, 0, content_w - 1, CHARA_ICON_H + 24), radius=22, fill=(255, 255, 255, 120))
    pic.paste(header_panel, (cx, cy), header_panel.split()[3])

    # 6) 预加载缩略图
    all_cards_flat = [
        card
        for cid in active_chars
        for attr in active_attrs
        for card in grid.get(cid, {}).get(attr, [])
    ]
    thumb_coros = []
    for card in all_cards_flat:
        is_lim = (cardtype(card['id'], cardCostume3ds, costume3ds) == 1
                  or card.get('cardRarityType') == 'rarity_birthday')
        is_fes = is_fes_card(card, card_supplies, pjsk_type) if is_lim else False
        is_trained = card.get('cardRarityType') in ('rarity_3', 'rarity_4')
        thumb_coros.append(cardthumnail(
            card['id'], istrained=is_trained, cards=allcards,
            limitedbadge=is_lim and not is_fes, fesbadge=is_fes,
            pjsk_type=pjsk_type,
        ))
    thumbs = await _gather_limited(thumb_coros)
    thumb_map: Dict[int, Image.Image] = {}
    for card, thumb in zip(all_cards_flat, thumbs):
        if not isinstance(thumb, Exception):
            thumb_map[card['id']] = thumb

    # 7) 预加载角色颜色
    gcu_data = await async_load_master_data('gameCharacterUnits.json', pjsk_type)
    chara_colors: Dict[int, Tuple[int, int, int]] = {}
    for gcu in gcu_data:
        if not isinstance(gcu, dict):
            continue
        cid = gcu.get('id')
        code = gcu.get('colorCode', '#888888').lstrip('#')
        try:
            chara_colors[cid] = tuple(int(code[i:i+2], 16) for i in (0, 2, 4))
        except Exception:
            chara_colors[cid] = (136, 136, 136)

    # 8) 绘制角色头像行
    header_y = cy + 7
    for cid in active_chars:
        col_x = col_x_map[cid]
        col_w = col_widths[cid]
        color = chara_colors.get(cid, (136, 136, 136))
        soft_color = tuple(min(255, int(v * 0.22 + 255 * 0.78)) for v in color)

        # 头像底座
        pill_w = min(col_w - 10, CHARA_ICON_SZ + 18)
        pill_x = col_x + (col_w - pill_w) // 2
        pill = Image.new("RGBA", (pill_w, CHARA_ICON_H - 12), (0, 0, 0, 0))
        pd = ImageDraw.Draw(pill)
        pd.rounded_rectangle((0, 0, pill_w - 1, CHARA_ICON_H - 13), radius=18, fill=(*soft_color, 165), outline=(*color, 170), width=2)
        pic.paste(pill, (pill_x, header_y - 3), pill.split()[3])

        icon_x = col_x + (col_w - CHARA_ICON_SZ) // 2
        try:
            icon = _load_chara_icon(cid, CHARA_ICON_SZ, outer_color=color)
            _paste_rgba(pic, icon, (icon_x, header_y))
        except Exception as e:
            logger.debug(f"角色头像加载失败 {cid}: {e}")

        # 角色颜色分隔线
        line_y = cy + CHARA_ICON_H - 7
        draw.rounded_rectangle((col_x + 10, line_y, col_x + col_w - 11, line_y + 4), radius=2, fill=color)

    # 9) 绘制属性行和动态格子
    row_y = cy + CHARA_ICON_H
    font_missing = _bold(10)
    font_card_id = _medium(9)
    for row_idx, attr in enumerate(active_attrs):
        attr_y_map[attr] = row_y
        row_h = attr_heights[attr]
        row_color = ATTR_ROW_COLORS.get(attr, (240, 240, 240))
        accent = ATTR_ACCENT_COLORS.get(attr, (150, 150, 170))

        # 属性行背景
        row_overlay = Image.new("RGBA", (content_w - 14, row_h), (0, 0, 0, 0))
        rd = ImageDraw.Draw(row_overlay)
        rd.rounded_rectangle((0, 0, row_overlay.width - 1, row_h - 1), radius=16, fill=(*row_color, 150))
        pic.paste(row_overlay, (cx + 7, row_y), row_overlay.split()[3])

        # 属性图标列胶囊
        attr_pill = Image.new("RGBA", (ATTR_COL_W - 14, row_h - 12), (0, 0, 0, 0))
        ad = ImageDraw.Draw(attr_pill)
        ad.rounded_rectangle((0, 0, attr_pill.width - 1, attr_pill.height - 1), radius=18, fill=(*accent, 42), outline=(*accent, 120), width=2)
        pic.paste(attr_pill, (cx + 7, row_y + 6), attr_pill.split()[3])

        # 属性图标（居中）
        try:
            attr_icon = open_pjsk_image(data_path / f'chara/icon_attribute_{attr}.png', mode='RGBA', size=(ATTR_ICON_SZ, ATTR_ICON_SZ))
            ix = cx + (ATTR_COL_W - ATTR_ICON_SZ) // 2
            iy = row_y + (row_h - ATTR_ICON_SZ) // 2
            _paste_rgba(pic, attr_icon, (ix, iy))
        except Exception as e:
            logger.debug(f"属性图标加载失败 {attr}: {e}")

        for col_idx, cid in enumerate(active_chars):
            col_x = col_x_map[cid]
            col_w = col_widths[cid]
            cell_cards = grid.get(cid, {}).get(attr, [])

            # 角色列的轻微分隔底色
            if col_idx % 2 == 0:
                col_overlay = Image.new("RGBA", (col_w, row_h - 8), (255, 255, 255, 44))
                pic.paste(col_overlay, (col_x, row_y + 4), col_overlay.split()[3])

            cols, rows, inner_w, inner_h = cell_layout[(cid, attr)]
            if cols <= 0:
                continue
            base_x = col_x + (col_w - inner_w) // 2 + COL_PAD
            base_y = row_y + (row_h - inner_h) // 2 + ROW_PAD

            for card_idx, card in enumerate(cell_cards):
                thumb = thumb_map.get(card['id'])
                if thumb is None:
                    continue
                inner_col = card_idx % cols
                inner_row = card_idx // cols
                tx = base_x + inner_col * (THUMB_SZ + CELL_GAP)
                ty = base_y + inner_row * (THUMB_SZ + CELL_GAP)
                _draw_card_tile(
                    pic,
                    thumb,
                    (tx, ty),
                    card.get('has', True),
                    font_missing,
                    card.get('master_rank', 0),
                    card.get('id', 0),
                    font_card_id,
                )

        if row_idx < n_rows - 1:
            sep_y = row_y + row_h + CELL_GAP // 2
            draw.line((cx + 18, sep_y, cx + content_w - 18, sep_y), fill=(255, 255, 255), width=2)

        row_y += row_h + CELL_GAP

    # 10) 属性列右侧柔和分隔线
    draw.rounded_rectangle((cx + ATTR_COL_W - 2, cy + 12, cx + ATTR_COL_W + 1, cy + content_h - 12),
                           radius=2, fill=(210, 205, 225))

    return pic2b64_fast(pic, quality=88)
