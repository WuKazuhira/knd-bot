import asyncio
import datetime
import json
import os
import re
import time
import unicodedata
from typing import Tuple

import pytz
import yaml
from mutagen.mp3 import MP3
from PIL import Image, ImageDraw, ImageFilter

from services import logger
from utils.http_utils import AsyncHttpx
from utils.imageutils import pic2b64

from ._autoask import pjsk_update_manager
from ._common_utils import PJSK_WATERMARK_TEXT, callapi, string_similar
from ._config import MUSIC_ALIAS_API_URL, SERVER_CONFIG, SERVER_MAP, data_path
from ._models import MusicInfo, PjskSongsAlias
from ._utils import (
    async_load_master_data,
    get_pjsk_font,
    load_master_data,
    open_pjsk_image,
    run_pjsk_thread,
    vertical_gradient,
)

PJSKINFO_CACHE_VERSION = 7


# 判断歌曲是否未实装
def isleak(musicid: int, musics=None, pjsk_type: int = 0):
    if musics is None:
        musics = load_master_data('musics.json', pjsk_type)
    for i in musics:
        if i['id'] == musicid:
            # 其它服务器的时间戳逻辑暂不处理
            if int(time.time() * 1000) < i['publishedAt']:
                return True
            else:
                return False
    return True


# 歌曲定数
def getPlayLevel(musicid: int, difficulty: str, musicDifficulties=None, pjsk_type: int = 0):
    if musicDifficulties is None:
        musicDifficulties = load_master_data('musicDifficulties.json', pjsk_type)
    for diff in musicDifficulties:
        if musicid == diff['musicId'] and diff['musicDifficulty'] == difficulty:
            return diff['playLevel']


# 更新从uniapi获取的歌曲alias
async def save_songs_data(song_id: int):
    url = f'https://api.unipjsk.com/getalias2/{song_id}'
    try:
        song_list = (await AsyncHttpx.get(url)).json()
        for song in song_list:
            if await PjskSongsAlias.add_alias(
                song_id, song['alias'], 114514, 114514, datetime.datetime.now(), True
            ):
                logger.info(f"更新歌曲id:{song_id}别称({song['alias']})成功")
    except Exception as e:
        logger.warning(f"从 unipjsk 更新曲目 {song_id} 失败: {e}")

# 从Haruki同步全量歌曲别称
async def sync_haruki_music_aliases(pjsk_type: int = 0):
    if not MUSIC_ALIAS_API_URL:
        logger.warning("未配置 endpoints.music_alias_api_url，跳过外部歌曲别名同步")
        return
    musics = await async_load_master_data('musics.json', pjsk_type)
    if not musics:
        return
    logger.info(f"开始从haruki同步歌曲别名...共计 {len(musics)} 首歌")
    updated_num = 0
    from ._config import SERVER_MAP
    server_name = SERVER_MAP.get(pjsk_type, 'jp')

    async def sync_music(mid: int):
        nonlocal updated_num
        try:
            url = MUSIC_ALIAS_API_URL.format(music_id=mid)
            resp = await AsyncHttpx.get(url, timeout=10)
            data = resp.json()
            if data and 'aliases' in data:
                aliases = data['aliases']
                # 排除韩语别名
                aliases = [a for a in aliases if not any('\uac00' <= c <= '\ud7af' for c in a)]
                for alias in aliases:
                    if await PjskSongsAlias.add_alias(
                        mid, alias, 114514, 114514, datetime.datetime.now(), True
                    ):
                        updated_num += 1
                        logger.info(f"更新歌曲id:{mid} Haruki别称({alias})成功")
        except Exception:
            pass

    import asyncio
    # 按照 batch_size 限制并发数
    batch_size = 10
    for i in range(0, len(musics), batch_size):
        batch = musics[i:i + batch_size]
        await asyncio.gather(*(sync_music(m['id']) for m in batch))
        await asyncio.sleep(1)
        
    logger.info(f"从haruki同步歌曲别名完成，共计更新 {updated_num} 条数据")

    
