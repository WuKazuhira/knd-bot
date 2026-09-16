"""难度排行（diffrank）出图。

原 plugins/pjsk/diffrank/__init__.py 的绘制部分。
指令侧负责定数计算与筛选，这里只负责画。
"""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from typing import Dict, List, Tuple

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from config.path_config import FONT_PATH
from services.log import logger
from utils.pjsk_paths import STATIC_PATH

from ..context import get_context
from ..primitives import get_pjsk_music_jacket_cached, image_to_jpeg, run_pjsk_thread, vertical_gradient
from ..profile_header import PjskHeaderData, draw_pjsk_profile_header
from ..registry import register

static_path = STATIC_PATH


DIFFRANK_ASSET_LIMIT = 12
DIFFRANK_JACKET_CACHE_LIMIT = 256
_DIFFRANK_JACKET_CACHE: OrderedDict[Tuple[int, int], Image.Image] = OrderedDict()
_DIFFRANK_ICON_CACHE: Dict[str, Image.Image] = {}
_DIFFRANK_FONT_CACHE: Dict[Tuple[str, int], ImageFont.FreeTypeFont] = {}

DIFFRANK_BG_TOP = (255, 246, 250)
DIFFRANK_BG_BOTTOM = (236, 244, 255)
DIFFRANK_PANEL = (255, 255, 255, 226)
DIFFRANK_PANEL_STRONG = (255, 255, 255, 234)
DIFFRANK_LINE = (255, 255, 255, 245)
DIFFRANK_TEXT = (44, 36, 58)
DIFFRANK_MUTED = (118, 112, 132)
DIFFRANK_ACCENT = (0, 204, 187)
DIFFRANK_WARN = (225, 80, 96)
DIFFRANK_CANVAS_MIN_W = 760
DIFFRANK_PAD = 36
DIFFRANK_HEADER_H = 258
DIFFRANK_STATUS_HEADER_H = 150
DIFFRANK_FOOTER_H = 118


def _font(name: str, size: int) -> ImageFont.FreeTypeFont:
    key = (name, size)
    font = _DIFFRANK_FONT_CACHE.get(key)
    if font is None:
        font = ImageFont.truetype(str(FONT_PATH / name), size)
        _DIFFRANK_FONT_CACHE[key] = font
    return font


def _bold(size: int) -> ImageFont.FreeTypeFont:
    return _font('SourceHanSansCN-Bold.otf', size)


def _medium(size: int) -> ImageFont.FreeTypeFont:
    return _font('SourceHanSansCN-Medium.otf', size)


def _rodin(size: int) -> ImageFont.FreeTypeFont:
    return _font('FOT-RodinNTLGPro-DB.ttf', size)


def _get_font(size: int) -> ImageFont.FreeTypeFont:
    return _bold(size)


def _text_width(font, text: str) -> int:
    try:
        bbox = font.getbbox(str(text))
        return bbox[2] - bbox[0]
    except AttributeError:
        return font.getsize(str(text))[0]


