import base64
import os
import random
import re
import shlex
import tempfile
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Union

import yaml
from nonebot.adapters.onebot.v11 import (
    GroupMessageEvent,
    Message,
    MessageEvent,
    unescape,
)
from nonebot.typing import T_State
from nonebot.utils import run_sync
from PIL import Image, ImageChops, ImageDraw, ImageFont

from config.path_config import CONFIG_PATH, FONT_PATH, IMAGE_PATH
from utils.http_utils import AsyncHttpx
from utils.imageutils import BuildImage, Text2Image
from utils.pjsk_paths import ONDEMAND_PATH, STATIC_PATH

from .depends import REGEX_ARG, download_image
from .utils import UserInfo

BONDS_DIR = STATIC_PATH / "bonds"
PICS_DIR = STATIC_PATH / "pics"
SERVERS_YAML = CONFIG_PATH / "pjsk" / "servers.yaml"

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
ROLE_CARD_TIERS: Dict[str, Tuple[str, int, bool]] = {
    "普牌": ("normal", 1, False),
    "普": ("normal", 1, False),
    "普通牌": ("normal", 1, False),
    "普通": ("normal", 1, False),
    "羽牌": ("feather", 2, False),
    "羽": ("feather", 2, False),
    "花牌": ("flower", 4, False),
    "花": ("flower", 4, False),
    "奖牌": ("medal", 4, True),
    "奖": ("medal", 4, True),
    "金牌": ("medal", 4, True),
    "金奖": ("medal", 4, True),
}
ROLE_CARD_TIER_CHOICES: Tuple[Tuple[str, int, bool], ...] = (
    ("normal", 1, False),
    ("feather", 2, False),
    ("flower", 4, False),
    ("medal", 4, True),
)
DEFAULT_FRAME = 4

CANVAS_SIZE = (380, 80)
AVATAR_SIZE = 72
AVATAR_Y = 4
# 头像按原牌角色可见区中心摆放
AVATAR_LEFT_X = 44
AVATAR_RIGHT_X = 264
ROLE_TEXT_CENTER_X = 138
ROLE_TEXT_MAX_WIDTH = 168
TEXT_FONT = FONT_PATH / "SourceHanSansCN-Bold.otf"
TEXT_MAX_LENGTH = 12
_LEVEL_TOKEN_RE = re.compile(r"^(?:lv\s*)?(\d+)(?:\s*级)?$", re.IGNORECASE)

USAGE = (
    "羁绊牌 @A [颜色] @B [颜色] [花牌/羽牌/普牌] [文字]\n"
    "羁绊牌 [角色缩写] [角色缩写] [花牌/羽牌/普牌] [文字]\n"
    "头像模式颜色可省略（省略则随机），牌型可省略（默认花牌），文字可省略\n"
    "角色模式例：羁绊牌 mnr hrk 大海般的颜色\n"
    "头像模式例：羁绊牌 @小明 5 @小红 12 羽牌 命运相遇\n"
    "也可发送：羁绊牌 help / 羁绊牌 颜色"
)
ROLE_USAGE = (
    "角色牌 [角色名/别名] [字] [普牌/羽牌/花牌/奖牌] [实际等级(0~10)]\n"
    "四个参数均可指定或不指定：角色、牌等级、实际等级不指定时随机，字不指定时使用官方默认\n"
    "例：角色牌\n"
    "例：角色牌 一歌\n"
    "例：角色牌 knd 奏宝单推 花牌 8\n"
    "例：角色牌 ick 奖牌 3"
)

_color_cache: Optional[List[int]] = None
_character_alias_cache: Optional[Dict[str, int]] = None
_character_all_alias_cache: Optional[Dict[str, int]] = None
_jp_rip_sources_cache: Optional[List[str]] = None

