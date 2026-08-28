"""表情包总目录。

kndbot 的表情分散在三个插件里：
  * petpet     —— 头像类，底图已按本项目需要改过
  * memes      —— 文字类
  * meme_extra —— 从上游 meme-generator 增量引入的部分

三边各有各的帮助指令时，群友不翻遍三条命令就不知道有哪些能用。
这里把三份合成一张总表，三个帮助指令共用。

注意：本模块**只在指令触发时**才去 import 那三个插件。它们都由 nonebot 的
PluginManager 管理，在插件加载期交叉 import 会打乱加载顺序，等到有人真正
发指令时再取就没有这个问题了。
"""

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
    from plugins.petpet.data_source import commands

    # 第 0 条是「改图/图片操作」入口，它有独立的帮助图，不混进表情列表。
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
    """逐个取，某个插件没加载起来也不影响其余部分出图。"""
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
    """把一节的条目按固定列数排版，返回整块。"""
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