def _load_music_title_translations(pjsk_type: int = 0):
    server_name = SERVER_MAP.get(pjsk_type, 'jp')
    trans_path = data_path / server_name / 'translate.yaml'
    if not trans_path.exists():
        return {}

    with open(trans_path, encoding='utf-8') as f:
        trans_data = yaml.load(f, Loader=yaml.FullLoader) or {}

    if not isinstance(trans_data, dict):
        logger.warning(f'[{server_name}] 翻译文件格式异常，已跳过曲名翻译: {trans_path}')
        return {}

    music_titles = trans_data.get('music_titles', {})
    if not isinstance(music_titles, dict):
        logger.warning(f'[{server_name}] 曲名翻译格式异常，已跳过曲名翻译: {trans_path}')
        return {}

    return music_titles


def _normalize_song_query(text: str) -> str:
    text = unicodedata.normalize('NFKC', str(text or '')).lower()
    return ''.join(ch for ch in text if re.match(r'[\w\u3040-\u30ff\u3400-\u9fff]', ch))


def _safe_similarity(s1: str, s2: str) -> float:
    if not s1 or not s2:
        return 0.0
    return max(0.0, string_similar(s1, s2))


def _song_match_score(query: str, candidate: str) -> float:
    query = str(query or '').strip()
    candidate = str(candidate or '').strip()
    if not query or not candidate:
        return 0.0
    q_norm = _normalize_song_query(query)
    c_norm = _normalize_song_query(candidate)
    if not q_norm or not c_norm:
        return 0.0
    if q_norm == c_norm:
        return 1.0

    raw_score = _safe_similarity(query.lower(), candidate.lower())
    norm_score = _safe_similarity(q_norm, c_norm)
    score = max(raw_score * 0.35 + norm_score * 0.65, norm_score)

    short, long = (q_norm, c_norm) if len(q_norm) <= len(c_norm) else (c_norm, q_norm)
    if short and short in long:
        contain_score = 0.72 + 0.24 * (len(short) / max(len(long), 1))
        if long.startswith(short):
            contain_score += 0.04
        score = max(score, min(contain_score, 0.98))

    return min(score, 1.0)


def _split_translations(value: str) -> list[str]:
    return [item.strip() for item in str(value or '').split('/') if item.strip()]


def _song_result(music_id: int, match: float, title: str, translate: str = '', *, candidates=None,
                 matched_alias: str = '', exact: bool = False):
    if translate == title:
        translate = ''
    return {
        'match': match,
        'musicId': music_id,
        'status': 'success' if music_id else 'false',
        'title': title,
        'translate': translate,
        'candidates': candidates or [],
        'matched_alias': matched_alias,
        'exact': exact,
    }


async def _matchname_candidates(alias: str, pjsk_type: int = 0, limit: int = 5) -> list[dict]:
    data = await async_load_master_data('musics.json', pjsk_type)
    trans = _load_music_title_translations(pjsk_type)
    music_by_id = {int(music['id']): music for music in data}
    candidates: dict[int, dict] = {}

    def add_candidate(music_id: int, name: str, source: str):
        music = music_by_id.get(int(music_id))
        if not music or not name:
            return
        score = _song_match_score(alias, name)
        if score <= 0:
            return
        old = candidates.get(int(music_id))
        if old is None or score > old['match']:
            translate = trans.get(int(music_id), '')
            candidates[int(music_id)] = _song_result(
                int(music_id), score, music['title'], translate,
                matched_alias=name, exact=False,
            ) | {'source': source}

    for music in data:
        music_id = int(music['id'])
        add_candidate(music_id, music['title'], 'title')
        for title in _split_translations(trans.get(music_id, '')):
            add_candidate(music_id, title, 'translate')

    try:
        alias_pairs = await PjskSongsAlias.query_alias_pairs()
    except Exception as e:
        logger.warning(f'读取歌曲别名用于模糊匹配失败: {e}')
        alias_pairs = []
    for music_id, song_alias in alias_pairs:
        add_candidate(music_id, song_alias, 'alias')

    result = sorted(candidates.values(), key=lambda item: item['match'], reverse=True)
    return result[:limit]