_DEFAULT_CHARACTER_ABBREVIATIONS = {
    "ick": 1, "saki": 2, "hnm": 3, "shiho": 4,
    "mnr": 5, "hrk": 6, "airi": 7, "szk": 8,
    "khn": 9, "an": 10, "akt": 11, "toya": 12,
    "tks": 13, "emu": 14, "nene": 15, "rui": 16,
    "knd": 17, "mfy": 18, "ena": 19, "mzk": 20,
    "miku": 21, "rin": 22, "len": 23, "luka": 24,
    "meiko": 25, "kaito": 26,
}


def available_colors() -> List[int]:
    global _color_cache
    if _color_cache is None:
        colors = []
        for path in BONDS_DIR.glob("*.png"):
            if path.stem.isdigit():
                colors.append(int(path.stem))
        _color_cache = sorted(colors)
    return _color_cache


def character_abbreviations() -> Dict[str, int]:
    global _character_alias_cache
    if _character_alias_cache is not None:
        return _character_alias_cache

    aliases = dict(_DEFAULT_CHARACTER_ABBREVIATIONS)
    path = STATIC_PATH / "character_nicknames.yaml"
    try:
        with path.open(encoding="utf-8") as file:
            data = yaml.safe_load(file) or {}
        for item in data.get("nicknames", []):
            if not isinstance(item, dict) or not item.get("id"):
                continue
            nicknames = item.get("nicknames") or []
            if nicknames:
                aliases[str(nicknames[0]).strip().casefold()] = int(item["id"])
    except Exception:
        pass

    _character_alias_cache = aliases
    return aliases


@dataclass
class _Slot:
    user: UserInfo
    color: Optional[int] = None


def _text_image(text: str, fontsize: int, **kwargs) -> BuildImage:
    return BuildImage(Text2Image.from_text(text, fontsize, **kwargs).to_image())




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

    raise ValueError(f"未知羁绊牌帮助类型：{kind}")


def _parse_character_message(
    state: T_State,
) -> Optional[Tuple[List[int], int, str]]:
    msg: Message = state[REGEX_ARG]
    plain = msg.extract_plain_text().strip()
    try:
        tokens = shlex.split(unescape(plain))
    except ValueError:
        tokens = plain.split()
    if len(tokens) < 2:
        return None

    aliases = character_abbreviations()
    character_ids = [aliases.get(token.casefold()) for token in tokens[:2]]
    if any(character_id is None for character_id in character_ids):
        return None

    frame_no = DEFAULT_FRAME
    text_parts: List[str] = []
    for token in tokens[2:]:
        if token in CARD_TYPES:
            frame_no = CARD_TYPES[token]
        else:
            text_parts.append(token)
    text = " ".join(text_parts)
    if len(text) > TEXT_MAX_LENGTH:
        return None
    return [int(character_id) for character_id in character_ids], frame_no, text


def _parse_message(
    event: MessageEvent, state: T_State
) -> Union[str, BytesIO, Tuple[List[_Slot], int, str]]:
    msg: Message = state[REGEX_ARG]
    group = str(event.group_id) if isinstance(event, GroupMessageEvent) else ""
    colors = available_colors()

    plain = msg.extract_plain_text().strip()
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
    colors = available_colors()
    used = {slot.color for slot in slots if slot.color is not None}
    for slot in slots:
        if slot.color is None:
            pool = [c for c in colors if c not in used] or colors
            slot.color = random.choice(pool)
            used.add(slot.color)


def _bonds_background(color1: int, color2: int) -> Image.Image:
    with Image.open(BONDS_DIR / f"{color1}.png") as source:
        base = source.convert("RGBA")
    with Image.open(BONDS_DIR / f"{color2}.png") as source:
        right = source.convert("RGBA")
    base.paste(right.crop((190, 0, 380, 80)), (190, 0))
    return base


def _circle_avatar(user: UserInfo) -> Image.Image:
    # 上下各裁 1/10 再切圆，免得压进圆框太小
    image = user.img.convert("RGBA")
    crop_height = int(image.height * 0.1)
    if crop_height > 0 and image.height - crop_height * 2 > 0:
        image = image.crop(
            (0, crop_height, image.width, image.height - crop_height)
        )
    return image.circle().resize((AVATAR_SIZE, AVATAR_SIZE)).image


