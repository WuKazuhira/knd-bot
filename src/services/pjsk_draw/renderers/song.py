"""歌曲信息（pjskinfo）出图。

原 plugins/pjsk/_song_utils.py 的绘制部分。指令侧只需要传 music_id 与服务器。
"""

from __future__ import annotations

import asyncio
import datetime
import os
import time
from pathlib import Path
from typing import Optional

import pytz
from mutagen.mp3 import MP3
from PIL import Image, ImageDraw, ImageFilter

from services.log import logger
from utils.pjsk_paths import ONDEMAND_PATH, STATIC_PATH

from ..context import get_context
from ..primitives import (
    PJSK_WATERMARK_TEXT,
    get_pjsk_font,
    open_pjsk_image,
    run_pjsk_thread,
    vertical_gradient,
)
from ..registry import register

PJSKINFO_CACHE_VERSION = 7

data_path = ONDEMAND_PATH

PJSK_STYLE_TEXT = (64, 48, 72)
PJSK_STYLE_MUTED = (130, 104, 138)
PJSK_STYLE_ACCENT = (0, 204, 187)
PJSK_STYLE_PANEL = (255, 255, 255, 226)
PJSK_STYLE_LINE = (255, 255, 255, 245)
PJSK_DIFF_COLORS = [
    (102, 221, 17),   # easy
    (51, 187, 238),   # normal
    (254, 170, 0),    # hard
    (238, 67, 102),   # expert
    (187, 51, 238),   # master
]
PJSK_DIFF_NAMES = ["EASY", "NORMAL", "HARD", "EXPERT", "MASTER"]
PJSK_CHARA_ICON_FILES = {
    1: 'ick.png', 2: 'saki.png', 3: 'hnm.png', 4: 'shiho.png',
    5: 'mnr.png', 6: 'hrk.png', 7: 'airi.png', 8: 'szk.png',
    9: 'khn.png', 10: 'an.png', 11: 'akt.png', 12: 'toya.png',
    13: 'tks.png', 14: 'emu.png', 15: 'nene.png', 16: 'rui.png',
    17: 'knd.png', 18: 'mfy.png', 19: 'ena.png', 20: 'mzk.png',
    21: 'miku.png', 22: 'rin.png', 23: 'len.png', 24: 'luka.png',
    25: 'meiko.png', 26: 'kaito.png',
}


class _SongInfo:
    """pjskinfo 绘图需要的字段集合（原 _models.MusicInfo 的绘图子集）。"""

    def __init__(self):
        self.title = ''
        self.lyricist = ''
        self.composer = ''
        self.arranger = ''
        self.publishedAt = 0
        self.length = 0
        self.playLevel = [0, 0, 0, 0, 0]
        self.noteCount = [0, 0, 0, 0, 0]
        self.playLevelAdjust = [0, 0, 0, 0, 0]
        self.fullComboAdjust = [0, 0, 0, 0, 0]
        self.fullPerfectAdjust = [0, 0, 0, 0, 0]
        self.fillerSec = 0
        self.categories = []


def _text_size(draw: ImageDraw.ImageDraw, text: str, font) -> tuple[int, int]:
    bbox = draw.textbbox((0, 0), str(text), font=font)
    return bbox[2] - bbox[0], bbox[3] - bbox[1]


def _fit_font(text: str, font_name: str, max_width: int, start_size: int, min_size: int):
    probe = Image.new('RGB', (10, 10))
    draw = ImageDraw.Draw(probe)
    for size in range(start_size, min_size - 1, -2):
        font = get_pjsk_font(font_name, size)
        if _text_size(draw, text, font)[0] <= max_width:
            return font
    return get_pjsk_font(font_name, min_size)