# 模糊搜索曲名的具体函数
def _matchname(alias, pjsk_type: int = 0):
    match = {'match': 0, 'musicId': 0, 'status': 'false', 'title': '', 'translate': ''}
    data = load_master_data('musics.json', pjsk_type)
    trans = _load_music_title_translations(pjsk_type)

    for musics in data:
        name = musics['title']
        similar = string_similar(alias.lower(), name.lower())
        if similar > match['match']:
            match['match'] = similar
            match['musicId'] = musics['id']
            match['title'] = musics['title']
        try:
            translate = trans[musics['id']]
            if '/' in translate:
                alltrans = translate.split('/')
                for i in alltrans:
                    similar = string_similar(alias.lower(), i.lower())
                    if similar > match['match']:
                        match['match'] = similar
                        match['musicId'] = musics['id']
                        match['title'] = musics['title']
            else:
                similar = string_similar(alias.lower(), translate.lower())
                if similar > match['match']:
                    match['match'] = similar
                    match['musicId'] = musics['id']
                    match['title'] = musics['title']
        except KeyError:
            pass
    try:
        match['translate'] = trans[match['musicId']]
        if match['translate'] == match['title']:
            match['translate'] = ''
    except KeyError:
        match['translate'] = ''
    if match['match'] > 0:
        match['status'] = 'success'
    return match


# 准确/模糊搜索曲名
async def get_songs_data(alias: str, isfuzzy: bool = False, pjsk_type: int = 0):
    alias = str(alias or '').strip()
    data = await async_load_master_data('musics.json', pjsk_type)
    music_by_id = {int(music['id']): music for music in data}
    trans = _load_music_title_translations(pjsk_type)

    def by_id(song_id: int, *, matched_alias: str = '', exact: bool = True):
        music = music_by_id.get(int(song_id))
        if not music:
            return None
        return _song_result(
            int(song_id), 1.0 if exact else 0.0, music['title'], trans.get(int(song_id), ''),
            matched_alias=matched_alias or music['title'], exact=exact,
        )

    if alias.isdigit():
        ret = by_id(int(alias), matched_alias=alias, exact=True)
        if ret:
            return ret

    sid = await PjskSongsAlias.query_sid(alias)
    if sid:
        ret = by_id(int(sid), matched_alias=alias, exact=True)
        if ret:
            return ret

    normalized_alias = _normalize_song_query(alias)
    for music_id, music in music_by_id.items():
        if _normalize_song_query(music['title']) == normalized_alias:
            return by_id(music_id, matched_alias=music['title'], exact=True)
        for title in _split_translations(trans.get(music_id, '')):
            if _normalize_song_query(title) == normalized_alias:
                return by_id(music_id, matched_alias=title, exact=True)

    if isfuzzy:
        candidates = await _matchname_candidates(alias, pjsk_type)
        if candidates:
            best = dict(candidates[0])
            best['candidates'] = candidates
            best['status'] = 'success'
            return best
    return {
        "match": 0,
        "musicId": 0,
        "status": "false",
        "title": "",
        "translate": "",
        "candidates": [],
        "matched_alias": "",
        "exact": False,
    }


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


def _draw_fit_text(draw: ImageDraw.ImageDraw, pos: tuple[int, int], text: str, font_name: str,
                   max_width: int, start_size: int, min_size: int, fill):
    font = _fit_font(text, font_name, max_width, start_size, min_size)
    draw.text(pos, str(text), fill=fill, font=font)
    return font


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


def _load_vocal_chara_icon(chara_id: int, size: int = 46) -> Image.Image | None:
    filename = PJSK_CHARA_ICON_FILES.get(int(chara_id or 0))
    if not filename:
        return None
    path = data_path / 'chara' / 'chara_icon' / filename
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


