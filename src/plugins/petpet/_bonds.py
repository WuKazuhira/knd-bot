"""羁绊牌：用两个人的 QQ 头像替换 pjsk 羁绊牌里的 chr_sd 小人。

底图与边框直接复用 data/pjsk/masterdata 下的素材，绘制流程对齐
plugins/pjsk/_utils.py 里 bonds 牌子的大图分支（左半底色取 A、右半取 B，
最后叠边框），区别只是把 chr_sd 换成圆形头像。
"""

import base64
import random
import shlex
from dataclasses import dataclass
from io import BytesIO
from typing import List, Optional, Tuple, Union

from nonebot.adapters.onebot.v11 import (
    GroupMessageEvent,
    Message,
    MessageEvent,
    unescape,
)
from nonebot.typing import T_State
from nonebot.utils import run_sync
from PIL import Image, ImageChops, ImageDraw, ImageFont

from config.path_config import FONT_PATH, IMAGE_PATH
from plugins.pjsk._config import data_path
from utils.imageutils import BuildImage, Text2Image

from .depends import REGEX_ARG, download_image
from .utils import UserInfo

BONDS_DIR = data_path / "bonds"
PICS_DIR = data_path / "pics"

# 花/羽/普 对应 frame_degree_m_4 / _2 / _1
CARD_TYPES = {
    "花牌": 4,
    "花": 4,
    "羽牌": 2,
    "羽": 2,
    "普牌": 1,
    "普": 1,
    "普通牌": 1,
    "普通": 1,
}
DEFAULT_FRAME = 4

CANVAS_SIZE = (380, 80)
AVATAR_SIZE = 72
AVATAR_Y = 4
# PJSK 主牌的两名角色分别贴在 (0,-40)、(220,-40)，头像按其可见区域的中心布置。
AVATAR_LEFT_X = 44
AVATAR_RIGHT_X = 264
TEXT_FONT = FONT_PATH / "SourceHanSansCN-Bold.otf"
TEXT_MAX_LENGTH = 12

USAGE = (
    "羁绊牌 @A [颜色] @B [颜色] [花牌/羽牌/普牌] [文字]\n"
    "颜色可省略（省略则随机），牌型可省略（默认花牌），文字可省略\n"
    "例：羁绊牌 @小明 5 @小红 12 羽牌 命运相遇\n"
    "也可发送：羁绊牌 help / 羁绊牌 颜色"
)

_color_cache: Optional[List[int]] = None


def available_colors() -> List[int]:
    """bonds 目录下形如 x.png 的可用颜色编号（_sub 是小图，不参与）。"""
    global _color_cache
    if _color_cache is None:
        colors = []
        for path in BONDS_DIR.glob("*.png"):
            if path.stem.isdigit():
                colors.append(int(path.stem))
        _color_cache = sorted(colors)
    return _color_cache


@dataclass
class _Slot:
    user: UserInfo
    color: Optional[int] = None


def _text_image(text: str, fontsize: int, **kwargs) -> BuildImage:
    return BuildImage(Text2Image.from_text(text, fontsize, **kwargs).to_image())


def _usage_help_image(text: str) -> BytesIO:
    """复用 Kndbot 全局帮助的 usage.jpg + knd.png 背景模板。"""
    textimg = Text2Image.from_text(
        text,
        fontsize=24,
        fontname="SourceHanSansCN-Regular.otf",
        ischeckchar=False,
    ).to_image()
    width, height = textimg.size
    background = BuildImage.open(IMAGE_PATH / "background" / "usage.jpg")
    scale = background.width / background.height
    width = max(int(height * scale), width) * 1.15
    height = max(int(width / scale), height) * 1.2
    width, height = int(width), int(height)
    background = background.resize((width, height))
    chara_size = min(int(0.15 * width), height)
    chara = BuildImage.open(IMAGE_PATH / "background" / "knd.png").resize((chara_size, chara_size))
    background.paste(chara, (int(width - 0.95 * chara_size), int(height - chara_size)), alpha=True)
    background.paste(textimg, (int(width * 0.05), 0), alpha=True, center_type="by_height")
    output = background.save_png()
    output.seek(0)
    return output


