from __future__ import annotations

import math
from dataclasses import dataclass, field
from io import BytesIO
from typing import Callable

from nonebot.utils import run_sync

from services.log import logger
from utils.imageutils import BuildImage, Text2Image

BG_COLOR = "#f7f5fb"
TITLE_COLOR = "#2f2b3a"
SECTION_COLOR = "#6a4fa3"
HINT_COLOR = "#6b6b7b"
COLUMNS = 4


@dataclass
class Section:
    title: str
    hint: str
    entries: list[str] = field(default_factory=list)


def _petpet_entries() -> list[str]:
    # 延迟 import，避免插件加载期互相引用
    from plugins.petpet.data_source import commands

    # 第 0 条是改图入口，单算帮助图
    return ["/".join(c.keywords) for c in commands[1:] if c.keywords]


def _memes_entries() -> list[str]:
    from plugins.memes.data_source import memes

    return ["/".join(m.keywords) for m in memes if m.keywords]


def _extra_entries() -> list[str]:
    from plugins.meme_extra import SPECS

    return ["/".join(s.keywords) for s in SPECS if s.keywords]


_SOURCES: list[tuple[str, str, Callable[[], list[str]]]] = [
    ("头像表情包", "指令 + @某人 / qq号 / 自己 / 图片", _petpet_entries),
    ("表情包制作", "指令 + 文字，多段文字用空格隔开", _memes_entries),
    ("扩展表情", "同上；只发指令则用你的头像或默认文案", _extra_entries),
]


def collect_sections() -> list[Section]:
    sections: list[Section] = []
    for title, hint, getter in _SOURCES:
        try:
            entries = getter()
        except Exception as e:
            logger.warning(f"[meme_catalog] 读取 {title} 失败，本节跳过: {e}")
            continue
        if entries:
            sections.append(Section(title=title, hint=hint, entries=entries))
    return sections


def _columns_image(entries: list[str], start: int) -> BuildImage:
    per_column = math.ceil(len(entries) / COLUMNS)
    chunks = [entries[i:i + per_column] for i in range(0, len(entries), per_column)]
    images = []
    for col_idx, chunk in enumerate(chunks):
        base = start + col_idx * per_column
        text = "\n".join(f"{base + i}. {name}" for i, name in enumerate(chunk))
        images.append(Text2Image.from_text(text, 26).to_image(padding=(22, 8)))
    width = sum(img.width for img in images)
    height = max(img.height for img in images)
    block = BuildImage.new("RGBA", (width, height), BG_COLOR)
    x = 0
    for img in images:
        block.paste(img, (x, 0), alpha=True)
        x += img.width
    return block


@run_sync
def render_catalog() -> BytesIO:
    sections = collect_sections()
    total = sum(len(s.entries) for s in sections)

    blocks: list[BuildImage] = []
    header = Text2Image.from_text(
        f"表情包总目录　共 {total} 个",
        38,
        weight="bold",
        fill=TITLE_COLOR,
    ).to_image(padding=(24, 14))
    blocks.append(header)

    index = 1
    for section in sections:
        head = Text2Image.from_text(
            f"【{section.title}】{len(section.entries)} 个　—　{section.hint}",
            28,
            weight="bold",
            fill=SECTION_COLOR,
        ).to_image(padding=(24, 12))
        blocks.append(head)
        blocks.append(_columns_image(section.entries, index))
        index += len(section.entries)

    tail = Text2Image.from_text(
        "改图 / 图片操作 的指令请发送「改图指令」查看",
        24,
        fill=HINT_COLOR,
    ).to_image(padding=(24, 14))
    blocks.append(tail)

    width = max(b.width for b in blocks)
    height = sum(b.height for b in blocks)
    canvas = BuildImage.new("RGBA", (width, height), BG_COLOR)
    y = 0
    for block in blocks:
        canvas.paste(block, (0, y), alpha=True)
        y += block.height
    return canvas.save_jpg()