def _character_all_aliases() -> Dict[str, int]:
    global _character_all_alias_cache
    if _character_all_alias_cache is not None:
        return _character_all_alias_cache

    aliases = dict(_DEFAULT_CHARACTER_ABBREVIATIONS)
    path = STATIC_PATH / "character_nicknames.yaml"
    try:
        with path.open(encoding="utf-8") as file:
            data = yaml.safe_load(file) or {}
        for item in data.get("nicknames", []):
            if not isinstance(item, dict) or not item.get("id"):
                continue
            cid = int(item["id"])
            for nick in item.get("nicknames") or []:
                key = str(nick).strip().casefold()
                if key:
                    aliases.setdefault(key, cid)
    except Exception:
        pass

    _character_all_alias_cache = aliases
    return aliases


async def _resolve_character_id(token: str, group_id: Optional[int]) -> Optional[int]:
    normalized = token.strip().casefold()
    if not normalized:
        return None
    aliases = _character_all_aliases()
    if normalized in aliases:
        return aliases[normalized]
    try:
        from plugins.media.image_management.pjsk_images.pjsk_db_source import PjskAlias

        primary = await PjskAlias.query_name(
            token.strip(), fuzzy_search=False, group_id=group_id
        )
        if primary:
            return aliases.get(str(primary).strip().casefold())
    except Exception:
        pass
    return None


def _draw_text(
    card: Image.Image,
    text: str,
    *,
    center_x: int = CANVAS_SIZE[0] // 2,
    max_width: int = 220,
) -> None:
    if not text:
        return
    draw = ImageDraw.Draw(card)
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
    x = center_x - (bbox[2] - bbox[0]) // 2 - bbox[0]
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


def _paste_frame(card: Image.Image, frame_no: int) -> None:
    with Image.open(PICS_DIR / f"frame_degree_m_{frame_no}.png") as source:
        frame = source.convert("RGBA")
    # 普牌边框窄一点，右移 8px
    inset = 8 if frame.width < card.width else 0
    card.paste(frame, (inset, 0), frame.getchannel("A"))


def _paste_level_diamonds(card: Image.Image, level: int) -> None:
    level = max(0, min(int(level), 10))
    if level <= 0:
        return
    with Image.open(PICS_DIR / "icon_degreeLv.png") as source:
        lv_icon = source.convert("RGBA")
    for i in range(min(level, 5)):
        card.paste(lv_icon, (54 + 16 * i, 63), lv_icon.getchannel("A"))
    if level > 5:
        with Image.open(PICS_DIR / "icon_degreeLv6.png") as source:
            lv6_icon = source.convert("RGBA")
        for i in range(level - 5):
            card.paste(lv6_icon, (54 + 16 * i, 63), lv6_icon.getchannel("A"))


def _draw_character_card(character_ids: List[int], frame_no: int, text: str) -> BytesIO:
    card = _bonds_background(character_ids[0], character_ids[1])
    for character_id, x in zip(character_ids, (0, 220)):
        name = f"chr_sd_{character_id:02d}_01"
        path = STATIC_PATH / "chara" / name / f"{name}.png"
        with Image.open(path) as source:
            chara = source.convert("RGBA")
        card.paste(chara, (x, -40), chara.getchannel("A"))

    _draw_text(card, text)
    _apply_main_mask(card)
    _paste_frame(card, frame_no)

    output = BytesIO()
    card.save(output, format="PNG")
    output.seek(0)
    return output


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

    # 边框要在 mask 之后贴，不然圆框外的装饰会被裁掉
    _draw_text(card, text)
    _apply_main_mask(card)
    _paste_frame(card, frame_no)

    output = BytesIO()
    card.save(output, format="PNG")
    output.seek(0)
    return output