def _help_image(kind: str) -> BytesIO:
    if kind == "颜色":
        colors = available_colors()
        title_font = ImageFont.truetype(str(TEXT_FONT), 30)
        label_font = ImageFont.truetype(str(TEXT_FONT), 22)
        note_font = ImageFont.truetype(str(TEXT_FONT), 20)
        preview_size = (228, 48)
        item_width = 320
        item_height = 64
        columns = 2
        rows = (len(colors) + columns - 1) // columns
        margin_x = 36
        top = 92
        bottom = 72
        canvas_width = margin_x * 2 + item_width * columns
        canvas_height = top + item_height * rows + bottom

        background = BuildImage.open(IMAGE_PATH / "background" / "usage.jpg").resize(
            (canvas_width, canvas_height)
        )
        chara_size = min(int(0.15 * canvas_width), canvas_height)
        chara = BuildImage.open(IMAGE_PATH / "background" / "knd.png").resize(
            (chara_size, chara_size)
        )
        background.paste(
            chara,
            (canvas_width - int(0.95 * chara_size), canvas_height - chara_size),
            alpha=True,
        )
        base = background.image.convert("RGBA")
        draw = ImageDraw.Draw(base)
        draw.text((margin_x, 24), "羁绊牌颜色列表", font=title_font, fill=(30, 30, 40, 255))

        for index, color in enumerate(colors):
            column = index % columns
            row = index // columns
            x = margin_x + column * item_width
            y = top + row * item_height
            with Image.open(BONDS_DIR / f"{color}.png") as source:
                preview = source.convert("RGBA").resize(preview_size, Image.Resampling.LANCZOS)
            base.paste(preview, (x, y), preview.getchannel("A"))
            draw.rounded_rectangle(
                (x + preview_size[0] + 8, y + 7, x + preview_size[0] + 68, y + 41),
                radius=8,
                fill=(255, 255, 255, 210),
            )
            draw.text(
                (x + preview_size[0] + 18, y + 8),
                str(color),
                font=label_font,
                fill=(30, 30, 40, 255),
            )

        draw.text(
            (margin_x, canvas_height - 42),
            "使用示例：羁绊牌 @A 5 @B 12",
            font=note_font,
            fill=(30, 30, 40, 255),
        )
        output = BytesIO()
        base.save(output, format="PNG")
        output.seek(0)
        return output

    return _usage_help_image(
        "羁绊牌使用说明\n\n"
        "语法：\n"
        "羁绊牌 @A [颜色] @B [颜色] [花牌/羽牌/普牌] [文字]\n\n"
        "默认行为：\n"
        "颜色省略则随机取色；牌型省略默认为花牌；文字可以省略。\n\n"
        "参数顺序：\n"
        "颜色必须写在对应的人后面；支持 @、QQ号、自己或回复图片。\n"
        "文字放在牌型之后，含空格时请使用引号。\n\n"
        "示例：\n"
        "羁绊牌 @小明 5 @小红 12 羽牌 命运相遇\n"
        "羁绊牌 @A @B 花牌 \"我们的羁绊\"\n\n"
        "羁绊牌 颜色：查看全部颜色预览\n"
        "羁绊牌 说明：查看本说明"
    )


def _parse_message(
    event: MessageEvent, state: T_State
) -> Union[str, BytesIO, Tuple[List[_Slot], int, str]]:
    """按原始消息顺序解析用户、颜色、牌型和末尾文字。"""
    msg: Message = state[REGEX_ARG]
    group = str(event.group_id) if isinstance(event, GroupMessageEvent) else ""
    colors = available_colors()

    plain = msg.extract_plain_text().strip()
    if plain.casefold() in {"help", "说明", "帮助", "用法"}:
        return _help_image("说明")
    if plain in {"颜色", "颜色列表"}:
        return _help_image("颜色")

    slots: List[_Slot] = []
    frame_no = DEFAULT_FRAME
    text_parts: List[str] = []

    if event.reply:
        for img in event.reply.message.get("image", []):
            slots.append(_Slot(UserInfo(img_url=str(img.data.get("url", "")))))

    for seg in msg:
        if seg.type == "at":
            qq = str(seg.data.get("qq", ""))
            if qq:
                slots.append(_Slot(UserInfo(qq=qq, group=group)))
        elif seg.type == "image":
            slots.append(_Slot(UserInfo(img_url=str(seg.data.get("url", "")))))
        elif seg.type == "text":
            try:
                tokens = shlex.split(unescape(str(seg)))
            except ValueError:
                tokens = str(seg).split()
            for token in tokens:
                token = token.strip()
                if not token:
                    continue
                if token in CARD_TYPES:
                    frame_no = CARD_TYPES[token]
                elif token == "自己":
                    slots.append(_Slot(UserInfo(qq=str(event.user_id), group=group)))
                elif token.isdigit() and len(token) >= 5 and len(token) <= 11 and len(slots) < 2:
                    slots.append(_Slot(UserInfo(qq=token)))
                elif token.isdigit() and slots and slots[-1].color is None:
                    number = int(token)
                    if number not in colors:
                        return f"没有颜色 {number}，可用颜色：{', '.join(map(str, colors))}"
                    slots[-1].color = number
                elif len(slots) >= 2:
                    text_parts.append(token)
                else:
                    return f"看不懂参数「{token}」\n" + USAGE

    if len(slots) != 2:
        return f"羁绊牌需要正好两个人，当前识别到 {len(slots)} 个\n" + USAGE
    text = " ".join(text_parts)
    if len(text) > TEXT_MAX_LENGTH:
        return f"文字最多 {TEXT_MAX_LENGTH} 个字符，请缩短后再试"
    return slots, frame_no, text



