"""World Bloom 六部件自定义边框出图（拼装长方形边框 + 角色卡面 + 星级/等级/玩家名/牌子）。"""

from __future__ import annotations

import asyncio
import logging
import random
from io import BytesIO
from typing import Any, Dict, List, Optional, Sequence, Tuple

from PIL import Image, ImageChops, ImageDraw

from utils.pjsk_paths import STATIC_PATH

from ..context import get_context
from ..honor import generatehonor
from ..primitives import get_pjsk_asset_cached, get_pjsk_font, open_pjsk_image
from ..registry import register

logger = logging.getLogger(__name__)

PLAYER_FRAME_ASSET_ROOT = "startapp/player_frame"
MEMBER_CUTOUT_ASSET_ROOT = "startapp/character/member_cutout"
# 每个玩家部件由这些透明图层组成；顺序保持资源文件的 center/left/right 绘制顺序。
PART_LAYER_NAMES = {
    1: ("center", "left", "right"),
    2: ("left",),
    3: ("right",),
    4: ("left",),
    5: ("right",),
    6: ("left", "right"),
}

# 边框整体尺寸（依据官方 PSD 换算），以及去掉边框后的角色内框。
PART_FRAME_SIZE = (341, 668)
PART_FRAME_INNER = (34, 70, 307, 597)
# 竖边上下两段之间保留的接缝，让「头接头、尾接尾」有自然缝隙。
FRAME_SEGMENT_GAP = 16
# 上竖段实体顶部相对帧顶的位置（其余位置据此对称推导，抹平手搓 PSD 的误差）。
FRAME_UPPER_TOP = 40

# 立绘取景（cover）与信息面板布局，均以 PART_FRAME_SIZE 为基准，最终整体放大。
OUTPUT_SCALE = 2
ILLUST_BOX = (14, 6, 327, 470)           # 立绘铺满整个框内（塞到边框下方，顶部不留空）
ILLUST_RADIUS = 16
ILLUST_TOP_BIAS = 0.04
# 所有框内内容裁到这个圆角轮廓，超出边框的部分一律剪掉。
CONTENT_CLIP_INSET = 5
CONTENT_CLIP_RADIUS = 30
PANEL_BOX = (24, 442, 317, 634)          # 不透明名牌面板（贴近底部）
PANEL_RADIUS = 20
SCRIM_TOP, SCRIM_BOTTOM = 352, 442       # 立绘底部的半透明压暗，衬托白字
STAR_ROW = (44, 398)                     # 星级行左上
STAR_PX, STAR_PITCH = 24, 25
MASTER_CENTER = (288, 408)               # 专家等级菱形中心
MASTER_SIZE = 54
LEVEL_POS = (44, 428)
NAME_CENTER = (170, 468)
HONOR_MAIN_TOP = 492
HONOR_SUB_TOP = 558
HONOR_MAIN_SIZE = (250, 52)
HONOR_SUB_SIZE = (118, 52)

LEVEL_BY_RARITY = {"rarity_1": 20, "rarity_2": 30, "rarity_3": 50, "rarity_4": 60, "rarity_birthday": 60}
STAR_BY_RARITY = {"rarity_1": 1, "rarity_2": 2, "rarity_3": 3, "rarity_4": 4}


def frame_id_for_color(color: str) -> int:
    """将命令颜色写法映射到 World Bloom 边框 ID。"""
    color_id = {"100": 20001, "tb": 20001, "1000": 20002, "tk": 20002}.get(
        str(color).strip().casefold()
    )
    if color_id is None:
        raise ValueError("边框颜色仅支持 100/tb 或 1000/tk")
    return color_id


def expand_character_ids(character_ids: Sequence[int]) -> List[int]:
    """接受一个或六个有效角色 ID；单个 ID 展开到六个部件。"""
    if len(character_ids) == 1:
        character_ids = list(character_ids) * 6
    elif len(character_ids) != 6:
        raise ValueError("角色缩写需要正好提供 1 个或 6 个")
    normalized = [int(value) for value in character_ids]
    if any(value < 1 or value > 26 for value in normalized):
        raise ValueError("角色缩写无法映射到有效角色")
    return normalized