def _truncate_text(draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> str:
    text = str(text or '')
    if draw.textlength(text, font=font) <= max_width:
        return text
    while text and draw.textlength(text + '…', font=font) > max_width:
        text = text[:-1]
    return text + '…' if text else '…'


def _make_gradient_background(width: int, height: int) -> Image.Image:
    img = vertical_gradient(width, height, DIFFRANK_BG_TOP, DIFFRANK_BG_BOTTOM)
    glow = Image.new('RGBA', (width, height), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    gd.ellipse((-width // 5, -height // 5, width // 2, height // 3), fill=(255, 190, 220, 76))
    gd.ellipse((width // 2, height // 4, width + width // 4, height + height // 5), fill=(170, 210, 255, 68))
    gd.ellipse((width // 3, -height // 7, width, height // 2), fill=(210, 190, 255, 34))
    img.paste(glow, (0, 0), glow.split()[-1])
    return img.convert('RGBA')


def _soft_shadow(size: Tuple[int, int], radius: int = 24, alpha: int = 52) -> Image.Image:
    shadow = Image.new('RGBA', size, (0, 0, 0, 0))
    d = ImageDraw.Draw(shadow)
    d.rounded_rectangle((0, 0, size[0] - 1, size[1] - 1), radius=radius, fill=(70, 55, 90, alpha))
    return shadow.filter(ImageFilter.GaussianBlur(10))


def _draw_round_panel(base: Image.Image, xy: Tuple[int, int, int, int], radius: int = 24,
                      fill=DIFFRANK_PANEL, outline=DIFFRANK_LINE, shadow: bool = True):
    x1, y1, x2, y2 = xy
    w, h = x2 - x1, y2 - y1
    if shadow:
        sh = _soft_shadow((w, h), radius=radius, alpha=48)
        base.paste(sh, (x1 + 4, y1 + 7), sh.split()[-1])
    panel = Image.new('RGBA', (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(panel)
    d.rounded_rectangle((0, 0, w - 1, h - 1), radius=radius, fill=fill, outline=outline, width=1 if outline else 0)
    base.paste(panel, (x1, y1), panel.split()[-1])


def _rounded_image(img: Image.Image, radius: int = 16) -> Image.Image:
    img = img.convert('RGBA')
    mask = Image.new('L', img.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, img.width - 1, img.height - 1), radius=radius, fill=255)
    out = Image.new('RGBA', img.size, (0, 0, 0, 0))
    out.paste(img, (0, 0), mask)
    return out


def _resize_jacket_sync(jacket: Image.Image) -> Image.Image:
    return jacket.convert('RGBA').resize((120, 120), Image.Resampling.LANCZOS)


async def _load_jacket(music_id: int, pjsk_type: int) -> Image.Image:
    cache_key = (pjsk_type, music_id)
    cached = _DIFFRANK_JACKET_CACHE.get(cache_key)
    if cached is not None:
        _DIFFRANK_JACKET_CACHE.move_to_end(cache_key)
        return cached.copy()

    jacket = await get_pjsk_music_jacket_cached(
        music_id,
        pjsk_type=pjsk_type,
        mode='RGBA',
        size=(120, 120),
        prefer_thumbnail=True,
    )
    asset_loaded = jacket is not None
    if jacket is None:
        jacket = Image.new('RGBA', (120, 120), (230, 230, 230, 255))
        ImageDraw.Draw(jacket).text((28, 48), str(music_id), fill=(80, 80, 80), font=_get_font(45))
    if asset_loaded:
        _DIFFRANK_JACKET_CACHE[cache_key] = jacket.copy()
    while len(_DIFFRANK_JACKET_CACHE) > DIFFRANK_JACKET_CACHE_LIMIT:
        _, stale = _DIFFRANK_JACKET_CACHE.popitem(last=False)
        stale.close()
    return jacket.copy()


async def _prefetch_jackets(music_ids, pjsk_type: int) -> Dict[int, Image.Image]:
    unique_ids = list(dict.fromkeys(music_ids))
    sem = asyncio.Semaphore(DIFFRANK_ASSET_LIMIT)

    async def _limited(mid: int):
        async with sem:
            try:
                return mid, await _load_jacket(mid, pjsk_type)
            except Exception as e:
                logger.warning(f"[diffrank] 加载歌曲封面失败 music_id={mid}: {e}")
                fallback = Image.new('RGBA', (120, 120), (230, 230, 230, 255))
                ImageDraw.Draw(fallback).text((28, 48), str(mid), fill=(80, 80, 80), font=_get_font(45))
                return mid, fallback

    results = await asyncio.gather(*(_limited(mid) for mid in unique_ids), return_exceptions=True)
    jackets = {}
    for result in results:
        if isinstance(result, Exception):
            continue
        mid, jacket = result
        jackets[mid] = jacket
    return jackets


def _get_result_icon(name: str) -> Image.Image:
    icon = _DIFFRANK_ICON_CACHE.get(name)
    if icon is None:
        icon = Image.open(static_path / f'pics/{name}').convert('RGBA')
        _DIFFRANK_ICON_CACHE[name] = icon
    return icon.copy()


async def singleLevelRankPic(musicData, difficulty, musicResult=None, oneRowCount=None, pjsk_type: int = 0):
    all_music_ids = [mid for ids in musicData.values() for mid in ids]
    jackets = await _prefetch_jackets(all_music_ids, pjsk_type)
    return await run_pjsk_thread(
        _single_level_rank_sync, musicData, difficulty, jackets, musicResult, oneRowCount
    )


def _single_level_rank_sync(musicData, difficulty, jackets, musicResult=None, oneRowCount=None):
    diff = {
        'easy': 0,
        'normal': 1,
        'hard': 2,
        'expert': 3,
        'master': 4
    }
    color = {
        'master': (187, 51, 238),
        'expert': (238, 67, 102),
        'hard': (254, 170, 0),
        'normal': (51, 187, 238),
        'easy': (102, 221, 17),
    }
    iconName = {
        0: 'icon_notClear.png',
        1: 'icon_clear.png',
        2: 'icon_fullCombo.png',
        3: 'icon_allPerfect.png',
    }
    cover_size = 96
    cover_gap = 14
    label_w = 110
    top_pad = 20
    bottom_pad = 22
    block_gap = 18
    rank_blocks = []
    max_block_w = 0

    auto_row_count = oneRowCount is None
    if auto_row_count:
        max_group_count = max((len(ids) for ids in musicData.values()), default=1)
        oneRowCount = max(1, min(4, max_group_count))
    else:
        oneRowCount = max(1, min(4, oneRowCount))

    for rank, music_ids in musicData.items():
        rows = int((len(music_ids) - 1) / oneRowCount) + 1
        block_w = label_w + 28 + oneRowCount * cover_size + max(0, oneRowCount - 1) * cover_gap + 26
        block_h = top_pad + rows * cover_size + max(0, rows - 1) * cover_gap + bottom_pad
        block = Image.new('RGBA', (block_w, block_h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(block)
        _draw_round_panel(block, (0, 0, block_w - 8, block_h - 8), radius=24, fill=DIFFRANK_PANEL_STRONG, outline=(255, 255, 255, 245), shadow=True)

        diff_color = color[difficulty]
        badge_x = 18
        badge_y = 22
        draw.rounded_rectangle((badge_x, badge_y, badge_x + 76, badge_y + 42), radius=21, fill=diff_color)
        draw.text((badge_x + 38, badge_y + 20), str(rank), fill=(255, 255, 255), font=_rodin(25), anchor='mm')
        draw.text((badge_x + 38, badge_y + 66), difficulty.upper(), fill=DIFFRANK_MUTED, font=_rodin(13), anchor='mm')

        start_x = label_w + 22
        for idx, musicId in enumerate(music_ids):
            row = idx // oneRowCount
            col = idx % oneRowCount
            x = start_x + col * (cover_size + cover_gap)
            y = top_pad + row * (cover_size + cover_gap)
            jacket = jackets.get(musicId)
            if jacket is None:
                jacket = Image.new('RGBA', (120, 120), (230, 230, 230, 255))
                ImageDraw.Draw(jacket).text((28, 48), str(musicId), fill=(80, 80, 80), font=_bold(40))
            jacket = _rounded_image(jacket.resize((cover_size, cover_size), Image.Resampling.LANCZOS), radius=15)
            draw.rounded_rectangle((x - 3, y - 3, x + cover_size + 3, y + cover_size + 3), radius=18, fill=(255, 255, 255, 235))
            block.paste(jacket, (x, y), jacket.split()[-1])
            if musicResult is not None:
                try:
                    icon = _get_result_icon(iconName[musicResult[musicId][diff[difficulty]]]).resize((28, 28), Image.Resampling.LANCZOS)
                    block.paste(icon, (x + cover_size - 25, y + cover_size - 25), icon.split()[-1])
                except Exception:
                    pass
        rank_blocks.append(block)
        max_block_w = max(max_block_w, block_w)

    column_gap = 22
    columns = 2 if len(rank_blocks) > 1 else 1
    rows = [rank_blocks[i:i + columns] for i in range(0, len(rank_blocks), columns)]
    canvas_w = max_block_w * columns + column_gap * (columns - 1)
    canvas_h = sum(max(block.height for block in row) for row in rows) + max(0, len(rows) - 1) * block_gap
    pic = Image.new('RGBA', (canvas_w, max(1, canvas_h)), (0, 0, 0, 0))
    y = 0
    for row in rows:
        row_h = max(block.height for block in row)
        for col, block in enumerate(row):
            x = col * (max_block_w + column_gap)
            pic.paste(block, (x, y), block.split()[-1])
        y += row_h + block_gap

    return pic


@register("diffrank")
async def render_diffrank(payload: dict) -> bytes:
    """难度排行图。载荷：

    - music_data:         {定数标签: [musicId, ...]}，按定数从高到低
    - difficulty:         easy/normal/hard/expert/master
    - music_result:       玩家成绩表 {musicId: [各难度状态]}，null 表示不显示成绩角标
    - one_row_count:      每行封面数，null 表示自动
    - title / mode_text:  标题与右侧模式徽标文本
    - header:             玩家信息 Header 数据，null 表示画「无数据」提示条
    - is_private:         无 Header 时用于区分提示语
    - server_label:       右上角服务器标记
    - constants_time_text / suite_update_text: 页脚两行时间文本
    - pjsk_type:          服务器
    """
    pjsk_type = int(payload.get("pjsk_type", 0))
    difficulty = payload.get("difficulty") or 'master'
    music_data: Dict[str, List[int]] = {
        str(key): [int(mid) for mid in value]
        for key, value in (payload.get("music_data") or {}).items()
    }
    raw_result = payload.get("music_result")
    music_result = None
    if raw_result is not None:
        music_result = {int(key): value for key, value in raw_result.items()}
    one_row_count = payload.get("one_row_count")
    title = (payload.get("title") or '').strip()
    mode_text = payload.get("mode_text") or 'CLEAR'
    header_payload = payload.get("header")
    server_label = (payload.get("server_label") or 'JP').upper()

    rankPic = await singleLevelRankPic(
        music_data, difficulty, music_result,
        oneRowCount=one_row_count, pjsk_type=pjsk_type,
    )

    has_profile_header = header_payload is not None
    header_h = DIFFRANK_HEADER_H if has_profile_header else DIFFRANK_STATUS_HEADER_H
    content_w = max(DIFFRANK_CANVAS_MIN_W - DIFFRANK_PAD * 2, rankPic.width)
    canvas_w = max(DIFFRANK_CANVAS_MIN_W, content_w + DIFFRANK_PAD * 2)
    rank_x = (canvas_w - rankPic.width) // 2
    title_y = header_h + 24
    rank_y = title_y + 86
    footer_y = rank_y + rankPic.height + 22
    canvas_h = footer_y + DIFFRANK_FOOTER_H + 26
    pic = _make_gradient_background(canvas_w, canvas_h)
    draw = ImageDraw.Draw(pic)

    cards = await get_context().async_load_master_data('cards.json', pjsk_type=pjsk_type)
    card_asset_map = {card.get('id'): card.get('assetbundleName', '') for card in cards if isinstance(card, dict)}

    if has_profile_header:
        await draw_pjsk_profile_header(
            pic,
            (DIFFRANK_PAD, 24, canvas_w - DIFFRANK_PAD, header_h - 10),
            PjskHeaderData.from_payload(header_payload),
            module_label='DIFFICULTY RANK',
            pjsk_type=pjsk_type,
            card_asset_map=card_asset_map,
            extra_badges=[('SERVER', server_label)],
            show_cutout=True,
        )
    else:
        _draw_round_panel(pic, (DIFFRANK_PAD, 20, canvas_w - DIFFRANK_PAD, header_h - 8), radius=26, fill=DIFFRANK_PANEL, outline=DIFFRANK_LINE, shadow=True)
        is_private = bool(payload.get("is_private"))
        status_title = '成绩已隐藏' if is_private else '数据已无法获取'
        status_tip = '发送“给看”可查看歌曲成绩' if is_private else '未读取到玩家打歌数据，将仅展示难度排序'
        icon_x = DIFFRANK_PAD + 58
        draw.rounded_rectangle((icon_x - 30, 46, icon_x + 30, 106), radius=18, fill=(255, 246, 251), outline=(245, 218, 232))
        draw.text((icon_x, 76), '♪', fill=DIFFRANK_ACCENT, font=_rodin(36), anchor='mm')
        draw.text((DIFFRANK_PAD + 112, 50), status_title, fill=DIFFRANK_TEXT, font=_bold(27), anchor='la')
        draw.text((DIFFRANK_PAD + 114, 92), status_tip, fill=DIFFRANK_MUTED, font=_medium(16), anchor='la')
        draw.rounded_rectangle((canvas_w - DIFFRANK_PAD - 144, 42, canvas_w - DIFFRANK_PAD - 24, 74), radius=16, fill=(88, 92, 118, 220))
        draw.text((canvas_w - DIFFRANK_PAD - 84, 58), server_label, fill=(255, 255, 255), font=_rodin(16), anchor='mm')
        draw.text((canvas_w - DIFFRANK_PAD - 24, header_h - 38), 'DIFFICULTY RANK', fill=DIFFRANK_MUTED, font=_rodin(16), anchor='ra')

    _draw_round_panel(pic, (DIFFRANK_PAD, title_y, canvas_w - DIFFRANK_PAD, title_y + 64), radius=24, fill=(255, 255, 255, 226), outline=DIFFRANK_LINE, shadow=True)
    draw.text((DIFFRANK_PAD + 28, title_y + 24), title, fill=DIFFRANK_TEXT, font=_bold(29), anchor='lm')
    draw.text((DIFFRANK_PAD + 30, title_y + 48), '按定数从高到低排列，定数非官方，仅供参考', fill=DIFFRANK_MUTED, font=_medium(13), anchor='lm')
    mode_x1 = DIFFRANK_PAD + 36 + min(520, _text_width(_bold(29), title) + 20)
    draw.rounded_rectangle((mode_x1, title_y + 13, mode_x1 + 94, title_y + 45), radius=16, fill=DIFFRANK_ACCENT)
    draw.text((mode_x1 + 47, title_y + 29), mode_text, fill=(255, 255, 255), font=_rodin(17), anchor='mm')

    pic.paste(rankPic, (rank_x, rank_y), rankPic.split()[-1])

    _draw_round_panel(pic, (DIFFRANK_PAD, footer_y, canvas_w - DIFFRANK_PAD, footer_y + DIFFRANK_FOOTER_H - 18), radius=22, fill=(255, 255, 255, 205), outline=DIFFRANK_LINE, shadow=True)
    draw.text((DIFFRANK_PAD + 24, footer_y + 26), '定数来源：https://profile.pjsekai.moe/   ※三服共用 JP 定数，非官方', fill=DIFFRANK_ACCENT, font=_medium(17), anchor='lm')
    constants_time_text = payload.get("constants_time_text") or ''
    draw.text((DIFFRANK_PAD + 24, footer_y + 56), f'定数更新时间：{constants_time_text}   ※定数每次统计时可能会改变', fill=DIFFRANK_MUTED, font=_medium(15), anchor='lm')
    suite_update_text = payload.get("suite_update_text")
    if suite_update_text:
        draw.text((DIFFRANK_PAD + 24, footer_y + 82), f'用户数据上传时间：{suite_update_text}', fill=DIFFRANK_MUTED, font=_medium(15), anchor='lm')

    # 大图无透明，JPEG 编码比 PNG 快数倍、消息体积也小得多
    return await run_pjsk_thread(image_to_jpeg, pic.convert("RGB"), quality=90)