def _fill_random_colors(slots: List[_Slot]) -> None:
    """未指定的随机取色，尽量避开已用的，免得两边底色一样看不出分界。"""
    colors = available_colors()
    used = {slot.color for slot in slots if slot.color is not None}
    for slot in slots:
        if slot.color is None:
            pool = [c for c in colors if c not in used] or colors
            slot.color = random.choice(pool)
            used.add(slot.color)


def _bonds_background(color1: int, color2: int) -> Image.Image:
    """左半取 color1、右半取 color2，切分点与 pjsk 的 bondsbackground 一致。"""
    with Image.open(BONDS_DIR / f"{color1}.png") as source:
        base = source.convert("RGBA")
    with Image.open(BONDS_DIR / f"{color2}.png") as source:
        right = source.convert("RGBA")
    base.paste(right.crop((190, 0, 380, 80)), (190, 0))
    return base


def _circle_avatar(user: UserInfo) -> Image.Image:
    # 游戏里的小人上下会被裁掉一部分；先裁掉头像上下各约 1/10，
    # 再做方形和圆形裁切，避免把完整头像压进圆框后显得过于缩小。
    image = user.img.convert("RGBA")
    crop_height = int(image.height * 0.1)
    if crop_height > 0 and image.height - crop_height * 2 > 0:
        image = image.crop(
            (0, crop_height, image.width, image.height - crop_height)
        )
    return image.circle().resize((AVATAR_SIZE, AVATAR_SIZE)).image


def _draw_text(card: Image.Image, text: str) -> None:
    if not text:
        return
    draw = ImageDraw.Draw(card)
    max_width = 220
    fontsize = 28
    while fontsize >= 12:
        font = ImageFont.truetype(str(TEXT_FONT), fontsize)
        bbox = draw.textbbox((0, 0), text, font=font, stroke_width=1)
        if bbox[2] - bbox[0] <= max_width:
            break
        fontsize -= 1
    else:
        font = ImageFont.truetype(str(TEXT_FONT), 12)
        bbox = draw.textbbox((0, 0), text, font=font, stroke_width=1)
    x = (CANVAS_SIZE[0] - (bbox[2] - bbox[0])) // 2 - bbox[0]
    y = (CANVAS_SIZE[1] - (bbox[3] - bbox[1])) // 2 - bbox[1]
    draw.text(
        (x, y),
        text,
        font=font,
        fill=(255, 255, 255, 255),
        stroke_width=2,
        stroke_fill=(55, 55, 70, 220),
    )


def _apply_main_mask(card: Image.Image) -> Image.Image:
    with Image.open(PICS_DIR / "mask_degree_main.png") as source:
        mask = source.convert("RGBA").getchannel("A")
    alpha = ImageChops.multiply(card.getchannel("A"), mask)
    card.putalpha(alpha)
    return card


def _draw_card(slots: List[_Slot], frame_no: int, text: str) -> BytesIO:
    card = _bonds_background(slots[0].color, slots[1].color)

    for slot, x in ((slots[0], AVATAR_LEFT_X), (slots[1], AVATAR_RIGHT_X)):
        avatar = _circle_avatar(slot.user)
        ring = Image.new("RGBA", (AVATAR_SIZE + 6, AVATAR_SIZE + 6), (0, 0, 0, 0))
        ImageDraw.Draw(ring).ellipse(
            (0, 0, AVATAR_SIZE + 5, AVATAR_SIZE + 5), fill=(255, 255, 255, 255)
        )
        card.paste(ring, (x - 3, AVATAR_Y - 3), ring.getchannel("A"))
        card.paste(avatar, (x, AVATAR_Y), avatar.getchannel("A"))

    # 先裁切底图、头像和文字区域；边框自身不能经过主牌 mask，
    # 否则 frame_degree_m_4.png 圆框外的装饰元素会被截掉。
    _draw_text(card, text)
    _apply_main_mask(card)

    with Image.open(PICS_DIR / f"frame_degree_m_{frame_no}.png") as source:
        frame = source.convert("RGBA")
    # 与 PJSK 原版一致：花/羽牌满宽，普牌窄边框右移 8px。
    inset = 8 if frame.width < card.width else 0
    card.paste(frame, (inset, 0), frame.getchannel("A"))

    output = BytesIO()
    card.save(output, format="PNG")
    output.seek(0)
    return output


async def bonds_card(event: MessageEvent, state: T_State):
    parsed = _parse_message(event, state)
    if isinstance(parsed, (str, BytesIO)):
        return parsed
    slots, frame_no, text = parsed

    _fill_random_colors(slots)
    try:
        for slot in slots:
            await download_image(slot.user)
    except Exception:
        return "头像下载失败了，稍后再试吧"

    return await run_sync(_draw_card)(slots, frame_no, text)