async def bonds_card(event: MessageEvent, state: T_State):
    character_parsed = _parse_character_message(state)
    if character_parsed is not None:
        character_ids, frame_no, text = character_parsed
        try:
            return await run_sync(_draw_character_card)(character_ids, frame_no, text)
        except FileNotFoundError:
            return "找不到对应角色的羁绊牌素材，请检查角色缩写是否正确"
        except Exception:
            return "角色羁绊牌生成失败了，稍后再试吧"

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


def _jp_rip_sources() -> List[str]:
    global _jp_rip_sources_cache
    if _jp_rip_sources_cache is not None:
        return _jp_rip_sources_cache

    urls: List[str] = []
    try:
        with SERVERS_YAML.open(encoding="utf-8") as file:
            cfg = yaml.safe_load(file) or {}
        for src in ((cfg.get("jp") or {}).get("rip") or {}).get("sources") or []:
            base = str((src or {}).get("base_url") or "").strip().rstrip("/")
            if base:
                urls.append(base)
    except Exception:
        pass
    if not urls:
        urls = ["http://host.docker.internal:8082/jp-assets"]
    _jp_rip_sources_cache = urls
    return urls


def _build_asset_url(base_url: str, rel_path: str) -> str:
    rel = rel_path.lstrip("/")
    if "sekai.best" in base_url:
        return f"{base_url}/{rel}"
    return f"{base_url}/startapp/{rel}"


def _write_atomic(target: Path, data: bytes) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{target.name}.", dir=str(target.parent))
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        os.replace(tmp_name, target)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _read_rgba_sync(path: Path) -> Image.Image:
    with Image.open(path) as source:
        return source.convert("RGBA")


async def _load_jp_startapp_image(rel_path: str) -> Image.Image:
    cache_file = ONDEMAND_PATH / "jp" / "startapp" / rel_path
    if cache_file.exists():
        return await run_sync(_read_rgba_sync)(cache_file)

    last_error: Optional[Exception] = None
    for base_url in _jp_rip_sources():
        url = _build_asset_url(base_url, rel_path)
        is_local = "host.docker.internal" in url or "127.0.0.1" in url or "localhost" in url
        try:
            resp = await AsyncHttpx.get(
                url,
                use_proxy=not is_local,
                timeout=10,
                retries=1,
            )
            if resp.status_code != 200 or not resp.content:
                continue
            with Image.open(BytesIO(resp.content)) as probe:
                rgba = probe.convert("RGBA")
            await run_sync(_write_atomic)(cache_file, resp.content)
            return rgba
        except Exception as exc:
            last_error = exc
            continue
    raise FileNotFoundError(f"无法获取素材 {rel_path}: {last_error}")


def _parse_diamond_level(token: str) -> Optional[int]:
    matched = _LEVEL_TOKEN_RE.match(token.strip())
    if not matched:
        return None
    value = int(matched.group(1))
    return value if 0 <= value <= 10 else None


