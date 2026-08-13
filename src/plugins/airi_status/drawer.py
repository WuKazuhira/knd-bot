# Adapted from AiriCore plugins/airi_status (MIT License)
from __future__ import annotations

import os
import platform
import random
from io import BytesIO

import nonebot
import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont
from nonebot import __version__ as __nb_version__

from .color import (
    cpu_color,
    details_color,
    disk_color,
    nickname_color,
    ram_color,
    swap_color,
    transparent_color,
)
from .model import get_status_info
from .path import adlam_font_path, baotu_font_path, bg_dir_path, marker_img_path, mask_img_path, spicy_font_path
from .utils import truncate_string


def _load_font(path, size: int):
    try:
        return ImageFont.truetype(str(path), size)
    except Exception:
        return ImageFont.load_default(size=size)


adlam_fnt = _load_font(adlam_font_path, 36)
spicy_fnt = _load_font(spicy_font_path, 38)
baotu_fnt = _load_font(baotu_font_path, 64)


class LiquidGlass:
    def __init__(
        self,
        displacement_scale: float = 70,
        blur_amount: float = 12,
        saturation: float = 140,
        aberration_intensity: float = 2,
        corner_radius: int = 999,
        edge_highlight_intensity: float = 0.3,
        white_overlay_opacity: float = 0.25,
    ):
        self.displacement_scale = displacement_scale
        self.blur_amount = blur_amount
        self.saturation = saturation
        self.aberration_intensity = aberration_intensity
        self.corner_radius = corner_radius
        self.edge_highlight_intensity = edge_highlight_intensity
        self.white_overlay_opacity = white_overlay_opacity

    def _create_rounded_mask(self, size: tuple[int, int]) -> Image.Image:
        scale_factor = 4
        w, h = size
        large_size = (w * scale_factor, h * scale_factor)
        mask = Image.new("L", large_size, 0)
        draw = ImageDraw.Draw(mask)
        draw.rounded_rectangle([(0, 0), large_size], radius=self.corner_radius * scale_factor, fill=255)
        return mask.resize(size, Image.Resampling.LANCZOS)

    def _create_edge_highlight(self, size: tuple[int, int]) -> Image.Image:
        w, h = size
        highlight = Image.new("RGBA", (w, h), (255, 255, 255, 0))
        alpha_outer = min(255, max(0, int(255 * self.edge_highlight_intensity * 0.8)))
        alpha_inner = min(255, max(0, int(255 * self.edge_highlight_intensity * 1.2)))
        temp_outer = Image.new("RGBA", (w, h), (255, 255, 255, 0))
        temp_draw = ImageDraw.Draw(temp_outer)
        temp_draw.rounded_rectangle([(0, 0), (w, h)], radius=max(self.corner_radius - 1, 0), outline=(255, 255, 255, alpha_outer), width=3)
        temp_outer = temp_outer.filter(ImageFilter.GaussianBlur(radius=1))
        temp_inner = Image.new("RGBA", (w, h), (255, 255, 255, 0))
        temp_draw_inner = ImageDraw.Draw(temp_inner)
        temp_draw_inner.rounded_rectangle([(1, 1), (w - 1, h - 1)], radius=max(self.corner_radius - 1, 0), outline=(255, 255, 255, alpha_inner), width=1)
        highlight = Image.alpha_composite(highlight, temp_outer)
        return Image.alpha_composite(highlight, temp_inner)

    def _adjust_saturation(self, img: Image.Image, saturation: float) -> Image.Image:
        hsv = img.convert("RGB").convert("HSV")
        h, s, v = hsv.split()
        s = Image.fromarray(np.clip(np.array(s) * (saturation / 100), 0, 255).astype(np.uint8))
        return Image.merge("HSV", (h, s, v)).convert("RGB")

    def _chromatic_aberration(self, img: Image.Image, intensity: float) -> Image.Image:
        img_array = np.array(img)
        r, g, b = img_array[:, :, 0], img_array[:, :, 1], img_array[:, :, 2]
        offset = int(intensity)
        aberration = np.dstack([np.roll(r, offset, axis=1), g, np.roll(b, -offset, axis=1)])
        return Image.fromarray(aberration.astype(np.uint8))

    def _generate_displacement_map(self, size: tuple[int, int]) -> Image.Image:
        w, h = size
        x = np.linspace(0, 1, w)
        y = np.linspace(0, 1, h)
        xx, yy = np.meshgrid(x, y)
        dist = np.sqrt((xx - 0.5) ** 2 + (yy - 0.5) ** 2)
        displacement = np.clip(1 - dist * 2, 0, 1)
        disp = (displacement * 255).astype(np.uint8)
        return Image.fromarray(np.dstack([disp, disp, disp]))

    def _apply_displacement(self, img: Image.Image, disp_map: Image.Image, scale: float) -> Image.Image:
        img_array = np.array(img)
        disp_array = np.array(disp_map)
        h, w = img_array.shape[:2]
        xx, yy = np.meshgrid(np.arange(w), np.arange(h))
        dx = (disp_array[:, :, 0] / 255 - 0.5) * scale
        dy = (disp_array[:, :, 1] / 255 - 0.5) * scale
        new_xx = np.clip(xx + dx, 0, w - 1).astype(int)
        new_yy = np.clip(yy + dy, 0, h - 1).astype(int)
        return Image.fromarray(img_array[new_yy, new_xx].astype(np.uint8))

    def apply(self, background_img: Image.Image, glass_position: tuple[int, int], glass_size: tuple[int, int]) -> Image.Image:
        x, y = glass_position
        w, h = glass_size
        glass_region = background_img.crop((x, y, x + w, y + h)).copy()
        rounded_mask = self._create_rounded_mask((w, h))
        blurred = glass_region.filter(ImageFilter.GaussianBlur(radius=self.blur_amount))
        saturated = self._adjust_saturation(blurred, self.saturation)
        displaced = self._apply_displacement(saturated, self._generate_displacement_map((w, h)), self.displacement_scale)
        aberrated = self._chromatic_aberration(displaced, self.aberration_intensity).convert("RGBA")
        overlay = Image.new("RGBA", (w, h), (0, 0, 0, int(self.white_overlay_opacity * 255)))
        glass = Image.alpha_composite(aberrated, overlay)
        glass.putalpha(rounded_mask)
        glass = Image.alpha_composite(glass, self._create_edge_highlight((w, h)))
        result = background_img.convert("RGBA")
        result.paste(glass, (x, y), glass)
        return result.convert("RGB")