def resolve_frame_group(frame_id: int, frames: Sequence[dict], groups: Sequence[dict]) -> Tuple[dict, dict]:
    frame = next((row for row in frames if int(row.get("id", 0)) == int(frame_id)), None)
    if frame is None:
        raise ValueError(f"找不到边框 ID {frame_id}")
    if int(frame.get("partsCount", 0)) != 6:
        raise ValueError(f"边框 ID {frame_id} 不是六部件边框")
    group_id = int(frame.get("playerFrameGroupId", 0))
    group = next((row for row in groups if int(row.get("id", 0)) == group_id), None)
    if group is None or not group.get("assetbundleName"):
        raise ValueError(f"找不到边框组 {group_id} 的资源映射")
    return frame, group


def resolve_frame_parts(
    group_id: int, character_ids: Sequence[int], parts: Sequence[dict]
) -> List[dict]:
    expanded = expand_character_ids(character_ids)
    selected = []
    for position, character_id in enumerate(expanded, 1):
        part = next(
            (
                row
                for row in parts
                if int(row.get("playerFrameGroupId", 0)) == int(group_id)
                and int(row.get("gameCharacterId", 0)) == character_id
            ),
            None,
        )
        if part is None or int(part.get("id", 0)) <= 0:
            raise ValueError(f"part{position} 找不到角色 ID {character_id} 的边框部件")
        selected.append(part)
    return selected


def frame_part_asset_paths(
    group_name: str, frame_id: int, parts: Sequence[dict]
) -> List[Tuple[int, str, str, str]]:
    """按命令位置与角色部件 ID 生成 vertical 目录下的全部透明图层。"""
    paths = []
    for position, part in enumerate(parts, 1):
        part_id = int(part.get("id", 0))
        if part_id <= 0:
            raise ValueError(f"part{position} 的资源 ID 无效")
        category = f"{PLAYER_FRAME_ASSET_ROOT}/{group_name}/{int(frame_id)}/{part_id}/vertical"
        for side in PART_LAYER_NAMES[position]:
            paths.append((position, side, category, f"frame_parts{position}_{side}.png"))
    return paths


def frame_base_asset_path(group_name: str, frame_id: int) -> Tuple[str, str]:
    """frame_base 使用边框 ID 作为自己的部件目录 ID。"""
    category = f"{PLAYER_FRAME_ASSET_ROOT}/{group_name}/{int(frame_id)}/{int(frame_id)}/vertical"
    return category, "frame_base.png"


def require_asset(image: Optional[Image.Image], label: str) -> Image.Image:
    if image is None:
        raise FileNotFoundError(label)
    return image


def choose_frame_card(
    cards: Sequence[dict],
    card_id: int = 0,
    leader_card_id: int = 0,
    leader_trained: Optional[bool] = None,
    requested_trained: Optional[bool] = None,
    character_id: int = 0,
    chooser=None,
) -> Tuple[dict, bool]:
    """选择显式卡牌、绑定队长卡或（按角色）随机卡牌及立绘状态。"""
    by_id = {
        int(card["id"]): card
        for card in cards
        if isinstance(card, dict) and card.get("id") is not None and card.get("assetbundleName")
    }
    explicit = int(card_id or 0)
    leader = int(leader_card_id or 0)
    if explicit:
        card = by_id.get(explicit)
        if card is None:
            raise ValueError(f"卡牌 ID {explicit} 不存在或没有立绘资源")
        default_trained = False
    elif leader and leader in by_id:
        card = by_id[leader]
        default_trained = bool(leader_trained)
    else:
        pool = list(by_id.values())
        if character_id:
            by_char = [c for c in pool if int(c.get("characterId", 0)) == int(character_id)]
            pool = by_char or pool
        if not pool:
            raise ValueError("cards.json 中没有可用卡牌")
        card = (chooser or random.choice)(pool)
        rarity = str(card.get("cardRarityType", ""))
        default_trained = rarity in ("rarity_3", "rarity_4") and bool((chooser or random.choice)((False, True)))

    rarity = str(card.get("cardRarityType", ""))
    trained = bool(requested_trained) if requested_trained is not None else default_trained
    if rarity not in ("rarity_3", "rarity_4"):
        trained = False
    return card, trained


def card_cutout_path(card: dict, trained: bool) -> Tuple[str, str]:
    suffix = "after_training" if trained and card.get("cardRarityType") in ("rarity_3", "rarity_4") else "normal"
    return f"{MEMBER_CUTOUT_ASSET_ROOT}/{card['assetbundleName']}", f"{suffix}.png"


