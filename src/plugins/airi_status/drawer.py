# Adapted from AiriCore plugins/airi_status (MIT License)

from __future__ import annotations

import json
import math
import os
import platform
import random
import time
from io import BytesIO
from typing import Optional

import nonebot
import psutil
from nonebot import __version__ as __nb_version__
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont

from config.path_config import FONT_PATH

from .color import cpu_color, details_color, disk_color, nickname_color, ram_color, swap_color
from .model import get_status_info
from .path import baotu_font_path, bg_dir_path, mask_img_path
from .utils import truncate_string

CANVAS = (1080, 1814)

# mask 里四个图标位置，行距 154px
ICON_BOX = (154, 723, 272, 840)
ICON_SRC_STEP = 154
ICON_SRC_SIZE = ICON_BOX[2] - ICON_BOX[0]

MAIN_CARD = (95, 544, 983, 1258)
DETAIL_CARD = (95, 1288, 983, 1524)
FOOTER_CARD = (95, 1553, 983, 1655)

ICON_SIZE = 104
ROW_STEP = 140
ROW_TOP = 690          # 第一行图标顶边
ICON_X = 158
LABEL_X = 300
VALUE_X = 424
RIGHT_EDGE = 946       # 百分比右对齐基线

# 圆环相对图标框的比例
RING_CENTER_RATIO = (59 / ICON_SRC_SIZE, 58 / ICON_SRC_SIZE)
RING_RADIUS_RATIO = 57 / ICON_SRC_SIZE
RING_WIDTH = 8

CARD_RADIUS = 46
TEXT_SHADOW = (0, 0, 0, 150)


def _font(path, size: int) -> ImageFont.FreeTypeFont:
    try:
        return ImageFont.truetype(str(path), size)
    except Exception:
        return ImageFont.load_default(size=size)


def _cjk(size: int, weight: str = "Medium") -> ImageFont.FreeTypeFont:
    return _font(FONT_PATH / f"SourceHanSansCN-{weight}.otf", size)


title_fnt = _font(baotu_font_path, 62)
title_fallback_fnt = _cjk(58, "Heavy")
label_fnt = _cjk(34, "Bold")
value_fnt = _cjk(34, "Medium")
pct_fnt = _cjk(26, "Bold")
detail_label_fnt = _cjk(30, "Medium")
detail_value_fnt = _cjk(30, "Normal")
footer_fnt = _cjk(24, "Normal")
alias_fnt = _cjk(25, "Normal")


def _rounded_mask(size: tuple[int, int], radius: int, ssaa: int = 4) -> Image.Image:
    w, h = size
    big = Image.new("L", (w * ssaa, h * ssaa), 0)
    ImageDraw.Draw(big).rounded_rectangle(
        (0, 0, w * ssaa - 1, h * ssaa - 1), radius=radius * ssaa, fill=255
    )
    return big.resize(size, Image.Resampling.LANCZOS)


