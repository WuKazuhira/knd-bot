"""
新版个人信息绘图模块 - 自定义背景 + 卡牌装饰 + 竖排布局
"""
import asyncio
import json
import os
import random
import time
from pathlib import Path
from typing import Optional, Tuple, List, Dict, Any

from PIL import Image, ImageDraw, ImageFont, ImageFilter

from .._config import data_path, suite_path, SERVER_MAP
from .._utils import (
    generatehonor,
    get_pjsk_font,
    open_pjsk_image,
    master_data_by_id,
)
from .._card_utils import cardthumnail
from .._autoask import pjsk_update_manager
from .._models import UserProfile

# ============ 路径常量 ============
PICS_PATH = data_path / 'pics'
BG_DIR = PICS_PATH / 'bg'
CHARA_PATH = data_path / 'chara'
CHARA_RANK_ICON_PATH = CHARA_PATH / 'chara_rank_icon'
PROFILE_BG_DIR = data_path / 'profile_bg'
PROFILE_BG_SETTINGS_FILE = PROFILE_BG_DIR / 'settings.json'

# ============ 性能缓存 ============
_SETTINGS_CACHE_META: Optional[Tuple[int, int]] = None
_SETTINGS_CACHE: dict = {}
_PROFILE_BG_CACHE: Dict[Tuple[Any, ...], Image.Image] = {}
_DECK_THUMB_CACHE: Dict[Tuple[int, int, bool], Image.Image] = {}
_HONOR_CACHE: Dict[str, Image.Image] = {}
_MAX_PROFILE_BG_CACHE = 24
_MAX_DECK_THUMB_CACHE = 512
_MAX_HONOR_CACHE = 512


def _cache_put(cache: dict, key: Any, value: Image.Image, max_size: int):
    if len(cache) >= max_size:
        try:
            cache.pop(next(iter(cache)))
        except Exception:
            cache.clear()
    cache[key] = value

# ============ 颜色常量 ============
COLOR_AVATAR_FRAME = (86, 86, 116)
COLOR_HONOR_BORDER = (144, 163, 176)
COLOR_HONOR_FILL = (176, 189, 200)
COLOR_FRAME_BORDER = (137, 137, 169)
COLOR_TEAL = (0, 204, 187)
COLOR_WHITE = (255, 255, 255)
COLOR_BLACK = (0, 0, 0)
COLOR_GRAY = (100, 100, 100)
DIFF_COLORS: Dict[str, Tuple[int, int, int]] = {
    'easy': (102, 221, 17),
    'normal': (51, 187, 238),
    'hard': (230, 170, 0),
    'expert': (238, 68, 102),
    'master': (187, 51, 238),
    'append': (255, 105, 180),
}

# ============ 角色/团体映射 ============
CID_UNIT_MAP: Dict[int, str] = {
    1: 'light_sound', 2: 'light_sound', 3: 'light_sound', 4: 'light_sound',
    5: 'idol', 6: 'idol', 7: 'idol', 8: 'idol',
    9: 'street', 10: 'street', 11: 'street', 12: 'street',
    13: 'theme_park', 14: 'theme_park', 15: 'theme_park', 16: 'theme_park',
    17: 'school_refusal', 18: 'school_refusal', 19: 'school_refusal', 20: 'school_refusal',
    21: 'virtual_singer', 22: 'virtual_singer', 23: 'virtual_singer',
    24: 'virtual_singer', 25: 'virtual_singer', 26: 'virtual_singer',
}

UNIT_BG_MAP: Dict[str, List[str]] = {
    'light_sound': ['bg_area_5.png', 'bg_area_17.png', 'bg_light_sound.png'],
    'idol': ['bg_area_7.png', 'bg_area_18.png', 'bg_idol.png'],
    'street': ['bg_area_8.png', 'bg_area_19.png', 'bg_street.png'],
    'theme_park': ['bg_area_9.png', 'bg_area_20.png', 'bg_theme_park.png'],
    'school_refusal': ['bg_area_10.png', 'bg_area_21.png', 'bg_school_refusal.png'],
    'virtual_singer': ['bg_virtual_singer.png'],
}

COMMON_BG: List[str] = [
    'bg_area_1.png', 'bg_area_2.png', 'bg_area_3.png', 'bg_area_4.png',
    'bg_area_11.png', 'bg_area_12.png', 'bg_area_13.png',
    'bg_area_25.png', 'bg_area_26.png', 'bg_area_27.png',
]

CHARA_ORDER: List[List[str]] = [
    ['miku', 'rin', 'len', 'luka'],
    ['meiko', 'kaito'],
    ['ick', 'saki', 'hnm', 'shiho'],
    ['mnr', 'hrk', 'airi', 'szk'],
    ['khn', 'an', 'akt', 'toya'],
    ['tks', 'emu', 'nene', 'rui'],
    ['knd', 'mfy', 'ena', 'mzk'],
]

CHARA_NAME_TO_ID: Dict[str, int] = {
    'miku': 21, 'rin': 22, 'len': 23, 'luka': 24,
    'meiko': 25, 'kaito': 26,
    'ick': 1, 'saki': 2, 'hnm': 3, 'shiho': 4,
    'mnr': 5, 'hrk': 6, 'airi': 7, 'szk': 8,
    'khn': 9, 'an': 10, 'akt': 11, 'toya': 12,
    'tks': 13, 'emu': 14, 'nene': 15, 'rui': 16,
    'knd': 17, 'mfy': 18, 'ena': 19, 'mzk': 20,
}

# ============ 背景设置管理 ============
def _ensure_profile_bg_dir():
    """确保 profile_bg 目录存在"""
    PROFILE_BG_DIR.mkdir(parents=True, exist_ok=True)