# 歌曲演奏者信息
def _vocalimg(musicid, alpha, pjsk_type: int = 0):
    if alpha:
        color = (255, 255, 255)
    else:
        color = (67, 70, 101)
    musicVocals = load_master_data('musicVocals.json', pjsk_type)
    outsideCharacters = load_master_data('outsideCharacters.json', pjsk_type)
    pos = 20
    row = 0
    height = [20, 92, 164, 236, 308]
    cut = [0, 0]
    vs = 0
    sekai = 0
    noan = True

    for vocal in musicVocals:
        if vocal['musicId'] == musicid:
            if vocal['musicVocalType'] == "original_song":
                vs += 1
            elif vocal['musicVocalType'] == "sekai":
                sekai += 1
            elif vocal['musicVocalType'] == "virtual_singer":
                vs += 1
            elif vocal['musicVocalType'] == "instrumental":
                img = open_pjsk_image(data_path / 'pics/inst.png')
                return img
            else:
                noan = False
                break
    if vs > 1:
        noan = False

    if noan:
        font_style = get_pjsk_font("SourceHanSansCN-Bold.otf", 35)
        img = open_pjsk_image(data_path / 'pics/vocal.png')
        if vs == 0:
            draw = ImageDraw.Draw(img)
            draw.text((220, 102), 'SEKAI Ver. ONLY', fill=(227, 246, 251), font=font_style)
        if sekai == 0:
            draw = ImageDraw.Draw(img)
            draw.text((165, 257), 'Virtual Singer Ver. ONLY', fill=(227, 246, 251), font=font_style)
        for vocal in musicVocals:
            if vocal['musicId'] == musicid:
                vocalimg = Image.new('RGBA', (750, 85), color=(0, 0, 0, 0))
                draw = ImageDraw.Draw(vocalimg)
                innerpos = 0
                for chara in vocal['characters']:
                    if chara['characterType'] == 'game_character':
                        chara = open_pjsk_image(
                            # 角色头像目前可能通用？如果是服务器特定的，可能需要 pjsk_type
                            data_path / f'chara/chr_ts_{chara["characterId"]}.png'
                        ).resize((70, 70))
                        r, g, b, mask = chara.split()
                        vocalimg.paste(chara, (innerpos + 5, 8), mask)
                        innerpos += 80
                    else:
                        try:
                            chara = open_pjsk_image(
                                data_path / f'chara/outsideCharacters/{chara["characterId"]}.png'
                            ).resize((70, 70))
                            r, g, b, mask = chara.split()
                            vocalimg.paste(chara, (innerpos + 5, 8), mask)
                            innerpos += 80
                        except:
                            for i in outsideCharacters:
                                if i['id'] == chara['characterId']:
                                    draw.text((innerpos + 8, 20), i['name'], fill=(67, 70, 101), font=font_style)
                                    innerpos += 8 + font_style.getsize(str(i['name']))[0]
                vocalimg = vocalimg.crop((0, 0, innerpos + 15, 150))
                r, g, b, mask = vocalimg.split()
                if vocal['musicVocalType'] == "original_song" or vocal['musicVocalType'] == "virtual_singer":
                    img.paste(vocalimg, (370 - int(vocalimg.size[0] / 2), 162 - int(vocalimg.size[1] / 2)), mask)
                elif vocal['musicVocalType'] == "sekai":
                    img.paste(vocalimg, (370 - int(vocalimg.size[0] / 2), 317 - int(vocalimg.size[1] / 2)), mask)
    else:
        font_style = get_pjsk_font("SourceHanSansCN-Bold.otf", 27)
        img = Image.new('RGBA', (720, 380), color=(0, 0, 0, 0))
        for vocal in musicVocals:
            if vocal['musicId'] == musicid:
                vocalimg = Image.new('RGBA', (700, 70), color=(0, 0, 0, 0))
                draw = ImageDraw.Draw(vocalimg)
                if vocal['musicVocalType'] == "original_song":
                    text = '原曲版'
                elif vocal['musicVocalType'] == "sekai":
                    text = 'SEKAI版'
                elif vocal['musicVocalType'] == "virtual_singer":
                    text = 'V版'
                elif vocal['musicVocalType'] == "april_fool_2022":
                    text = '2022愚人节版'
                elif vocal['musicVocalType'] == "another_vocal":
                    text = '其他'
                elif vocal['musicVocalType'] == "instrumental":
                    text = '无人声伴奏'
                else:
                    text = vocal['musicVocalType']
                innerpos = 25 + font_style.getsize(str(text))[0]
                draw.text((20, 20), text, fill=color, font=font_style)
                for chara in vocal['characters']:
                    if chara['characterType'] == 'game_character':
                        chara = open_pjsk_image(data_path / f'chara/chr_ts_{chara["characterId"]}.png').resize((60, 60))
                        r, g, b, mask = chara.split()
                        vocalimg.paste(chara, (innerpos + 5, 8), mask)
                        innerpos += 65
                    else:
                        try:
                            chara = open_pjsk_image(data_path / f'chara/outsideCharacters/{chara["characterId"]}.png').resize((60, 60))
                            r, g, b, mask = chara.split()
                            vocalimg.paste(chara, (innerpos + 5, 8), mask)
                            innerpos += 65
                        except:
                            for i in outsideCharacters:
                                if i['id'] == chara['characterId']:
                                    draw.text((innerpos + 8, 20), i['name'], fill=(67, 70, 101), font=font_style)
                                    innerpos += 8 + font_style.getsize(str(i['name']))[0]
                vocalimg = vocalimg.crop((0, 0, innerpos + 15, 72))
                r, g, b, mask = vocalimg.split()

                if pos + vocalimg.size[0] > 720:
                    pos = 20
                    row += 1
                img.paste(vocalimg, (pos, height[row]), mask)
                if pos + vocalimg.size[0] > cut[0]:
                    cut[0] = pos + vocalimg.size[0]
                pos += vocalimg.size[0]
                if (vocal['musicVocalType'] == "sekai" or vocal['musicVocalType'] == "original_song"
                    or vocal['musicVocalType'] == "virtual_singer") and pos != 20:
                    pos = 20
                    row += 1
        if pos == 20:
            row -= 1
        cut[1] = height[row] + 65
        img = img.crop((0, 0, cut[0] + 10, cut[1] + 10))
    return img


