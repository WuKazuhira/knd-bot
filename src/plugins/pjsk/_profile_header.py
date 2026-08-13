"""PJSk 出图共用玩家信息 Header。"""
import asyncio
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from config.path_config import FONT_PATH
from services.log import logger
from ._autoask import pjsk_update_manager
from ._utils import generatehonor


HEADER_BG = (255, 255, 255, 218)
ACCENT_COLOR = (88, 92, 118)
_FONT_CACHE: Dict[Tuple[str, int], ImageFont.FreeTypeFont] = {}


@dataclass
class PjskHeaderData:
    userid: str
    name: str = '???'
    rank: int = 0
    is_private: bool = False
    user_decks: List[int] = field(default_factory=list)
    special_training: List[bool] = field(default_factory=list)
    user_profile_honors: List[dict] = field(default_factory=list)
    user_honor_missions: List[dict] = field(default_factory=list)
    suite_update_time: Optional[Any] = None


def build_header_data_from_profile(profile, userid: str, is_private: bool, suite_data: Optional[dict] = None, suite_raw_data: Optional[dict] = None) -> PjskHeaderData:
    suite_data = suite_data if isinstance(suite_data, dict) else {}
    suite_raw_data = suite_raw_data if isinstance(suite_raw_data, dict) else {}
    return PjskHeaderData(
        userid=str(userid),
        name=profile.name or suite_data.get('name') or suite_raw_data.get('name') or '???',
        rank=profile.rank or suite_data.get('rank', 0) or suite_raw_data.get('rank', 0),
        is_private=is_private,
        user_decks=profile.userDecks or suite_data.get('userDecks', []) or suite_raw_data.get('userDecks', []),
        special_training=profile.special_training or suite_data.get('special_training', []),
        user_profile_honors=profile.userProfileHonors or suite_data.get('userProfileHonors', []) or suite_raw_data.get('userProfileHonors', []),
        user_honor_missions=profile.userHonorMissions or suite_data.get('userHonorMissions', []) or suite_raw_data.get('userHonorMissions', []),
        suite_update_time=suite_data.get('upload_time') or suite_raw_data.get('upload_time') or suite_data.get('updatedAt') or suite_raw_data.get('updatedAt') or getattr(profile, 'updatedAt', None),
    )


def _font(name: str, size: int) -> ImageFont.FreeTypeFont:
    key = (name, size)
    if key not in _FONT_CACHE:
        _FONT_CACHE[key] = ImageFont.truetype(str(FONT_PATH / name), size)
    return _FONT_CACHE[key]


def _bold(size: int) -> ImageFont.FreeTypeFont:
    return _font('SourceHanSansCN-Bold.otf', size)


def _medium(size: int) -> ImageFont.FreeTypeFont:
    return _font('SourceHanSansCN-Medium.otf', size)


def _rodin(size: int) -> ImageFont.FreeTypeFont:
    return _font('FOT-RodinNTLGPro-DB.ttf', size)


def _text_width(font, text: str) -> int:
    try:
        bbox = font.getbbox(str(text))
        return bbox[2] - bbox[0]
    except AttributeError:
        return font.getsize(str(text))[0]