def _load_settings() -> dict:
    """读取背景设置 JSON（带 mtime 缓存）"""
    global _SETTINGS_CACHE_META, _SETTINGS_CACHE
    _ensure_profile_bg_dir()
    if not PROFILE_BG_SETTINGS_FILE.exists():
        _SETTINGS_CACHE_META = None
        _SETTINGS_CACHE = {}
        return {}
    try:
        stat = PROFILE_BG_SETTINGS_FILE.stat()
        meta = (stat.st_mtime_ns, stat.st_size)
        if _SETTINGS_CACHE_META == meta:
            return dict(_SETTINGS_CACHE)
        with open(PROFILE_BG_SETTINGS_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
        _SETTINGS_CACHE_META = meta
        _SETTINGS_CACHE = data if isinstance(data, dict) else {}
        return dict(_SETTINGS_CACHE)
    except (json.JSONDecodeError, OSError):
        return {}


def _save_settings(settings: dict):
    """保存背景设置 JSON，并同步缓存"""
    global _SETTINGS_CACHE_META, _SETTINGS_CACHE
    _ensure_profile_bg_dir()
    with open(PROFILE_BG_SETTINGS_FILE, 'w', encoding='utf-8') as f:
        json.dump(settings, f, ensure_ascii=False, separators=(',', ':'))
    try:
        stat = PROFILE_BG_SETTINGS_FILE.stat()
        _SETTINGS_CACHE_META = (stat.st_mtime_ns, stat.st_size)
        _SETTINGS_CACHE = dict(settings)
    except OSError:
        _SETTINGS_CACHE_META = None
        _SETTINGS_CACHE = dict(settings)


def get_user_bg_settings(userid: str, server: str) -> dict:
    """获取用户背景设置"""
    settings = _load_settings()
    key = f'{server}:{userid}'
    return settings.get(key, {})


def set_user_bg_settings(userid: str, server: str, **kwargs):
    """设置用户背景参数（只更新非None的值）"""
    settings = _load_settings()
    key = f'{server}:{userid}'
    if key not in settings:
        settings[key] = {}
    for k, v in kwargs.items():
        if v is not None:
            settings[key][k] = v
    _save_settings(settings)


def get_user_bg_path(userid: str, server: str) -> Path:
    """获取用户自定义背景图路径"""
    return PROFILE_BG_DIR / server / f'{userid}.jpg'


def save_user_bg(userid: str, server: str, img: Image.Image):
    """保存用户自定义背景图，限制最大边 3000px，保存为 jpg quality=85"""
    bg_path = get_user_bg_path(userid, server)
    bg_path.parent.mkdir(parents=True, exist_ok=True)
    # 限制最大边
    max_side = 3000
    w, h = img.size
    if w > max_side or h > max_side:
        ratio = max_side / max(w, h)
        img = img.resize((int(w * ratio), int(h * ratio)), Image.LANCZOS)
    img = img.convert('RGB')
    img.save(bg_path, 'JPEG', quality=85)
    # 自动设置默认参数
    current = get_user_bg_settings(userid, server)
    if 'vertical' not in current:
        set_user_bg_settings(userid, server, vertical=False)
    if 'blur' not in current:
        set_user_bg_settings(userid, server, blur=1)
    if 'alpha' not in current:
        set_user_bg_settings(userid, server, alpha=180)


def remove_user_bg(userid: str, server: str):
    """删除用户自定义背景图"""
    bg_path = get_user_bg_path(userid, server)
    if bg_path.exists():
        bg_path.unlink()

# ============ 绘图辅助函数 ============
def draw_pill(draw: ImageDraw.ImageDraw, xy: Tuple[int, int], size: Tuple[int, int],
              fill=None, outline=None, border_width: int = 1):
    """绘制药丸形状（圆角矩形，radius=h//2）"""
    x, y = xy
    w, h = size
    radius = h // 2
    draw.rounded_rectangle(
        [x, y, x + w, y + h],
        radius=radius,
        fill=fill,
        outline=outline,
        width=border_width,
    )


def draw_double_pill(draw: ImageDraw.ImageDraw, xy: Tuple[int, int], size: Tuple[int, int],
                     color: Tuple[int, int, int], border: int = 1, gap: int = 1):
    """绘制双层药丸：外层边框 + 内层填充"""
    x, y = xy
    w, h = size
    radius = h // 2
    # 外层边框
    draw.rounded_rectangle(
        [x, y, x + w, y + h],
        radius=radius,
        fill=None,
        outline=color,
        width=border,
    )
    # 内层填充
    inner_offset = border + gap
    draw.rounded_rectangle(
        [x + inner_offset, y + inner_offset, x + w - inner_offset, y + h - inner_offset],
        radius=max(1, radius - inner_offset),
        fill=color,
        outline=None,
    )


def draw_rounded_rect(draw: ImageDraw.ImageDraw, xy: Tuple[int, int], size: Tuple[int, int],
                      radius: int = 8, fill=None, outline=None, border_width: int = 1):
    """绘制圆角矩形"""
    x, y = xy
    w, h = size
    draw.rounded_rectangle(
        [x, y, x + w, y + h],
        radius=radius,
        fill=fill,
        outline=outline,
        width=border_width,
    )


def draw_text_centered(draw: ImageDraw.ImageDraw, xy: Tuple[int, int], size: Tuple[int, int],
                       text: str, font: ImageFont.FreeTypeFont, fill=(0, 0, 0)):
    """在指定矩形区域内居中绘制文字"""
    x, y = xy
    w, h = size
    bbox = font.getbbox(text)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    tx = x + (w - tw) // 2
    ty = y + (h - th) // 2
    draw.text((tx, ty), text, fill=fill, font=font)


def _crop_center(img: Image.Image, target_w: int, target_h: int) -> Image.Image:
    """缩放并居中裁剪图片到目标尺寸"""
    w, h = img.size
    # 计算缩放比例，使图片覆盖目标区域
    scale = max(target_w / w, target_h / h)
    new_w = int(w * scale)
    new_h = int(h * scale)
    img = img.resize((new_w, new_h), Image.LANCZOS)
    # 居中裁剪
    left = (new_w - target_w) // 2
    top = (new_h - target_h) // 2
    return img.crop((left, top, left + target_w, top + target_h))

# ============ 背景获取与叠加 ============
def _get_background(userid: str, server: str, leader_chara_id: int,
                    canvas_w: int, canvas_h: int, blur: int = 1) -> Image.Image:
    """获取背景图片：优先用户自定义，否则根据队长角色随机选取（带缓存）"""
    blur = blur or 0
    user_bg_path = get_user_bg_path(userid, server)
    if user_bg_path.exists():
        try:
            stat = user_bg_path.stat()
            key = ('user', str(user_bg_path), stat.st_mtime_ns, stat.st_size, canvas_w, canvas_h, blur)
            cached = _PROFILE_BG_CACHE.get(key)
            if cached is not None:
                return cached.copy()
            bg = Image.open(user_bg_path).convert('RGB')
            bg = _crop_center(bg, canvas_w, canvas_h)
            if blur > 0:
                bg = bg.filter(ImageFilter.GaussianBlur(radius=blur * 2))
            _cache_put(_PROFILE_BG_CACHE, key, bg, _MAX_PROFILE_BG_CACHE)
            return bg.copy()
        except Exception:
            pass

    unit = CID_UNIT_MAP.get(leader_chara_id, 'virtual_singer')
    bg_candidates = UNIT_BG_MAP.get(unit, []) + COMMON_BG
    existing = [name for name in bg_candidates if (BG_DIR / name).exists()]
    if not existing:
        return Image.new('RGB', (canvas_w, canvas_h), (200, 210, 220))
    chosen = random.choice(existing)
    bg_path = BG_DIR / chosen
    try:
        stat = bg_path.stat()
        key = ('default', str(bg_path), stat.st_mtime_ns, stat.st_size, canvas_w, canvas_h, blur)
        cached = _PROFILE_BG_CACHE.get(key)
        if cached is not None:
            return cached.copy()
        bg = Image.open(bg_path).convert('RGB')
        bg = _crop_center(bg, canvas_w, canvas_h)
        if blur > 0:
            bg = bg.filter(ImageFilter.GaussianBlur(radius=blur * 2))
        _cache_put(_PROFILE_BG_CACHE, key, bg, _MAX_PROFILE_BG_CACHE)
        return bg.copy()
    except Exception:
        return Image.new('RGB', (canvas_w, canvas_h), (200, 210, 220))


def _draw_overlay(bg: Image.Image, alpha: int = 180) -> Image.Image:
    """在背景上叠加半透明白色遮罩"""
    overlay = Image.new('RGBA', bg.size, (255, 255, 255, alpha))
    bg_rgba = bg.convert('RGBA')
    result = Image.alpha_composite(bg_rgba, overlay)
    return result

# ============ 卡组卡面获取 ============
async def _get_deck_card(profile: UserProfile, cards_by_id: dict, cards_list: list, index: int, pjsk_type: int) -> Optional[Image.Image]:
    """获取卡组中指定位置的卡面缩略图（含边框/星级/属性装饰，带缓存）"""
    try:
        card_id = profile.userDecks[index]
        if not card_id or card_id not in cards_by_id:
            return None
        is_trained = bool(index < len(profile.special_training) and profile.special_training[index])
        key = (pjsk_type, int(card_id), bool(is_trained))
        cached = _DECK_THUMB_CACHE.get(key)
        if cached is not None:
            return cached.copy()
        pic = await cardthumnail(card_id, istrained=is_trained, cards=cards_list, pjsk_type=pjsk_type)
        if pic is not None:
            _cache_put(_DECK_THUMB_CACHE, key, pic.copy(), _MAX_DECK_THUMB_CACHE)
            return pic.copy()
        return None
    except (IndexError, TypeError, AttributeError, FileNotFoundError):
        return None


async def _get_honor_cached(honor: dict, ismain: bool, missions: list, pjsk_type: int) -> Optional[Image.Image]:
    """生成 honor 图片（带缓存）"""
    try:
        key_data = {
            'pjsk_type': pjsk_type,
            'ismain': ismain,
            'honor': honor,
            'missions': missions or [],
        }
        key = json.dumps(key_data, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
        cached = _HONOR_CACHE.get(key)
        if cached is not None:
            return cached.copy()
        img = await generatehonor(honor, ismain, missions, pjsk_type=pjsk_type)
        if img is not None:
            _cache_put(_HONOR_CACHE, key, img.copy(), _MAX_HONOR_CACHE)
            return img.copy()
    except Exception:
        return None
    return None


# ============ 横版布局绘制 ============
def _draw_horizontal(img: Image.Image, draw: ImageDraw.ImageDraw, profile: UserProfile,
                     userid: str, isprivate: bool, pjsk_type: int,
                     deck_imgs: List[Optional[Image.Image]],
                     honor_imgs: List[Optional[Image.Image]],
                     cards_by_id: dict):
    """横版布局 1600x1100"""
    display_id = '保密' if isprivate else userid

    # ---- 字体 ----
    font_name_45 = get_pjsk_font("SourceHanSansCN-Bold.otf", 45)
    font_id_20 = get_pjsk_font("FOT-RodinNTLGPro-DB.ttf", 20)
    font_pill_20 = get_pjsk_font("FOT-RodinNTLGPro-DB.ttf", 20)
    font_rank_28 = get_pjsk_font("FOT-RodinNTLGPro-DB.ttf", 28)
    font_twitter_20 = get_pjsk_font("FOT-RodinNTLGPro-DB.ttf", 20)
    font_word_22 = get_pjsk_font("SourceHanSansCN-Medium.otf", 22)
    font_diff_name_14 = get_pjsk_font("FOT-RodinNTLGPro-DB.ttf", 14)
    font_diff_num_20 = get_pjsk_font("FOT-RodinNTLGPro-DB.ttf", 20)
    font_section_16 = get_pjsk_font("FOT-RodinNTLGPro-DB.ttf", 16)
    font_count_24 = get_pjsk_font("FOT-RodinNTLGPro-DB.ttf", 24)
    font_chara_rank_18 = get_pjsk_font("FOT-RodinNTLGPro-DB.ttf", 18)

    # ======== 左侧 ========
    # 头像框
    draw.rounded_rectangle(
        [117, 50, 117 + 151, 50 + 151],
        radius=8,
        fill=None,
        outline=COLOR_AVATAR_FRAME,
        width=3,
    )
    # 内部填充
    draw.rounded_rectangle(
        [117 + 6, 50 + 6, 117 + 6 + 139, 50 + 6 + 139],
        radius=6,
        fill=COLOR_AVATAR_FRAME,
    )
    # 粘贴队长卡面
    if deck_imgs and deck_imgs[0] is not None:
        leader_thumb = deck_imgs[0].resize((139, 139), Image.LANCZOS).convert('RGBA')
        img.paste(leader_thumb, (123, 56), leader_thumb.split()[-1])

    # 等级药丸
    draw_double_pill(draw, (307, 150), (208, 50), color=COLOR_AVATAR_FRAME, border=2, gap=1)
    # 等级图标
    try:
        rank_icon = open_pjsk_image(PICS_PATH / 'icon_playerRank.png', mode='RGBA', size=(35, 35))
        img.paste(rank_icon, (315, 157), rank_icon.split()[-1])
    except Exception:
        pass
    draw.text((355, 155), "Lv.", fill=COLOR_WHITE, font=font_pill_20)
    draw.text((385, 152), str(profile.rank), fill=COLOR_WHITE, font=font_rank_28)

    # 昵称
    draw.text((295, 45), profile.name or '???', fill=COLOR_BLACK, font=font_name_45)
    # ID
    draw.text((298, 116), f'ID: {display_id}', fill=COLOR_BLACK, font=font_id_20)

    # ---- 牌子 ----
    # 主牌子框（略微内缩，贴合实际牌子）
    draw_pill(draw, (119, 231), (234, 49), fill=COLOR_HONOR_FILL, outline=COLOR_HONOR_BORDER, border_width=3)
    # 副牌子框（继续内缩，贴合实际牌子）
    draw_pill(draw, (390, 231), (96, 49), fill=COLOR_HONOR_FILL, outline=COLOR_HONOR_BORDER, border_width=3)
    draw_pill(draw, (523, 233), (96, 49), fill=COLOR_HONOR_FILL, outline=COLOR_HONOR_BORDER, border_width=3)
    # 粘贴牌子图片
    if honor_imgs:
        for idx, honor_img in enumerate(honor_imgs):
            if honor_img is None:
                continue
            if idx == 0:
                resized = honor_img.resize((244, 51), Image.LANCZOS).convert('RGBA')
                img.paste(resized, (114, 230), resized.split()[-1])
            elif idx == 1:
                resized = honor_img.resize((106, 51), Image.LANCZOS).convert('RGBA')
                img.paste(resized, (385, 230), resized.split()[-1])
            elif idx == 2:
                resized = honor_img.resize((106, 51), Image.LANCZOS).convert('RGBA')
                img.paste(resized, (518, 232), resized.split()[-1])

    # ---- Twitter ----
    draw_rounded_rect(draw, (114, 309), (481, 49), radius=8, fill=None, outline=COLOR_FRAME_BORDER, border_width=2)
    try:
        x_icon = open_pjsk_image(PICS_PATH / 'icon_X.png', mode='RGBA', size=(30, 30))
        img.paste(x_icon, (124, 316), x_icon.split()[-1])
    except Exception:
        pass
    twitter_text = f'@{profile.twitterId}' if profile.twitterId else ''
    draw.text((162, 320), twitter_text, fill=COLOR_BLACK, font=font_twitter_20)

    # ---- 签名 ----
    draw_rounded_rect(draw, (114, 379), (481, 90), radius=8, fill=None, outline=COLOR_FRAME_BORDER, border_width=2)
    word = profile.word or ''
    # 自动换行（每行约22字）
    max_chars_per_line = 22
    lines = []
    while len(word) > max_chars_per_line:
        lines.append(word[:max_chars_per_line])
        word = word[max_chars_per_line:]
    if word:
        lines.append(word)
    for line_idx, line in enumerate(lines[:3]):
        draw.text((132, 392 + line_idx * 28), line, fill=COLOR_BLACK, font=font_word_22)

    # ---- 卡组 ----
    card_start_x = 114
    card_start_y = 489
    card_size = 128
    card_spacing = 138
    for i in range(5):
        if i < len(deck_imgs) and deck_imgs[i] is not None:
            card_img = deck_imgs[i].resize((card_size, card_size), Image.LANCZOS).convert('RGBA')
            cx = card_start_x + i * card_spacing
            img.paste(card_img, (cx, card_start_y), card_img.split()[-1])
            # masterRank 图标
            master_rank = 0
            if i < len(profile.deck_master_ranks):
                master_rank = profile.deck_master_ranks[i]
            if master_rank and master_rank > 0:
                try:
                    rank_img = open_pjsk_image(
                        CHARA_PATH / f'train_rank_{master_rank}.png', mode='RGBA', size=(28, 28)
                    )
                    img.paste(rank_img, (cx + card_size - 28, card_start_y + card_size - 28), rank_img.split()[-1])
                except Exception:
                    pass

    # ---- CLEAR / FULL COMBO / ALL PERFECT ----
    diff_names = ['easy', 'normal', 'hard', 'expert', 'master', 'append']
    section_labels = ['CLEAR', 'FULL COMBO', 'ALL PERFECT']
    section_data = [profile.clear, profile.full_combo, profile.full_perfect]
    section_y_starts = [631, 764, 897]
    diff_rect_y_offsets = [686, 819, 953]

    for sec_idx, (label, y_start) in enumerate(zip(section_labels, section_y_starts)):
        # 标签药丸
        label_font = get_pjsk_font("FOT-RodinNTLGPro-DB.ttf", 18)
        label_w = 650
        label_x = 101
        draw_pill(draw, (label_x, y_start), (label_w, 36), fill=COLOR_TEAL)
        draw_text_centered(draw, (label_x, y_start), (label_w, 36), label, label_font, fill=COLOR_WHITE)

        # 难度格子（总宽约650px，对齐卡组）
        rect_y = diff_rect_y_offsets[sec_idx]
        rect_w = 95
        rect_h = 36
        gap = 16
        start_x = 101
        data_list = section_data[sec_idx]
        for d_idx, diff in enumerate(diff_names):
            rx = start_x + d_idx * (rect_w + gap)
            color = DIFF_COLORS.get(diff, COLOR_GRAY)
            draw_rounded_rect(draw, (rx, rect_y), (rect_w, rect_h), radius=6, fill=color)
            # 难度名
            draw_text_centered(draw, (rx, rect_y), (rect_w, 16), diff.upper(), font_diff_name_14, fill=COLOR_WHITE)
            # 数量
            count = 0
            if d_idx < len(data_list):
                count = data_list[d_idx] if isinstance(data_list[d_idx], int) else 0
            draw_text_centered(draw, (rx, rect_y + 14), (rect_w, 22), str(count), font_diff_num_20, fill=COLOR_WHITE)

    # ======== 右侧 ========
    # MULTI LIVE
    draw_pill(draw, (798, 48), (650, 36), fill=COLOR_TEAL)
    draw_text_centered(draw, (798, 48), (650, 36), "MULTI LIVE", font_section_16, fill=COLOR_WHITE)

    # MVP
    draw_pill(draw, (836, 135), (108, 46), fill=COLOR_TEAL)
    draw_text_centered(draw, (836, 135), (108, 46), "MVP", font_count_24, fill=COLOR_WHITE)
    draw.text((952, 141), f'{profile.mvpCount}回', fill=COLOR_BLACK, font=font_count_24)

    # SUPER STAR
    draw_pill(draw, (1143, 135), (108, 46), fill=COLOR_TEAL)
    # 两行文字
    draw_text_centered(draw, (1143, 135), (108, 22), "SUPER", font_diff_name_14, fill=COLOR_WHITE)
    draw_text_centered(draw, (1143, 157), (108, 24), "STAR", font_diff_name_14, fill=COLOR_WHITE)
    draw.text((1259, 141), f'{profile.superStarCount}回', fill=COLOR_BLACK, font=font_count_24)

    # CHALLENGE LIVE
    draw_pill(draw, (798, 223), (650, 36), fill=COLOR_TEAL)
    draw_text_centered(draw, (798, 223), (650, 36), "CHALLENGE LIVE", font_section_16, fill=COLOR_WHITE)

    # SOLO
    draw_pill(draw, (836, 308), (108, 46), fill=COLOR_TEAL)
    draw_text_centered(draw, (836, 308), (108, 46), "SOLO", font_count_24, fill=COLOR_WHITE)
    # 角色图标 + 高分
    try:
        chara_icon = open_pjsk_image(CHARA_PATH / f'chr_ts_{profile.characterId}.png', mode='RGBA', size=(70, 70))
        img.paste(chara_icon, (952, 293), chara_icon.split()[-1])
    except Exception:
        pass
    draw.text((1032, 315), str(profile.highScore), fill=COLOR_BLACK, font=font_count_24)

    # CHARACTER RANK
    draw_pill(draw, (799, 402), (650, 36), fill=COLOR_TEAL)
    draw_text_centered(draw, (799, 402), (650, 36), "CHARACTER RANK", font_section_16, fill=COLOR_WHITE)

    # 角色等级图标网格
    character_rank_map: Dict[int, int] = {}
    for item in (profile.characterRank or []):
        if isinstance(item, dict):
            character_rank_map[item.get('characterId', 0)] = item.get('characterRank', 0)

    icon_w = 155
    icon_h = 75
    gap_x = 10
    gap_y = 8
    grid_start_x = 799
    grid_start_y = 456

    for row_idx, row in enumerate(CHARA_ORDER):
        for col_idx, chara_name in enumerate(row):
            cid = CHARA_NAME_TO_ID.get(chara_name, 0)
            current_x = grid_start_x + col_idx * (icon_w + gap_x)
            current_y = grid_start_y + row_idx * (icon_h + gap_y)
            # 尝试加载角色等级图标
            try:
                icon_path = CHARA_RANK_ICON_PATH / f'{chara_name}.png'
                if icon_path.exists():
                    icon = open_pjsk_image(icon_path, mode='RGBA', size=(icon_w, icon_h))
                    img.paste(icon, (current_x, current_y), icon.split()[-1])
            except Exception:
                pass
            # 等级数字
            rank_val = character_rank_map.get(cid, 0)
            pill_x = current_x + 77
            pill_y = current_y + 21
            pill_w = 78
            pill_h = 54
            draw_text_centered(draw, (pill_x, pill_y), (pill_w, pill_h),
                               str(rank_val), font_chara_rank_18, fill=COLOR_BLACK)

# ============ 竖版布局绘制 ============
def _draw_vertical(img: Image.Image, draw: ImageDraw.ImageDraw, profile: UserProfile,
                   userid: str, isprivate: bool, pjsk_type: int,
                   deck_imgs: List[Optional[Image.Image]],
                   honor_imgs: List[Optional[Image.Image]],
                   cards_by_id: dict):
    """竖版布局 800x1650"""
    display_id = '保密' if isprivate else userid
    canvas_w = 800

    # ---- 字体 ----
    font_name_40 = get_pjsk_font("SourceHanSansCN-Bold.otf", 40)
    font_id_18 = get_pjsk_font("FOT-RodinNTLGPro-DB.ttf", 18)
    font_pill_18 = get_pjsk_font("FOT-RodinNTLGPro-DB.ttf", 18)
    font_rank_26 = get_pjsk_font("FOT-RodinNTLGPro-DB.ttf", 26)
    font_twitter_18 = get_pjsk_font("FOT-RodinNTLGPro-DB.ttf", 18)
    font_word_20 = get_pjsk_font("SourceHanSansCN-Medium.otf", 20)
    font_diff_name_12 = get_pjsk_font("FOT-RodinNTLGPro-DB.ttf", 12)
    font_diff_num_18 = get_pjsk_font("FOT-RodinNTLGPro-DB.ttf", 18)
    font_section_14 = get_pjsk_font("FOT-RodinNTLGPro-DB.ttf", 14)
    font_count_22 = get_pjsk_font("FOT-RodinNTLGPro-DB.ttf", 22)
    font_chara_rank_16 = get_pjsk_font("FOT-RodinNTLGPro-DB.ttf", 16)
    font_label_16 = get_pjsk_font("FOT-RodinNTLGPro-DB.ttf", 16)

    # ======== 头像 + 名字 + ID + 等级 ========
    # 竖版顶部：头像左侧，右侧自上而下放昵称/ID/等级，避免重叠
    avatar_x = 85
    avatar_y = 35
    draw.rounded_rectangle(
        [avatar_x, avatar_y, avatar_x + 130, avatar_y + 130],
        radius=8,
        fill=None,
        outline=COLOR_AVATAR_FRAME,
        width=3,
    )
    draw.rounded_rectangle(
        [avatar_x + 5, avatar_y + 5, avatar_x + 125, avatar_y + 125],
        radius=6,
        fill=COLOR_AVATAR_FRAME,
    )
    if deck_imgs and deck_imgs[0] is not None:
        leader_thumb = deck_imgs[0].resize((120, 120), Image.LANCZOS).convert('RGBA')
        img.paste(leader_thumb, (avatar_x + 5, avatar_y + 5), leader_thumb.split()[-1])

    # 昵称 / ID / 等级（头像右侧竖排）
    info_x = avatar_x + 155
    name_text = profile.name or '???'
    draw.text((info_x, avatar_y + 8), name_text, fill=COLOR_BLACK, font=font_name_40)

    id_text = f'ID: {display_id}'
    draw.text((info_x, avatar_y + 58), id_text, fill=COLOR_BLACK, font=font_id_18)

    # 等级药丸
    pill_w = 180
    pill_x = info_x
    pill_y = avatar_y + 88
    draw_double_pill(draw, (pill_x, pill_y), (pill_w, 42), color=COLOR_AVATAR_FRAME, border=2, gap=1)
    try:
        rank_icon = open_pjsk_image(PICS_PATH / 'icon_playerRank.png', mode='RGBA', size=(30, 30))
        img.paste(rank_icon, (pill_x + 10, pill_y + 6), rank_icon.split()[-1])
    except Exception:
        pass
    draw.text((pill_x + 48, pill_y + 8), "Lv.", fill=COLOR_WHITE, font=font_pill_18)
    draw.text((pill_x + 75, pill_y + 6), str(profile.rank), fill=COLOR_WHITE, font=font_rank_26)

    # ======== 牌子 ========
    honor_y = 190
    # 主牌子
    main_honor_w = 206
    main_honor_x = (canvas_w - main_honor_w - 10 - 86 - 10 - 86) // 2
    draw_pill(draw, (main_honor_x, honor_y + 2), (main_honor_w, 42), fill=COLOR_HONOR_FILL, outline=COLOR_HONOR_BORDER, border_width=3)
    # 副牌子
    sub1_x = main_honor_x + main_honor_w + 10
    draw_pill(draw, (sub1_x, honor_y + 2), (86, 42), fill=COLOR_HONOR_FILL, outline=COLOR_HONOR_BORDER, border_width=3)
    sub2_x = sub1_x + 86 + 10
    draw_pill(draw, (sub2_x, honor_y + 2), (86, 42), fill=COLOR_HONOR_FILL, outline=COLOR_HONOR_BORDER, border_width=3)
    # 粘贴牌子
    if honor_imgs:
        for idx, honor_img in enumerate(honor_imgs):
            if honor_img is None:
                continue
            if idx == 0:
                resized = honor_img.resize((200, 40), Image.LANCZOS).convert('RGBA')
                img.paste(resized, (main_honor_x + 3, honor_y + 3), resized.split()[-1])
            elif idx == 1:
                resized = honor_img.resize((80, 40), Image.LANCZOS).convert('RGBA')
                img.paste(resized, (sub1_x + 3, honor_y + 3), resized.split()[-1])
            elif idx == 2:
                resized = honor_img.resize((80, 40), Image.LANCZOS).convert('RGBA')
                img.paste(resized, (sub2_x + 3, honor_y + 3), resized.split()[-1])

    # ======== Twitter + 签名 ========
    tw_y = 255
    tw_w = 600
    tw_x = (canvas_w - tw_w) // 2
    draw_rounded_rect(draw, (tw_x, tw_y), (tw_w, 42), radius=8, fill=None, outline=COLOR_FRAME_BORDER, border_width=2)
    try:
        x_icon = open_pjsk_image(PICS_PATH / 'icon_X.png', mode='RGBA', size=(26, 26))
        img.paste(x_icon, (tw_x + 8, tw_y + 8), x_icon.split()[-1])
    except Exception:
        pass
    twitter_text = f'@{profile.twitterId}' if profile.twitterId else ''
    draw.text((tw_x + 40, tw_y + 10), twitter_text, fill=COLOR_BLACK, font=font_twitter_18)

    sig_y = 310
    draw_rounded_rect(draw, (tw_x, sig_y), (tw_w, 70), radius=8, fill=None, outline=COLOR_FRAME_BORDER, border_width=2)
    word = profile.word or ''
    max_chars = 28
    lines = []
    while len(word) > max_chars:
        lines.append(word[:max_chars])
        word = word[max_chars:]
    if word:
        lines.append(word)
    for line_idx, line in enumerate(lines[:2]):
        draw.text((tw_x + 12, sig_y + 10 + line_idx * 26), line, fill=COLOR_BLACK, font=font_word_20)

    # ======== 卡组 ========
    card_size = 128
    card_spacing = 130
    total_cards_w = 5 * card_size + 4 * (card_spacing - card_size)
    card_start_x = (canvas_w - total_cards_w) // 2
    card_start_y = 395
    for i in range(5):
        if i < len(deck_imgs) and deck_imgs[i] is not None:
            card_img = deck_imgs[i].resize((card_size, card_size), Image.LANCZOS).convert('RGBA')
            cx = card_start_x + i * card_spacing
            img.paste(card_img, (cx, card_start_y), card_img.split()[-1])
            master_rank = 0
            if i < len(profile.deck_master_ranks):
                master_rank = profile.deck_master_ranks[i]
            if master_rank and master_rank > 0:
                try:
                    rank_img = open_pjsk_image(
                        CHARA_PATH / f'train_rank_{master_rank}.png', mode='RGBA', size=(24, 24)
                    )
                    img.paste(rank_img, (cx + card_size - 24, card_start_y + card_size - 24), rank_img.split()[-1])
                except Exception:
                    pass

    # ======== CLEAR / FC / AP + 难度格子 ========
    diff_names = ['easy', 'normal', 'hard', 'expert', 'master', 'append']
    section_labels = ['CLEAR', 'FULL COMBO', 'ALL PERFECT']
    section_data = [profile.clear, profile.full_combo, profile.full_perfect]

    section_base_y = 545
    rect_w = 95
    rect_h = 34
    gap = 16
    total_rects_w = 6 * rect_w + 5 * gap
    rects_start_x = (canvas_w - total_rects_w) // 2

    last_rect_bottom_y = section_base_y  # 跟踪最后一个难度格子的底部y坐标

    for sec_idx, (label, data_list) in enumerate(zip(section_labels, section_data)):
        label_y = section_base_y + sec_idx * 95
        # 标签药丸
        label_w = 650
        label_x = (canvas_w - label_w) // 2
        draw_pill(draw, (label_x, label_y), (label_w, 30), fill=COLOR_TEAL)
        draw_text_centered(draw, (label_x, label_y), (label_w, 30), label, font_label_16, fill=COLOR_WHITE)

        # 难度格子
        rect_y = label_y + 38
        last_rect_bottom_y = rect_y + rect_h
        for d_idx, diff in enumerate(diff_names):
            rx = rects_start_x + d_idx * (rect_w + gap)
            color = DIFF_COLORS.get(diff, COLOR_GRAY)
            draw_rounded_rect(draw, (rx, rect_y), (rect_w, rect_h), radius=5, fill=color)
            draw_text_centered(draw, (rx, rect_y), (rect_w, 14), diff.upper(), font_diff_name_12, fill=COLOR_WHITE)
            count = 0
            if d_idx < len(data_list):
                count = data_list[d_idx] if isinstance(data_list[d_idx], int) else 0
            draw_text_centered(draw, (rx, rect_y + 13), (rect_w, 21), str(count), font_diff_num_18, fill=COLOR_WHITE)

    # ======== MULTI LIVE / CHALLENGE LIVE（同一行两列） ========
    live_y = last_rect_bottom_y + 24
    half_pill_w = 315
    live_gap = 20
    live_left_x = (canvas_w - (half_pill_w * 2 + live_gap)) // 2
    live_right_x = live_left_x + half_pill_w + live_gap
    draw_pill(draw, (live_left_x, live_y), (half_pill_w, 30), fill=COLOR_TEAL)
    draw_text_centered(draw, (live_left_x, live_y), (half_pill_w, 30), "MULTI LIVE", font_section_14, fill=COLOR_WHITE)
    draw_pill(draw, (live_right_x, live_y), (half_pill_w, 30), fill=COLOR_TEAL)
    draw_text_centered(draw, (live_right_x, live_y), (half_pill_w, 30), "CHALLENGE LIVE", font_section_14, fill=COLOR_WHITE)

    # MULTI LIVE 下方：MVP 在上，SUPERSTAR 在下
    stat_pill_w = 100
    mvp_y = live_y + 42
    stat_x = live_left_x + 42
    draw_pill(draw, (stat_x, mvp_y), (stat_pill_w, 38), fill=COLOR_TEAL)
    draw_text_centered(draw, (stat_x, mvp_y), (stat_pill_w, 38), "MVP", font_count_22, fill=COLOR_WHITE)
    draw.text((stat_x + stat_pill_w + 12, mvp_y + 7), f'{profile.mvpCount}回', fill=COLOR_BLACK, font=font_count_22)

    ss_y = mvp_y + 48
    draw_pill(draw, (stat_x, ss_y), (stat_pill_w, 38), fill=COLOR_TEAL)
    draw_text_centered(draw, (stat_x, ss_y), (stat_pill_w, 18), "SUPER", font_diff_name_12, fill=COLOR_WHITE)
    draw_text_centered(draw, (stat_x, ss_y + 18), (stat_pill_w, 20), "STAR", font_diff_name_12, fill=COLOR_WHITE)
    draw.text((stat_x + stat_pill_w + 12, ss_y + 7), f'{profile.superStarCount}回', fill=COLOR_BLACK, font=font_count_22)

    # CHALLENGE LIVE 下方：SOLO + 角色 + 高分
    solo_y = live_y + 42
    solo_pill_w = 90
    solo_x = live_right_x + 42
    draw_pill(draw, (solo_x, solo_y), (solo_pill_w, 38), fill=COLOR_TEAL)
    draw_text_centered(draw, (solo_x, solo_y), (solo_pill_w, 38), "SOLO", font_count_22, fill=COLOR_WHITE)
    try:
        chara_icon = open_pjsk_image(CHARA_PATH / f'chr_ts_{profile.characterId}.png', mode='RGBA', size=(55, 55))
        img.paste(chara_icon, (solo_x + solo_pill_w + 12, solo_y - 8), chara_icon.split()[-1])
    except Exception:
        pass
    draw.text((solo_x + solo_pill_w + 74, solo_y + 8), str(profile.highScore), fill=COLOR_BLACK, font=font_count_22)

    # ======== CHARACTER RANK ========
    cr_y = ss_y + 58
    cr_pill_w = 650
    cr_pill_x = (canvas_w - cr_pill_w) // 2
    draw_pill(draw, (cr_pill_x, cr_y), (cr_pill_w, 30), fill=COLOR_TEAL)
    draw_text_centered(draw, (cr_pill_x, cr_y), (cr_pill_w, 30), "CHARACTER RANK", font_section_14, fill=COLOR_WHITE)

    # 角色等级图标网格
    character_rank_map: Dict[int, int] = {}
    for item in (profile.characterRank or []):
        if isinstance(item, dict):
            character_rank_map[item.get('characterId', 0)] = item.get('characterRank', 0)

    icon_w = 155
    icon_h = 75
    gap_x = 10
    gap_y = 8
    max_cols = 4
    total_grid_w = max_cols * icon_w + (max_cols - 1) * gap_x
    grid_start_x = (canvas_w - total_grid_w) // 2
    grid_start_y = cr_y + 42

    for row_idx, row in enumerate(CHARA_ORDER):
        row_w = len(row) * icon_w + (len(row) - 1) * gap_x
        row_start_x = (canvas_w - row_w) // 2
        for col_idx, chara_name in enumerate(row):
            cid = CHARA_NAME_TO_ID.get(chara_name, 0)
            current_x = row_start_x + col_idx * (icon_w + gap_x)
            current_y = grid_start_y + row_idx * (icon_h + gap_y)
            try:
                icon_path = CHARA_RANK_ICON_PATH / f'{chara_name}.png'
                if icon_path.exists():
                    icon = open_pjsk_image(icon_path, mode='RGBA', size=(icon_w, icon_h))
                    img.paste(icon, (current_x, current_y), icon.split()[-1])
            except Exception:
                pass
            rank_val = character_rank_map.get(cid, 0)
            pill_x = current_x + 77
            pill_y = current_y + 21
            pill_w_inner = 78
            pill_h_inner = 54
            draw_text_centered(draw, (pill_x, pill_y), (pill_w_inner, pill_h_inner),
                               str(rank_val), font_chara_rank_16, fill=COLOR_BLACK)

def _prepare_background(userid: str, server_name: str, leader_chara_id: int,
                        canvas_w: int, canvas_h: int, blur: int, alpha: int) -> Image.Image:
    """在线程中准备背景和遮罩。"""
    bg = _get_background(userid, server_name, leader_chara_id, canvas_w, canvas_h, blur=blur)
    return _draw_overlay(bg, alpha=alpha)


def _compose_profile_image_sync(profile: UserProfile, userid: str, isprivate: bool, pjsk_type: int,
                                server_name: str, bg: Image.Image, vertical: bool,
                                deck_imgs: List[Optional[Image.Image]],
                                honor_imgs: List[Optional[Image.Image]],
                                cards_by_id: dict) -> Image.Image:
    """在线程中完成最终 PIL 绘制，利用 Python 3.14t 自由线程并行 CPU 工作。"""
    img = bg.copy()
    draw = ImageDraw.Draw(img)

    if vertical:
        _draw_vertical(img, draw, profile, userid, isprivate, pjsk_type, deck_imgs, honor_imgs, cards_by_id)
    else:
        _draw_horizontal(img, draw, profile, userid, isprivate, pjsk_type, deck_imgs, honor_imgs, cards_by_id)

    if not profile.isNewData:
        font_ts = get_pjsk_font("SourceHanSansCN-Bold.otf", 22)
        user_suite_file = suite_path / server_name / f'{userid}.json'
        if user_suite_file.exists():
            mtime = user_suite_file.stat().st_mtime
            updatetime = time.localtime(mtime)
            draw.text(
                (68, 10),
                '数据上传时间：' + time.strftime("%Y-%m-%d %H:%M:%S", updatetime),
                fill=COLOR_GRAY,
                font=font_ts,
            )

    return img.convert('RGB')


# ============ 主绘图函数 ============
async def draw_new_profile(profile: UserProfile, userid: str, isprivate: bool, pjsk_type: int) -> Image.Image:
    """绘制新版个人信息图片"""
    server_name = SERVER_MAP.get(pjsk_type, 'jp')
    cards_by_id = master_data_by_id('cards.json', pjsk_type)
    cards_list = list(cards_by_id.values())
    user_settings = get_user_bg_settings(userid, server_name)
    vertical = user_settings.get('vertical', False)
    blur = user_settings.get('blur', 1) or 1
    alpha = user_settings.get('alpha', 180) or 180

    # 确定队长角色ID
    leader_chara_id = 21  # 默认 miku
    if profile.userDecks and profile.userDecks[0]:
        card = cards_by_id.get(profile.userDecks[0])
        if card:
            leader_chara_id = card.get('characterId', 21)

    # 画布尺寸
    if vertical:
        canvas_w, canvas_h = 800, 1650
    else:
        canvas_w, canvas_h = 1600, 1100

    # 背景处理、卡牌生成、牌子生成并发执行；背景与最终绘制走线程，利用 3.14t 自由线程
    bg_task = asyncio.to_thread(
        _prepare_background,
        userid, server_name, leader_chara_id, canvas_w, canvas_h, blur, alpha
    )

    deck_tasks = [_get_deck_card(profile, cards_by_id, cards_list, i, pjsk_type) for i in range(5)]
    deck_gather = asyncio.gather(*deck_tasks, return_exceptions=True)

    honor_tasks = []
    for honor in (profile.userProfileHonors or []):
        if not isinstance(honor, dict):
            continue
        seq = honor.get('seq')
        if seq == 1:
            honor_tasks.append((seq, _get_honor_cached(honor, True, profile.userHonorMissions, pjsk_type)))
        elif seq in (2, 3):
            honor_tasks.append((seq, _get_honor_cached(honor, False, profile.userHonorMissions, pjsk_type)))
    honor_gather = asyncio.gather(*[t[1] for t in honor_tasks], return_exceptions=True) if honor_tasks else None

    if honor_gather is not None:
        bg, deck_results, honor_results = await asyncio.gather(bg_task, deck_gather, honor_gather)
    else:
        bg, deck_results = await asyncio.gather(bg_task, deck_gather)
        honor_results = []

    deck_imgs: List[Optional[Image.Image]] = []
    for r in deck_results:
        deck_imgs.append(None if isinstance(r, Exception) or r is None else r)

    honor_imgs: List[Optional[Image.Image]] = [None, None, None]
    for idx, (seq, _) in enumerate(honor_tasks):
        if idx < len(honor_results) and not isinstance(honor_results[idx], Exception):
            if seq == 1:
                honor_imgs[0] = honor_results[idx]
            elif seq == 2:
                honor_imgs[1] = honor_results[idx]
            elif seq == 3:
                honor_imgs[2] = honor_results[idx]

    return await asyncio.to_thread(
        _compose_profile_image_sync,
        profile, userid, isprivate, pjsk_type, server_name, bg, vertical, deck_imgs, honor_imgs, cards_by_id
    )