# 歌曲长度
async def _musiclength(musicid, fillerSec=0, pjsk_type: int = 0):
    try:
        data = await async_load_master_data('musicVocals.json', pjsk_type)
        for vocal in data:
            if vocal['musicId'] == musicid:
                path = f'ondemand/music/long/{vocal["assetbundleName"]}'
                file = f'{vocal["assetbundleName"]}.mp3'
                # 这里假设其它服务器也支持 assets 更新
                await pjsk_update_manager.update_assets(path, file, pjsk_type=pjsk_type)
                audio = MP3(rf'{data_path / SERVER_MAP.get(pjsk_type, "jp") / path / file}')
                return audio.info.length - fillerSec
        return 0
    except Exception as e:
        logger.warning(f'获取歌曲长度失败，Error：{e}')
        return 0


# 歌曲详情
async def _drawpjskinfo(musicid: int, pjsk_type: int = 0) -> Tuple[bool, str]:
    server_name = SERVER_MAP.get(pjsk_type, 'jp')
    save_path = data_path / server_name / 'pics' / 'pjskinfo'
    save_path.mkdir(parents=True, exist_ok=True)

    info = MusicInfo()
    data = await async_load_master_data('musics.json', pjsk_type)
    for music in data:
        if music['id'] != musicid:
            continue
        info.title = music['title']
        info.lyricist = music['lyricist']
        info.composer = music['composer']
        info.arranger = music['arranger']
        info.publishedAt = music['publishedAt']
        info.fillerSec = music['fillerSec']
        info.categories = music['categories']

    data = await async_load_master_data('musicDifficulties.json', pjsk_type)
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
        pjsk_update_manager.get_asset(
            fr'startapp/music/jacket/jacket_s_{str(musicid).zfill(3)}',
            f'jacket_s_{str(musicid).zfill(3)}.png',
            pjsk_type=pjsk_type,
        ),
        _musiclength(musicid, info.fillerSec, pjsk_type=pjsk_type),
    )

    return await run_pjsk_thread(_compose_pjskinfo, musicid, pjsk_type, info, jacket, leak, save_path)