def _fallback_background(size=(1080, 1814)) -> Image.Image:
    w, h = size
    arr = np.zeros((h, w, 3), dtype=np.uint8)
    for y in range(h):
        t = y / max(h - 1, 1)
        arr[y, :, 0] = int(52 + 95 * t)
        arr[y, :, 1] = int(112 + 50 * (1 - t))
        arr[y, :, 2] = int(190 + 45 * (1 - t))
    return Image.fromarray(arr, "RGB").convert("RGBA")


def _load_background() -> Image.Image:
    try:
        files = [p for p in bg_dir_path.iterdir() if p.suffix.lower() in {".jpg", ".jpeg", ".png"}]
        if files:
            return Image.open(random.choice(files)).convert("RGBA")
    except Exception:
        pass
    return _fallback_background()


def _ratio(usage: float, total: float) -> float:
    if total <= 0:
        return 0.0
    return max(0.0, min(1.0, usage / total))


def _draw_arc(draw: ImageDraw.ImageDraw, box, ratio: float, fill, width: int):
    if ratio <= 0:
        return
    draw.arc(box, start=-90, end=ratio * 360 - 90, width=width, fill=fill)


def _draw_peach_icon(layer: Image.Image, center: tuple[int, int], color: tuple[int, int, int, int]):
    """绘制简化桃子图标，替代 mask 中带灰环的原始水果图层。"""
    cx, cy = center
    icon = Image.new("RGBA", layer.size, (255, 255, 255, 0))
    d = ImageDraw.Draw(icon)
    shadow = (max(color[0] - 45, 0), max(color[1] - 45, 0), max(color[2] - 45, 0), 130)
    hi = (min(color[0] + 35, 255), min(color[1] + 35, 255), min(color[2] + 35, 255), 230)
    d.ellipse((cx - 38, cy - 22, cx + 6, cy + 34), fill=color)
    d.ellipse((cx - 6, cy - 22, cx + 38, cy + 34), fill=color)
    d.polygon([(cx - 35, cy + 5), (cx, cy + 48), (cx + 35, cy + 5)], fill=color)
    d.arc((cx - 28, cy - 42, cx + 28, cy + 18), 210, 330, fill=shadow, width=4)
    d.ellipse((cx - 26, cy - 12, cx + 6, cy + 20), fill=hi)
    d.pieslice((cx - 6, cy - 50, cx + 44, cy - 10), 190, 330, fill=(105, 190, 145, 180))
    layer.alpha_composite(icon)


