"""烧烤查分（sk）系列出图。

原 plugins/pjsk/sk/__init__.py 的绘制部分：查分卡、榜线表、预测图、
预测曲线、cf/csb 统计图等。指令侧只负责取榜线与预测数据。
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from PIL import Image, ImageDraw, ImageFont

from config.path_config import FONT_PATH
from services import logger
from utils.pjsk_paths import STATIC_PATH

from ..context import get_context
from ..primitives import (
    get_pjsk_asset_cached,
    get_pjsk_font,
    image_to_jpeg,
    open_pjsk_image,
    run_pjsk_thread,
    vertical_gradient,
)
from ..registry import register

static_path = STATIC_PATH

# 与 plugins/pjsk/sk/_forecast_config.py 保持一致的展示用常量
# （只用于图上的文字与行序，预测逻辑本身仍在指令侧）
FORECAST_EXPIRE_HOURS = 3

FORECAST_SOURCE_NAMES = {
    'local': '本地预测',
    '33kit': '33Kit预测',
    'moe': 'Moesekai预测',
    'sekarun': 'SekaRun预测',
}

# 实时分数 & 时速显示的排名档位（表格行顺序）
LIVE_RANKS = [
    10, 20, 30, 40, 50, 100,
    200, 300, 400, 500,
    1000, 2000, 3000, 4000, 5000,
    10000,
]


def _source_name(source: str) -> str:
    return FORECAST_SOURCE_NAMES.get(source, source)


def _fmt_time_delta(ts: int) -> tuple:
    """返回 (时间文字, 是否过期)"""
    delta = datetime.now() - datetime.fromtimestamp(ts)
    secs = delta.total_seconds()
    if secs < 60:
        s = "刚刚"
    elif secs < 3600:
        s = f"{int(secs / 60)}分钟前"
    elif secs < 86400:
        s = f"{int(secs / 3600)}小时前"
    else:
        s = f"{int(secs / 86400)}天前"
    expired = secs > FORECAST_EXPIRE_HOURS * 3600
    return s, expired


class _RankingView:
    """ForecastRanking 的载荷视图。"""

    __slots__ = ('score', 'ts')

    def __init__(self, payload: dict):
        self.score = (payload or {}).get('score') or 0
        self.ts = (payload or {}).get('ts') or 0


class _RankForecastView:
    """RankForecastData 的载荷视图。"""

    __slots__ = ('final_score', 'history_final_score', 'future_rankings')

    def __init__(self, payload: dict):
        payload = payload or {}
        self.final_score = payload.get('final_score')
        history = payload.get('history_final_score')
        future = payload.get('future_rankings')
        self.history_final_score = [_RankingView(item) for item in history] if history else None
        self.future_rankings = [_RankingView(item) for item in future] if future else None


class ForecastView:
    """ForecastData 的载荷视图。

    指令侧用 dataclasses.asdict 把 ForecastData 拍平后放进载荷。
    """

    __slots__ = ('source', 'region', 'event_id', 'mtime', 'forecast_ts', 'rank_data')

    def __init__(self, payload: Optional[dict] = None):
        payload = payload or {}
        self.source = payload.get('source') or ''
        self.region = payload.get('region') or ''
        self.event_id = payload.get('event_id') or 0
        self.mtime = payload.get('mtime')
        self.forecast_ts = payload.get('forecast_ts')
        self.rank_data = {
            int(rank): _RankForecastView(data)
            for rank, data in (payload.get('rank_data') or {}).items()
        }


def _forecast_views(payload_list) -> List[ForecastView]:
    return [ForecastView(item) for item in (payload_list or [])]


def _int_key_map(raw) -> Dict[int, Any]:
    """JSON 的键都是字符串，这里还原成 int。"""
    result: Dict[int, Any] = {}
    for key, value in (raw or {}).items():
        try:
            result[int(key)] = value
        except (TypeError, ValueError):
            continue
    return result


def _history_map(raw) -> Dict[int, List[tuple]]:
    result: Dict[int, List[tuple]] = {}
    for rank, points in _int_key_map(raw).items():
        result[rank] = [tuple(point) for point in points or []]
    return result


def _sk_font(path_or_name, size: int):

    path_text = str(path_or_name)
    name = getattr(path_or_name, 'name', None) or path_text.split('/')[-1]
    return get_pjsk_font(str(name), size)


def _sk_gradient_bg(width: int, height: int) -> Image.Image:
    """SK 出图用柔和粉紫渐变背景。"""
    top = (255, 246, 250)
    bottom = (236, 244, 255)
    img = vertical_gradient(width, height, top, bottom)
    glow = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    gd.ellipse((-width // 5, -height // 4, width // 2, height // 3), fill=(255, 190, 220, 62))
    gd.ellipse((width // 2, height // 4, width + width // 4, height + height // 5), fill=(170, 210, 255, 54))
    img.paste(glow, (0, 0), glow.split()[3])
    return img


def _sk_panel(base: Image.Image, xy, radius: int = 18, fill=(255, 255, 255, 218), outline=(255, 255, 255, 220)):
    """在 RGB 画布上绘制半透明圆角面板。"""
    overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    od.rounded_rectangle(xy, radius=radius, fill=fill, outline=outline, width=1 if outline else 0)
    base.paste(overlay, (0, 0), overlay.split()[3])


def _sk_fit_text(draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> str:
    if draw.textlength(text, font=font) <= max_width:
        return text
    ellipsis = "…"
    while text and draw.textlength(text + ellipsis, font=font) > max_width:
        text = text[:-1]
    return text + ellipsis if text else ellipsis


def _sk_text_width(draw: ImageDraw.ImageDraw, text: str, font) -> int:
    return int(draw.textlength(str(text), font=font))


def _sk_chip(draw: ImageDraw.ImageDraw, xy, text: str, font, fill=(255, 255, 255), outline=(245, 218, 232), text_fill=(120, 80, 100), radius: int = 14, anchor: str = "mm"):
    x1, y1, x2, y2 = xy
    draw.rounded_rectangle(xy, radius=radius, fill=fill, outline=outline, width=1 if outline else 0)
    if anchor == "lm":
        pos = (x1 + 12, (y1 + y2) // 2)
    elif anchor == "rm":
        pos = (x2 - 12, (y1 + y2) // 2)
    else:
        pos = ((x1 + x2) // 2, (y1 + y2) // 2)
    draw.text(pos, str(text), font=font, fill=text_fill, anchor=anchor)


def _sk_title_panel(img: Image.Image, title: str, font, subtitle: str = "", subtitle_font=None, pad: int = 18, height: int = 58):
    d = ImageDraw.Draw(img)
    x1, y1, x2, y2 = pad, pad, img.width - pad, pad + height
    _sk_panel(img, (x1, y1, x2, y2), radius=22, fill=(255, 255, 255, 222), outline=(255, 255, 255, 230))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((x1 + 16, y1 + 15, x1 + 78, y1 + 21), radius=3, fill=(255, 128, 178))
    max_title_w = x2 - x1 - 44
    if subtitle and subtitle_font:
        max_title_w -= 150
    d.text((x1 + 18, y1 + height // 2 + 8), _sk_fit_text(d, title, font, max_title_w), font=font, fill=(50, 30, 50), anchor="lm")
    if subtitle and subtitle_font:
        _sk_chip(d, (x2 - 142, y1 + 16, x2 - 14, y1 + 44), subtitle, subtitle_font, fill=(255, 246, 251), text_fill=(120, 80, 100))


def _sk_draw_row(draw: ImageDraw.ImageDraw, xy, cells, col_widths, fonts, fills, bg=(255, 255, 255), outline=(255, 255, 255), radius: int = 14, pad_x: int = 14, align_right_from: int = -1):
    x1, y1, x2, y2 = xy
    draw.rounded_rectangle(xy, radius=radius, fill=bg, outline=outline, width=1 if outline else 0)
    x = x1
    for i, (cell, cw, font, fill) in enumerate(zip(cells, col_widths, fonts, fills)):
        if align_right_from >= 0 and i >= align_right_from:
            draw.text((x + cw - pad_x, (y1 + y2) // 2), str(cell), font=font, fill=fill, anchor="rm")
        else:
            draw.text((x + cw // 2, (y1 + y2) // 2), str(cell), font=font, fill=fill, anchor="mm")
        if i < len(col_widths) - 1:
            draw.rounded_rectangle((x + cw - 1, y1 + 8, x + cw + 1, y2 - 8), radius=1, fill=(235, 210, 226))
        x += cw


def compose_rank_table_image(title: str, ranks_data: List[dict], update_minutes_ago: int = 0, speed_header: str = "时速", speed_unit: str = "万/h") -> Image.Image:
    """用 PIL 绘制排名表格图片（用于时速、排名线等）"""
    font_path      = str(FONT_PATH / "SourceHanSansCN-Medium.otf")
    font_bold_path = str(FONT_PATH / "SourceHanSansCN-Bold.otf")
    f_title  = _sk_font(font_bold_path, 22)
    f_header = _sk_font(font_bold_path, 18)
    f_body   = _sk_font(font_path, 18)
    f_small  = _sk_font(font_path, 12)

    PAD_X    = 20
    ROW_H    = 40
    HEADER_H = 44
    TITLE_H  = 58
    FOOTER_H = 34
    OUT_PAD  = 18
    GAP      = 8

    _tmp = Image.new("RGB", (1, 1))
    _d   = ImageDraw.Draw(_tmp)

    def tw(text: str, font) -> int:
        return int(_d.textlength(text, font=font))

    col0_cands = [("排名", f_header)] + [(f"T{d['rank']}", f_body) for d in ranks_data]
    col0_w = max(tw(t, f) for t, f in col0_cands) + PAD_X * 2
    col1_cands = [("实时分数", f_header)] + [(f"{d['score'] / 10000:.2f}万" if d['score'] else "-", f_body) for d in ranks_data]
    col1_w = max(tw(t, f) for t, f in col1_cands) + PAD_X * 2
    col2_cands = [(speed_header, f_header)] + [(f"{d['speed']:.1f}{speed_unit}" if d['speed'] is not None else "-", f_body) for d in ranks_data]
    col2_w = max(tw(t, f) for t, f in col2_cands) + PAD_X * 2

    all_col_ws = [col0_w, col1_w, col2_w]
    table_w = sum(all_col_ws)
    content_w = max(table_w, tw(title, f_title) + PAD_X * 2, 520)
    total_w = content_w + OUT_PAD * 2
    total_h = OUT_PAD * 2 + TITLE_H + GAP + HEADER_H + GAP + ROW_H * len(ranks_data) + FOOTER_H

    C_HEAD_BG  = (230, 140, 170, 224)
    C_HEAD_FG  = (255, 255, 255)
    C_ROW_ODD  = (255, 255, 255, 224)
    C_ROW_EVEN = (255, 240, 248, 216)
    C_TEXT     = (50, 30, 50)
    C_MUTED    = (120, 80, 100)

    img = _sk_gradient_bg(total_w, total_h)
    d = ImageDraw.Draw(img)

    x0 = OUT_PAD
    x1 = total_w - OUT_PAD
    y = OUT_PAD

    _sk_panel(img, (x0, y, x1, y + TITLE_H), radius=22, fill=(255, 255, 255, 222))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((x0 + 18, y + 16, x0 + 80, y + 22), radius=3, fill=(255, 128, 178))
    d.text((x0 + 20, y + TITLE_H // 2 + 8), _sk_fit_text(d, title, f_title, content_w - 190), font=f_title, fill=C_TEXT, anchor="lm")
    update_text = f"{update_minutes_ago} 分钟前" if update_minutes_ago > 0 else "刚刚更新"
    d.rounded_rectangle((x1 - 132, y + 16, x1 - 14, y + 44), radius=14, fill=(255, 246, 251), outline=(245, 218, 232))
    d.text((x1 - 73, y + 30), update_text, font=f_small, fill=C_MUTED, anchor="mm")
    y += TITLE_H + GAP

    _sk_panel(img, (x0, y - 4, x1, total_h - OUT_PAD), radius=20, fill=(255, 255, 255, 132), outline=(255, 255, 255, 210))
    d = ImageDraw.Draw(img)

    def draw_row(row_y, cells, bg, fonts, fg_list, h=ROW_H):
        inset = 10
        d.rounded_rectangle((x0 + inset, row_y, x1 - inset, row_y + h - 2), radius=15, fill=bg, outline=(255, 255, 255, 180))
        widths = all_col_ws[:]
        widths[-1] += max(0, content_w - table_w - inset * 2)
        x = x0 + inset
        for i, (cell, font, fg) in enumerate(zip(cells, fonts, fg_list)):
            cw = widths[i]
            d.text((x + cw // 2, row_y + h // 2), cell, font=font, fill=fg, anchor="mm")
            if i < len(widths) - 1:
                d.rounded_rectangle((x + cw - 1, row_y + 8, x + cw + 1, row_y + h - 10), radius=1, fill=(235, 210, 226))
            x += cw

    draw_row(y, ["排名", "实时分数", speed_header], C_HEAD_BG, [f_header] * 3, [C_HEAD_FG] * 3, h=HEADER_H)
    y += HEADER_H + GAP

    for i, data in enumerate(ranks_data):
        bg = C_ROW_ODD if i % 2 == 0 else C_ROW_EVEN
        cells = [
            f"T{data['rank']}",
            f"{data['score'] / 10000:.2f}万" if data['score'] else "-",
            f"{data['speed']:.1f}{speed_unit}" if data['speed'] is not None else "-"
        ]
        draw_row(y, cells, bg, [f_body] * 3, [C_TEXT] * 3)
        y += ROW_H

    footer_text = f"数据更新于 {update_minutes_ago} 分钟前" if update_minutes_ago > 0 else "数据刚刚更新"
    d.text((x1 - 16, total_h - OUT_PAD - FOOTER_H // 2 + 2), footer_text, font=f_small, fill=C_MUTED, anchor="rm")
    return img


def _load_wl_chara_icon(cid: int, size: int = 36) -> Optional[Image.Image]:
    """加载 WL 表头角色头像。"""
    candidates = [
        static_path / 'chara' / f'chr_ts_90_{cid}.png',
        static_path / 'chara' / f'chr_ts_90_{cid}_2.png',
    ]
    for path in candidates:
        if path.exists():
            try:
                return Image.open(path).convert('RGBA').resize((size, size), Image.Resampling.LANCZOS)
            except Exception:
                pass
    return None


def compose_wl_rank_table_image(
    title: str,
    chapters: List[dict],
    rows: List[dict],
    update_minutes_ago: int = 0,
    value_mode: str = 'score',
    value_header: str = '分数',
    value_unit: str = '',
) -> Image.Image:
    """绘制 WL 总榜 + 各角色单榜横向表格。"""
    font_path      = str(FONT_PATH / "SourceHanSansCN-Medium.otf")
    font_bold_path = str(FONT_PATH / "SourceHanSansCN-Bold.otf")
    f_title  = _sk_font(font_bold_path, 22)
    f_header = _sk_font(font_bold_path, 16)
    f_body   = _sk_font(font_path, 16)
    f_small  = _sk_font(font_path, 12)

    OUT_PAD = 18
    TITLE_H = 58
    HEADER_H = 52
    ROW_H = 38
    FOOTER_H = 34
    GAP = 8
    RANK_W = 82
    TOTAL_W = 126
    CHAPTER_W = 104
    table_w = RANK_W + TOTAL_W + CHAPTER_W * len(chapters)
    content_w = max(table_w, 620)
    total_w = content_w + OUT_PAD * 2
    total_h = OUT_PAD * 2 + TITLE_H + GAP + HEADER_H + GAP + ROW_H * len(rows) + FOOTER_H

    C_HEAD_BG  = (230, 140, 170, 224)
    C_HEAD_FG  = (255, 255, 255)
    C_ROW_ODD  = (255, 255, 255, 224)
    C_ROW_EVEN = (255, 240, 248, 216)
    C_TEXT     = (50, 30, 50)
    C_MUTED    = (120, 80, 100)

    def fmt_value(item: Optional[dict]) -> str:
        if not item:
            return '-'
        if value_mode == 'speed':
            speed = item.get('speed')
            return f"{speed:.1f}{value_unit}" if speed is not None else '-'
        score = item.get('score')
        return f"{score / 10000:.2f}万" if score else '-'

    img = _sk_gradient_bg(total_w, total_h)
    _sk_title_panel(img, title, f_title, "WL总榜+单榜", f_small, pad=OUT_PAD, height=TITLE_H)
    d = ImageDraw.Draw(img)
    x0 = OUT_PAD
    x1 = total_w - OUT_PAD
    y = OUT_PAD + TITLE_H + GAP
    _sk_panel(img, (x0, y - 4, x1, total_h - OUT_PAD), radius=20, fill=(255, 255, 255, 140), outline=(255, 255, 255, 210))
    d = ImageDraw.Draw(img)

    col_widths = [RANK_W, TOTAL_W] + [CHAPTER_W] * len(chapters)
    table_x = x0 + max(0, (content_w - table_w) // 2)

    def draw_cell_text(text, x, yy, w, h, font, fill, anchor='mm'):
        d.text((x + w // 2, yy + h // 2), str(text), font=font, fill=fill, anchor=anchor)

    # header
    d.rounded_rectangle((table_x, y, table_x + table_w, y + HEADER_H - 2), radius=15, fill=C_HEAD_BG, outline=(255, 255, 255))
    x = table_x
    draw_cell_text('排名', x, y, RANK_W, HEADER_H, f_header, C_HEAD_FG); x += RANK_W
    draw_cell_text(f'总榜{value_header}', x, y, TOTAL_W, HEADER_H, f_header, C_HEAD_FG); x += TOTAL_W
    for chapter in chapters:
        icon = _load_wl_chara_icon(int(chapter.get('gameCharacterId', 0)), size=34)
        if icon:
            img.paste(icon, (x + (CHAPTER_W - icon.width) // 2, y + 6), icon.split()[3])
            d.text((x + CHAPTER_W // 2, y + 43), f"第{chapter.get('chapterNo')}章", font=f_small, fill=C_HEAD_FG, anchor='mm')
        else:
            draw_cell_text(f"第{chapter.get('chapterNo')}章", x, y, CHAPTER_W, HEADER_H, f_header, C_HEAD_FG)
        x += CHAPTER_W
    y += HEADER_H + GAP

    for idx, row in enumerate(rows):
        bg = C_ROW_ODD if idx % 2 == 0 else C_ROW_EVEN
        d.rounded_rectangle((table_x, y, table_x + table_w, y + ROW_H - 2), radius=14, fill=bg, outline=(255, 255, 255))
        x = table_x
        values = [f"T{row['rank']}", fmt_value(row.get('total'))]
        chapter_values = row.get('chapters') or {}
        for chapter in chapters:
            chapter_no = int(chapter.get('chapterNo', 0))
            cell = chapter_values.get(chapter_no)
            if cell is None:
                # Go 通过 JSON 传递 map[string]any，章节键会变成字符串；
                # Python 直传时则可能保留为整数键，因此两种格式都兼容。
                cell = chapter_values.get(str(chapter_no))
            values.append(fmt_value(cell))
        for value, w in zip(values, col_widths):
            draw_cell_text(value, x, y, w, ROW_H, f_body, C_TEXT)
            x += w
        y += ROW_H

    footer_text = f"数据更新于 {update_minutes_ago} 分钟前" if update_minutes_ago > 0 else "数据刚刚更新"
    d.text((x1 - 16, total_h - OUT_PAD - FOOTER_H // 2 + 2), footer_text, font=f_small, fill=C_MUTED, anchor="rm")
    return img


async def compose_wl_forecast_image(
    region: str,
    base_event_id: int,
    event_name: str,
    chapters: List[dict],
    total_forecasts: List['ForecastView'],
    chapter_forecasts: Dict[int, Optional['ForecastView']],
    live_scores: Dict[int, int],
    live_speeds: Dict[int, float],
    display_ranks: List[int],
) -> Image.Image:
    """绘制 WL 总榜预测 + 各章节单榜预测的横向表格图。"""
    font_path      = str(FONT_PATH / "SourceHanSansCN-Medium.otf")
    font_bold_path = str(FONT_PATH / "SourceHanSansCN-Bold.otf")
    f_title  = _sk_font(font_bold_path, 22)
    f_header = _sk_font(font_bold_path, 17)
    f_body   = _sk_font(font_path, 17)
    f_small  = _sk_font(font_path, 13)

    PAD_X    = 18
    ROW_H    = 40
    HEADER_H = 50
    TITLE_H  = 60
    FOOTER_H = 36
    OUT_PAD  = 18
    GAP      = 8
    ICON_SIZE = 32

    C_HEAD_BG   = (230, 140, 170)
    C_HEAD_FG   = (255, 255, 255)
    C_ROW_ODD   = (255, 255, 255)
    C_ROW_EVEN  = (255, 240, 248)
    C_TEXT      = (50, 30, 50)
    C_WARN      = (200, 60, 60)
    C_MUTED     = (120, 80, 100)
    C_WL_BG     = (200, 140, 200)  # 单榜列表头背景

    _tmp = Image.new("RGB", (1, 1))
    _d   = ImageDraw.Draw(_tmp)

    def tw(text: str, font) -> int:
        return int(_d.textlength(text, font=font))

    # --- 列宽计算 ---
    # col0: 排名
    col0_cands = [("排名", f_header)] + [(f"T{r}", f_body) for r in display_ranks]
    col0_w = max(tw(t, f) for t, f in col0_cands) + PAD_X * 2

    # col1/col2: 总榜当前分 / 时速
    score_strs = {r: (f"{s/10000:.2f}万" if s else "-") for r, s in live_scores.items()}
    speed_strs = {r: (f"{v:.1f}万/h" if v is not None else "-") for r, v in live_speeds.items()}
    col1_cands = [("当前分数", f_header)] + [(score_strs.get(r, "-"), f_body) for r in display_ranks]
    col1_w = max(tw(t, f) for t, f in col1_cands) + PAD_X * 2
    col2_cands = [("时速", f_header)] + [(speed_strs.get(r, "-"), f_body) for r in display_ranks]
    col2_w = max(tw(t, f) for t, f in col2_cands) + PAD_X * 2

    # 总榜预测列（可能多源）
    total_source_names = [_source_name(fc.source) for fc in total_forecasts]
    total_pred_col_ws: List[int] = []
    for idx, fc in enumerate(total_forecasts):
        cands = [(total_source_names[idx], f_header)]
        for rank in display_ranks:
            rd = fc.rank_data.get(rank)
            cands.append((f"{rd.final_score/10000:.2f}万" if (rd and rd.final_score) else "-", f_body))
        total_pred_col_ws.append(max(tw(t, f) for t, f in cands) + PAD_X * 2)

    # WL 章节预测列（每章一列，只有 local 来源）
    chapter_col_ws: List[int] = []
    chapter_list = sorted(chapters, key=lambda c: c.get('chapterNo', 0))
    for chapter in chapter_list:
        chapter_no = int(chapter.get('chapterNo', 0))
        fc = chapter_forecasts.get(chapter_no)
        cid = chapter.get('gameCharacterId', 0)
        col_header = f"第{chapter_no}章"
        cands = [(col_header, f_header)]
        for rank in display_ranks:
            rd = fc.rank_data.get(rank) if fc else None
            cands.append((f"{rd.final_score/10000:.2f}万" if (rd and rd.final_score) else "-", f_body))
        chapter_col_ws.append(max(tw(t, f) for t, f in cands) + PAD_X * 2)

    all_col_ws = [col0_w, col1_w, col2_w] + total_pred_col_ws + chapter_col_ws
    table_w = sum(all_col_ws)

    title_text = f"【{region.upper()}-{base_event_id}】{event_name}  WL榜线预测"
    content_w = max(table_w, tw(title_text, f_title) + PAD_X * 2, 700)
    extra_w = max(0, content_w - table_w)
    draw_col_ws = all_col_ws[:]
    draw_col_ws[-1] += extra_w

    total_w = content_w + OUT_PAD * 2
    # 行数：表头 + 数据行 + 时间行
    total_h = OUT_PAD * 2 + TITLE_H + GAP + HEADER_H + ROW_H * len(display_ranks) + HEADER_H + FOOTER_H + GAP * 2

    img = _sk_gradient_bg(total_w, total_h)
    _sk_title_panel(img, title_text, f_title, "WL榜线预测", f_small, pad=OUT_PAD, height=TITLE_H)
    d = ImageDraw.Draw(img)

    x0 = OUT_PAD
    x1 = total_w - OUT_PAD
    y  = OUT_PAD + TITLE_H + GAP
    _sk_panel(img, (x0, y - 4, x1, total_h - OUT_PAD), radius=20,
              fill=(255, 255, 255, 140), outline=(255, 255, 255, 210))
    d = ImageDraw.Draw(img)

    def draw_row(row_y, cells, bg, fonts, fg_list, h=ROW_H, right_from=1):
        _sk_draw_row(
            d,
            (x0 + 10, row_y, x1 - 10, row_y + h - 2),
            cells,
            draw_col_ws,
            fonts,
            fg_list,
            bg=bg,
            outline=(255, 255, 255),
            radius=14,
            pad_x=PAD_X,
            align_right_from=right_from,
        )

    # --- 表头行（含角色图标） ---
    # 先绘制纯色背景表头
    total_pred_count = len(total_forecasts)
    chapter_count = len(chapter_list)
    head_cells = (
        ["排名", "当前分数", "时速"]
        + total_source_names
        + [f"第{int(c.get('chapterNo',0))}章" for c in chapter_list]
    )
    # 总榜列用粉色表头，章节列用紫色背景区分
    head_bg_list = (
        [C_HEAD_BG] * (3 + total_pred_count)
        + [C_WL_BG] * chapter_count
    )
    # 由于 _sk_draw_row 只支持单一 bg，先画一整行粉色再局部覆盖章节列
    draw_row(y, head_cells, C_HEAD_BG,
             [f_header] * len(head_cells), [C_HEAD_FG] * len(head_cells),
             h=HEADER_H, right_from=-1)

    # 章节列背景覆盖（紫色）
    chapter_col_start_x = x0 + 10 + sum(draw_col_ws[:3 + total_pred_count])
    chapter_col_end_x   = x1 - 10
    if chapter_count:
        d.rounded_rectangle(
            (chapter_col_start_x, y, chapter_col_end_x, y + HEADER_H - 2),
            radius=14, fill=C_WL_BG,
        )
        d = ImageDraw.Draw(img)
        # 重新绘章节列文字及角色图标
        cx = chapter_col_start_x
        for idx, chapter in enumerate(chapter_list):
            chapter_no = int(chapter.get('chapterNo', 0))
            cid        = int(chapter.get('gameCharacterId', 0))
            cw         = draw_col_ws[3 + total_pred_count + idx]
            icon = _load_wl_chara_icon(cid, ICON_SIZE) if cid else None
            if icon:
                icon_x = cx + (cw - ICON_SIZE) // 2
                icon_y = y + (HEADER_H - ICON_SIZE) // 2 - 6
                img.paste(icon, (icon_x, icon_y), icon)
                d = ImageDraw.Draw(img)
                d.text((cx + cw // 2, y + HEADER_H - 10),
                       f"第{chapter_no}章", font=f_small,
                       fill=C_HEAD_FG, anchor="mm")
            else:
                d.text((cx + cw // 2, y + HEADER_H // 2),
                       f"第{chapter_no}章", font=f_header,
                       fill=C_HEAD_FG, anchor="mm")
            cx += cw
    y += HEADER_H

    # --- 数据行 ---
    for i, rank in enumerate(display_ranks):
        bg = C_ROW_ODD if i % 2 == 0 else C_ROW_EVEN
        cells = [f"T{rank}", score_strs.get(rank, "-"), speed_strs.get(rank, "-")]
        fgs   = [C_TEXT, C_TEXT, C_TEXT]
        for fc in total_forecasts:
            rd = fc.rank_data.get(rank)
            cells.append(f"{rd.final_score/10000:.2f}万" if (rd and rd.final_score) else "-")
            fgs.append(C_TEXT)
        for chapter in chapter_list:
            chapter_no = int(chapter.get('chapterNo', 0))
            fc = chapter_forecasts.get(chapter_no)
            rd = fc.rank_data.get(rank) if fc else None
            cells.append(f"{rd.final_score/10000:.2f}万" if (rd and rd.final_score) else "-")
            fgs.append(C_TEXT)
        draw_row(y, cells, bg, [f_body] * len(cells), fgs)
        y += ROW_H

    # --- 预测时间行 ---
    time_cells = ["预测时间", "-", "-"]
    time_fgs   = [C_HEAD_FG, C_HEAD_FG, C_HEAD_FG]
    for fc in total_forecasts:
        if fc and fc.forecast_ts:
            t_str, expired = _fmt_time_delta(fc.forecast_ts)
            time_cells.append(t_str + (" ⚠" if expired else ""))
            time_fgs.append(C_WARN if expired else C_HEAD_FG)
        else:
            time_cells.append("-")
            time_fgs.append(C_HEAD_FG)
    for chapter in chapter_list:
        chapter_no = int(chapter.get('chapterNo', 0))
        fc = chapter_forecasts.get(chapter_no)
        if fc and fc.forecast_ts:
            t_str, expired = _fmt_time_delta(fc.forecast_ts)
            time_cells.append(t_str + (" ⚠" if expired else ""))
            time_fgs.append(C_WARN if expired else C_HEAD_FG)
        else:
            time_cells.append("-")
            time_fgs.append(C_HEAD_FG)
    draw_row(y, time_cells, C_HEAD_BG,
             [f_header, f_body, f_body] + [f_body] * (total_pred_count + chapter_count),
             time_fgs, h=HEADER_H)
    y += HEADER_H + GAP

    # --- Footer ---
    source_names_str = " / ".join(dict.fromkeys(total_source_names)) if total_source_names else "无"
    footer = f"总榜预测源：{source_names_str}；单榜仅本地预测；实线来自本地排名记录，请谨慎参考"
    _sk_chip(d, (x0 + 10, y + 2, x1 - 10, y + FOOTER_H - 2),
             _sk_fit_text(d, footer, f_small, content_w - 60), f_small,
             fill=(255, 255, 255), outline=(255, 255, 255),
             text_fill=C_MUTED, radius=14, anchor="lm")

    # 总榜与章节列之间的分割线
    if total_pred_count and chapter_count:
        sep_x = x0 + 10 + sum(draw_col_ws[:3 + total_pred_count])
        d.rounded_rectangle(
            (sep_x - 1, OUT_PAD + TITLE_H + GAP + 8, sep_x + 2, y - 8),
            radius=1, fill=(180, 100, 160),
        )
    return img


async def compose_forecast_image(
    region: str,
    event_id: int,
    event_name: str,
    forecasts: List[ForecastView],
    live_scores: Dict[int, int],    # rank -> 最新分数
    live_speeds: Dict[int, float],  # rank -> 时速（万/h），None 表示无数据
    display_ranks: Optional[List[int]] = None,
) -> Image.Image:
    """用 PIL 绘制带条纹的预测表格图片。"""
    font_path      = str(FONT_PATH / "SourceHanSansCN-Medium.otf")
    font_bold_path = str(FONT_PATH / "SourceHanSansCN-Bold.otf")
    f_title  = _sk_font(font_bold_path, 22)
    f_header = _sk_font(font_bold_path, 18)
    f_body   = _sk_font(font_path, 18)
    f_small  = _sk_font(font_path, 14)

    if display_ranks is None:
        forecast_ranks: set = set()
        for fc in forecasts:
            if fc.rank_data:
                forecast_ranks.update(fc.rank_data.keys())
        display_ranks = sorted(set(LIVE_RANKS) | (forecast_ranks & set(LIVE_RANKS)))
    else:
        display_ranks = sorted({int(r) for r in display_ranks if int(r) > 0})

    source_names = [_source_name(fc.source) for fc in forecasts]

    PAD_X    = 20
    ROW_H    = 40
    HEADER_H = 44
    TITLE_H  = 58
    FOOTER_H = 36
    OUT_PAD  = 18
    GAP      = 8

    _tmp = Image.new("RGB", (1, 1))
    _d   = ImageDraw.Draw(_tmp)

    def tw(text: str, font) -> int:
        return int(_d.textlength(text, font=font))

    col0_cands = [("排名", f_header), ("预测时间", f_header)] + [(f"T{r}", f_body) for r in display_ranks]
    col0_w = max(tw(t, f) for t, f in col0_cands) + PAD_X * 2

    score_strs = {r: (f"{s / 10000:.2f}万" if s else "-") for r, s in live_scores.items()}
    col1_cands = [("当前分数", f_header), ("-", f_body)] + [(score_strs.get(r, "-"), f_body) for r in display_ranks]
    col1_w = max(tw(t, f) for t, f in col1_cands) + PAD_X * 2

    speed_strs = {}
    for r, spd in live_speeds.items():
        speed_strs[r] = f"{spd:.1f}万/h" if spd is not None else "-"
    col2_cands = [("时速", f_header), ("-", f_body)] + [(speed_strs.get(r, "-"), f_body) for r in display_ranks]
    col2_w = max(tw(t, f) for t, f in col2_cands) + PAD_X * 2

    pred_col_ws = []
    for idx, fc in enumerate(forecasts):
        cands = [(source_names[idx], f_header)]
        for rank in display_ranks:
            rd = fc.rank_data.get(rank)
            cands.append((f"{rd.final_score / 10000:.2f}万" if (rd and rd.final_score) else "-", f_body))
        if fc.forecast_ts:
            t_str, expired = _fmt_time_delta(fc.forecast_ts)
            cands.append((t_str + (" ⚠" if expired else ""), f_body))
        else:
            cands.append(("-", f_body))
        pred_col_ws.append(max(tw(t, f) for t, f in cands) + PAD_X * 2)

    all_col_ws = [col0_w, col1_w, col2_w] + pred_col_ws
    table_w = sum(all_col_ws)

    title_text = f"【{region.upper()}-{event_id}】{event_name}  榜线预测"
    content_w = max(table_w, tw(title_text, f_title) + PAD_X * 2, 620)
    extra_w = max(0, content_w - table_w)
    draw_col_ws = all_col_ws[:]
    draw_col_ws[-1] += extra_w

    total_w = content_w + OUT_PAD * 2
    total_h = OUT_PAD * 2 + TITLE_H + GAP + HEADER_H + ROW_H * len(display_ranks) + HEADER_H + FOOTER_H + GAP * 2

    C_HEAD_BG  = (230, 140, 170)
    C_HEAD_FG  = (255, 255, 255)
    C_ROW_ODD  = (255, 255, 255)
    C_ROW_EVEN = (255, 240, 248)
    C_TEXT     = (50, 30, 50)
    C_WARN     = (200, 60, 60)
    C_MUTED    = (120, 80, 100)

    img = _sk_gradient_bg(total_w, total_h)
    _sk_title_panel(img, title_text, f_title, "榜线预测", f_small, pad=OUT_PAD, height=TITLE_H)
    d = ImageDraw.Draw(img)

    x0 = OUT_PAD
    x1 = total_w - OUT_PAD
    y = OUT_PAD + TITLE_H + GAP
    _sk_panel(img, (x0, y - 4, x1, total_h - OUT_PAD), radius=20, fill=(255, 255, 255, 140), outline=(255, 255, 255, 210))
    d = ImageDraw.Draw(img)

    def draw_row(row_y, cells, bg, fonts, fg_list, h=ROW_H, right_from=1):
        _sk_draw_row(
            d,
            (x0 + 10, row_y, x1 - 10, row_y + h - 2),
            cells,
            draw_col_ws,
            fonts,
            fg_list,
            bg=bg,
            outline=(255, 255, 255),
            radius=14,
            pad_x=PAD_X,
            align_right_from=right_from,
        )

    head_cells = ["排名", "当前分数", "时速"] + source_names
    draw_row(y, head_cells, C_HEAD_BG, [f_header] * len(head_cells), [C_HEAD_FG] * len(head_cells), h=HEADER_H, right_from=-1)
    y += HEADER_H

    for i, rank in enumerate(display_ranks):
        bg = C_ROW_ODD if i % 2 == 0 else C_ROW_EVEN
        cells = [f"T{rank}", score_strs.get(rank, "-"), speed_strs.get(rank, "-")]
        fgs = [C_TEXT, C_TEXT, C_TEXT]
        for fc in forecasts:
            rd = fc.rank_data.get(rank)
            cells.append(f"{rd.final_score / 10000:.2f}万" if (rd and rd.final_score) else "-")
            fgs.append(C_TEXT)
        draw_row(y, cells, bg, [f_body] * len(cells), fgs)
        y += ROW_H

    time_cells = ["预测时间", "-", "-"]
    time_fgs   = [C_HEAD_FG, C_HEAD_FG, C_HEAD_FG]
    for fc in forecasts:
        if fc.forecast_ts:
            t_str, expired = _fmt_time_delta(fc.forecast_ts)
            time_cells.append(t_str + (" ⚠" if expired else ""))
            time_fgs.append(C_WARN if expired else C_HEAD_FG)
        else:
            time_cells.append("-")
            time_fgs.append(C_HEAD_FG)
    draw_row(y, time_cells, C_HEAD_BG, [f_header, f_body, f_body] + [f_body] * len(forecasts), time_fgs, h=HEADER_H)
    y += HEADER_H + GAP

    footer_text = "预测来自本地缓存/多源接口，实时分数来自本地排名记录，请谨慎参考"
    _sk_chip(d, (x0 + 10, y + 2, x1 - 10, y + FOOTER_H - 2), _sk_fit_text(d, footer_text, f_small, content_w - 60), f_small,
             fill=(255, 255, 255), outline=(255, 255, 255), text_fill=C_MUTED, radius=14, anchor="lm")

    sep_x = x0 + 10 + sum(draw_col_ws[:3])
    d.rounded_rectangle((sep_x - 1, OUT_PAD + TITLE_H + GAP + 8, sep_x + 2, y - 8), radius=1, fill=(205, 135, 165))
    return img


def compose_forecast_curve_image(
    region: str,
    event_id: int,
    event_name: str,
    ranks: List[int],
    history: Dict[int, List[tuple]],
    forecasts: List[ForecastView],
    pjsk_type: int = 0,
    remain_text: Optional[str] = None,
    time_range: Optional[tuple] = None,
) -> Image.Image:
    """绘制 tsugu 风格的真实历史曲线 + 预测曲线。"""
    font_path = str(FONT_PATH / "SourceHanSansCN-Medium.otf")
    font_bold_path = str(FONT_PATH / "SourceHanSansCN-Bold.otf")
    f_title = _sk_font(font_bold_path, 24)
    f_label = _sk_font(font_path, 16)
    f_small = _sk_font(font_path, 13)

    W, H = 1180, 700
    M_L, M_R, M_T, M_B = 90, 330, 80, 105
    plot_w = W - M_L - M_R
    plot_h = H - M_T - M_B

    start_ts, end_ts = time_range if time_range else (0, 0)
    if not start_ts:
        timestamps = [ts for points in history.values() for ts, _ in points]
        start_ts = min(timestamps) if timestamps else int(time.time()) - 3600
        end_ts = max(timestamps) if timestamps else int(time.time())
    if end_ts <= start_ts:
        end_ts = start_ts + 3600
    remain_text = remain_text or '未知' 

    current_scores: Dict[int, int] = {}
    current_speeds: Dict[int, Optional[float]] = {}
    for rank, points in history.items():
        if not points:
            continue
        current_scores[rank] = points[-1][1]
        latest_ts, latest_score = points[-1]
        older = next(((ts, score) for ts, score in points if ts >= latest_ts - 3600), None)
        current_speeds[rank] = None
        if older and latest_ts > older[0]:
            current_speeds[rank] = (latest_score - older[1]) * 3600 / (latest_ts - older[0]) / 10000

    latest_history_ts = max((points[-1][0] for points in history.values() if points), default=start_ts)
    now_ts = min(max(latest_history_ts, start_ts), end_ts)

    source_forecast_points: Dict[tuple, List[tuple]] = {}
    latest_forecasts: Dict[int, List[tuple]] = {rank: [] for rank in ranks}
    source_names = []
    for fc in forecasts:
        source_name = _source_name(fc.source)
        source_names.append(source_name)
        for rank in ranks:
            rd = fc.rank_data.get(rank)
            if not rd:
                continue
            points: List[tuple] = []
            if getattr(rd, 'future_rankings', None):
                points.extend((item.ts, item.score) for item in rd.future_rankings)
            elif rd.history_final_score:
                points.extend((item.ts, item.score) for item in rd.history_final_score)
            if points:
                min_pred_ts = now_ts if fc.source == 'local' else start_ts
                source_forecast_points[(rank, fc.source)] = sorted({
                    (int(ts), int(score)) for ts, score in points if min_pred_ts <= int(ts) <= end_ts
                })
            if rd.final_score:
                latest_forecasts.setdefault(rank, []).append((
                    fc.source,
                    source_name,
                    int(fc.forecast_ts or end_ts),
                    int(rd.final_score),
                ))

    all_scores = []
    for points in history.values():
        all_scores.extend(score for _, score in points)
    for points in source_forecast_points.values():
        all_scores.extend(score for _, score in points)
    for items in latest_forecasts.values():
        all_scores.extend(score for _, _, _, score in items)
    if not all_scores:
        all_scores = [0, 10000]
    min_score = max(0, min(all_scores) * 0.95)
    max_score = max(all_scores) * 1.05
    if max_score <= min_score:
        max_score = min_score + 10000

    def sx(ts: int) -> int:
        return int(M_L + (ts - start_ts) / (end_ts - start_ts) * plot_w)

    def sy(score: int) -> int:
        return int(M_T + (max_score - score) / (max_score - min_score) * plot_h)

    def fmt_score(score: float) -> str:
        return f"{score / 10000:.0f}万"

    def fit_text(text: str, font, max_width: int) -> str:
        """截断文字，避免右侧状态栏/底部说明超出图片边界。"""
        if d.textlength(text, font=font) <= max_width:
            return text
        ellipsis = "…"
        while text and d.textlength(text + ellipsis, font=font) > max_width:
            text = text[:-1]
        return text + ellipsis if text else ellipsis

    real_colors = [
        (210, 45, 95), (35, 115, 210), (45, 150, 85), (220, 125, 35),
        (120, 75, 200), (30, 160, 170), (200, 70, 175), (95, 95, 95),
    ]
    forecast_colors = [
        (130, 35, 210), (0, 180, 210), (245, 70, 45), (95, 190, 35),
        (255, 80, 170), (155, 95, 25), (40, 200, 145), (35, 35, 35),
    ]

    img = _sk_gradient_bg(W, H)
    d = ImageDraw.Draw(img)

    title = f"【{region.upper()}-{event_id}】{event_name}  ycx曲线"
    _sk_title_panel(img, title, f_title, f"剩余 {remain_text}", f_small, pad=18, height=54)
    d = ImageDraw.Draw(img)

    _sk_panel(img, (M_L - 12, M_T - 12, M_L + plot_w + 12, M_T + plot_h + 12), radius=24, fill=(255, 255, 255, 222), outline=(255, 255, 255, 230))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([(M_L, M_T), (M_L + plot_w, M_T + plot_h)], radius=18, fill=(255, 255, 255), outline=(235, 210, 226), width=1)

    # 网格与 Y 轴标签
    for i in range(6):
        y = M_T + int(plot_h * i / 5)
        score = max_score - (max_score - min_score) * i / 5
        d.line([(M_L, y), (M_L + plot_w, y)], fill=(240, 220, 230), width=1)
        d.text((M_L - 10, y), fmt_score(score), font=f_small, fill=(100, 80, 95), anchor="rm")

    duration = max(1, end_ts - start_ts)
    whole_days = int(duration // 86400)
    for day in range(whole_days + 1):
        ts = start_ts + day * 86400
        if ts > end_ts:
            break
        x = sx(ts)
        d.line([(x, M_T), (x, M_T + plot_h)], fill=(245, 230, 238), width=1)
        label = "0天" if day == 0 else f"第{day}天"
        d.text((x, M_T + plot_h + 18), label, font=f_small, fill=(100, 80, 95), anchor="mm")
    if end_ts > start_ts + whole_days * 86400:
        x = sx(end_ts)
        d.line([(x, M_T), (x, M_T + plot_h)], fill=(245, 230, 238), width=1)
        d.text((x, M_T + plot_h + 18), "结束", font=f_small, fill=(100, 80, 95), anchor="mm")

    now_x = sx(now_ts)
    d.line([(now_x, M_T), (now_x, M_T + plot_h)], fill=(170, 110, 140), width=2)
    d.text((now_x + 4, M_T + 8), "当前", font=f_small, fill=(150, 80, 115), anchor="la")

    d.text((M_L + plot_w // 2, H - 45), f"活动经过时间（剩余 {remain_text}）", font=f_label, fill=(80, 60, 75), anchor="mm")
    d.text((28, M_T + plot_h // 2), "活动分数", font=f_label, fill=(80, 60, 75), anchor="mm")

    def draw_polyline(points: List[tuple], color: tuple, width: int = 3):
        if len(points) < 2:
            return
        pts = [(sx(ts), sy(score)) for ts, score in points if start_ts <= ts <= end_ts]
        if len(pts) >= 2:
            d.line(pts, fill=color, width=width, joint="curve")

    def draw_dash_segment(x1: int, y1: int, x2: int, y2: int, color: tuple, width: int = 3, dash: int = 8, gap: int = 8):
        dist = max(1, ((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5)
        pattern = max(2, dash + gap)
        steps = max(1, int(dist // pattern * 2))
        for i in range(steps):
            a = i / steps
            b = min(1.0, a + dash / max(dist, 1))
            d.line([(x1 + (x2 - x1) * a, y1 + (y2 - y1) * a),
                    (x1 + (x2 - x1) * b, y1 + (y2 - y1) * b)], fill=color, width=width)

    def draw_dashed(points: List[tuple], color: tuple, width: int = 3, dash: int = 8, gap: int = 8):
        if len(points) < 2:
            return
        pts = [(sx(ts), sy(score)) for ts, score in points if start_ts <= ts <= end_ts]
        if len(pts) < 2:
            return
        for p1, p2 in zip(pts, pts[1:]):
            x1, y1 = p1
            x2, y2 = p2
            draw_dash_segment(x1, y1, x2, y2, color, width=width, dash=dash, gap=gap)

    def draw_value_label(x: int, y: int, text: str, color: tuple):
        plot_left = M_L + 2
        plot_right = M_L + plot_w - 4
        label = text
        tw = int(d.textlength(label, font=f_small))
        max_label_w = plot_right - plot_left - 10
        if tw > max_label_w:
            label = fit_text(text, f_small, max_label_w)
            tw = int(d.textlength(label, font=f_small))

        # 优先放在线尾右侧；右侧放不下时将框整体移到线尾左侧，而不是截成省略号。
        tx = x + 6
        if tx + tw + 5 > plot_right:
            tx = x - tw - 10
        tx = min(max(tx, plot_left), plot_right - tw - 5)
        ty = min(max(y - 11, M_T + 2), M_T + plot_h - 18)
        d.rounded_rectangle([(tx - 3, ty - 1), (tx + tw + 5, ty + 16)], radius=4, fill=(255, 255, 255), outline=color, width=1)
        d.text((tx, ty + 7), label, font=f_small, fill=color, anchor="lm")

    source_styles = {
        'local': (3, 9, 3),
        '33kit': (4, 8, 3),
        'moe': (6, 10, 3),
        'sekarun': (2, 8, 3),
    }

    for idx, rank in enumerate(ranks):
        real_color = real_colors[idx % len(real_colors)]
        forecast_color = forecast_colors[idx % len(forecast_colors)]
        real_points = history.get(rank, [])
        draw_polyline(real_points, real_color)
        if real_points:
            x, y = sx(real_points[-1][0]), sy(real_points[-1][1])
            d.ellipse([(x - 4, y - 4), (x + 4, y + 4)], fill=real_color)
            draw_value_label(x, y, f"T{rank} {real_points[-1][1] / 10000:.1f}万", real_color)

        for source_idx, fc in enumerate(forecasts):
            pred_hist = source_forecast_points.get((rank, fc.source), [])
            dash, gap, width = source_styles.get(fc.source, (3 + source_idx, 9, 3))
            if len(pred_hist) >= 2:
                draw_dashed(pred_hist, forecast_color, width=width, dash=dash, gap=gap)
                px, py = sx(pred_hist[-1][0]), sy(pred_hist[-1][1])
                draw_value_label(px, py, f"T{rank}预 {pred_hist[-1][1] / 10000:.1f}万", forecast_color)
            elif len(pred_hist) == 1:
                px, py = sx(pred_hist[0][0]), sy(pred_hist[0][1])
                d.rectangle([(px - 4, py - 4), (px + 4, py + 4)], fill=forecast_color)
                draw_value_label(px, py, f"T{rank}预 {pred_hist[0][1] / 10000:.1f}万", forecast_color)

        for source, _, pred_ts, pred_score in latest_forecasts.get(rank, []):
            if (rank, source) in source_forecast_points:
                continue
            pred_ts = min(max(pred_ts, start_ts), end_ts)
            px, py = sx(pred_ts), sy(pred_score)
            d.rectangle([(px - 4, py - 4), (px + 4, py + 4)], outline=forecast_color, width=2)
            draw_value_label(px, py, f"T{rank}预 {pred_score / 10000:.1f}万", forecast_color)

    # 图例 / 状态栏
    panel_x = M_L + plot_w + 24
    panel_right = W - 24
    panel_w = panel_right - panel_x
    _sk_panel(img, (panel_x - 12, M_T - 12, panel_right, M_T + plot_h + 12), radius=22, fill=(255, 255, 255, 205), outline=(255, 255, 255, 230))
    d = ImageDraw.Draw(img)
    lx, ly = panel_x, M_T
    _sk_chip(d, (lx, ly - 3, lx + 76, ly + 23), "图例", f_label, fill=(255, 246, 251), text_fill=(60, 45, 60))
    ly += 30
    for idx, rank in enumerate(ranks):
        real_color = real_colors[idx % len(real_colors)]
        forecast_color = forecast_colors[idx % len(forecast_colors)]
        d.line([(lx, ly + 5), (lx + 42, ly + 5)], fill=real_color, width=4)
        draw_dash_segment(lx, ly + 13, lx + 42, ly + 13, forecast_color, width=3, dash=3, gap=9)
        text = fit_text(f"T{rank} 上实线=真实 / 下虚线=预测", f_small, panel_w - 52)
        d.text((lx + 52, ly + 9), text, font=f_small, fill=(60, 45, 60), anchor="lm")
        ly += 24

    ly += 8
    d.text((lx, ly), "预测源线型", font=f_label, fill=(60, 45, 60), anchor="la")
    ly += 24
    for source_idx, source in enumerate(dict.fromkeys(fc.source for fc in forecasts)):
        name = _source_name(source)
        dash, gap, width = source_styles.get(source, (8 + source_idx * 2, 6, 2))
        draw_dash_segment(lx, ly + 8, lx + 42, ly + 8, (80, 80, 80), width=width, dash=dash, gap=gap)
        d.text((lx + 52, ly + 8), fit_text(name, f_small, panel_w - 52), font=f_small, fill=(60, 45, 60), anchor="lm")
        ly += 18
        if ly > M_T + plot_h - 230:
            d.text((lx, ly + 8), "…", font=f_small, fill=(100, 75, 90), anchor="la")
            ly += 18
            break

    ly += 12
    d.text((lx, ly), fit_text(f"剩余 {remain_text}", f_label, panel_w), font=f_label, fill=(120, 70, 95), anchor="la")
    ly += 28
    d.text((lx, ly), "当前状态", font=f_label, fill=(60, 45, 60), anchor="la")
    ly += 24
    for rank in ranks[:8]:
        if ly > H - 90:
            break
        score = current_scores.get(rank)
        speed = current_speeds.get(rank)
        preds = latest_forecasts.get(rank, [])
        score_text = f"{score / 10000:.1f}万" if score else "-"
        speed_text = f"{speed:.1f}万/h" if speed is not None else "-"
        header = fit_text(f"T{rank} {score_text}  时速{speed_text}", f_small, panel_w)
        d.text((lx, ly), header, font=f_small, fill=(60, 45, 60), anchor="la")
        ly += 18
        if preds:
            for _, name, _, pred_score in preds[:4]:
                if ly > H - 64:
                    break
                pred_line = fit_text(f"- {name}: {pred_score / 10000:.1f}万", f_small, panel_w - 12)
                d.text((lx + 12, ly), pred_line, font=f_small, fill=(100, 75, 90), anchor="la")
                ly += 16
            if len(preds) > 4 and ly <= H - 64:
                d.text((lx + 12, ly), f"…另 {len(preds) - 4} 个来源", font=f_small, fill=(100, 75, 90), anchor="la")
                ly += 16
        else:
            d.text((lx + 12, ly), "- 预测: -", font=f_small, fill=(100, 75, 90), anchor="la")
            ly += 16
        ly += 6

    footer = "预测源：" + (" / ".join(dict.fromkeys(source_names)) if source_names else "无")
    footer += "；实线为真实历史，高对比虚线为预测，本地预测仅显示当前之后"
    _sk_chip(d, (24, H - 34, W - 24, H - 8), fit_text(footer, f_small, W - 72), f_small,
             fill=(255, 255, 255), outline=(255, 255, 255), text_fill=(120, 80, 100), anchor="lm")
    return img


def compose_sk_image(name: str, uid: str, score: int, rank: int,
                     near_ranks: list, pred_data: dict = None,
                     remain_time: str = None, update_time: str = None,
                     team_info: tuple = None, team_image: Optional[Image.Image] = None) -> Image.Image:
    """用 PIL 绘制 sk 查分图片（粉白色系）。"""
    font_path      = str(FONT_PATH / "SourceHanSansCN-Medium.otf")
    font_bold_path = str(FONT_PATH / "SourceHanSansCN-Bold.otf")
    f_title  = _sk_font(font_bold_path, 24)
    f_score  = _sk_font(font_bold_path, 30)
    f_label  = _sk_font(font_bold_path, 18)
    f_value  = _sk_font(font_path, 18)
    f_small  = _sk_font(font_path, 14)

    PAD_X    = 22
    PAD_Y    = 18
    LINE_H   = 38
    TITLE_H  = 58
    TEAM_H   = 50 if team_info else 0
    MIN_WIDTH = 620

    _tmp = Image.new("RGB", (1, 1))
    _d   = ImageDraw.Draw(_tmp)

    uid_display = f"****{str(uid)[-4:]}" if len(str(uid)) > 4 else str(uid)
    title_text = f"{name} - {uid_display}"
    score_text = f"分数 {score / 10000:.1f}W，排名 {rank}"

    num_near_ranks = len(near_ranks)
    num_pred_lines = sum(1 for r in near_ranks if r.get('pred')) if pred_data else 0

    total_h = PAD_Y * 2 + TITLE_H + 8 + TEAM_H + 52
    total_h += LINE_H * num_near_ranks
    if num_pred_lines > 0:
        total_h += LINE_H * num_pred_lines + 42
    if remain_time:
        total_h += LINE_H + 8
    if update_time:
        total_h += LINE_H

    C_TEXT  = (50, 30, 50)
    C_LABEL = (208, 95, 142)
    C_PRED  = (180, 80, 135)
    C_MUTED = (120, 80, 100)

    img = _sk_gradient_bg(MIN_WIDTH, total_h)
    _sk_title_panel(img, title_text, f_title, "SK", f_small, pad=PAD_X, height=TITLE_H)
    d = ImageDraw.Draw(img)

    y = PAD_Y + TITLE_H + 8

    if team_info:
        team_name, team_tag = team_info
        team_text = team_name + (f'({team_tag})' if team_tag else '')
        _sk_panel(img, (PAD_X, y, MIN_WIDTH - PAD_X, y + TEAM_H - 4), radius=18, fill=(255, 255, 255, 210))
        d = ImageDraw.Draw(img)
        if team_image:
            team_image = team_image.resize((40, 40))
            try:
                r, g, b, mask = team_image.split()
                img.paste(team_image, (PAD_X + 10, y + 3), mask)
            except Exception:
                img.paste(team_image, (PAD_X + 10, y + 3))
            d.text((PAD_X + 62, y + TEAM_H // 2), team_text, font=f_value, fill=C_TEXT, anchor="lm")
        else:
            d.text((PAD_X + 14, y + TEAM_H // 2), team_text, font=f_value, fill=C_TEXT, anchor="lm")
        y += TEAM_H

    _sk_panel(img, (PAD_X, y, MIN_WIDTH - PAD_X, y + 50), radius=18, fill=(230, 140, 170, 226), outline=(255, 255, 255, 210))
    d = ImageDraw.Draw(img)
    d.text((PAD_X + 18, y + 25), score_text, font=f_score, fill=(255, 255, 255), anchor="lm")
    y += 58

    for idx, rank_info in enumerate(near_ranks):
        rank_num = rank_info['rank']
        rank_score = rank_info['score']
        tag = rank_info['tag']
        deviation = rank_info['deviation']
        rank_text = f"T{rank_num}  {rank_score / 10000:.1f}W  {tag}{deviation:.1f}W"
        bg = (255, 255, 255) if idx % 2 == 0 else (255, 240, 248)
        d.rounded_rectangle((PAD_X, y, MIN_WIDTH - PAD_X, y + LINE_H - 4), radius=15, fill=bg, outline=(255, 255, 255))
        d.text((PAD_X + 16, y + LINE_H // 2 - 2), rank_text, font=f_value, fill=C_TEXT, anchor="lm")
        y += LINE_H

    if num_pred_lines > 0:
        y += 8
        _sk_chip(d, (PAD_X, y, PAD_X + 96, y + 28), "预测线", f_label, fill=(255, 246, 251), text_fill=C_LABEL)
        y += 36
        for rank_info in near_ranks:
            pred = rank_info.get('pred')
            if pred:
                pred_text = f"T{rank_info['rank']}  预测 {pred / 10000:.1f}W"
                d.rounded_rectangle((PAD_X, y, MIN_WIDTH - PAD_X, y + LINE_H - 4), radius=15, fill=(255, 246, 251), outline=(245, 218, 232))
                d.text((PAD_X + 16, y + LINE_H // 2 - 2), pred_text, font=f_value, fill=C_PRED, anchor="lm")
                y += LINE_H
        d.text((MIN_WIDTH - PAD_X, y - 4), "预测线来自33（3-3.dev）", font=f_small, fill=C_MUTED, anchor="rm")

    if remain_time:
        y += 8
        _sk_chip(d, (PAD_X, y, MIN_WIDTH - PAD_X, y + 30), f"活动还剩 {remain_time}", f_value, fill=(255, 255, 255), text_fill=C_TEXT, anchor="lm")
        y += LINE_H

    if update_time:
        d.text((PAD_X, y + LINE_H // 2), f"数据生成于 {update_time}", font=f_small, fill=C_MUTED, anchor="lm")
    return img


def compose_sk_multi_image(players_data: list, update_time: str = None) -> Image.Image:
    """用 PIL 绘制多人 sk 查分图片（粉白色系）。"""
    font_path      = str(FONT_PATH / "SourceHanSansCN-Medium.otf")
    font_bold_path = str(FONT_PATH / "SourceHanSansCN-Bold.otf")
    f_title  = _sk_font(font_bold_path, 20)
    f_header = _sk_font(font_bold_path, 16)
    f_body   = _sk_font(font_path, 15)
    f_small  = _sk_font(font_path, 12)

    PAD_X    = 15
    ROW_H    = 42
    HEADER_H = 42
    TITLE_H  = 56
    FOOTER_H = 34
    OUT_PAD  = 18
    MIN_WIDTH = 600
    GAP = 8

    _tmp = Image.new("RGB", (1, 1))
    _d   = ImageDraw.Draw(_tmp)

    def tw(text: str, font) -> int:
        return int(_d.textlength(text, font=font))

    name_w = tw("玩家名", f_header) + PAD_X * 2
    for p in players_data:
        uid_display = f"****{str(p['uid'])[-4:]}" if len(str(p['uid'])) > 4 else str(p['uid'])
        name_text = f"{p['name']}({uid_display})"
        name_w = max(name_w, tw(name_text, f_body) + PAD_X * 2)
    score_w = max(tw("分数", f_header), tw("9999.9W", f_body)) + PAD_X * 2
    rank_w = max(tw("排名", f_header), tw("T99999", f_body)) + PAD_X * 2

    col_widths = {'name': name_w, 'score': score_w, 'rank': rank_w}
    table_w = sum(col_widths.values())
    content_w = max(MIN_WIDTH, table_w)
    extra_w = content_w - table_w
    draw_col_ws = [name_w + extra_w, score_w, rank_w]

    total_w = content_w + OUT_PAD * 2
    total_h = OUT_PAD * 2 + TITLE_H + GAP + HEADER_H + ROW_H * len(players_data) + FOOTER_H

    C_HEAD_BG  = (230, 140, 170)
    C_HEAD_FG  = (255, 255, 255)
    C_ROW_ODD  = (255, 255, 255)
    C_ROW_EVEN = (255, 240, 248)
    C_TEXT     = (50, 30, 50)
    C_MUTED    = (120, 80, 100)

    img = _sk_gradient_bg(total_w, total_h)
    _sk_title_panel(img, f"查询结果（共 {len(players_data)} 人）", f_title, "MULTI", f_small, pad=OUT_PAD, height=TITLE_H)
    d = ImageDraw.Draw(img)

    x0 = OUT_PAD
    x1 = total_w - OUT_PAD
    y = OUT_PAD + TITLE_H + GAP
    _sk_panel(img, (x0, y - 4, x1, total_h - OUT_PAD), radius=20, fill=(255, 255, 255, 140), outline=(255, 255, 255, 210))
    d = ImageDraw.Draw(img)

    headers = ["玩家名", "分数", "排名"]
    _sk_draw_row(d, (x0 + 10, y, x1 - 10, y + HEADER_H - 2), headers, draw_col_ws, [f_header] * 3, [C_HEAD_FG] * 3, bg=C_HEAD_BG, radius=14)
    y += HEADER_H

    for i, player in enumerate(players_data):
        bg = C_ROW_ODD if i % 2 == 0 else C_ROW_EVEN
        uid_display = f"****{str(player['uid'])[-4:]}" if len(str(player['uid'])) > 4 else str(player['uid'])
        name_text = f"{player['name']}({uid_display})"
        score_text = f"{player['score'] / 10000:.1f}W"
        rank_text = f"T{player['rank']}"
        _sk_draw_row(
            d,
            (x0 + 10, y, x1 - 10, y + ROW_H - 2),
            [name_text, score_text, rank_text],
            draw_col_ws,
            [f_body] * 3,
            [C_TEXT] * 3,
            bg=bg,
            radius=14,
        )
        y += ROW_H

    if update_time:
        _sk_chip(d, (x0 + 10, y + 4, x1 - 10, y + FOOTER_H - 2), f"数据生成于 {update_time}", f_small,
                 fill=(255, 255, 255), outline=(255, 255, 255), text_fill=C_MUTED, anchor="lm")
    return img


def compose_cf_range_image(cf_data_list: list) -> Image.Image:
    """用 PIL 绘制多个玩家的查房图片。"""
    font_path      = str(FONT_PATH / "SourceHanSansCN-Medium.otf")
    font_bold_path = str(FONT_PATH / "SourceHanSansCN-Bold.otf")
    f_header = _sk_font(font_bold_path, 16)
    f_body   = _sk_font(font_path, 14)

    PAD_X    = 15
    ROW_H    = 34
    HEADER_H = 38
    OUT_PAD  = 14
    MIN_WIDTH = 620
    MAX_WIDTH = 1200

    _tmp = Image.new("RGB", (1, 1))
    _d   = ImageDraw.Draw(_tmp)

    def tw(text: str, font) -> int:
        return int(_d.textlength(text, font=font))

    rank_w = max(tw("排名", f_header), tw("T100", f_body)) + PAD_X * 2
    name_w = tw("玩家名", f_header) + PAD_X * 2
    for dct in cf_data_list:
        uid_display = f"****{str(dct['uid'])[-4:]}" if len(str(dct['uid'])) > 4 else str(dct['uid'])
        name_w = max(name_w, tw(f"{dct['name']}({uid_display})", f_body) + PAD_X * 2)
    score_w = max(tw("当前分", f_header), tw("9999.9W", f_body)) + PAD_X * 2
    speed_w = max(tw("时速", f_header), tw("999.9W/h", f_body)) + PAD_X * 2
    play_w = max(tw("周回", f_header), tw("999", f_body)) + PAD_X * 2
    status_w = max(tw("状态", f_header), max(tw("周回中" if d['is_playing'] else "停车中", f_body) for d in cf_data_list) if cf_data_list else 0) + PAD_X * 2

    base_widths = [rank_w, name_w, score_w, speed_w, play_w, status_w]
    table_w = sum(base_widths)
    content_w = max(MIN_WIDTH, min(table_w, MAX_WIDTH))
    draw_col_ws = base_widths[:]
    if content_w > table_w:
        draw_col_ws[1] += content_w - table_w

    total_w = content_w + OUT_PAD * 2
    total_h = OUT_PAD * 2 + HEADER_H + ROW_H * len(cf_data_list)

    C_HEAD_BG  = (230, 140, 170)
    C_HEAD_FG  = (255, 255, 255)
    C_ROW_ODD  = (255, 255, 255)
    C_ROW_EVEN = (255, 240, 248)
    C_TEXT     = (50, 30, 50)

    img = _sk_gradient_bg(total_w, total_h)
    d = ImageDraw.Draw(img)
    x0 = OUT_PAD
    x1 = total_w - OUT_PAD
    y = OUT_PAD
    _sk_panel(img, (x0, y - 4, x1, total_h - OUT_PAD + 4), radius=20, fill=(255, 255, 255, 150), outline=(255, 255, 255, 220))
    d = ImageDraw.Draw(img)

    headers = ["排名", "玩家名", "当前分", "时速", "周回", "状态"]
    _sk_draw_row(d, (x0 + 8, y, x1 - 8, y + HEADER_H - 2), headers, draw_col_ws, [f_header] * 6, [C_HEAD_FG] * 6, bg=C_HEAD_BG, radius=14)
    y += HEADER_H

    status_positions = []
    for i, cf_data in enumerate(cf_data_list):
        bg = C_ROW_ODD if i % 2 == 0 else C_ROW_EVEN
        uid_display = f"****{str(cf_data['uid'])[-4:]}" if len(str(cf_data['uid'])) > 4 else str(cf_data['uid'])
        name_text = f"{cf_data['name']}({uid_display})"
        status_text = "周回中" if cf_data['is_playing'] else "停车中"
        score_val = cf_data.get('score', 0) or 0
        score_text = f"{score_val / 10000:.1f}W" if score_val >= 10000 else str(score_val)
        cells = [f"T{cf_data['rank']}", name_text, score_text, f"{cf_data['hourly_speed']:.1f}W/h", str(cf_data['play_count']), status_text]
        _sk_draw_row(d, (x0 + 8, y, x1 - 8, y + ROW_H - 2), cells, draw_col_ws, [f_body] * 6, [C_TEXT] * 6, bg=bg, radius=14)
        status_x = x0 + 8 + sum(draw_col_ws[:5])
        status_positions.append((status_x, y, draw_col_ws[5], status_text))
        y += ROW_H

    for status_x, row_y, col_w, status_text in status_positions:
        d.rounded_rectangle((status_x + 4, row_y + 5, status_x + col_w - 4, row_y + ROW_H - 7), radius=10, fill=(255, 255, 255))
        d.text((status_x + col_w // 2, row_y + ROW_H // 2 - 1), status_text, font=f_body, fill=C_TEXT, anchor="mm")

    return img


def compose_cf_image(name: str, uid: str, score: int, rank: int, hourly_speed: float, twenty_min_speed: float, 
                     play_count: int, avg_pt: float, last_pt: int, is_playing: bool, stop_duration,
                     wl_chapter_stats: Optional[List[dict]] = None) -> Image.Image:
    """用 PIL 绘制查房图片"""
    font_path      = str(FONT_PATH / "SourceHanSansCN-Medium.otf")
    font_bold_path = str(FONT_PATH / "SourceHanSansCN-Bold.otf")
    f_title  = _sk_font(font_bold_path, 22)
    f_label  = _sk_font(font_bold_path, 16)
    f_value  = _sk_font(font_path, 16)
    f_small  = _sk_font(font_path, 13)

    PAD_X    = 20
    PAD_Y    = 16
    LINE_H   = 38
    TITLE_H  = 58
    MIN_WIDTH = 520

    _tmp = Image.new("RGB", (1, 1))
    _d   = ImageDraw.Draw(_tmp)

    def tw(text: str, font) -> int:
        return int(_d.textlength(text, font=font))

    uid_display = f"****{str(uid)[-4:]}" if len(str(uid)) > 4 else str(uid)
    title_text = f"玩家 {name}(id={uid_display})"
    score_formatted = f"{score:,}"

    if is_playing:
        status_text = "周回中"
    else:
        if stop_duration:
            hours = int(stop_duration.total_seconds() // 3600)
            minutes = int((stop_duration.total_seconds() % 3600) // 60)
            seconds = int(stop_duration.total_seconds() % 60)
            duration_str = f"{hours}小时{minutes}分钟{seconds}秒" if hours > 0 else f"{minutes}分钟{seconds}秒"
            status_text = f"停车中（已停止 {duration_str}）"
        else:
            status_text = "停车中"

    lines = [
        ("总榜分数", score_formatted),
        ("总榜排名", f"T{rank}"),
        ("总榜时速", f"{hourly_speed:.1f}W/h"),
        ("20*3时速", f"{twenty_min_speed:.1f}W/h"),
        ("周回数", str(play_count)),
        ("近10次平均Pt", f"{avg_pt:.0f}"),
        ("最近一次Pt", str(last_pt)),
        ("状态", status_text),
    ]
    if wl_chapter_stats:
        for stat in wl_chapter_stats:
            value = '-'
            if stat.get('rank'):
                value = f"T{stat['rank']} / {stat['score'] / 10000:.2f}万 / {stat['hourly_speed']:.1f}W/h"
            lines.append((f"第{stat.get('chapter_no')}章", value))

    title_w = tw(title_text, f_title) + PAD_X * 2
    max_data_w = 0
    for label, value in lines:
        max_data_w = max(max_data_w, tw(label, f_label) + 24 + tw(value, f_value) + PAD_X * 2)

    total_w = max(title_w, max_data_w, MIN_WIDTH)
    total_h = TITLE_H + LINE_H * len(lines) + PAD_Y * 2 + 10

    C_TEXT  = (50, 30, 50)
    C_LABEL = (208, 95, 142)
    C_MUTED = (120, 80, 100)

    img = _sk_gradient_bg(total_w, total_h)
    d   = ImageDraw.Draw(img)

    _sk_panel(img, (10, 8, total_w - 10, TITLE_H + 4), radius=22, fill=(255, 255, 255, 222))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((PAD_X, 22, PAD_X + 62, 28), radius=3, fill=(255, 128, 178))
    d.text((PAD_X, TITLE_H // 2 + 8), _sk_fit_text(d, title_text, f_title, total_w - PAD_X * 2), font=f_title, fill=C_TEXT, anchor="lm")

    y = TITLE_H + PAD_Y
    for idx, (label, value) in enumerate(lines):
        row_fill = (255, 255, 255, 224) if idx % 2 == 0 else (255, 240, 248, 214)
        d.rounded_rectangle((PAD_X, y, total_w - PAD_X, y + LINE_H - 4), radius=15, fill=row_fill, outline=(255, 255, 255))
        d.text((PAD_X + 14, y + LINE_H // 2 - 2), label, font=f_label, fill=C_LABEL, anchor="lm")
        value_x = PAD_X + max(132, tw(label, f_label) + 28)
        d.text((value_x, y + LINE_H // 2 - 2), value, font=f_value, fill=C_TEXT, anchor="lm")
        y += LINE_H

    d.text((total_w - PAD_X, total_h - 12), "ACTIVITY CHECK", font=f_small, fill=C_MUTED, anchor="rm")
    return img


async def compose_csb_image(latest_name: str, latest_uid: str, latest_rank: int, latest_score: int,
                            hourly_counts: dict, start_date, update_time_str: str, stop_periods: list = None) -> Image.Image:
    """用 PIL 绘制查水表表格图片。"""
    if stop_periods is None:
        stop_periods = []

    font_path      = str(FONT_PATH / "SourceHanSansCN-Medium.otf")
    font_bold_path = str(FONT_PATH / "SourceHanSansCN-Bold.otf")
    f_title  = _sk_font(font_bold_path, 20)
    f_header = _sk_font(font_bold_path, 16)
    f_body   = _sk_font(font_path, 14)
    f_small  = _sk_font(font_path, 12)

    PAD_X    = 12
    ROW_H    = 32
    HEADER_H = 36
    TITLE_H  = 58
    FOOTER_H = 34
    STOP_H   = 28
    CELL_W   = 36
    OUT_PAD  = 18

    _tmp = Image.new("RGB", (1, 1))
    _d   = ImageDraw.Draw(_tmp)

    def tw(text: str, font) -> int:
        return int(_d.textlength(text, font=font))

    col0_w = max(tw("第99天", f_body), tw("时间", f_header)) + PAD_X * 2
    table_w = col0_w + CELL_W * 24
    title_text = f"【查水表】{latest_name} (ID: ****{str(latest_uid)[-4:]})"
    title_min_w = tw(title_text, f_title) + PAD_X * 2
    content_w = max(table_w, title_min_w)
    total_w = content_w + OUT_PAD * 2

    if hourly_counts:
        max_day = max([key[0] for key in hourly_counts.keys()])
        num_days = max_day + 1
    else:
        num_days = 1

    stop_cols = 2
    stop_rows = (len(stop_periods) + stop_cols - 1) // stop_cols if stop_periods else 0
    table_h = HEADER_H + ROW_H * num_days
    stop_h = STOP_H * stop_rows + (16 if stop_periods else 0)
    total_h = OUT_PAD * 2 + TITLE_H + 8 + table_h + stop_h + FOOTER_H

    C_HEAD_BG  = (230, 140, 170)
    C_HEAD_FG  = (255, 255, 255)
    C_ROW_ODD  = (255, 255, 255)
    C_ROW_EVEN = (255, 240, 248)
    C_STOP_BG  = (255, 246, 251)
    C_TEXT     = (50, 30, 50)
    C_MUTED    = (120, 80, 100)
    C_BORDER   = (235, 210, 226)

    img = _sk_gradient_bg(total_w, total_h)
    info_text = f"{title_text}  排名: T{latest_rank} | 分数: {latest_score}"
    _sk_title_panel(img, info_text, f_title, "CSB", f_small, pad=OUT_PAD, height=TITLE_H)
    d = ImageDraw.Draw(img)

    x0 = OUT_PAD
    y = OUT_PAD + TITLE_H + 8
    table_x1 = x0
    table_x2 = x0 + table_w
    _sk_panel(img, (table_x1, y - 4, table_x2, y + table_h + 4), radius=18, fill=(255, 255, 255, 150), outline=(255, 255, 255, 220))
    d = ImageDraw.Draw(img)

    # 表头行
    d.rounded_rectangle((table_x1 + 8, y, table_x2 - 8, y + HEADER_H - 2), radius=14, fill=C_HEAD_BG, outline=(255, 255, 255))
    d.text((table_x1 + col0_w // 2, y + HEADER_H // 2), "时间", font=f_header, fill=C_HEAD_FG, anchor="mm")
    for h in range(24):
        x = table_x1 + col0_w + h * CELL_W
        d.text((x + CELL_W // 2, y + HEADER_H // 2), f"{h}", font=f_header, fill=C_HEAD_FG, anchor="mm")
    y += HEADER_H

    if hourly_counts:
        max_day = max([key[0] for key in hourly_counts.keys()])
        for day in range(max_day + 1):
            bg = C_ROW_ODD if day % 2 == 0 else C_ROW_EVEN
            d.rounded_rectangle((table_x1 + 8, y, table_x2 - 8, y + ROW_H - 2), radius=12, fill=bg, outline=(255, 255, 255))
            day_text = f"第{day+1}天"
            d.text((table_x1 + col0_w // 2, y + ROW_H // 2), day_text, font=f_body, fill=C_TEXT, anchor="mm")
            for h in range(24):
                key = (day, h)
                count = hourly_counts.get(key, 0)
                x = table_x1 + col0_w + h * CELL_W
                fill = C_TEXT if count else C_MUTED
                d.text((x + CELL_W // 2, y + ROW_H // 2), str(count), font=f_body, fill=fill, anchor="mm")
            y += ROW_H

    if stop_periods:
        col_width = content_w // stop_cols
        panel_h = STOP_H * stop_rows + 8
        _sk_panel(img, (x0, y + 4, x0 + content_w, y + panel_h + 6), radius=18, fill=(255, 255, 255, 160), outline=(255, 255, 255, 220))
        d = ImageDraw.Draw(img)
        for i, period in enumerate(stop_periods):
            row = i // stop_cols
            col = i % stop_cols
            x_offset = x0 + col * col_width
            y_offset = y + 8 + row * STOP_H
            start_str = period['start'].strftime('%m-%d %H:%M')
            end_str = period['end'].strftime('%H:%M')
            stop_text = f"停车: {start_str} ~ {end_str} ({period['minutes']}分钟)"
            stop_text = _sk_fit_text(d, stop_text, f_body, col_width - 40)
            d.rounded_rectangle((x_offset + 8, y_offset + 2, x_offset + col_width - 8, y_offset + STOP_H - 2), radius=12, fill=C_STOP_BG, outline=C_BORDER)
            d.text((x_offset + 20, y_offset + STOP_H // 2), stop_text, font=f_body, fill=C_TEXT, anchor="lm")
        y += panel_h + 8

    footer_text = f"数据更新时间: {update_time_str}"
    _sk_chip(d, (x0, total_h - OUT_PAD - FOOTER_H + 4, x0 + content_w, total_h - OUT_PAD - 2), footer_text, f_small,
             fill=(255, 255, 255), outline=(255, 255, 255), text_fill=C_MUTED, anchor="lm")
    return img


def _me_curve_helpers() -> dict:
    """把 sk 模块现成的绘制辅助函数交给 _me_curve 使用。"""
    return {
        'gradient_bg': _sk_gradient_bg,
        'panel': _sk_panel,
        'title_panel': _sk_title_panel,
        'chip': _sk_chip,
        'fit_text': _sk_fit_text,
        'wl_icon': lambda cid, size: _load_wl_chara_icon(cid, size=size),
    }


def _load_chara_color_map(pjsk_type: int) -> Dict[int, str]:
    """gameCharacterId -> 印象色 colorCode。

    gameCharacterUnits.json 里 V家角色会有多条 unit 记录，取第一条即可。
    """
    colors: Dict[int, str] = {}
    try:
        data = get_context().load_master_data('gameCharacterUnits.json', pjsk_type)
    except Exception as e:
        logger.debug(f"[cnskme] 读取角色印象色失败: {e}")
        return colors
    for item in data or []:
        if not isinstance(item, dict):
            continue
        cid = item.get('gameCharacterId')
        code = item.get('colorCode')
        if cid is not None and code and cid not in colors:
            colors[int(cid)] = code
    return colors


def _me_curve_fonts() -> dict:
    font_path = str(FONT_PATH / "SourceHanSansCN-Medium.otf")
    font_bold_path = str(FONT_PATH / "SourceHanSansCN-Bold.otf")
    return {
        'title': _sk_font(font_bold_path, 24),
        'label': _sk_font(font_path, 16),
        'small': _sk_font(font_path, 13),
    }


# ---------- 对外渲染任务 ----------

def _timedelta_or_none(seconds):
    """载荷里的停车时长以秒传入，这里还原成 timedelta 供绘制使用。"""
    if seconds is None:
        return None
    try:
        return timedelta(seconds=float(seconds))
    except (TypeError, ValueError):
        return None


def _stop_periods(raw) -> List[dict]:
    """把载荷里的 ISO 时间字符串还原成 datetime。"""
    periods = []
    for item in raw or []:
        try:
            periods.append({
                'start': datetime.fromisoformat(item['start']),
                'end': datetime.fromisoformat(item['end']),
                'minutes': int(item.get('minutes') or 0),
            })
        except Exception:
            continue
    return periods


async def _fetch_team_image(event_asset: Optional[str], team_asset: Optional[str], pjsk_type: int):
    """cf 队伍图标由服务自取，指令侧只给资源名。"""
    if not event_asset or not team_asset:
        return None
    try:
        return await get_context().get_asset(
            f'ondemand/event/{event_asset}/team_image', f'{team_asset}.png',
            pjsk_type=pjsk_type, block=True,
        )
    except Exception as e:
        logger.debug(f"[sk] 拉取队伍图标失败: {e}")
        return None


@register("sk_single")
async def render_sk_single(payload: dict) -> bytes:
    """单人查分卡。载荷：name、uid、score、rank、near_ranks、pred_data、
    remain_time、update_time、team_info、event_asset、team_asset、pjsk_type。"""
    team_info = payload.get("team_info")
    team_image = await _fetch_team_image(
        payload.get("event_asset"), payload.get("team_asset"),
        int(payload.get("pjsk_type", 0)),
    )
    img = await run_pjsk_thread(
        compose_sk_image,
        name=payload.get("name") or '',
        uid=str(payload.get("uid") or ''),
        score=int(payload.get("score") or 0),
        rank=int(payload.get("rank") or 0),
        near_ranks=payload.get("near_ranks") or [],
        pred_data=payload.get("pred_data"),
        remain_time=payload.get("remain_time"),
        update_time=payload.get("update_time"),
        team_info=tuple(team_info) if team_info else None,
        team_image=team_image,
    )
    return await run_pjsk_thread(image_to_jpeg, img)


@register("sk_multi")
async def render_sk_multi(payload: dict) -> bytes:
    """多人查分卡。载荷：players、update_time。"""
    img = await run_pjsk_thread(
        compose_sk_multi_image, payload.get("players") or [], payload.get("update_time")
    )
    return await run_pjsk_thread(image_to_jpeg, img)


@register("sk_rank_table")
async def render_sk_rank_table(payload: dict) -> bytes:
    """榜线表。载荷：title、ranks_data、update_minutes_ago、speed_header、speed_unit。"""
    img = await run_pjsk_thread(
        compose_rank_table_image,
        payload.get("title") or '',
        payload.get("ranks_data") or [],
        int(payload.get("update_minutes_ago") or 0),
        speed_header=payload.get("speed_header") or '时速',
        speed_unit=payload.get("speed_unit") or '万/h',
    )
    return await run_pjsk_thread(image_to_jpeg, img)


@register("sk_wl_rank_table")
async def render_sk_wl_rank_table(payload: dict) -> bytes:
    """WL 分章榜线表。载荷：title、chapters、rows、update_minutes_ago、value_mode、value_header、value_unit。"""
    img = await run_pjsk_thread(
        compose_wl_rank_table_image,
        payload.get("title") or '',
        payload.get("chapters") or [],
        payload.get("rows") or [],
        int(payload.get("update_minutes_ago") or 0),
        value_mode=payload.get("value_mode") or 'score',
        value_header=payload.get("value_header") or '分数',
        value_unit=payload.get("value_unit") or '',
    )
    return await run_pjsk_thread(image_to_jpeg, img)


@register("sk_forecast")
async def render_sk_forecast(payload: dict) -> bytes:
    """预测图。载荷：region、event_id、event_name、forecasts、live_scores、live_speeds、display_ranks。"""
    img = await compose_forecast_image(
        payload.get("region") or '',
        int(payload.get("event_id") or 0),
        payload.get("event_name") or '',
        _forecast_views(payload.get("forecasts")),
        _int_key_map(payload.get("live_scores")),
        _int_key_map(payload.get("live_speeds")),
        payload.get("display_ranks"),
    )
    return await run_pjsk_thread(image_to_jpeg, img)


@register("sk_wl_forecast")
async def render_sk_wl_forecast(payload: dict) -> bytes:
    """WL 预测图。载荷：region、base_event_id、event_name、chapters、
    total_forecasts、chapter_forecasts、live_scores、live_speeds、display_ranks。"""
    chapter_forecasts = {
        rank: (ForecastView(value) if value else None)
        for rank, value in _int_key_map(payload.get("chapter_forecasts")).items()
    }
    img = await compose_wl_forecast_image(
        payload.get("region") or '',
        int(payload.get("base_event_id") or 0),
        payload.get("event_name") or '',
        payload.get("chapters") or [],
        _forecast_views(payload.get("total_forecasts")),
        chapter_forecasts,
        _int_key_map(payload.get("live_scores")),
        _int_key_map(payload.get("live_speeds")),
        [int(r) for r in payload.get("display_ranks") or []],
    )
    return await run_pjsk_thread(image_to_jpeg, img)


@register("sk_forecast_curve")
async def render_sk_forecast_curve(payload: dict) -> bytes:
    """预测曲线图。载荷：region、event_id、event_name、ranks、history、
    forecasts、pjsk_type、remain_text、time_range。"""
    time_range = payload.get("time_range")
    img = await run_pjsk_thread(
        compose_forecast_curve_image,
        payload.get("region") or '',
        int(payload.get("event_id") or 0),
        payload.get("event_name") or '',
        [int(r) for r in payload.get("ranks") or []],
        _history_map(payload.get("history")),
        _forecast_views(payload.get("forecasts")),
        int(payload.get("pjsk_type", 0)),
        payload.get("remain_text"),
        tuple(time_range) if time_range else None,
    )
    return await run_pjsk_thread(image_to_jpeg, img)


@register("sk_cf")
async def render_sk_cf(payload: dict) -> bytes:
    """cf 单人统计图。"""
    img = await run_pjsk_thread(
        compose_cf_image,
        payload.get("name") or '',
        str(payload.get("uid") or ''),
        int(payload.get("score") or 0),
        int(payload.get("rank") or 0),
        float(payload.get("hourly_speed") or 0),
        float(payload.get("twenty_min_speed") or 0),
        int(payload.get("play_count") or 0),
        float(payload.get("avg_pt") or 0),
        int(payload.get("last_pt") or 0),
        bool(payload.get("is_playing")),
        _timedelta_or_none(payload.get("stop_seconds")),
        payload.get("wl_chapter_stats"),
    )
    return await run_pjsk_thread(image_to_jpeg, img)


@register("sk_cf_range")
async def render_sk_cf_range(payload: dict) -> bytes:
    """cf 区间统计图。载荷：cf_data_list。"""
    img = await run_pjsk_thread(compose_cf_range_image, payload.get("cf_data_list") or [])
    return await run_pjsk_thread(image_to_jpeg, img)


@register("sk_csb")
async def render_sk_csb(payload: dict) -> bytes:
    """csb 分时统计图。

    hourly_counts 以 [[day, hour, count], ...] 传入（JSON 没有元组键）。
    """
    img = await compose_csb_image(
        payload.get("latest_name") or '',
        str(payload.get("latest_uid") or ''),
        int(payload.get("latest_rank") or 0),
        int(payload.get("latest_score") or 0),
        {
            (int(day), int(hour)): int(count)
            for day, hour, count in payload.get("hourly_counts") or []
        },
        payload.get("start_date"),
        payload.get("update_time_str") or '',
        _stop_periods(payload.get("stop_periods")),
    )
    return await run_pjsk_thread(image_to_jpeg, img)