def _compose_pjskinfo(musicid, pjsk_type, info, jacket, leak, save_path) -> Tuple[bool, str]:
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
    server_name = SERVER_MAP.get(pjsk_type, 'jp').upper()
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
        icon_path = data_path / f'pics/{icon_type}.png'
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
    img.save(save_path / f'pjskinfo_v{PJSKINFO_CACHE_VERSION}_{musicid}.png')
    return leak, pic2b64(img)


# 歌曲详情入口
async def info(musicid, pjsk_type: int = 0) -> Tuple[bool, str]:
    server_name = SERVER_MAP.get(pjsk_type, 'jp')
    path = data_path / server_name / 'pics' / 'pjskinfo' / f'pjskinfo_v{PJSKINFO_CACHE_VERSION}_{musicid}.png'
    if path.exists():
        pjskinfotime = datetime.datetime.fromtimestamp(os.path.getmtime(path))
        
        diff_path = data_path / server_name / 'realtime' / 'musicDifficulties.json'
        if not diff_path.exists():
            diff_path = data_path / server_name / 'musicDifficulties.json'
            
        playdatatime = datetime.datetime.fromtimestamp(os.path.getmtime(diff_path))
        musics = await async_load_master_data('musics.json', pjsk_type)
        for i in musics:
            if i['id'] == musicid:
                publishedAt = i['publishedAt'] / 1000
                break
        else:
            raise IndexError('找不到对应曲目')
        if pjskinfotime > playdatatime:  # 缓存后数据未变化
            if time.time() < publishedAt:  # 偷跑
                return True, ""
            else:  # 已上线
                if pjskinfotime.timestamp() < publishedAt:  # 缓存是上线前的
                    return await _drawpjskinfo(musicid, pjsk_type)
                return False, ""
        else:
            return await _drawpjskinfo(musicid, pjsk_type)
    else:
        return await _drawpjskinfo(musicid, pjsk_type)


# PJSK 进度图表
def jinduChart(score):
    try:
        del score['33+musicId']
    except KeyError:
        pass

    delLevel = []
    for level in score:
        if score[level][3] == 0:
            delLevel.append(level)

    for level in delLevel:
        del score[level]

    pic = Image.new("RGBA", (50 + 40 * len(score), 220), (0, 0, 0, 0))
    i = 0

    font = get_pjsk_font("SourceHanSansCN-Bold.otf", 18)
    draw = ImageDraw.Draw(pic)
    for level in score:
        draw.text((34 + 40 * i, 185), str(level), (0, 0, 0), font)

        # 画总曲数
        draw.rectangle((28 + 40 * i, 40, 60 + 40 * i, 180), fill=(68, 68, 102))
        w = int(font.getsize(str(score[level][3]))[0] / 2)
        draw.text(
            (43 + 40 * i - w, 12), str(score[level][3]), (68, 68, 102), font,
            stroke_width=2, stroke_fill=(255, 255, 255)
        )

        # Clear
        ratio = score[level][2] / score[level][3]
        draw.rectangle((28 + 40 * i, 180 - int(140 * ratio), 60 + 40 * i, 180), fill=(255, 183, 77))
        if score[level][2] != 0:
            w = int(font.getsize(str(score[level][2]))[0] / 2)
            draw.text(
                (43 + 40 * i - w, 152 - int(140 * ratio)), str(score[level][2]), (255, 183, 77), font,
                stroke_width=2, stroke_fill=(255, 255, 255)
            )

        # FC
        ratio = score[level][1] / score[level][3]
        draw.rectangle((28 + 40 * i, 180 - int(140 * ratio), 60 + 40 * i, 180), fill=(240, 98, 146))
        if score[level][1] != 0:
            w = int(font.getsize(str(score[level][1]))[0] / 2)
            draw.text(
                (43 + 40 * i - w, 152 - int(140 * ratio)), str(score[level][1]), (240, 98, 146), font,
                stroke_width=2, stroke_fill=(255, 255, 255)
            )

        # AP
        ratio = score[level][0] / score[level][3]
        draw.rectangle((28 + 40 * i, 180 - int(140 * ratio), 60 + 40 * i, 180), fill=(251, 217, 221))
        if score[level][0] != 0:
            w = int(font.getsize(str(score[level][0]))[0] / 2)
            draw.text(
                (43 + 40 * i - w, 152 - int(140 * ratio)), str(score[level][0]), (100, 181, 246), font,
                stroke_width=2, stroke_fill=(255, 255, 255)
            )

        i += 1
    return pic