def _make_pjsk_style_background(width: int, height: int) -> Image.Image:
    top = (255, 246, 250)
    bottom = (236, 244, 255)
    img = vertical_gradient(width, height, top, bottom)
    glow = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    gd.ellipse((-width // 5, -height // 4, width // 2, height // 3), fill=(255, 190, 220, 76))
    gd.ellipse((width // 2, height // 4, width + width // 4, height + height // 5), fill=(170, 210, 255, 68))
    gd.ellipse((width // 3, -height // 6, width, height // 2), fill=(210, 190, 255, 32))
    img.paste(glow, (0, 0), glow.split()[-1])
    return img.convert("RGBA")


def _soft_shadow(size: tuple[int, int], radius: int = 28, alpha: int = 58) -> Image.Image:
    shadow = Image.new("RGBA", size, (0, 0, 0, 0))
    d = ImageDraw.Draw(shadow)
    d.rounded_rectangle((0, 0, size[0] - 1, size[1] - 1), radius=radius, fill=(70, 55, 90, alpha))
    return shadow.filter(ImageFilter.GaussianBlur(12))


def _draw_round_panel(base: Image.Image, xy: tuple[int, int, int, int], radius: int = 28,
                      fill=PJSK_STYLE_PANEL, outline=PJSK_STYLE_LINE, shadow: bool = True):
    x1, y1, x2, y2 = xy
    w, h = x2 - x1, y2 - y1
    if shadow:
        sh = _soft_shadow((w, h), radius=radius, alpha=50)
        base.paste(sh, (x1 + 5, y1 + 8), sh.split()[-1])
    panel = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(panel)
    d.rounded_rectangle((0, 0, w - 1, h - 1), radius=radius, fill=fill, outline=outline, width=1)
    base.paste(panel, (x1, y1), panel.split()[-1])


def _rounded_image(img: Image.Image, radius: int = 30) -> Image.Image:
    img = img.convert("RGBA")
    mask = Image.new("L", img.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, img.width - 1, img.height - 1), radius=radius, fill=255)
    out = Image.new("RGBA", img.size, (0, 0, 0, 0))
    out.paste(img, (0, 0), mask)
    return out


def _draw_badge(draw: ImageDraw.ImageDraw, xy: tuple[int, int, int, int], text: str, fill,
                text_fill=(255, 255, 255), font=None, radius: int = 16):
    if font is None:
        font = get_pjsk_font("FOT-RodinNTLGPro-DB.ttf", 24)
    draw.rounded_rectangle(xy, radius=radius, fill=fill)
    draw.text(((xy[0] + xy[2]) // 2, (xy[1] + xy[3]) // 2), str(text), fill=text_fill, font=font, anchor="mm")


def _draw_label_value(draw: ImageDraw.ImageDraw, label: str, value: str, x: int, y: int, width: int):
    label_font = get_pjsk_font("FOT-RodinNTLGPro-DB.ttf", 25)
    value_font = _fit_font(value or "-", "SourceHanSansCN-Bold.otf", width - 170, 32, 20)
    draw.text((x, y), label.upper(), fill=PJSK_STYLE_MUTED, font=label_font)
    draw.text((x + 170, y - 4), value or "-", fill=PJSK_STYLE_TEXT, font=value_font)


def _truncate_text(draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> str:
    text = str(text or '')
    if _text_size(draw, text, font)[0] <= max_width:
        return text
    while text and _text_size(draw, text + '…', font)[0] > max_width:
        text = text[:-1]
    return text + '…' if text else '…'


def _draw_pjsk_watermark(img: Image.Image, text: str = PJSK_WATERMARK_TEXT):
    draw = ImageDraw.Draw(img)
    font = get_pjsk_font("SourceHanSansCN-Medium.otf", 22)
    draw.text((1888, 1046), text, fill=PJSK_STYLE_ACCENT, font=font, anchor="ra")


def _circle_chara_icon(icon: Image.Image, size: int = 46, outer=(255, 255, 255), inner=(248, 246, 252)) -> Image.Image:
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(canvas)
    d.ellipse((0, 0, size - 1, size - 1), fill=outer)
    d.ellipse((3, 3, size - 4, size - 4), fill=inner)
    icon = icon.convert("RGBA").resize((size - 8, size - 8), Image.Resampling.LANCZOS)
    mask = Image.new("L", icon.size, 0)
    ImageDraw.Draw(mask).ellipse((0, 0, icon.width - 1, icon.height - 1), fill=255)
    canvas.paste(icon, (4, 4), mask)
    return canvas


def _load_vocal_chara_icon(chara_id: int, size: int = 46) -> Optional[Image.Image]:
    filename = PJSK_CHARA_ICON_FILES.get(int(chara_id or 0))
    if not filename:
        return None
    path = STATIC_PATH / 'chara' / 'chara_icon' / filename
    if not path.exists():
        return None
    return _circle_chara_icon(Image.open(path), size=size)


def _outside_vocal_icon(name: str, size: int = 46) -> Image.Image:
    icon = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(icon)
    d.ellipse((0, 0, size - 1, size - 1), fill=(88, 92, 118))
    d.ellipse((3, 3, size - 4, size - 4), fill=(255, 255, 255))
    label = (name or "?").strip()[:1].upper()
    d.text((size // 2, size // 2), label, fill=(88, 92, 118), font=get_pjsk_font("SourceHanSansCN-Bold.otf", 22), anchor="mm")
    return icon


def _music_vocal_cards(musicid: int, pjsk_type: int = 0) -> list[dict]:
    load_master_data = get_context().load_master_data
    music_vocals = load_master_data('musicVocals.json', pjsk_type)
    game_characters = {c['id']: c for c in load_master_data('gameCharacters.json', pjsk_type) if isinstance(c, dict)}
    outside_characters = {c['id']: c for c in load_master_data('outsideCharacters.json', pjsk_type) if isinstance(c, dict)}
    cards = []
    for vocal in music_vocals:
        if not isinstance(vocal, dict) or vocal.get('musicId') != musicid:
            continue
        chars = []
        for char in vocal.get('characters') or []:
            ctype = char.get('characterType')
            cid = char.get('characterId')
            if ctype == 'game_character':
                cdata = game_characters.get(cid, {})
                name = (cdata.get('givenName') or cdata.get('firstName') or str(cid)).strip()
                icon = _load_vocal_chara_icon(cid)
            else:
                cdata = outside_characters.get(cid, {})
                name = cdata.get('name') or str(cid)
                icon = _outside_vocal_icon(name)
            chars.append({'name': name, 'icon': icon, 'seq': char.get('seq', 0)})
        chars.sort(key=lambda x: x.get('seq', 0))
        cards.append({
            'caption': vocal.get('caption') or vocal.get('musicVocalType') or 'VOCAL',
            'type': vocal.get('musicVocalType') or '',
            'seq': vocal.get('seq', 0),
            'chars': chars,
        })
    cards.sort(key=lambda x: x.get('seq', 0))
    return cards


def _draw_vocal_cards(img: Image.Image, musicid: int, pjsk_type: int, xy: tuple[int, int, int, int]):
    draw = ImageDraw.Draw(img)
    x1, y1, x2, y2 = xy
    cards = _music_vocal_cards(musicid, pjsk_type)
    if not cards:
        draw.text((x1, y1 + 36), "NO VOCAL DATA", fill=PJSK_STYLE_MUTED, font=get_pjsk_font("FOT-RodinNTLGPro-DB.ttf", 24))
        return

    card_w = (x2 - x1 - 18) // 2
    card_h = 42
    gap_x = 18
    gap_y = 8
    caption_font = get_pjsk_font("SourceHanSansCN-Bold.otf", 18)
    type_font = get_pjsk_font("FOT-RodinNTLGPro-DB.ttf", 13)
    max_cards = min(len(cards), 4)
    type_labels = {
        'original_song': 'ORG',
        'virtual_singer': 'VS',
        'sekai': 'SEK',
        'another_vocal': 'AV',
        'instrumental': 'INS',
    }
    for idx, vocal in enumerate(cards[:max_cards]):
        col = idx % 2
        row = idx // 2
        x = x1 + col * (card_w + gap_x)
        y = y1 + row * (card_h + gap_y)
        draw.rounded_rectangle((x, y, x + card_w, y + card_h), radius=18, fill=(255, 255, 255, 176), outline=(255, 255, 255, 232))
        type_color = (238, 67, 102) if vocal['type'] == 'sekai' else ((88, 92, 118) if vocal['type'] in ('original_song', 'virtual_singer') else PJSK_STYLE_ACCENT)
        draw.rounded_rectangle((x + 10, y + 9, x + 68, y + 33), radius=12, fill=type_color)
        draw.text((x + 39, y + 21), type_labels.get(vocal['type'], 'VOC'), fill=(255, 255, 255), font=type_font, anchor="mm")
        caption = _truncate_text(draw, vocal['caption'], caption_font, card_w - 188)
        draw.text((x + 82, y + 10), caption, fill=PJSK_STYLE_TEXT, font=caption_font)
        icon_x = x + card_w - 42
        for char in reversed(vocal['chars'][:5]):
            if char.get('icon') is not None:
                compact_icon = char['icon'].resize((34, 34), Image.Resampling.LANCZOS)
                img.paste(compact_icon, (icon_x, y + 4), compact_icon.split()[-1])
                icon_x -= 28
    if len(cards) > max_cards:
        draw.text((x2, y2 - 18), f"+{len(cards) - max_cards} more", fill=PJSK_STYLE_MUTED, font=get_pjsk_font("FOT-RodinNTLGPro-DB.ttf", 16), anchor="ra")


# 歌曲长度
async def _musiclength(musicid, fillerSec=0, pjsk_type: int = 0):
    ctx = get_context()
    try:
        data = await ctx.async_load_master_data('musicVocals.json', pjsk_type)
        for vocal in data:
            if vocal['musicId'] == musicid:
                path = f'ondemand/music/long/{vocal["assetbundleName"]}'
                file = f'{vocal["assetbundleName"]}.mp3'
                await ctx.update_assets(path, file, pjsk_type=pjsk_type)
                audio = MP3(rf'{data_path / ctx.server_name(pjsk_type) / path / file}')
                return audio.info.length - fillerSec
        return 0
    except Exception as e:
        logger.warning(f'获取歌曲长度失败，Error：{e}')
        return 0


def _cache_path(musicid: int, pjsk_type: int) -> Path:
    server_name = get_context().server_name(pjsk_type)
    return data_path / server_name / 'pics' / 'pjskinfo' / f'pjskinfo_v{PJSKINFO_CACHE_VERSION}_{musicid}.png'


# 歌曲详情
async def _drawpjskinfo(musicid: int, pjsk_type: int = 0) -> bytes:
    ctx = get_context()
    save_path = _cache_path(musicid, pjsk_type).parent
    save_path.mkdir(parents=True, exist_ok=True)

    info = _SongInfo()
    data = await ctx.async_load_master_data('musics.json', pjsk_type)
    for music in data:
        if music['id'] != musicid:
            continue
        info.title = music['title']
        info.lyricist = music['lyricist']
        info.composer = music['composer']
        info.arranger = music['arranger']
        info.publishedAt = music['publishedAt']
        info.fillerSec = music['fillerSec']
        # categories 因服务器/数据源而异：jp 的 haruki-sekai-master 曾整段缺失该键；
        # cn/tw 的数据源里它是对象数组 [{"musicCategoryName": "mv"}]，而绘制逻辑按
        # 字符串数组 ['mv'] 处理。这里统一归一化成字符串数组，缺失时留空(图标区留空)，
        # 避免因类型不匹配或 KeyError 导致整体出图失败。
        raw_cats = music.get('categories')
        if isinstance(raw_cats, list):
            info.categories = []
            for c in raw_cats:
                if isinstance(c, str):
                    info.categories.append(c)
                elif isinstance(c, dict) and isinstance(c.get('musicCategoryName'), str):
                    info.categories.append(c['musicCategoryName'])
        else:
            info.categories = []

    data = await ctx.async_load_master_data('musicDifficulties.json', pjsk_type)
    for i in range(0, len(data)):
        if data[i]['musicId'] == musicid:
            info.playLevel = [data[i]['playLevel'], data[i + 1]['playLevel'],
                              data[i + 2]['playLevel'], data[i + 3]['playLevel'], data[i + 4]['playLevel']]
            info.noteCount = [data[i]['totalNoteCount'], data[i + 1]['totalNoteCount'],
                              data[i + 2]['totalNoteCount'], data[i + 3]['totalNoteCount'],
                              data[i + 4]['totalNoteCount']]
            try:
                info.playLevelAdjust = [0, 0, 0, data[i + 3]['playLevelAdjust'],
                                        data[i + 4]['playLevelAdjust']]
                info.fullComboAdjust = [0, 0, 0, data[i + 3]['fullComboAdjust'],
                                        data[i + 4]['fullComboAdjust']]
                info.fullPerfectAdjust = [0, 0, 0, data[i + 3]['fullPerfectAdjust'],
                                          data[i + 4]['fullPerfectAdjust']]
            except KeyError:
                pass
            break
    if sum(info.playLevel) == 0 or sum(info.noteCount) == 0:
        for j in range(0, len(data)):
            if data[j]['musicId'] == musicid:
                info.playLevel = [data[j]['playLevel'], data[j + 1]['playLevel'],
                                  data[j + 2]['playLevel'], data[j + 3]['playLevel'], data[j + 4]['playLevel']]
                info.noteCount = [data[j]['totalNoteCount'], data[j + 1]['totalNoteCount'],
                                  data[j + 2]['totalNoteCount'], data[j + 3]['totalNoteCount'], data[j + 4]['totalNoteCount']]
                break
    now = int(time.time() * 1000)
    leak = now < info.publishedAt

    jacket, info.length = await asyncio.gather(
        ctx.get_asset(
            fr'startapp/music/jacket/jacket_s_{str(musicid).zfill(3)}',
            f'jacket_s_{str(musicid).zfill(3)}.png',
            pjsk_type=pjsk_type,
        ),
        _musiclength(musicid, info.fillerSec, pjsk_type=pjsk_type),
    )

    return await run_pjsk_thread(_compose_pjskinfo, musicid, pjsk_type, info, jacket, leak, save_path)


def _compose_pjskinfo(musicid, pjsk_type, info, jacket, leak, save_path) -> bytes:
    """1920x1080 的纯 PIL 合成，实测约 0.95s。

    必须跑在线程池里：留在事件循环上的话，每次 pjskinfo 缓存未命中都会让整个
    bot（所有群、所有插件）卡住约一秒。
    """
    img = _make_pjsk_style_background(1920, 1080)
    draw = ImageDraw.Draw(img)

    # 背景装饰标题
    draw.text((64, 42), "PROJECT SEKAI", fill=(255, 255, 255, 170), font=get_pjsk_font("FOT-RodinNTLGPro-DB.ttf", 44))
    draw.text((64, 92), "MUSIC DATABASE", fill=(255, 255, 255, 128), font=get_pjsk_font("FOT-RodinNTLGPro-DB.ttf", 24))

    # 左侧曲绘卡片
    _draw_round_panel(img, (70, 132, 670, 732), radius=36, fill=(255, 255, 255, 236), outline=(255, 255, 255, 255), shadow=True)
    if jacket:
        jacket = jacket.convert("RGBA").resize((540, 540), Image.Resampling.LANCZOS)
        jacket = _rounded_image(jacket, radius=28)
        img.paste(jacket, (100, 162), jacket.split()[-1])
    else:
        draw.rounded_rectangle((100, 162, 640, 702), radius=28, fill=(238, 234, 246))
        draw.text((370, 432), "NO JACKET", fill=PJSK_STYLE_MUTED, font=get_pjsk_font("FOT-RodinNTLGPro-DB.ttf", 34), anchor="mm")

    # 曲绘下方状态卡
    _draw_round_panel(img, (70, 758, 670, 1008), radius=30, fill=(255, 255, 255, 218), outline=(255, 255, 255, 245), shadow=True)
    draw.text((112, 790), f"MUSIC ID  #{musicid}", fill=PJSK_STYLE_TEXT, font=get_pjsk_font("FOT-RodinNTLGPro-DB.ttf", 34))
    server_name = get_context().server_name(pjsk_type).upper()
    _draw_badge(draw, (112, 846, 222, 888), server_name, (88, 92, 118), font=get_pjsk_font("FOT-RodinNTLGPro-DB.ttf", 22))
    if leak:
        _draw_badge(draw, (240, 846, 350, 888), "LEAK", (238, 67, 102), font=get_pjsk_font("FOT-RodinNTLGPro-DB.ttf", 22))
    elif info.playLevelAdjust[4] == 0:
        _draw_badge(draw, (240, 846, 350, 888), "NEW", PJSK_STYLE_ACCENT, font=get_pjsk_font("FOT-RodinNTLGPro-DB.ttf", 22))

    icon_x = 112
    for category in info.categories:
        icon_type = 'mv_3d' if category == 'mv' else category
        if icon_type == 'image':
            continue
        icon_path = STATIC_PATH / f'pics/{icon_type}.png'
        if not icon_path.exists():
            continue
        type_pic = open_pjsk_image(icon_path).resize((52, 52), Image.Resampling.LANCZOS)
        img.paste(type_pic, (icon_x, 924), type_pic.split()[-1])
        icon_x += 62

    # 右侧标题和基础信息
    _draw_round_panel(img, (710, 82, 1848, 286), radius=34, fill=(255, 255, 255, 224), outline=(255, 255, 255, 245), shadow=True)
    draw.text((752, 114), "MUSIC INFO", fill=PJSK_STYLE_MUTED, font=get_pjsk_font("FOT-RodinNTLGPro-DB.ttf", 28))
    title_font = _fit_font(info.title, "SourceHanSansCN-Bold.otf", 1020, 66, 30)
    draw.text((752, 154), info.title, fill=PJSK_STYLE_TEXT, font=title_font)
    draw.rounded_rectangle((752, 238, 880, 264), radius=13, fill=(0, 204, 187, 42))
    draw.text((816, 251), "TITLE", fill=(0, 150, 140), font=get_pjsk_font("FOT-RodinNTLGPro-DB.ttf", 16), anchor="mm")

    _draw_round_panel(img, (710, 318, 1848, 640), radius=34, fill=(255, 255, 255, 214), outline=(255, 255, 255, 242), shadow=True)
    if info.length:
        length_str = f'{round(info.length, 1)}秒 ({int(info.length / 60)}分{round(info.length - int(info.length / 60) * 60, 1)}秒)'
    else:
        length_str = 'No data'
    if info.publishedAt < 1601438400000:
        info.publishedAt = 1601438400000
    uptime = datetime.datetime.fromtimestamp(
        info.publishedAt / 1000, pytz.timezone('Asia/Shanghai')
    ).strftime('%Y/%m/%d %H:%M:%S (UTC+8)')
    info_rows = [
        ("LYRICIST", info.lyricist),
        ("COMPOSER", info.composer),
        ("ARRANGER", info.arranger),
        ("LENGTH", length_str),
        ("RELEASE", uptime),
    ]
    for row_idx, (label, value) in enumerate(info_rows):
        y = 352 + row_idx * 55
        if row_idx % 2 == 0:
            draw.rounded_rectangle((738, y - 11, 1818, y + 35), radius=18, fill=(255, 255, 255, 118))
        _draw_label_value(draw, label, value, 762, y, 1010)

    # Vocal 信息区：使用角色头像卡片重绘，不再套旧 vocal 模板
    _draw_round_panel(img, (710, 656, 1848, 812), radius=30, fill=(255, 255, 255, 204), outline=(255, 255, 255, 236), shadow=True)
    draw.text((752, 680), "VOCAL", fill=PJSK_STYLE_MUTED, font=get_pjsk_font("FOT-RodinNTLGPro-DB.ttf", 24))
    _draw_vocal_cards(img, musicid, pjsk_type, (752, 704, 1818, 800))

    # 谱面信息区：标题和难度胶囊分离，避免 CHARTS 被遮挡
    _draw_round_panel(img, (710, 836, 1848, 1020), radius=30, fill=(255, 255, 255, 218), outline=(255, 255, 255, 242), shadow=True)
    draw.text((752, 864), "CHARTS", fill=PJSK_STYLE_MUTED, font=get_pjsk_font("FOT-RodinNTLGPro-DB.ttf", 24))
    col_w = 198
    start_x = 796
    for i in range(5):
        x = start_x + i * col_w
        diff_color = PJSK_DIFF_COLORS[i]
        _draw_badge(draw, (x, 890, x + 150, 928), PJSK_DIFF_NAMES[i], diff_color, font=get_pjsk_font("FOT-RodinNTLGPro-DB.ttf", 18), radius=19)
        draw.text((x + 75, 962), str(info.playLevel[i]), fill=diff_color, font=get_pjsk_font("SourceHanSansCN-Bold.otf", 38), anchor="mm")
        draw.text((x + 75, 994), f"{info.noteCount[i]} NOTES", fill=PJSK_STYLE_TEXT, font=get_pjsk_font("FOT-RodinNTLGPro-DB.ttf", 16), anchor="mm")
        if info.playLevelAdjust[4] != 0 and not leak and i >= 3:
            if info.playLevelAdjust[i] is not None:
                const = str(round(info.playLevel[i] + info.playLevelAdjust[i], 1))
            else:
                const = f"{info.playLevel[i]}.?"
            draw.text((x + 75, 1014), f"CONST {const}", fill=PJSK_STYLE_MUTED, font=get_pjsk_font("FOT-RodinNTLGPro-DB.ttf", 14), anchor="mm")

    _draw_pjsk_watermark(img)
    img = img.convert("RGB")
    cache_file = save_path / f'pjskinfo_v{PJSKINFO_CACHE_VERSION}_{musicid}.png'
    img.save(cache_file)
    # 直接回读缓存文件，保证「首次出图」和「命中缓存」返回的是同一份字节。
    return cache_file.read_bytes()


@register("pjskinfo")
async def render_pjskinfo(payload: dict) -> bytes:
    """歌曲详情图。载荷：music_id、pjsk_type。

    磁盘缓存仍在服务侧：主数据没更新且缓存晚于上线时间就直接回读缓存文件。
    """
    musicid = int(payload["music_id"])
    pjsk_type = int(payload.get("pjsk_type", 0))
    ctx = get_context()
    server_name = ctx.server_name(pjsk_type)
    path = _cache_path(musicid, pjsk_type)
    if not path.exists():
        return await _drawpjskinfo(musicid, pjsk_type)

    pjskinfotime = datetime.datetime.fromtimestamp(os.path.getmtime(path))
    diff_path = data_path / server_name / 'realtime' / 'musicDifficulties.json'
    if not diff_path.exists():
        diff_path = data_path / server_name / 'musicDifficulties.json'
    playdatatime = datetime.datetime.fromtimestamp(os.path.getmtime(diff_path))
    musics = await ctx.async_load_master_data('musics.json', pjsk_type)
    for i in musics:
        if i['id'] == musicid:
            publishedAt = i['publishedAt'] / 1000
            break
    else:
        raise IndexError('找不到对应曲目')

    if pjskinfotime <= playdatatime:  # 主数据比缓存新，重画
        return await _drawpjskinfo(musicid, pjsk_type)
    if time.time() >= publishedAt and pjskinfotime.timestamp() < publishedAt:
        # 缓存是上线前画的，重画一次拿掉 LEAK 标
        return await _drawpjskinfo(musicid, pjsk_type)
    return await asyncio.to_thread(path.read_bytes)