def _frosted_card(base: Image.Image, box: tuple[int, int, int, int], radius: int = CARD_RADIUS) -> None:
    x0, y0, x1, y1 = box
    w, h = x1 - x0, y1 - y0
    region = base.crop(box).convert("RGB")

    # 先降采样再模糊，快且能压住背景大色块
    small = region.resize((max(w // 10, 1), max(h // 10, 1)), Image.Resampling.LANCZOS)
    small = small.filter(ImageFilter.GaussianBlur(radius=8))
    blurred = small.resize((w, h), Image.Resampling.LANCZOS)
    blurred = ImageEnhance.Color(blurred).enhance(0.85)

    glass = blurred.convert("RGBA")
    # 压暗底色再叠白膜，让浅色字看得清
    glass = Image.alpha_composite(glass, Image.new("RGBA", (w, h), (14, 16, 30, 104)))
    glass = Image.alpha_composite(glass, Image.new("RGBA", (w, h), (255, 255, 255, 20)))
    # 顶部高光
    sheen = Image.new("RGBA", (w, h), (255, 255, 255, 0))
    sheen_draw = ImageDraw.Draw(sheen)
    for i in range(h // 3):
        alpha = int(22 * (1 - i / max(h // 3, 1)))
        sheen_draw.line([(0, i), (w, i)], fill=(255, 255, 255, alpha))
    glass = Image.alpha_composite(glass, sheen)

    mask = _rounded_mask((w, h), radius)
    glass.putalpha(mask)

    border = Image.new("RGBA", (w, h), (255, 255, 255, 0))
    bd = ImageDraw.Draw(border)
    bd.rounded_rectangle((0, 0, w - 1, h - 1), radius=radius, outline=(255, 255, 255, 96), width=2)
    bd.rounded_rectangle((2, 2, w - 3, h - 3), radius=max(radius - 2, 0), outline=(255, 255, 255, 38), width=1)
    border.putalpha(Image.composite(border.getchannel("A"), Image.new("L", (w, h), 0), mask))
    glass = Image.alpha_composite(glass, border)

    base.paste(glass, (x0, y0), glass)


def _text(draw: ImageDraw.ImageDraw, xy, text: str, font, fill, shadow: bool = True) -> None:
    if shadow:
        draw.text((xy[0] + 2, xy[1] + 2), text, font=font, fill=TEXT_SHADOW)
    draw.text(xy, text, font=font, fill=fill)


def _pick_title_font(text: str) -> ImageFont.FreeTypeFont:
    # baotu 缺中文字，遇到中文退回思源黑体
    try:
        if all(title_fnt.getmask(ch).getbbox() for ch in text if not ch.isspace()):
            return title_fnt
    except Exception:
        pass
    return title_fallback_fnt


def _fallback_background() -> Image.Image:
    w, h = CANVAS
    bg = Image.new("RGB", (w, h), (60, 96, 168))
    d = ImageDraw.Draw(bg)
    for y in range(h):
        t = y / (h - 1)
        d.line([(0, y), (w, y)], fill=(int(52 + 95 * t), int(112 + 50 * (1 - t)), int(190 + 45 * (1 - t))))
    return bg


def _load_background() -> Image.Image:
    try:
        files = [p for p in bg_dir_path.iterdir() if p.suffix.lower() in {".jpg", ".jpeg", ".png"}]
        if files:
            img = Image.open(random.choice(files)).convert("RGB")
            if img.size != CANVAS:
                img = img.resize(CANVAS, Image.Resampling.LANCZOS)
            return img
    except Exception:
        pass
    return _fallback_background()


_mask_cache: Optional[Image.Image] = None


def _icon(index: int) -> Optional[Image.Image]:
    global _mask_cache
    if _mask_cache is None:
        if not mask_img_path.exists():
            return None
        _mask_cache = Image.open(mask_img_path).convert("RGBA")
    x0, y0, x1, y1 = ICON_BOX
    offset = ICON_SRC_STEP * index
    icon = _mask_cache.crop((x0, y0 + offset, x1, y1 + offset))
    return icon.resize((ICON_SIZE, ICON_SIZE), Image.Resampling.LANCZOS)


def _ratio(usage: float, total: float) -> float:
    if total <= 0:
        return 0.0
    return max(0.0, min(1.0, usage / total))


def _draw_ring(canvas: Image.Image, top_left: tuple[int, int], ratio: float, color, ssaa: int = 4) -> None:
    layer = Image.new("RGBA", (ICON_SIZE * ssaa, ICON_SIZE * ssaa), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    cx = RING_CENTER_RATIO[0] * ICON_SIZE * ssaa
    cy = RING_CENTER_RATIO[1] * ICON_SIZE * ssaa
    r = RING_RADIUS_RATIO * ICON_SIZE * ssaa
    width = RING_WIDTH * ssaa
    box = (cx - r, cy - r, cx + r, cy + r)
    # 底环
    d.arc(box, 0, 360, fill=(255, 255, 255, 64), width=width)
    if ratio > 0:
        end = -90 + ratio * 360
        d.arc(box, -90, end, fill=color, width=width)
        for ang in (-90, end):  # 圆头端点
            px = cx + r * math.cos(math.radians(ang))
            py = cy + r * math.sin(math.radians(ang))
            d.ellipse((px - width / 2, py - width / 2, px + width / 2, py + width / 2), fill=color)
    layer = layer.resize((ICON_SIZE, ICON_SIZE), Image.Resampling.LANCZOS)
    canvas.alpha_composite(layer, top_left)


def _uptime_text() -> str:
    try:
        seconds = int(time.time() - psutil.Process().create_time())
    except Exception:
        return "unknown"
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    if days:
        return f"{days}天 {hours}小时 {minutes}分"
    if hours:
        return f"{hours}小时 {minutes}分"
    return f"{minutes}分"


def _bot_nickname() -> str:
    # 不用 driver.config.nickname，它是 Set 顺序不稳定
    try:
        from config.config import NICKNAME

        if NICKNAME:
            return str(NICKNAME)
    except Exception:
        pass
    return "KndBot"


def _nickname_aliases(exclude: str = "") -> list[str]:
    # 优先直接读 env 保住顺序，driver.config.nickname 是 Set
    names: list[str] = []
    raw = os.getenv("NICKNAME")
    if raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                names = [str(n).strip() for n in parsed if str(n).strip()]
        except (ValueError, TypeError):
            names = [n.strip() for n in raw.strip("[]").replace('"', "").split(",") if n.strip()]
    if not names:
        try:
            names = sorted(str(n) for n in (nonebot.get_driver().config.nickname or []))
        except Exception:
            names = []
    seen, ordered = set(), []
    for name in names:
        if name and name != exclude and name not in seen:
            seen.add(name)
            ordered.append(name)
    return ordered


def draw() -> bytes:
    cpu, ram, swap, disk = get_status_info()
    try:
        plugin_count = len(nonebot.get_loaded_plugins())
    except Exception:
        plugin_count = 0

    base = _load_background().convert("RGBA")
    # 背景压暗
    base = Image.alpha_composite(base, Image.new("RGBA", CANVAS, (10, 12, 26, 56)))

    for box in (MAIN_CARD, DETAIL_CARD, FOOTER_CARD):
        _frosted_card(base, box)

    layer = Image.new("RGBA", CANVAS, (0, 0, 0, 0))
    draw_ctx = ImageDraw.Draw(layer)

    nickname = truncate_string(_bot_nickname(), 18)
    _text(draw_ctx, (ICON_X, 566), nickname, _pick_title_font(nickname), nickname_color)
    aliases = _nickname_aliases(exclude=nickname)
    if aliases:
        alias_line = truncate_string(" / ".join(aliases), 40)
        _text(draw_ctx, (ICON_X + 4, 638), alias_line, alias_fnt, (226, 214, 232, 225))

    rows = [
        ("CPU", f"{cpu.freq}GHz · {cpu.core}核/{cpu.logical_core}线程", cpu.usage / 100, cpu_color),
        ("RAM", f"{ram.usage} / {ram.total} GB", _ratio(ram.usage, ram.total), ram_color),
        ("SWAP", f"{swap.usage} / {swap.total} GB", _ratio(swap.usage, swap.total), swap_color),
        ("DISK", f"{disk.usage} / {disk.total} GB", _ratio(disk.usage, disk.total), disk_color),
    ]
    for idx, (label, value, ratio, color) in enumerate(rows):
        top = ROW_TOP + ROW_STEP * idx
        icon = _icon(idx)
        if icon is not None:
            layer.alpha_composite(icon, (ICON_X, top))
        _draw_ring(layer, (ICON_X, top), ratio, color)

        center_y = top + ICON_SIZE // 2
        _text(draw_ctx, (LABEL_X, center_y - 22), label, label_fnt, color)
        _text(draw_ctx, (VALUE_X, center_y - 22), value, value_fnt, (238, 240, 246, 255))
        pct = f"{round(ratio * 100)}%"
        _text(
            draw_ctx,
            (RIGHT_EDGE - draw_ctx.textlength(pct, font=pct_fnt), center_y - 17),
            pct,
            pct_fnt,
            color,
        )

    system = platform.uname()
    details = [
        ("CPU", truncate_string(cpu.brand or platform.processor() or platform.machine(), 30)),
        ("System", truncate_string(f"{system.system} {system.release} {system.machine}", 32)),
        ("NoneBot", str(__nb_version__)),
        ("Plugins", f"{plugin_count} 个已加载"),
    ]
    detail_top = DETAIL_CARD[1] + 30
    for idx, (label, value) in enumerate(details):
        y = detail_top + idx * 46
        _text(draw_ctx, (ICON_X - 8, y), label, detail_label_fnt, details_color)
        _text(draw_ctx, (VALUE_X - 72, y), value, detail_value_fnt, (232, 232, 238, 255))

    footer_y = FOOTER_CARD[1] + 34
    _text(draw_ctx, (ICON_X - 8, footer_y), "运行时长", detail_label_fnt, details_color)
    _text(draw_ctx, (VALUE_X - 72, footer_y), _uptime_text(), detail_value_fnt, (232, 232, 238, 255))
    credit = "Adapted from AiriCore status · MIT"
    _text(
        draw_ctx,
        (RIGHT_EDGE - draw_ctx.textlength(credit, font=footer_fnt), footer_y + 8),
        credit,
        footer_fnt,
        (206, 206, 214, 175),
    )

    out = Image.alpha_composite(base, layer).convert("RGB")
    buf = BytesIO()
    # 立绘背景用 JPEG 更快更小，关色度抽样避免文字发糊
    out.save(buf, format="jpeg", quality=92, subsampling=0, optimize=True)
    return buf.getvalue()
