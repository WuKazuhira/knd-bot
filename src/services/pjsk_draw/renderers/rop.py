"""烧烤收歌进度（rop）出图。

原 plugins/pjsk/rop/__init__.py 的绘制部分，附带原 _song_utils.jinduChart。
"""

from __future__ import annotations

from typing import Dict, Optional

from PIL import Image, ImageDraw

from ..context import get_context
from ..primitives import (
    get_pjsk_font,
    image_to_jpeg,
    run_pjsk_thread,
    vertical_gradient,
)
from ..profile_header import PjskHeaderData, draw_pjsk_profile_header
from ..registry import register


def _score_by_level(raw: Optional[dict]) -> Dict[int, list]:
    """载荷里的等级键经过 JSON 会变成字符串，这里还原成 int。"""
    result: Dict[int, list] = {}
    for key, value in (raw or {}).items():
        try:
            result[int(key)] = list(value)
        except (TypeError, ValueError):
            continue
    return result


def _rop_gradient_bg(width: int, height: int, diff: str) -> Image.Image:
    if diff == 'expert':
        top, bottom = (255, 246, 250), (255, 235, 242)
    else:
        top, bottom = (248, 246, 255), (236, 244, 255)
    img = vertical_gradient(width, height, top, bottom)
    return img


def _rop_panel(base: Image.Image, xy, radius: int = 24, fill=(255, 255, 255, 218), outline=(255, 255, 255, 232)):
    overlay = Image.new('RGBA', base.size, (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    od.rounded_rectangle(xy, radius=radius, fill=fill, outline=outline)
    base.paste(overlay, (0, 0), overlay.split()[-1])


def _draw_stat_bar(draw: ImageDraw.ImageDraw, xy, ratio: float, color):
    x1, y1, x2, y2 = xy
    ratio = max(0.0, min(1.0, ratio))
    draw.rounded_rectangle(xy, radius=(y2 - y1) // 2, fill=(238, 234, 246))
    if ratio > 0:
        draw.rounded_rectangle((x1, y1, x1 + int((x2 - x1) * ratio), y2), radius=(y2 - y1) // 2, fill=color)


def _draw_level_card(draw: ImageDraw.ImageDraw, level: int, values, xy):
    x, y, w, h = xy
    ap, fc, clear, total = values
    total = max(int(total or 0), 1)
    draw.rounded_rectangle((x, y, x + w, y + h), radius=18, fill=(255, 255, 255, 224), outline=(255, 255, 255, 245))
    draw.rounded_rectangle((x + 12, y + 13, x + 78, y + h - 13), radius=16, fill=(244, 238, 255), outline=(224, 214, 246))
    draw.text((x + 45, y + h // 2), f"Lv.{level}", fill=(74, 54, 86), font=get_pjsk_font("SourceHanSansCN-Bold.otf", 18), anchor="mm")

    compact = h < 62
    font_label = get_pjsk_font("FOT-RodinNTLGPro-DB.ttf", 10 if compact else 11)
    font_num = get_pjsk_font("SourceHanSansCN-Bold.otf", 13 if compact else 15)
    stats = [
        ("AP", ap, (228, 159, 251)),
        ("FC", fc, (254, 143, 249)),
        ("CLEAR", clear, (255, 199, 92)),
    ]
    sx = x + 92
    bar_x1 = x + 186
    bar_x2 = x + w - 16
    row_gap = 14 if compact else 18
    start_y = y + 7 if compact else y + 11
    bar_h = 7 if compact else 9
    for idx, (label, value, color) in enumerate(stats):
        yy = start_y + idx * row_gap
        draw.text((sx, yy + bar_h // 2), label, fill=(130, 104, 138), font=font_label, anchor="lm")
        draw.text((sx + 58, yy + bar_h // 2), f"{int(value)}/{total}", fill=(64, 48, 72), font=font_num, anchor="mm")
        _draw_stat_bar(draw, (bar_x1, yy, bar_x2, yy + bar_h), int(value or 0) / total, color)


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


@register("rop")
async def render_rop(payload: dict) -> bytes:
    """收歌进度图。载荷：

    - diff:              'master' 或 'expert'
    - score:             {等级: [ap, fc, clear, total]}
    - header:            玩家信息 Header 数据
    - data_update_text:  非实时数据时的更新时间提示（可为空）
    - pjsk_type:         服务器
    """
    diff = payload.get("diff") or 'master'
    pjsk_type = int(payload.get("pjsk_type", 0))
    score = _score_by_level(payload.get("score"))

    img = _rop_gradient_bg(1050, 1000, diff)
    _rop_panel(img, (36, 226, 1014, 710), radius=26, fill=(255, 255, 255, 148), outline=(255, 255, 255, 220))
    _rop_panel(img, (36, 728, 1014, 948), radius=26, fill=(255, 255, 255, 178), outline=(255, 255, 255, 220))

    cards_by_id = get_context().master_data_by_id('cards.json', pjsk_type)
    card_asset_map = {
        cid: card.get('assetbundleName', '')
        for cid, card in cards_by_id.items() if isinstance(card, dict)
    }
    title = "MASTER PROGRESS" if diff == 'master' else "EXPERT PROGRESS"
    await draw_pjsk_profile_header(
        img,
        (36, 28, 1014, 192),
        PjskHeaderData.from_payload(payload.get("header")),
        module_label=title,
        pjsk_type=pjsk_type,
        card_asset_map=card_asset_map,
        compact=True,
        show_cutout=False,
    )

    draw = ImageDraw.Draw(img)
    levelmin = 26 if diff == 'master' else 21

    draw.text((64, 238), "LEVEL PROGRESS", fill=(74, 54, 86), font=get_pjsk_font("FOT-RodinNTLGPro-DB.ttf", 18))
    draw.text((920, 238), "AP / FC / CLEAR", fill=(130, 104, 138), font=get_pjsk_font("FOT-RodinNTLGPro-DB.ttf", 13), anchor="ra")

    for i in range(0, 5):
        level = i + levelmin
        values = score.get(level, [0, 0, 0, 0])
        _draw_level_card(draw, level, values, (64, 266 + i * 82, 430, 68))

    secondRawCount = 7 if diff == 'master' else 6
    for i in range(0, secondRawCount):
        level = i + levelmin + 5
        values = score.get(level, [0, 0, 0, 0])
        _draw_level_card(draw, level, values, (556, 266 + i * 60, 430, 54))

    chart = jinduChart(dict(score))
    img.paste(chart, (58, 728), chart.split()[-1])
    draw.text((996, 918), "PROGRESS", fill=(120, 80, 100), font=get_pjsk_font("FOT-RodinNTLGPro-DB.ttf", 18), anchor="rm")

    update_text = payload.get("data_update_text")
    if update_text:
        draw.text((54, 960), update_text, fill=(92, 72, 98), font=get_pjsk_font("SourceHanSansCN-Bold.otf", 25))

    return await run_pjsk_thread(image_to_jpeg, img.convert("RGB"), quality=90)