def _bot_nickname() -> str:
    return "KndBot"


def draw() -> bytes:
    loaded_plugins = nonebot.get_loaded_plugins()
    mask_img = Image.open(mask_img_path).convert("RGBA") if mask_img_path.exists() else Image.new("RGBA", (1080, 1814), (0, 0, 0, 0))
    canvas_size = mask_img.size
    base = _load_background()
    if base.size != canvas_size:
        base = base.resize(canvas_size, Image.Resampling.LANCZOS)

    marker = Image.open(marker_img_path).convert("RGBA") if marker_img_path.exists() else None
    img = Image.new("RGBA", canvas_size, (255, 255, 255, 0))

    sx = canvas_size[0] / 1080
    sy = canvas_size[1] / 1814

    def p(x: int, y: int) -> tuple[int, int]:
        return int(x * sx), int(y * sy)

    def rect(x: int, y: int, w: int, h: int) -> tuple[tuple[int, int], tuple[int, int]]:
        return p(x, y), (int(w * sx), int(h * sy))

    base = Image.alpha_composite(base, Image.new("RGBA", base.size, (0, 0, 0, 40)))
    liquid_glass = LiquidGlass(displacement_scale=50, blur_amount=16, saturation=160, aberration_intensity=2, corner_radius=50, edge_highlight_intensity=0.2, white_overlay_opacity=0.35)
    pos, size = rect(95, 544, 888, 708)
    base = liquid_glass.apply(base, glass_position=pos, glass_size=size)
    pos, size = rect(95, 1282, 888, 236)
    base = liquid_glass.apply(base, glass_position=pos, glass_size=size)
    pos, size = rect(95, 1547, 888, 122)
    base = liquid_glass.apply(base, glass_position=pos, glass_size=size).convert("RGBA")

    cpu, ram, swap, disk = get_status_info()
    cpu_info = f"{cpu.usage}% - {cpu.freq}GHz [{cpu.core}/{cpu.logical_core} cores]"
    ram_info = f"{ram.usage} / {ram.total} GB"
    swap_info = f"{swap.usage} / {swap.total} GB"
    disk_info = f"{disk.usage} / {disk.total} GB"

    ssaa_scale = 4
    img_w, img_h = img.size
    temp_img = Image.new("RGBA", (img_w * ssaa_scale, img_h * ssaa_scale), (255, 255, 255, 0))
    temp_draw = ImageDraw.Draw(temp_img)

    def s(val):
        return int(val * ssaa_scale)

    def sb(x1, y1, x2, y2):
        px1, py1 = p(x1, y1)
        px2, py2 = p(x2, y2)
        return s(px1), s(py1), s(px2), s(py2)

    ring_boxes = [
        (150, 690, 270, 810, cpu.usage / 100, cpu_color),
        (150, 835, 270, 955, _ratio(ram.usage, ram.total), ram_color),
        (150, 981, 270, 1101, _ratio(swap.usage, swap.total), swap_color),
        (150, 1112, 270, 1232, _ratio(disk.usage, disk.total), disk_color),
    ]
    for x1, y1, x2, y2, ratio, color in ring_boxes:
        # 先画完整的灰色底环，再画彩色进度弧，避免依赖 mask 里偏移的半圆装饰。
        temp_draw.ellipse(sb(x1, y1, x2, y2), outline=(220, 215, 205, 210), width=s(9))
        _draw_arc(temp_draw, sb(x1, y1, x2, y2), ratio, color, s(9))
    img = Image.alpha_composite(img, temp_img.resize((img_w, img_h), Image.Resampling.LANCZOS))
    for center, color in [
        (p(210, 750), (92, 174, 246, 230)),
        (p(210, 895), (255, 150, 185, 230)),
        (p(210, 1041), (255, 154, 112, 230)),
        (p(210, 1172), (212, 174, 135, 230)),
    ]:
        _draw_peach_icon(img, center, color)

    content = ImageDraw.Draw(img)
    nickname = truncate_string(_bot_nickname(), 18)
    content.text(p(155, 562), nickname, font=baotu_fnt, fill=nickname_color)
    value_x = 430
    label_x = 300
    rows = [
        ("CPU", cpu_info, 728, cpu_color),
        ("RAM", ram_info, 874, ram_color),
        ("SWAP", swap_info, 1020, swap_color),
        ("DISK", disk_info, 1166, disk_color),
    ]
    for label, value, y, color in rows:
        content.text(p(label_x, y), label, font=spicy_fnt, fill=color)
        content.text(p(value_x, y), value, font=spicy_fnt, fill=color)

    system = platform.uname()
    cpu_brand = truncate_string(cpu.brand or platform.processor() or platform.machine(), 28)
    system_text = truncate_string(f"{system.system} {system.release} {system.machine}", 30)
    details = [
        cpu_brand,
        system_text,
        f"NoneBot {__nb_version__}",
        f"{len(loaded_plugins)} loaded",
    ]
    detail_labels = ["CPU", "System", "Version", "Plugins"]
    for idx, text in enumerate(details):
        y = 1302 + idx * 50
        content.text(p(150, y), detail_labels[idx], font=adlam_fnt, fill=details_color)
        content.text(p(352, y), text, font=adlam_fnt, fill=details_color)

    if marker is not None:
        try:
            nickname_length = int(baotu_fnt.getlength(nickname))
        except Exception:
            nickname_length = len(nickname) * 40
        marker_pos = p(155 + nickname_length + 60, 570)
        img.paste(marker, marker_pos, marker)

    # 原 AiriCore mask 自带固定英文标签和版权文字，迁移后会和动态内容错位；只保留水果/装饰图标，标签改由代码绘制。
    mask_clean = mask_img.copy()
    mask_draw = ImageDraw.Draw(mask_clean)
    # 擦掉原 mask 中固定的圆环/标签区域；水果图标单独裁出后按我们自己的圆心重贴。
    mask_draw.rectangle((*p(90, 650), *p(300, 1335)), fill=(255, 255, 255, 0))
    mask_draw.rectangle((*p(245, 670), *p(425, 1235)), fill=(255, 255, 255, 0))
    mask_draw.rectangle((*p(115, 1275), *p(295, 1555)), fill=(255, 255, 255, 0))
    mask_draw.rectangle((*p(0, 1490), *p(1080, 1814)), fill=(255, 255, 255, 0))
    footer = Image.new("RGBA", canvas_size, (255, 255, 255, 0))
    footer_draw = ImageDraw.Draw(footer)
    footer_draw.text(p(150, 1608), "Adapted from AiriCore status · MIT", font=adlam_fnt, fill=(220, 220, 220, 210))
    out = Image.alpha_composite(Image.alpha_composite(base, mask_clean), Image.alpha_composite(img, footer))
    byte_io = BytesIO()
    out.save(byte_io, format="png")
    return byte_io.getvalue()