async def _parse_single_character_args(
    event: MessageEvent, state: T_State
) -> Union[str, Tuple[int, Optional[str], Tuple[str, int, bool], int]]:
    msg: Message = state[REGEX_ARG]
    plain = msg.extract_plain_text().strip()
    if plain.lower() in {"help", "帮助", "?"}:
        return ROLE_USAGE

    try:
        tokens = shlex.split(unescape(plain))
    except ValueError:
        tokens = plain.split()
    tokens = [token.strip() for token in tokens if token.strip()]

    group_id = event.group_id if isinstance(event, GroupMessageEvent) else None
    character_id: Optional[int] = None
    if tokens:
        resolved = await _resolve_character_id(tokens[0], group_id)
        if resolved is not None:
            character_id = resolved
            tokens = tokens[1:]
    if character_id is None:
        character_id = random.randint(1, 26)

    tier_idx: Optional[int] = None
    for idx in range(len(tokens) - 1, -1, -1):
        if tokens[idx] in ROLE_CARD_TIERS:
            tier_idx = idx
            break

    level: Optional[int] = None
    if tier_idx is not None:
        tier_spec = ROLE_CARD_TIERS[tokens[tier_idx]]
        before = tokens[:tier_idx]
        after = tokens[tier_idx + 1 :]
        if after:
            parsed_level = _parse_diamond_level(after[-1])
            if parsed_level is not None:
                level = parsed_level
                after = after[:-1]
        text_tokens = before + after
    else:
        tier_spec = random.choice(ROLE_CARD_TIER_CHOICES)
        if tokens:
            parsed_level = _parse_diamond_level(tokens[-1])
            if parsed_level is not None:
                level = parsed_level
                tokens = tokens[:-1]
        text_tokens = tokens

    text = " ".join(text_tokens).strip()
    if len(text) > TEXT_MAX_LENGTH:
        return f"文字最多 {TEXT_MAX_LENGTH} 个字符，请缩短后再试"

    if level is None:
        tier_name = tier_spec[0]
        if tier_name == "normal":
            level = random.randint(1, 5)
        elif tier_name == "medal":
            level = random.randint(1, 6)
        else:
            level = random.randint(1, 10)

    return character_id, (text or None), tier_spec, level


def _compose_single_character_card(
    bg_img: Image.Image,
    word_img: Optional[Image.Image],
    medal_img: Optional[Image.Image],
    text: Optional[str],
    frame_no: int,
    level: int,
) -> BytesIO:
    card = bg_img.convert("RGBA")
    if text:
        _draw_text(
            card,
            text,
            center_x=ROLE_TEXT_CENTER_X,
            max_width=ROLE_TEXT_MAX_WIDTH,
        )
    elif word_img is not None:
        pos = (
            (card.width - word_img.width) // 2,
            (card.height - word_img.height) // 2,
        )
        card.paste(word_img, pos, word_img.getchannel("A"))

    _paste_frame(card, frame_no)
    _paste_level_diamonds(card, level)
    if medal_img is not None:
        card.paste(medal_img, (0, 0), medal_img.getchannel("A"))

    output = BytesIO()
    card.save(output, format="PNG")
    output.seek(0)
    return output


async def character_card(event: MessageEvent, state: T_State):
    parsed = await _parse_single_character_args(event, state)
    if isinstance(parsed, str):
        return parsed

    character_id, text, (tier_name, frame_no, is_medal), level = parsed
    style_no = 2 if tier_name == "medal" else 1
    bg_rel = f"honor_background/honor_bg_style_{style_no:02d}_{character_id:02d}/degree_main.png"
    try:
        bg_img = await _load_jp_startapp_image(bg_rel)
    except FileNotFoundError:
        if style_no == 2:
            try:
                bg_img = await _load_jp_startapp_image(
                    f"honor_background/honor_bg_style_01_{character_id:02d}/degree_main.png"
                )
            except FileNotFoundError:
                return "找不到对应角色牌素材，请稍后再试"
        else:
            return "找不到对应角色牌素材，请稍后再试"
    except Exception:
        return "角色牌底图加载失败了，稍后再试吧"

    word_img: Optional[Image.Image] = None
    if text is None:
        word_style = 2 if tier_name == "medal" else 1
        word_rarity = 4 if tier_name == "medal" else frame_no
        word_rel = f"honor_word/honor_word_{character_id:02d}_{word_style:02d}_{word_rarity:02d}.png"
        try:
            word_img = await _load_jp_startapp_image(word_rel)
        except Exception:
            word_img = None

    medal_img: Optional[Image.Image] = None
    if is_medal:
        try:
            medal_img = await _load_jp_startapp_image("honor_medal/medal/icon_degree_medal1.png")
        except Exception:
            medal_img = None

    return await run_sync(_compose_single_character_card)(
        bg_img, word_img, medal_img, text, frame_no, level
    )