# 歌曲 BPM
async def parse_bpm(music_id, pjsk_type: int = 0):
    try:
        server_name = SERVER_MAP.get(pjsk_type, 'jp')
        await pjsk_update_manager.update_assets(rf'startapp/music/music_score/{music_id:04d}_01', 'expert', pjsk_type=pjsk_type)

        with open(
            data_path / server_name / rf'startapp/music/music_score/{music_id:04d}_01/expert', encoding='utf-8'
        ) as f:
            r = f.read()
    except FileNotFoundError:
        return 0, [{'time': 0.0, 'bpm': '无数据'}], 0

    score = {}
    max_time = 0
    for line in r.split('\n'):
        match: re.Match = re.match(r'#(...)(...?)\s*\:\s*(\S*)', line)
        if match:
            time, key, value = match.groups()
            score[(time, key)] = value
            if time.isdigit():
                max_time = max(max_time, int(time) + 1)

    bpm_palette = {}
    for time, key in score:
        if time == 'BPM':
            bpm_palette[key] = float(score[(time, key)])

    bpm_events = {}
    for time, key in score:
        if time.isdigit() and key == '08':
            value = score[(time, key)]
            length = len(value) // 2

            for i in range(length):
                bpm_key = value[i * 2:(i + 1) * 2]
                if bpm_key == '00':
                    continue
                bpm = bpm_palette[bpm_key]
                t = int(time) + i / length
                bpm_events[t] = bpm

    bpm_sequence = [{
        'time': time,
        'bpm': bpm,
    } for time, bpm in sorted(bpm_events.items())]

    for i in range(len(bpm_sequence)):
        if i > 0 and bpm_sequence[i]['bpm'] == bpm_sequence[i - 1]['bpm']:
            bpm_sequence[i]['deleted'] = True

    bpm_sequence = [bpm_event for bpm_event in bpm_sequence if bpm_event.get('deleted') != True]

    bpms = {}
    for i in range(len(bpm_sequence)):
        bpm = bpm_sequence[i]['bpm']
        if bpm not in bpms:
            bpms[bpm] = 0.0

        if i + 1 < len(bpm_sequence):
            bpms[bpm] += (bpm_sequence[i + 1]['time'] - bpm_sequence[i]['time']) / bpm
        else:
            bpms[bpm] += (max_time - bpm_sequence[i]['time']) / bpm

    sorted_bpms = sorted([(bpms[bpm], bpm) for bpm in bpms], reverse=True)
    mean_bpm = sorted_bpms[0][1]

    return mean_bpm, bpm_sequence, max_time


# 歌曲标题
def idtoname(musicid, musics=None, pjsk_type: int = 0):
    if musics is None:
        musics = load_master_data('musics.json', pjsk_type)
    for i in musics:
        if i['id'] == musicid:
            return i['title']
    return ""