def _png_bytes(image: Image.Image) -> bytes:
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def _solid_bbox(image: Image.Image, threshold: int = 60) -> Tuple[int, int, int, int]:
    """部件资源带柔和外发光，取 alpha 高的实体区域才能精确对齐、对称。"""
    alpha = image.convert("RGBA").getchannel("A")
    bbox = alpha.point(lambda value: 255 if value > threshold else 0).getbbox()
    return bbox or image.getbbox() or (0, 0, image.width, image.height)


def _fit_cover(image: Image.Image, target_w: int, target_h: int, top_bias: float) -> Image.Image:
    """等比裁剪铺满目标框；top_bias 偏上取景以留住脸部。"""
    bbox = image.getbbox()
    if bbox:
        image = image.crop(bbox)
    scale = max(target_w / image.width, target_h / image.height)
    resized = image.resize(
        (max(1, round(image.width * scale)), max(1, round(image.height * scale))),
        Image.Resampling.LANCZOS,
    )
    x = max(0, (resized.width - target_w) // 2)
    y = int(round((resized.height - target_h) * top_bias))
    y = max(0, min(y, resized.height - target_h))
    return resized.crop((x, y, x + target_w, y + target_h))


def _compose_ring(layers: Sequence[Tuple[int, str, Image.Image]]) -> Image.Image:
    """把九个部件对称地拼成一个长方形冰晶圈（透明底）。

    右侧镜像左侧、下竖段镜像上竖段，消除手搓 PSD 的左右/上下误差。
    """
    if not layers:
        raise ValueError("未加载到任何边框部件")

    width, height = PART_FRAME_SIZE
    center_x = width / 2
    canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    by_key = {(position, side): source.convert("RGBA") for position, side, source in layers}

    def solid_of(key):
        return _solid_bbox(by_key[key])

    def height_of(key):
        bbox = solid_of(key)
        return bbox[3] - bbox[1]

    # 每个部件的实体顶部目标 y（left/right 成对共用；上下段对称围绕中线）。
    upper_top = min(FRAME_UPPER_TOP, int(height / 2 - FRAME_SEGMENT_GAP / 2 - height_of((2, "left"))))
    targets = {}
    if (1, "left") in by_key:
        targets[(1, "left")] = ("left", 0)
    if (1, "right") in by_key:
        targets[(1, "right")] = ("right", 0)
    if (1, "center") in by_key:
        targets[(1, "center")] = ("center", 0)
    for key in ((2, "left"), (3, "right")):
        if key in by_key:
            targets[key] = (key[1], upper_top)
    for key in ((4, "left"), (5, "right")):
        if key in by_key:
            targets[key] = (key[1], int(height / 2 + FRAME_SEGMENT_GAP / 2))
    for key in ((6, "left"), (6, "right")):
        if key in by_key:
            targets[key] = (key[1], height - height_of(key))

    draw_order = [(2, "left"), (3, "right"), (4, "left"), (5, "right"),
                  (1, "left"), (1, "right"), (6, "left"), (6, "right"), (1, "center")]
    for key in draw_order:
        if key not in by_key:
            continue
        layer = by_key[key]
        anchor, top_y = targets[key]
        sl, st, sr, sb = solid_of(key)
        if anchor == "left":
            px = -sl
        elif anchor == "right":
            px = int(round(width - sr))
        else:
            px = int(round(center_x - (sl + sr) / 2))
        py = int(round(top_y - st))
        canvas.alpha_composite(layer, (px, py))
    return canvas


def _star_row(count: int, trained: bool, rarity: str, star_px: int) -> Optional[Image.Image]:
    """返回一整行稀有度星星（生日卡返回单枚生日标记）。"""
    if rarity == "rarity_birthday":
        try:
            mark = open_pjsk_image(STATIC_PATH / "chara" / "rarity_birthday.png", mode="RGBA",
                                   size=(star_px + 4, star_px + 3))
        except Exception:
            return None
        return mark
    if count <= 0:
        return None
    name = "rarity_star_afterTraining.png" if trained else "rarity_star_normal.png"
    try:
        star = open_pjsk_image(STATIC_PATH / "chara" / name, mode="RGBA", size=(star_px, star_px))
    except Exception:
        return None
    pitch = int(star_px * 1.05)
    row = Image.new("RGBA", (pitch * (count - 1) + star_px, star_px), (0, 0, 0, 0))
    for idx in range(count):
        row.alpha_composite(star, (idx * pitch, 0))
    return row


def _master_rank_icon(rank: int, size: int) -> Optional[Image.Image]:
    """游戏原生的专家等级（突破）菱形数字素材 train_rank_{1..5}.png；0 级不显示。"""
    if rank < 1:
        return None
    try:
        return open_pjsk_image(STATIC_PATH / "chara" / f"train_rank_{rank}.png",
                               mode="RGBA", size=(size, size))
    except Exception:
        return None


def _vertical_scrim(width: int, top: int, bottom: int, height: int) -> Image.Image:
    """立绘底部的半透明压暗渐变（透明→深色），衬托白色星级/等级。"""
    scrim = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    span = max(1, bottom - top)
    for y in range(top, min(bottom, height)):
        alpha = int(150 * (y - top) / span)
        ImageDraw.Draw(scrim).line((0, y, width, y), fill=(20, 26, 44, alpha))
    for y in range(min(bottom, height), height):
        ImageDraw.Draw(scrim).line((0, y, width, y), fill=(20, 26, 44, 150))
    return scrim


def _compose_card(
    ring: Image.Image,
    illustration: Image.Image,
    *,
    stars: Optional[Image.Image],
    level: int,
    master_rank: int,
    name: str,
    honors: Sequence[Image.Image],
) -> Image.Image:
    """把立绘、蒙版、星级/等级/玩家名/牌子和边框叠成最终卡片，并整体放大。"""
    width, height = PART_FRAME_SIZE
    canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))

    # 1) 角色立绘铺在内框（塞到边框下、顶部不留空），圆角遮罩。
    il, it, ir, ib = ILLUST_BOX
    iw, ih = ir - il, ib - it
    filled = _fit_cover(illustration.convert("RGBA"), iw, ih, ILLUST_TOP_BIAS)
    mask = Image.new("L", (iw, ih), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, iw - 1, ih - 1), radius=ILLUST_RADIUS, fill=255)
    canvas.paste(filled, (il, it), mask)

    # 2) 半透明压暗蒙版：立绘下缘 → 深色，衬托白色文字。
    canvas.alpha_composite(_vertical_scrim(width, SCRIM_TOP, SCRIM_BOTTOM, height))

    # 3) 不透明名牌面板（浅色圆角）。
    pl, pt, pr, pb = PANEL_BOX
    panel = Image.new("RGBA", (pr - pl, pb - pt), (0, 0, 0, 0))
    ImageDraw.Draw(panel).rounded_rectangle(
        (0, 0, pr - pl - 1, pb - pt - 1), radius=PANEL_RADIUS,
        fill=(247, 248, 251, 245), outline=(214, 221, 233, 255), width=2,
    )
    canvas.alpha_composite(panel, (pl, pt))

    # 4) 星级行 + 专家等级菱形 + 等级文字（压在压暗蒙版上）。
    if stars is not None:
        sx, sy = STAR_ROW
        canvas.alpha_composite(stars, (sx, sy - stars.height // 2))
    master_icon = _master_rank_icon(master_rank, MASTER_SIZE)
    if master_icon is not None:
        canvas.alpha_composite(master_icon, (MASTER_CENTER[0] - MASTER_SIZE // 2,
                                              MASTER_CENTER[1] - MASTER_SIZE // 2))
    draw = ImageDraw.Draw(canvas)
    lv_font = get_pjsk_font("FOT-RodinNTLGPro-DB.ttf", 26)
    draw.text(LEVEL_POS, "Lv.", font=get_pjsk_font("FOT-RodinNTLGPro-DB.ttf", 18),
              fill=(255, 255, 255, 255), anchor="lm", stroke_width=2, stroke_fill=(40, 48, 70, 220))
    draw.text((LEVEL_POS[0] + 30, LEVEL_POS[1] - 1), str(level), font=lv_font,
              fill=(255, 255, 255, 255), anchor="lm", stroke_width=2, stroke_fill=(40, 48, 70, 220))

    # 5) 玩家名（面板上、深色字）。
    name_font = get_pjsk_font("SourceHanSansCN-Bold.otf", 30)
    draw.text(NAME_CENTER, name or "Player", font=name_font, fill=(52, 62, 88, 255), anchor="mm")

    # 6) 一个大牌子 + 两个小牌子。
    honors = list(honors)
    if honors:
        main = honors[0].convert("RGBA").resize(HONOR_MAIN_SIZE, Image.Resampling.LANCZOS)
        canvas.alpha_composite(main, ((width - HONOR_MAIN_SIZE[0]) // 2, HONOR_MAIN_TOP))
    subs = honors[1:3]
    if subs:
        gap = 12
        total = len(subs) * HONOR_SUB_SIZE[0] + (len(subs) - 1) * gap
        start_x = (width - total) // 2
        for idx, sub in enumerate(subs):
            sub_img = sub.convert("RGBA").resize(HONOR_SUB_SIZE, Image.Resampling.LANCZOS)
            canvas.alpha_composite(sub_img, (start_x + idx * (HONOR_SUB_SIZE[0] + gap), HONOR_SUB_TOP))

    # 7) 把框内所有内容裁到圆角轮廓，剪掉超出边框的部分。
    clip = Image.new("L", (width, height), 0)
    ImageDraw.Draw(clip).rounded_rectangle(
        (CONTENT_CLIP_INSET, CONTENT_CLIP_INSET, width - 1 - CONTENT_CLIP_INSET, height - 1 - CONTENT_CLIP_INSET),
        radius=CONTENT_CLIP_RADIUS, fill=255,
    )
    canvas.putalpha(ImageChops.multiply(canvas.getchannel("A"), clip))

    # 8) 冰晶边框压在最上层（本身不裁剪）。
    canvas.alpha_composite(ring)

    if OUTPUT_SCALE != 1:
        canvas = canvas.resize((width * OUTPUT_SCALE, height * OUTPUT_SCALE), Image.Resampling.LANCZOS)
    return canvas


def _world_bloom_honor_choices(
    character_id: int,
    world_blooms: Sequence[dict],
    events: Sequence[dict],
    honor_groups: Sequence[dict],
    honors: Sequence[dict],
) -> List[dict]:
    """收集该角色所有 World Bloom 活动的 TOP100 牌子（总合 + 各章节）。"""
    events_by_id = {int(e.get("id", 0)): e for e in events}
    chapters = [
        row for row in world_blooms
        if int(row.get("gameCharacterId", 0)) == int(character_id)
        and row.get("worldBloomChapterType") == "game_character"
    ]
    wanted_names = set()
    for row in chapters:
        event = events_by_id.get(int(row.get("eventId", 0)))
        if not event or not event.get("name"):
            continue
        wanted_names.add(f"{event['name']} 総合")
        wanted_names.add(f"{event['name']} チャプター{row.get('chapterNo')}")
    group_ids = {
        int(group["id"])
        for group in honor_groups
        if group.get("honorType") == "event" and group.get("name") in wanted_names
    }
    found, seen = [], set()
    for honor in honors:
        if int(honor.get("groupId", 0)) not in group_ids:
            continue
        if not str(honor.get("assetbundleName", "")).startswith("honor_top_000100"):
            continue
        hid = int(honor.get("id", 0))
        if hid in seen:
            continue
        seen.add(hid)
        found.append(honor)
    return found


async def _render_given_honors(honors_data: Sequence[dict], pjsk_type: int, want: int = 3) -> List[Image.Image]:
    """渲染玩家个人资料里实际展示的牌子（seq1 主牌，seq2/3 副牌）。"""
    ordered = sorted(honors_data, key=lambda h: h.get("seq", 9))
    images: List[Image.Image] = []
    for honor in ordered:
        if len(images) >= want:
            break
        try:
            image = await generatehonor(dict(honor), ismain=(len(images) == 0), pjsk_type=pjsk_type)
        except Exception:
            logger.debug("玩家展示牌子渲染失败 %s", honor.get("honorId"), exc_info=True)
            image = None
        if image is not None:
            images.append(image)
    return images


async def _pick_honor_images(character_id: int, pjsk_type: int, want: int = 3) -> List[Image.Image]:
    """随机挑该角色最多 want 个 World Bloom TOP100 牌子并渲染（第一个为大牌子）。"""
    context = get_context()
    try:
        world_blooms, events, honor_groups, honors = await asyncio.gather(
            context.async_load_master_data("worldBlooms.json", pjsk_type),
            context.async_load_master_data("events.json", pjsk_type),
            context.async_load_master_data("honorGroups.json", pjsk_type),
            context.async_load_master_data("honors.json", pjsk_type),
        )
    except Exception:
        logger.warning("World Bloom 牌子主数据加载失败", exc_info=True)
        return []

    choices = _world_bloom_honor_choices(character_id, world_blooms, events, honor_groups, honors)
    if not choices:
        return []
    random.shuffle(choices)

    images: List[Image.Image] = []
    for honor in choices:
        if len(images) >= want:
            break
        honor_payload = {"honorId": int(honor["id"]), "profileHonorType": "normal", "honorLevel": 1}
        try:
            image = await generatehonor(honor_payload, ismain=(len(images) == 0), pjsk_type=pjsk_type)
        except Exception:
            logger.debug("牌子渲染失败 honorId=%s", honor.get("id"), exc_info=True)
            image = None
        if image is not None:
            images.append(image)
    return images


@register("world_bloom_frame")
async def render_world_bloom_frame(payload: Dict[str, Any]) -> bytes:
    context = get_context()
    pjsk_type = int(payload.get("pjsk_type", 0))
    try:
        frame_id = int(payload.get("frame_id", 0))
        character_ids = expand_character_ids(payload.get("character_ids") or [])
    except (TypeError, ValueError) as exc:
        raise ValueError(f"边框参数无效：{exc}") from exc

    frames, groups, parts, cards = await asyncio.gather(
        context.async_load_master_data("playerFrames.json", pjsk_type),
        context.async_load_master_data("playerFrameGroups.json", pjsk_type),
        context.async_load_master_data("playerFrameParts.json", pjsk_type),
        context.async_load_master_data("cards.json", pjsk_type),
    )
    frame, group = resolve_frame_group(frame_id, frames, groups)
    selected_parts = resolve_frame_parts(int(group["id"]), character_ids, parts)
    paths = frame_part_asset_paths(str(group["assetbundleName"]), int(frame["id"]), selected_parts)

    async def load_part(position: int, side: str, category: str, raw: str) -> Tuple[int, str, Image.Image]:
        image = await get_pjsk_asset_cached(category, raw, pjsk_type=pjsk_type, mode="RGBA")
        return position, side, require_asset(image, f"边框 part{position} {side} 资源缺失：{category}/{raw}")

    layers = await asyncio.gather(
        *(load_part(position, side, category, raw) for position, side, category, raw in paths)
    )
    card, trained = choose_frame_card(
        cards,
        card_id=int(payload.get("card_id", 0) or 0),
        leader_card_id=int(payload.get("leader_card_id", 0) or 0),
        leader_trained=payload.get("leader_trained"),
        requested_trained=payload.get("trained"),
        character_id=int(character_ids[0]) if character_ids else 0,
    )
    category, raw = card_cutout_path(card, trained)
    card_image = require_asset(
        await get_pjsk_asset_cached(category, raw, pjsk_type=pjsk_type, mode="RGBA"),
        f"卡牌 {card['id']} 立绘资源缺失：{category}/{raw}",
    )

    rarity = str(card.get("cardRarityType", ""))
    star_count = STAR_BY_RARITY.get(rarity, 0)
    character_id = int(card.get("characterId", 0)) or (int(character_ids[0]) if character_ids else 0)

    # 若发令者已绑定且用的是其队长卡，等级/突破/玩家名/展示牌子都取真实资料。
    leader_card_id = int(payload.get("leader_card_id", 0) or 0)
    is_leader = bool(leader_card_id) and int(card.get("id", 0)) == leader_card_id

    leader_level = int(payload.get("leader_level", 0) or 0)
    level = leader_level if (is_leader and leader_level > 0) else LEVEL_BY_RARITY.get(rarity, 60)

    leader_master = payload.get("leader_master_rank")
    if is_leader and leader_master is not None:
        master_rank = int(leader_master)
    else:
        master_rank = random.randint(1, 5)

    default_name = ""
    try:
        game_characters = await context.async_load_master_data("gameCharacters.json", pjsk_type)
        default_name = str(context.getcharaname(character_id, game_characters, pjsk_type=pjsk_type) or "")
    except Exception:
        default_name = ""
    profile_name = str(payload.get("profile_name") or "").strip() if is_leader else ""
    name = str(payload.get("player_name") or "").strip() or profile_name or default_name or "Player"

    honors_data = payload.get("honors") if is_leader else None
    honors: List[Image.Image] = []
    if honors_data:
        honors = await _render_given_honors(honors_data, pjsk_type, want=3)
    if not honors:  # 未绑定、无展示牌子或渲染失败时，回退到该角色随机 WL 牌子。
        honors = await _pick_honor_images(character_id, pjsk_type, want=3)

    ring = _compose_ring(layers)
    stars = _star_row(star_count, trained, rarity, STAR_PX)
    result = _compose_card(
        ring, card_image,
        stars=stars, level=level, master_rank=master_rank, name=name, honors=honors,
    )
    return _png_bytes(result)
