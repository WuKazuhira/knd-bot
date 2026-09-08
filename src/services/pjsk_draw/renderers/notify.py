"""订阅推送出图：新曲上线列表与虚拟 Live 卡片。

原 plugins/pjsk/subscribe/_notify.py 的绘制部分。
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import List

from PIL import Image, ImageDraw

from services import logger

from ..primitives import (
    get_pjsk_asset_cached,
    get_pjsk_font,
    image_to_png,
    run_pjsk_thread,
    vertical_gradient,
)
from ..registry import register


def _panel_bg(width: int, height: int) -> Image.Image:
    top, bottom = (255, 235, 244), (232, 240, 255)
    img = vertical_gradient(width, height, top, bottom)
    return img


def _draw_rows_image(title: str, rows: List[List[str]], footer: str = '') -> Image.Image:
    f_title = get_pjsk_font('SourceHanSansCN-Bold.otf', 24)
    f_body = get_pjsk_font('SourceHanSansCN-Medium.otf', 18)
    f_small = get_pjsk_font('SourceHanSansCN-Medium.otf', 13)

    pad, row_h, title_h = 24, 30, 56
    _tmp = ImageDraw.Draw(Image.new('RGB', (1, 1)))
    width = max(
        [int(_tmp.textlength(title, font=f_title)) + pad * 2 + 20]
        + [int(max((_tmp.textlength(line, font=f_body) for line in row), default=0)) + pad * 2 + 24 for row in rows]
        + [560]
    )
    total_lines = sum(len(r) for r in rows)
    height = title_h + pad * 2 + total_lines * row_h + len(rows) * 16 + (26 if footer else 0)

    img = _panel_bg(width, height)
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((pad - 8, pad - 8, width - pad + 8, pad + title_h - 12), radius=14, fill=(255, 255, 255, 230))
    d.text((pad + 4, pad + 8), title, font=f_title, fill=(60, 34, 60))
    y = title_h + pad + 8
    for row in rows:
        block_h = len(row) * row_h + 8
        d.rounded_rectangle((pad - 6, y - 6, width - pad + 6, y + block_h - 4), radius=12, fill=(255, 255, 255))
        for line in row:
            d.text((pad + 8, y), line, font=f_body, fill=(50, 40, 56))
            y += row_h
        y += 16
    if footer:
        d.text((pad, height - 24), footer, font=f_small, fill=(130, 110, 130))
    return img


def _fmt_ts(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000).strftime('%m-%d %H:%M')


def _vlive_state_text(v: dict) -> tuple:
    """返回 (状态文本, 剩余场次)。"""
    now_ts = datetime.now().timestamp()
    schedules = v.get('virtualLiveSchedules') or []
    rest = sum(1 for s in schedules if s.get('startAt', 0) / 1000 > now_ts)
    current = next((s for s in schedules if s.get('endAt', 0) / 1000 > now_ts), None)
    if current and current.get('startAt', 0) / 1000 <= now_ts:
        return '当前Live进行中!', rest
    if current:
        return f'下一场: {_fmt_ts(current["startAt"])}', rest
    return '已无剩余场次', rest


def draw_vlive_cards(title: str, vlives: List[dict], banners: dict, footer: str = '') -> Image.Image:
    """带 banner 缩略图的虚拟Live卡片列表。

    banners: {vlive_id: PIL.Image | None}
    """
    f_title = get_pjsk_font('SourceHanSansCN-Bold.otf', 26)
    f_name = get_pjsk_font('SourceHanSansCN-Bold.otf', 20)
    f_body = get_pjsk_font('SourceHanSansCN-Medium.otf', 17)
    f_small = get_pjsk_font('SourceHanSansCN-Medium.otf', 13)

    pad = 24
    card_h = 132
    card_gap = 14
    title_h = 62
    banner_w, banner_h = 200, 108
    width = 760
    height = pad * 2 + title_h + len(vlives) * (card_h + card_gap) + (26 if footer else 0)

    img = _panel_bg(width, height)
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((pad - 8, pad - 6, width - pad + 8, pad + title_h - 16), radius=16, fill=(255, 255, 255, 235))
    d.rounded_rectangle((pad + 4, pad + 8, pad + 10, pad + 34), radius=3, fill=(255, 128, 178))
    d.text((pad + 22, pad + 8), title, font=f_title, fill=(60, 34, 60))

    y = pad + title_h
    for v in vlives:
        d.rounded_rectangle((pad - 6, y, width - pad + 6, y + card_h), radius=16, fill=(255, 255, 255))
        banner = banners.get(v.get('id'))
        text_x = pad + 8
        if banner is not None:
            bx, by = pad + 4, y + (card_h - banner_h) // 2
            mask = Image.new('L', (banner_w, banner_h), 0)
            ImageDraw.Draw(mask).rounded_rectangle((0, 0, banner_w - 1, banner_h - 1), radius=12, fill=255)
            img.paste(banner, (bx, by), mask)
            text_x = bx + banner_w + 16

        name = v.get('name', '')
        max_w = width - pad - text_x - 12
        while name and d.textlength(f'【{v["id"]}】{name}', font=f_name) > max_w:
            name = name[:-1]
        d.text((text_x, y + 16), f'【{v["id"]}】{name}', font=f_name, fill=(48, 32, 56))

        state, rest = _vlive_state_text(v)
        d.text((text_x, y + 48), f'开始 {_fmt_ts(v.get("startAt", 0))}  结束 {_fmt_ts(v.get("endAt", 0))}',
               font=f_body, fill=(96, 82, 104))
        d.text((text_x, y + 76), state, font=f_body, fill=(196, 72, 128))
        rest_text = f'剩余 {rest} 场'
        rw = int(d.textlength(rest_text, font=f_small)) + 22
        d.rounded_rectangle((width - pad - rw - 4, y + 74, width - pad - 4, y + 100), radius=13,
                            fill=(255, 246, 251), outline=(245, 218, 232))
        d.text((width - pad - rw // 2 - 4, y + 87), rest_text, font=f_small, fill=(150, 96, 126), anchor='mm')
        y += card_h + card_gap

    if footer:
        d.text((pad, height - 24), footer, font=f_small, fill=(130, 110, 130))
    return img


async def fetch_vlive_banners(vlives: List[dict], pjsk_type: int = 0) -> dict:
    """并发拉取各 vlive 的 banner 缩略图，失败的记为 None。"""
    async def _one(v: dict):
        asset = v.get('assetbundleName')
        if not asset:
            return v.get('id'), None
        try:
            img = await get_pjsk_asset_cached(
                f'virtual_live/select/banner/{asset}',
                f'{asset}.png',
                pjsk_type=pjsk_type,
                mode='RGBA',
                size=(200, 108),
            )
        except Exception as e:
            logger.debug(f'[pjsk订阅] 拉取 vlive banner 失败 {asset}: {e}')
            img = None
        return v.get('id'), img

    results = await asyncio.gather(*[_one(v) for v in vlives], return_exceptions=True)
    return {r[0]: r[1] for r in results if not isinstance(r, Exception)}


@register("notify_rows")
async def render_notify_rows(payload: dict) -> bytes:
    """文字列表卡片（新曲上线等）。载荷：title、rows、footer。"""
    return await run_pjsk_thread(
        lambda: image_to_png(_draw_rows_image(
            payload.get("title") or '',
            [list(row) for row in payload.get("rows") or []],
            payload.get("footer") or '',
        ))
    )


@register("vlive_cards")
async def render_vlive_cards(payload: dict) -> bytes:
    """虚拟 Live 卡片列表。载荷：title、vlives、footer、pjsk_type。

    banner 缩略图由服务自己拉取，指令侧只需给出 vlive 数据。
    """
    vlives = list(payload.get("vlives") or [])
    banners = await fetch_vlive_banners(vlives, int(payload.get("pjsk_type", 0)))
    return await run_pjsk_thread(
        lambda: image_to_png(draw_vlive_cards(
            payload.get("title") or '',
            vlives,
            banners,
            payload.get("footer") or '',
        ))
    )