def _fit_text(draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> str:
    text = str(text or '')
    if draw.textlength(text, font=font) <= max_width:
        return text
    while text and draw.textlength(text + '…', font=font) > max_width:
        text = text[:-1]
    return text + '…' if text else '…'


def _soft_shadow(size: Tuple[int, int], radius: int = 14, alpha: int = 48) -> Image.Image:
    shadow = Image.new('RGBA', size, (0, 0, 0, 0))
    d = ImageDraw.Draw(shadow)
    d.rounded_rectangle((0, 0, size[0] - 1, size[1] - 1), radius=radius, fill=(70, 55, 90, alpha))
    return shadow.filter(ImageFilter.GaussianBlur(10))


def draw_round_panel(base: Image.Image, xy: Tuple[int, int, int, int], radius: int, fill, outline=None, shadow: bool = True):
    x1, y1, x2, y2 = xy
    w, h = x2 - x1, y2 - y1
    if shadow:
        sh = _soft_shadow((w, h), radius=radius)
        base.paste(sh, (x1 + 4, y1 + 6), sh.split()[3])
    panel = Image.new('RGBA', (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(panel)
    d.rounded_rectangle((0, 0, w - 1, h - 1), radius=radius, fill=fill, outline=outline, width=1 if outline else 0)
    base.paste(panel, (x1, y1), panel.split()[3])


def paste_round_image(base: Image.Image, img: Image.Image, xy: Tuple[int, int], size: Tuple[int, int], radius: int):
    img = img.convert('RGBA').resize(size, Image.Resampling.LANCZOS)
    mask = Image.new('L', size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, size[0] - 1, size[1] - 1), radius=radius, fill=255)
    base.paste(img, xy, mask)


def _format_relative_time(timestamp: Any) -> str:
    try:
        timestamp = int(float(timestamp))
        if timestamp > 10_000_000_000:
            timestamp //= 1000
    except Exception:
        return '未知'
    now = int(time.time())
    diff = now - timestamp
    if diff < 0:
        return '未来'
    if diff < 60:
        return '刚刚'
    if diff < 3600:
        return f'{diff // 60}分钟前'
    if diff < 86400:
        return f'{diff // 3600}小时前'
    if diff < 2592000:
        return f'{diff // 86400}天前'
    return f'{diff // 2592000}个月前'


def _format_absolute_time(timestamp: Any) -> str:
    try:
        timestamp = int(float(timestamp))
        if timestamp > 10_000_000_000:
            timestamp //= 1000
        return datetime.fromtimestamp(timestamp).strftime('%m-%d %H:%M')
    except Exception:
        return '未知'


async def _load_avatar(data: PjskHeaderData, card_asset_map: Optional[Dict[int, str]], pjsk_type: int) -> Optional[Image.Image]:
    if not data.user_decks:
        return None
    try:
        asset_name = card_asset_map.get(data.user_decks[0], '') if card_asset_map else ''
        if not asset_name:
            return None
        suffix = 'after_training' if (data.special_training and data.special_training[0]) else 'normal'
        return await pjsk_update_manager.get_asset('startapp/thumbnail/chara', f'{asset_name}_{suffix}.png', pjsk_type=pjsk_type)
    except Exception as e:
        logger.debug(f'[header] 加载头像卡面失败: {e}')
        return None


async def _load_cutout(data: PjskHeaderData, card_asset_map: Optional[Dict[int, str]], pjsk_type: int) -> Optional[Image.Image]:
    if not data.user_decks:
        return None
    try:
        asset_name = card_asset_map.get(data.user_decks[0], '') if card_asset_map else ''
        if not asset_name:
            return None
        suffix = 'after_training' if (data.special_training and data.special_training[0]) else 'normal'
        cutout = await pjsk_update_manager.get_asset(
            f'startapp/character/member_cutout_trm/{asset_name}', f'{suffix}.png', pjsk_type=pjsk_type, download=False
        )
        if cutout is None:
            cutout = await pjsk_update_manager.get_asset(
                f'startapp/character/member_cutout_trm/{asset_name}/{suffix}', f'{suffix}.png', pjsk_type=pjsk_type, download=False
            )
        return cutout
    except Exception as e:
        logger.debug(f'[header] 加载立绘失败: {e}')
        return None


async def draw_pjsk_profile_header(
    img: Image.Image,
    xy: Tuple[int, int, int, int],
    data: PjskHeaderData,
    *,
    module_label: str,
    pjsk_type: int,
    card_asset_map: Optional[Dict[int, str]] = None,
    extra_badges: Optional[List[Tuple[str, str]]] = None,
    show_cutout: bool = True,
    compact: bool = False,
):
    draw = ImageDraw.Draw(img)
    x1, y1, x2, y2 = xy
    w = x2 - x1
    h = y2 - y1
    radius = 24 if compact else 30
    draw_round_panel(img, xy, radius=radius, fill=HEADER_BG, outline=(255, 255, 255, 230), shadow=True)
    draw.rounded_rectangle((x1 + 22, y1 + 22, x1 + 118, y1 + 30), radius=4, fill=(255, 128, 178))
    draw.text((x2 - 24, y1 + 26), module_label, fill=(150, 135, 165), font=_rodin(16 if compact else 18), anchor='ra')

    avatar_size = 104 if compact else 132
    avatar_x = x1 + 28
    avatar_y = y1 + (h - avatar_size) // 2 + (8 if compact else 6)
    draw_round_panel(
        img,
        (avatar_x, avatar_y, avatar_x + avatar_size, avatar_y + avatar_size),
        radius=18 if compact else 22,
        fill=(255, 255, 255, 245),
        outline=(255, 255, 255, 255),
        shadow=True,
    )
    avatar = await _load_avatar(data, card_asset_map, pjsk_type)
    if avatar is not None:
        inset = 7
        paste_round_image(img, avatar, (avatar_x + inset, avatar_y + inset), (avatar_size - inset * 2, avatar_size - inset * 2), radius=16)

    if show_cutout and not compact:
        cutout = await _load_cutout(data, card_asset_map, pjsk_type)
        if cutout is not None:
            cutout = cutout.convert('RGBA')
            scale = min(0.38, 230 / max(1, cutout.height))
            cutout = cutout.resize((int(cutout.width * scale), int(cutout.height * scale)), Image.Resampling.LANCZOS)
            img.paste(cutout, (x2 - cutout.width - 36, y1 + 22), cutout.split()[-1])

    info_x = avatar_x + avatar_size + 28
    name_font = _bold(30 if compact else 36)
    name_max_w = x2 - info_x - (280 if not compact else 180)
    draw.text((info_x, y1 + (38 if compact else 52)), _fit_text(draw, data.name, name_font, name_max_w), fill=(44, 36, 58), font=name_font, anchor='la')

    chip_y = y1 + (82 if compact else 106)
    rank_w = 132 if not compact else 118
    draw.rounded_rectangle((info_x, chip_y, info_x + rank_w, chip_y + 36), radius=18, fill=(244, 238, 255), outline=(224, 214, 246))
    draw.text((info_x + rank_w // 2, chip_y + 18), f'Rank {data.rank}', fill=ACCENT_COLOR, font=_rodin(18 if compact else 20), anchor='mm')

    userid = '保密' if data.is_private else str(data.userid)
    id_text = f'ID {userid}'
    id_w = max(126, _text_width(_rodin(15), id_text) + 34)
    id_x = info_x + rank_w + 14
    draw.rounded_rectangle((id_x, chip_y, id_x + id_w, chip_y + 36), radius=18, fill=(238, 248, 255), outline=(215, 232, 248))
    draw.text((id_x + 16, chip_y + 18), id_text, fill=(82, 92, 110), font=_rodin(15), anchor='lm')

    badge_x = id_x + id_w + 12
    if extra_badges:
        for label, value in extra_badges:
            text = f'{label} {value}'
            bw = max(92, _text_width(_rodin(15), text) + 28)
            if badge_x + bw > x2 - 32:
                break
            draw.rounded_rectangle((badge_x, chip_y, badge_x + bw, chip_y + 36), radius=18, fill=(255, 246, 251), outline=(245, 218, 232))
            draw.text((badge_x + bw // 2, chip_y + 18), text, fill=(132, 92, 116), font=_rodin(15), anchor='mm')
            badge_x += bw + 10

    update_y = y1 + (126 if compact else 154)
    if data.suite_update_time and update_y + 28 < y2:
        update_text = f"数据更新 {_format_relative_time(data.suite_update_time)} · {_format_absolute_time(data.suite_update_time)}"
        uw = min(max(230, _text_width(_medium(14), update_text) + 28), x2 - info_x - 32)
        draw.rounded_rectangle((info_x, update_y, info_x + uw, update_y + 28), radius=14, fill=(255, 246, 251), outline=(245, 218, 232))
        draw.text((info_x + 14, update_y + 14), update_text, fill=(132, 92, 116), font=_medium(14), anchor='lm')

    if compact:
        return

    honors = []
    for h in data.user_profile_honors or []:
        if not isinstance(h, dict):
            continue
        seq = h.get('seq', 0)
        if seq == 1:
            honors.append(('main', h))
        elif seq in (2, 3):
            honors.append(('sub', h))
    if not honors:
        return
    tasks = [generatehonor(h, t == 'main', data.user_honor_missions, pjsk_type=pjsk_type) for t, h in honors]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    honor_x = info_x
    honor_y = y2 - 58
    max_x = x2 - 30
    for (htype, _), res in zip(honors, results):
        if isinstance(res, Exception) or res is None:
            continue
        try:
            size = (188, 40) if htype == 'main' else (90, 40)
            if honor_x + size[0] > max_x:
                break
            honor = res.resize(size, Image.Resampling.LANCZOS).convert('RGBA')
            img.paste(honor, (honor_x, honor_y), honor.split()[-1])
            honor_x += size[0] + 10
        except Exception:
            continue
