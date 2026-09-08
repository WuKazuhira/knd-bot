"""烧烤 b30 出图。

原 plugins/pjsk/b30/__init__.py 的绘制部分。指令侧负责算定数与权重，
本模块只把结果画成图。
"""

from __future__ import annotations

import asyncio
from typing import Dict, Tuple

from PIL import Image, ImageDraw, ImageFont

from services.log import logger
from utils.pjsk_paths import STATIC_PATH

from ..context import get_context
from ..primitives import (
    get_pjsk_asset_cached,
    get_pjsk_font,
    image_to_jpeg,
    open_pjsk_image,
    run_pjsk_thread,
)
from ..profile_header import PjskHeaderData, draw_pjsk_profile_header
from ..registry import register

static_path = STATIC_PATH

_IMAGE_CACHE: Dict[str, Image.Image] = {}
B30_TASK_LIMIT = 8


def _get_font(font_name: str, size: int) -> ImageFont.FreeTypeFont:
    return get_pjsk_font(font_name, size)


def _get_cached_image(name: str) -> Image.Image:
    img = _IMAGE_CACHE.get(name)
    if img is None:
        img = open_pjsk_image(static_path / 'pics' / name, mode='RGBA')
        _IMAGE_CACHE[name] = img
    return img.copy()


def _gradient_bg(width: int, height: int) -> Image.Image:
    top = (255, 246, 250)
    bottom = (236, 244, 255)
    # 1x2 渐变条 + 双线性放大，避免逐行画线（1800 行 ≈ 数十毫秒）
    strip = Image.new("RGB", (1, 2))
    strip.putpixel((0, 0), top)
    strip.putpixel((0, 1), bottom)
    img = strip.resize((width, height), Image.Resampling.BILINEAR)
    glow = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    gd.ellipse((-width // 4, -height // 8, width // 2, height // 4), fill=(255, 190, 220, 70))
    gd.ellipse((width // 2, height // 4, width + width // 5, height + height // 6), fill=(170, 210, 255, 58))
    img.paste(glow, (0, 0), glow.split()[-1])
    return img


def _panel(base: Image.Image, xy, radius: int = 24, fill=(255, 255, 255, 218), outline=(255, 255, 255, 232)):
    overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    od.rounded_rectangle(xy, radius=radius, fill=fill, outline=outline)
    base.paste(overlay, (0, 0), overlay.split()[-1])


def _fit_text(draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> str:
    text = str(text or '')
    if draw.textlength(text, font=font) <= max_width:
        return text
    while text and draw.textlength(text + '…', font=font) > max_width:
        text = text[:-1]
    return text + '…' if text else '…'


def _paste_round(base: Image.Image, img: Image.Image, xy: Tuple[int, int], size: Tuple[int, int], radius: int = 18):
    img = img.convert('RGBA').resize(size, Image.Resampling.LANCZOS)
    mask = Image.new('L', size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, size[0], size[1]), radius=radius, fill=255)
    base.paste(img, xy, mask)


def _build_music_title_map(musics) -> Dict[int, str]:
    return {
        music.get('id'): music.get('title', '')
        for music in musics
        if isinstance(music, dict) and music.get('id') is not None
    }


def _build_card_asset_map(cards) -> Dict[int, str]:
    return {
        card.get('id'): card.get('assetbundleName', '')
        for card in cards
        if isinstance(card, dict) and card.get('id') is not None
    }


async def b30single(diff, music_title_map: Dict[int, str], pjsk_type: int = 0):
    try:
        jacket = await get_pjsk_asset_cached(
            'startapp/thumbnail/music_jacket',
            f'jacket_s_{str(diff["musicId"]).zfill(3)}.png',
            pjsk_type=pjsk_type,
            mode='RGBA',
            size=(100, 100),
        )
    except Exception:
        jacket = None
    return await run_pjsk_thread(_b30single_sync, diff, music_title_map, jacket)


def _b30single_sync(diff, music_title_map: Dict[int, str], jacket):
    color = {
        'master': (187, 51, 238),
        'expert': (238, 67, 102),
        'hard': (254, 170, 0),
        'normal': (51, 187, 238),
        'easy': (102, 221, 17),
    }
    musictitle = music_title_map.get(diff['musicId'], '') or f"Music {diff['musicId']}"
    accent = color.get(diff['musicDifficulty'], (230, 140, 170))
    pic = Image.new("RGBA", (310, 120), (0, 0, 0, 0))
    draw = ImageDraw.Draw(pic)
    draw.rounded_rectangle((0, 0, 310, 120), radius=18, fill=(255, 255, 255, 236), outline=(255, 255, 255, 255))

    if jacket is not None:
        _paste_round(pic, jacket, (10, 10), (100, 100), radius=16)
    else:
        draw.rounded_rectangle((10, 10, 110, 110), radius=16, fill=(235, 235, 245))
        draw.text((60, 60), "♪", fill=(160, 150, 180), font=_get_font('SourceHanSansCN-Bold.otf', 38), anchor="mm")

    draw.rounded_rectangle((124, 14, 178, 42), radius=14, fill=accent)
    draw.text((151, 27), str(diff['playLevel']), fill=(255, 255, 255), font=_get_font('SourceHanSansCN-Bold.otf', 20), anchor="mm")
    draw.text((186, 16), diff['musicDifficulty'].upper(), fill=accent, font=_get_font('FOT-RodinNTLGPro-DB.ttf', 14))

    font_title = _get_font('SourceHanSansCN-Bold.otf', 18)
    draw.text((124, 48), _fit_text(draw, musictitle, font_title, 170), fill=(42, 32, 48), font=font_title)

    result_text = 'AP' if diff.get('result') == 2 else 'FC' if diff.get('result') == 1 else '--'
    result_fill = (255, 110, 170) if result_text == 'AP' else (85, 170, 245)
    draw.rounded_rectangle((124, 82, 170, 108), radius=13, fill=result_fill)
    draw.text((147, 94), result_text, fill=(255, 255, 255), font=_get_font('FOT-RodinNTLGPro-DB.ttf', 15), anchor="mm")
    base_const = diff.get('aplevel+', diff.get('playLevel', 0))
    weight = diff.get('rank') or 0
    const_text = f"{base_const:.1f}→{weight:.1f}"
    draw.text((182, 80), const_text, fill=(70, 52, 78), font=_get_font('SourceHanSansCN-Bold.otf', 17))
    draw.text((182, 101), "constant weight", fill=(142, 118, 150), font=_get_font('FOT-RodinNTLGPro-DB.ttf', 10))
    return pic


@register("b30")
async def render_b30(payload: dict) -> bytes:
    """B30 成绩图。载荷：

    - diff:              已排序的前 30 条谱面数据（含 result / rank / aplevel+ / playLevel）
    - highest:           当前理论值
    - header:            玩家信息 Header 数据
    - data_update_text:  非实时数据时左上角的更新时间提示（可为空）
    - pjsk_type:         服务器
    """
    pjsk_type = int(payload.get("pjsk_type", 0))
    ctx = get_context()
    diff = payload.get("diff") or []
    highest = payload.get("highest") or 0

    cards = await ctx.async_load_master_data('cards.json', pjsk_type)
    musics = await ctx.async_load_master_data('musics.json', pjsk_type)
    card_asset_map = _build_card_asset_map(cards)
    music_title_map = _build_music_title_map(musics)

    pic = _gradient_bg(1120, 1810)
    draw = ImageDraw.Draw(pic)
    _panel(pic, (36, 330, 1084, 1668), radius=28, fill=(255, 255, 255, 132), outline=(255, 255, 255, 210))

    rank = 0
    valid_count = 0
    b30_tasks = [b30single(item, music_title_map, pjsk_type=pjsk_type) for item in diff[:30]]
    if b30_tasks:
        sem = asyncio.Semaphore(B30_TASK_LIMIT)

        async def _limited(task_coro):
            async with sem:
                return await task_coro

        b30_results = await asyncio.gather(*[_limited(task) for task in b30_tasks], return_exceptions=True)
        for i, single in enumerate(b30_results):
            if isinstance(single, Exception):
                logger.error(f"Error generating b30 single {i}: {single}")
                continue
            valid_count += 1
            rank = rank + (diff[i].get('rank') or 0)
            pic.paste(single, ((int(53 + (i % 3) * 342)), int(356 + int(i / 3) * 130)), single.split()[-1])

    rank = round(rank / valid_count, 2) if valid_count else 0
    await draw_pjsk_profile_header(
        pic,
        (36, 28, 1084, 286),
        PjskHeaderData.from_payload(payload.get("header")),
        module_label="BEST 30 REPORT",
        pjsk_type=pjsk_type,
        card_asset_map=card_asset_map,
        extra_badges=[("B30", str(rank))],
    )

    font_style = _get_font('SourceHanSansCN-Medium.otf', 15)
    draw.text((50, 1716), f'注：33+FC权重减1，其他减1.5，非官方算法，仅供参考娱乐，当前理论值为{highest}', fill=(92, 72, 98),
              font=font_style)
    draw.text((50, 1742), '※定数非官方 仅供参考娱乐 请勿当真', fill=(130, 104, 138),
              font=font_style)
    draw.text((1070, 1744), "BEST 30", fill=(120, 80, 100), font=_get_font('FOT-RodinNTLGPro-DB.ttf', 18), anchor="rm")

    update_text = payload.get("data_update_text")
    if update_text:
        draw.text((68, 20), update_text, fill=(100, 100, 100), font=_get_font('SourceHanSansCN-Bold.otf', 25))

    return await run_pjsk_thread(image_to_jpeg, pic.convert("RGB"), quality=90)
