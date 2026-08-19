from __future__ import annotations

import asyncio
import math
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

from PIL import Image, ImageDraw, ImageFilter

from services.log import logger

from .._profile_header import PjskHeaderData, draw_pjsk_profile_header
from .._utils import load_master_data
from ._data import (
    MySekaiError,
    build_fixture_collection,
    build_talk_collection,
    ensure_master,
    fixture_genre_name,
    get_by_id,
    get_current_phenomena,
    get_fixture_by_blueprint_id,
    get_fixture_icon,
    get_harvest_fixture_icon,
    get_phenomena_icon,
    get_res_icon,
    get_res_name,
    get_site_names,
    get_unit_group_chars,
    get_visit_info,
    item_name,
    listify,
    summarize_resources,
)
from ._utils import (
    ACCENT,
    BG_COLOR,
    CARD_BG,
    HEADER_BG,
    MUTED_COLOR,
    MYSEKAI_HARVEST_MAP_IMAGE_SCALE,
    MYSEKAI_PICS_PATH,
    OK_COLOR,
    PHENOMENA_COLORS,
    RARE_RES_LIGHT_LARGE,
    RARE_RES_LIGHT_SMALL,
    SCORE_COLOR,
    SITE_ID_ORDER,
    SITE_MAP_INFO,
    TEXT_COLOR,
    TIP_COLOR,
    UNIT_COLORS,
    UNIT_GATEID_MAP,
    WARN_COLOR,
    bold,
    data_path,
    draw_watermark,
    find_by,
    format_time,
    get_chara_icon_by_chara_unit_id,
    get_character_icon,
    get_last_refresh_time_and_reason,
    get_refresh_hours,
    get_res_rarity,
    load_pic,
    load_pic_optional,
    medium,
    open_pjsk_image,
    paste_alpha,
    placeholder,
    rip_img,
    rodin,
    rounded_rect,
    server_name,
    text_width,
    truncate_text,
)

_SITE_BACKGROUND_CACHE: dict[tuple[int, int, float], tuple[Image.Image, dict]] = {}

# 画布常量

CANVAS_W = 1100
PAD = 30
CARD_R = 14


# 卡片样式工具

def _card(w: int, h: int, fill=(255, 255, 255, 230)) -> Image.Image:
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    rounded_rect(d, (0, 0, w - 1, h - 1), fill, radius=CARD_R)
    return img


def _shadow(w: int, h: int, blur=4) -> Image.Image:
    s = Image.new("RGBA", (w + blur * 2, h + blur * 2), (0, 0, 0, 0))
    d = ImageDraw.Draw(s)
    rounded_rect(d, (blur, blur, blur + w - 1, blur + h - 1), (0, 0, 0, 35), radius=CARD_R)
    return s.filter(ImageFilter.GaussianBlur(blur))


def _paste_card(base: Image.Image, card: Image.Image, x: int, y: int, shadow: bool = True) -> None:
    if shadow:
        sh = _shadow(card.width, card.height)
        paste_alpha(base, sh, (x - 4, y - 4))
    paste_alpha(base, card, (x, y))


def _gradient_canvas(width: int, height: int) -> Image.Image:
    """B30 同风格浅色渐变背景。"""
    top = (255, 246, 250)
    bottom = (236, 244, 255)
    img = Image.new("RGB", (width, height), top)
    draw = ImageDraw.Draw(img)
    for y in range(height):
        t = y / max(1, height - 1)
        color = tuple(int(top[i] * (1 - t) + bottom[i] * t) for i in range(3))
        draw.line((0, y, width, y), fill=color)
    glow = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    gd.ellipse((-width // 4, -height // 8, width // 2, height // 4), fill=(255, 190, 220, 70))
    gd.ellipse((width // 2, height // 4, width + width // 5, height + height // 6), fill=(170, 210, 255, 58))
    img.paste(glow, (0, 0), glow.split()[-1])
    return img


def _make_canvas(height: int) -> Image.Image:
    return _gradient_canvas(CANVAS_W, height)


def _make_weather_canvas(width: int, height: int, phenom_id: Optional[int]) -> Image.Image:
    # 保留函数名兼容现有调用，但不再使用不齐全的天气背景图。
    return _gradient_canvas(width, height)


# 玩家头部名片

async def draw_player_header(
    profile: dict,
    is_private: bool,
    title: str,
    subtitle: str,
    pjsk_type: int = 0,
    width: int = CANVAS_W - PAD * 2,
) -> Image.Image:
    """绘制与 B30 组卡统一风格的玩家信息头部。"""
    h = 230
    img = Image.new("RGBA", (width, h), (0, 0, 0, 0))
    header_data = PjskHeaderData(
        userid=str(profile.get("userid", "")),
        name=profile.get("name") or "MySekai User",
        rank=int(profile.get("rank") or 0),
        is_private=is_private,
        user_decks=profile.get("userDecks") or [],
        special_training=profile.get("special_training") or [],
        user_profile_honors=profile.get("userProfileHonors") or [],
        user_honor_missions=profile.get("userHonorMissions") or [],
        suite_update_time=profile.get("suite_update_time"),
    )
    card_asset_map = {}
    try:
        card_asset_map = {
            c.get("id"): c.get("assetbundleName", "")
            for c in listify(load_master_data("cards.json", pjsk_type))
            if isinstance(c, dict) and c.get("id") is not None
        }
    except Exception as e:
        logger.debug(f"MySekai header 卡面索引加载失败: {e}")
    await draw_pjsk_profile_header(
        img, (0, 0, width, h), header_data,
        module_label=title, pjsk_type=pjsk_type, card_asset_map=card_asset_map,
        extra_badges=[("模式", subtitle)] if subtitle else None,
        show_cutout=False, compact=False,
    )
    return img


_CHARA_ICON_FILE_BY_CID = {
    1: "ick.png", 2: "saki.png", 3: "hnm.png", 4: "shiho.png", 5: "mnr.png",
    6: "hrk.png", 7: "airi.png", 8: "szk.png", 9: "khn.png", 10: "an.png",
    11: "akt.png", 12: "toya.png", 13: "tks.png", 14: "emu.png", 15: "nene.png",
    16: "rui.png", 17: "knd.png", 18: "mfy.png", 19: "ena.png", 20: "mzk.png",
    21: "miku.png", 22: "rin.png", 23: "len.png", 24: "luka.png", 25: "meiko.png", 26: "kaito.png",
}


async def get_visit_chara_icon(cuid: int, pjsk_type: int = 0, size=(72, 72)) -> Image.Image:
    """来访角色使用 data/pjsk/masterdata/chara/chara_icon 下的方形头像。"""
    cu = get_by_id("gameCharacterUnits.json", cuid, pjsk_type) or {}
    cid = cu.get("gameCharacterId") or cuid
    unit = cu.get("unit")
    fname = None
    fname = _CHARA_ICON_FILE_BY_CID.get(cid)
    if fname:
        path = data_path / "chara" / "chara_icon" / fname
        if path.exists():
            try:
                img = Image.open(path).convert("RGBA").resize(size, Image.Resampling.LANCZOS)
                return img
            except Exception as e:
                logger.debug(f"读取来访角色头像失败 {path}: {e}")
    return await get_chara_icon_by_chara_unit_id(cuid, pjsk_type, size)


async def get_site_thumbnail(sid: int, pjsk_type: int = 0, size=(190, 92)) -> Image.Image:
    """加载 MySekai 地图缩略图，复用本地/远程图片缓存。"""
    return await rip_img(
        f"mysekai/site/sitemap/texture/img_harvest_site_{sid}.png",
        pjsk_type=0,
        size=size,
        fallback=placeholder(size, str(sid)),
    )


def get_gate_material_groups(pjsk_type: int = 0) -> dict[int, dict[int, list[dict]]]:
    groups: dict[int, dict[int, list[dict]]] = {}
    for item in ensure_master("mysekaiGateMaterialGroups.json", pjsk_type):
        if not isinstance(item, dict):
            continue
        group_id = item.get("groupId")
        if group_id is None:
            continue
        gid = int(group_id) // 1000
        lv = int(group_id) % 1000
        groups.setdefault(gid, {}).setdefault(lv, []).append(item)
    return groups


async def get_special_resource_hints(mysekai_info: dict, pjsk_type: int = 0) -> list[dict]:
    """摘要卡片用的特殊资源刷新提示，仅提示指定 4 类资源。"""
    site_names = get_site_names(pjsk_type)
    target_order = {
        "mysekai_material_5": 0,   # 夕桐 / 特殊木头
        "mysekai_material_12": 1,  # 钻石
        "mysekai_material_20": 2,  # 四叶草
        "mysekai_material_24": 3,  # 音色 / 音符类
    }
    by_key: dict[str, dict] = {}
    for site_map in (mysekai_info or {}).get("updatedResources", {}).get("userMysekaiHarvestMaps", []) or []:
        sid = site_map.get("mysekaiSiteId")
        for drop in site_map.get("userMysekaiSiteHarvestResourceDrops", []) or []:
            if drop.get("mysekaiSiteHarvestResourceDropStatus") != "before_drop":
                continue
            res_key = f"{drop.get('resourceType')}_{drop.get('resourceId')}"
            if res_key not in target_order:
                continue
            rec = by_key.setdefault(res_key, {"key": res_key, "qty": 0, "sites": []})
            rec["qty"] += int(drop.get("quantity", 0))
            if sid is not None:
                rec["sites"].append(site_names.get(sid, f"区域{sid}"))
    hints = list(by_key.values())
    hints.sort(key=lambda h: target_order[h["key"]])
    for h in hints:
        h["icon"] = await get_res_icon(h["key"], pjsk_type, (28, 28))
        seen = set()
        h["site_text"] = "、".join(s for s in h["sites"] if not (s in seen or seen.add(s)))
    return hints


# msr 概要

async def compose_summary_image(
    profile: dict,
    is_private: bool,
    mysekai_info: dict,
    suite_data: Optional[dict],
    data_msg: str,
    pjsk_type: int = 0,
) -> Image.Image:
    cur_phenom, phenoms, phenom_start_times = get_current_phenomena(mysekai_info, pjsk_type)
    phenom_icons = (
        await asyncio.gather(*[get_phenomena_icon(pid, pjsk_type, (54, 54)) for pid in phenoms], return_exceptions=True)
        if phenoms else []
    )
    visit = get_visit_info(mysekai_info, pjsk_type)
    special_hints = await get_special_resource_hints(mysekai_info, pjsk_type)

    last_refresh_dt, last_reason = get_last_refresh_time_and_reason(pjsk_type)
    if last_reason == "natural":
        reason_text = "自然刷新"
    elif last_reason.startswith("bdstart_"):
        cid = int(last_reason.split("_", 1)[1])
        reason_text = f"{item_name('gameCharacters.json', cid, pjsk_type, '角色')}生日开始"
    elif last_reason.startswith("bdend_"):
        cid = int(last_reason.split("_", 1)[1])
        reason_text = f"{item_name('gameCharacters.json', cid, pjsk_type, '角色')}生日结束"
    else:
        reason_text = last_reason

    panel_gap = 18
    panel_w = (CANVAS_W - PAD * 2 - panel_gap) // 2
    panel_h = 220
    canvas_h = PAD + 230 + 18 + panel_h * 2 + panel_gap + PAD + 35
    pic = _make_canvas(canvas_h)
    y = PAD
    header = await draw_player_header(profile, is_private, "MySekai 摘要", server_name(pjsk_type).upper(), pjsk_type)
    _paste_card(pic, header, PAD, y)
    y += header.height + 18

    def title(card: Image.Image, text: str):
        d = ImageDraw.Draw(card)
        d.text((18, 14), text, fill=TEXT_COLOR, font=bold(22))
        return d

    # 数据状态
    data_card = _card(panel_w, panel_h, (255, 255, 255, 222))
    d = title(data_card, "数据状态")
    d.text((22, 56), f"MySekai：{format_time(mysekai_info.get('upload_time'))}", fill=MUTED_COLOR, font=medium(17))
    d.text((22, 88), f"上次刷新：{last_refresh_dt.strftime('%m-%d %H:%M')}（{reason_text}）", fill=ACCENT, font=medium(16))
    msg_y = 122
    if data_msg:
        d.text((22, msg_y), truncate_text(data_msg, medium(15), panel_w - 44), fill=WARN_COLOR, font=medium(15))
        msg_y += 26
    d.text((22, msg_y), "特殊资源：", fill=TEXT_COLOR, font=bold(16))
    if special_hints:
        sx = 110
        sy = msg_y - 4
        for idx, hint in enumerate(special_hints[:4]):
            paste_alpha(data_card, hint["icon"], (sx, sy))
            d.text((sx + 30, sy + 2), f"x{hint['qty']}", fill=WARN_COLOR, font=bold(14))
            site_text = truncate_text(hint.get("site_text", ""), medium(11), 68)
            d.text((sx, sy + 28), site_text, fill=MUTED_COLOR, font=medium(11))
            sx += 92
        if len(special_hints) > 4:
            d.text((sx, sy + 7), f"+{len(special_hints) - 4}", fill=WARN_COLOR, font=bold(15))
    else:
        d.text((110, msg_y), "本次未刷新", fill=MUTED_COLOR, font=medium(14))
        sx = 198
        sy = msg_y - 5
        for key in ("mysekai_material_5", "mysekai_material_12", "mysekai_material_20", "mysekai_material_24"):
            icon = await get_res_icon(key, pjsk_type, (28, 28))
            paste_alpha(data_card, icon, (sx, sy))
            sx += 34

    # 来访角色
    visit_card = _card(panel_w, panel_h, (255, 255, 255, 222))
    d = title(visit_card, "来访角色")
    gate_id = visit.get("gate_id")
    gate_lv = visit.get("gate_level")
    x = 22
    if gate_id:
        paste_alpha(visit_card, load_pic(f"gate_icon/gate_{gate_id}.png", (54, 54)), (x, 72))
        unit_color = UNIT_COLORS[gate_id - 1][:3] if 1 <= gate_id <= 5 else ACCENT
        d.text((x + 7, 126), f"Lv.{gate_lv or 0}", fill=unit_color, font=bold(16))
    x = 105
    if visit.get("visit_cgids"):
        for cgid in visit["visit_cgids"][:5]:
            cuids = get_unit_group_chars(cgid, pjsk_type)
            if not cuids:
                continue
            cuid = cuids[0]
            chara = await get_visit_chara_icon(cuid, pjsk_type, (60, 60))
            paste_alpha(visit_card, chara, (x, 62))
            if cgid == visit.get("reservation_cgid"):
                inv = load_pic_optional("invitationcard.png")
                if inv is not None:
                    paste_alpha(visit_card, inv.resize((22, 22), Image.Resampling.LANCZOS), (x + 2, 110))
            cu = get_by_id("gameCharacterUnits.json", cuid, pjsk_type) or {}
            cid = cu.get("gameCharacterId")
            if cid:
                memoria = await rip_img(f"mysekai/item_preview/material/item_memoria_{cid}.png", pjsk_type, size=(26, 26))
                paste_alpha(visit_card, memoria, (x + 42, 106))
            x += 74
    else:
        d.text((105, 88), "暂无来访角色数据", fill=MUTED_COLOR, font=medium(17))

    # 天气预报
    weather_card = _card(panel_w, panel_h, (255, 255, 255, 222))
    d = title(weather_card, "天气预报")
    wx = 22
    for i, icon in enumerate(phenom_icons[:5]):
        if isinstance(icon, Exception) or icon is None:
            icon = placeholder((54, 54))
        is_cur = i < len(phenoms) and phenoms[i] == cur_phenom
        fill = (235, 255, 255, 240) if is_cur else (246, 246, 252, 230)
        d.rounded_rectangle((wx, 58, wx + 78, 136), radius=12, fill=fill, outline=(216, 234, 240))
        label = phenom_start_times[i].strftime("%H:%M") if i < len(phenom_start_times) else "--:--"
        d.text((wx + 20, 64), label, fill=TEXT_COLOR if is_cur else MUTED_COLOR, font=bold(13))
        paste_alpha(weather_card, icon.resize((44, 44), Image.Resampling.LANCZOS), (wx + 17, 86))
        wx += 88
    if not phenom_icons:
        d.text((22, 88), "无天气数据", fill=MUTED_COLOR, font=medium(17))

    # 当前门升级材料预览
    gate_card = _card(panel_w, panel_h, (255, 255, 255, 222))
    d = title(gate_card, "当前门升级材料")
    cur_gate_id = visit.get("gate_id")
    cur_gate_lv = int(visit.get("gate_level") or 0)
    user_mats = {
        m.get("mysekaiMaterialId"): int(m.get("quantity", 0))
        for m in (suite_data or {}).get("userMysekaiMaterials", [])
        if isinstance(m, dict)
    }
    gate_groups = get_gate_material_groups(pjsk_type)
    if cur_gate_id and cur_gate_lv:
        paste_alpha(gate_card, load_pic(f"gate_icon/gate_{cur_gate_id}.png", (42, 42)), (22, 52))
        d.text((72, 62), f"当前 Lv.{cur_gate_lv}", fill=ACCENT, font=bold(18))
        for row_idx, target_lv in enumerate((cur_gate_lv + 1, cur_gate_lv + 2)):
            yy = 100 + row_idx * 55
            mats = gate_groups.get(cur_gate_id, {}).get(target_lv, [])
            d.text((24, yy + 14), f"Lv.{target_lv}", fill=TEXT_COLOR, font=bold(17))
            if not mats:
                d.text((92, yy + 14), "没有材料表或已接近满级", fill=MUTED_COLOR, font=medium(15))
                continue
            mx = 92
            for mat in mats[:5]:
                mid = mat.get("mysekaiMaterialId")
                need = int(mat.get("quantity", 0))
                own = int(user_mats.get(mid, 0))
                icon = await get_res_icon(f"mysekai_material_{mid}", pjsk_type, (30, 30))
                paste_alpha(gate_card, icon, (mx, yy + 5))
                color = OK_COLOR if own >= need else WARN_COLOR
                text = f"{own}/{need}"
                d.text((mx + 34, yy + 10), text, fill=color, font=bold(13))
                mx += 82
    else:
        d.text((22, 96), "没有当前门或 Suite 材料数据", fill=MUTED_COLOR, font=medium(17))

    cards = [data_card, visit_card, weather_card, gate_card]
    for idx, card in enumerate(cards):
        col, row = idx % 2, idx // 2
        _paste_card(pic, card, PAD + col * (panel_w + panel_gap), y + row * (panel_h + panel_gap))

    return pic.crop((0, 0, CANVAS_W, canvas_h))

# 资源数量列表

async def compose_res_list_image(
    profile: dict,
    is_private: bool,
    mysekai_info: dict,
    show_harvested: bool,
    data_msg: str,
    pjsk_type: int = 0,
) -> Image.Image:
    res_sum = summarize_resources(mysekai_info, show_harvested)
    site_names = get_site_names(pjsk_type)
    ordered_by_site: list[tuple[int, list[tuple[str, int]]]] = []
    total_rows = 0
    for sid in SITE_ID_ORDER:
        items = res_sum.get(sid, {})
        if not items:
            continue
        ordered = sorted(items.items(), key=lambda kv: (-get_res_rarity(kv[0]), -kv[1], kv[0]))
        ordered_by_site.append((sid, ordered))
        total_rows += max(1, math.ceil(len(ordered) / 5))

    height = 30 + 230 + 18 + len(ordered_by_site) * 34 + total_rows * 62 + 220
    pic = _make_canvas(max(620, height))
    y = PAD

    header = await draw_player_header(profile, is_private, "MySekai 资源", "数量视图", pjsk_type)
    _paste_card(pic, header, PAD, y)
    y += header.height + 18

    # 每个区域资源：左侧区域缩略图，右侧资源官方缩略图 + 数量。
    for sid, ordered in ordered_by_site:
        rows = max(1, math.ceil(len(ordered) / 5))
        card_h = max(126, 26 + rows * 62)
        card = _card(CANVAS_W - PAD * 2, card_h, (255, 255, 255, 215))
        cd = ImageDraw.Draw(card)
        site_img = await get_site_thumbnail(sid, pjsk_type, size=(190, 92))
        paste_alpha(card, site_img, (18, 18))
        cd.rounded_rectangle((18, 18, 208, 110), radius=8, outline=(220, 230, 235), width=1)
        cd.text((26, 86), truncate_text(site_names.get(sid, f"区域{sid}"), bold(16), 170), fill=(255, 255, 255), font=bold(16), stroke_width=2, stroke_fill=(50, 50, 55))

        icons = await asyncio.gather(
            *[get_res_icon(k, pjsk_type, (42, 42)) for k, _ in ordered],
            return_exceptions=True,
        )
        for idx, ((key, qty), icon) in enumerate(zip(ordered, icons)):
            col, row = idx % 5, idx // 5
            cell_x = 230 + col * 160
            cell_y = 16 + row * 62
            if isinstance(icon, Exception) or icon is None:
                icon = placeholder((42, 42))
            rarity = get_res_rarity(key)
            box_fill = (250, 235, 255, 210) if rarity == 2 else (235, 252, 250, 210) if rarity == 1 else (235, 248, 250, 190)
            cd.rounded_rectangle((cell_x, cell_y + 2, cell_x + 46, cell_y + 48), radius=6, fill=box_fill)
            paste_alpha(card, icon, (cell_x + 2, cell_y + 4))
            color = WARN_COLOR if rarity == 2 else (80, 70, 190) if rarity == 1 else TEXT_COLOR
            cd.text((cell_x + 56, cell_y + 7), str(qty), fill=color, font=bold(32))
        _paste_card(pic, card, PAD, y)
        y += card_h + 16

    if data_msg:
        msg_card = _card(CANVAS_W - PAD * 2, 54, (255, 255, 255, 205))
        ImageDraw.Draw(msg_card).text((18, 16), truncate_text(data_msg, medium(16), CANVAS_W - PAD * 2 - 36), fill=WARN_COLOR, font=medium(16))
        _paste_card(pic, msg_card, PAD, y)
        y += msg_card.height + 16

    return pic.crop((0, 0, CANVAS_W, min(pic.height, y + 20)))


# msr 地图

async def _site_background(sid: int, pjsk_type: int) -> tuple[Image.Image, dict]:
    """返回 (按 site_info 已 crop+缩放的底图, 缩放后的 site_info 副本)。

    输出的 site_info 中 ``offset_x``, ``offset_z``, ``grid_size`` 已经预乘 scale。
    像素坐标基于该底图。
    """
    cache_key = (int(sid or 0), int(pjsk_type), float(MYSEKAI_HARVEST_MAP_IMAGE_SCALE))
    cached = _SITE_BACKGROUND_CACHE.get(cache_key)
    if cached is not None:
        cached_image, cached_info = cached
        return cached_image.copy(), dict(cached_info)

    info = SITE_MAP_INFO.get(sid, {})
    if not info:
        return placeholder((600, 400)), {
            "scale": 1.0, "grid_size": 24.0, "offset_x": 0.0, "offset_z": 0.0,
            "dir_x": 1, "dir_z": -1, "rev_xz": False,
        }

    # 优先本地 site/<image>，否则远程 mysekai/site/sitemap/texture_rip/img_harvest_site_X.png
    local_rel = info.get("image")
    img: Optional[Image.Image] = None
    if local_rel:
        local = MYSEKAI_PICS_PATH / local_rel
        if local.exists():
            try:
                img = open_pjsk_image(local, mode="RGBA")
            except Exception as e:
                logger.debug(f"读取 site 图 {local} 失败: {e}")
    if img is None:
        try:
            img = await rip_img(
                f"mysekai/site/sitemap/texture_rip/img_harvest_site_{sid}.png", 0,
            )
        except Exception:
            img = None
    if img is None:
        return placeholder((600, 400)), {
            "scale": 1.0, "grid_size": float(info.get("grid_size", 24)),
            "offset_x": 0.0, "offset_z": 0.0,
            "dir_x": info.get("dir_x", 1), "dir_z": info.get("dir_z", -1),
            "rev_xz": info.get("rev_xz", False),
        }

    # 直接以原图尺寸（1920×1080）作为坐标系基准，paint.html 的 SCENES 配置就是这么定义的
    offset_x = float(info.get("offset_x", 0))
    offset_z = float(info.get("offset_z", 0))

    scale = MYSEKAI_HARVEST_MAP_IMAGE_SCALE
    new_w = max(1, int(img.width * scale))
    new_h = max(1, int(img.height * scale))
    img = img.resize((new_w, new_h), Image.Resampling.LANCZOS)

    scaled_info = {
        "scale": scale,
        "grid_size": float(info.get("grid_size", 24)) * scale,
        "offset_x": offset_x * scale,
        "offset_z": offset_z * scale,
        "dir_x": info.get("dir_x", 1),
        "dir_z": info.get("dir_z", -1),
        "rev_xz": info.get("rev_xz", False),
    }
    _SITE_BACKGROUND_CACHE[cache_key] = (img.copy(), dict(scaled_info))
    return img, scaled_info


def _build_pos_to_pixel(site_info: dict, draw_w: int, draw_h: int):
    """返回 (positionX, positionZ) → (px, py) 像素坐标的闭包。"""
    grid_size = site_info["grid_size"]
    offset_x = site_info["offset_x"]
    offset_z = site_info["offset_z"]
    dir_x = site_info["dir_x"]
    dir_z = site_info["dir_z"]
    rev_xz = site_info["rev_xz"]
    mid_x, mid_z = draw_w / 2, draw_h / 2

    def to_px(x, z) -> tuple[int, int]:
        x = float(x); z = float(z)
        if rev_xz:
            x, z = z, x
        x = x * grid_size * dir_x
        z = z * grid_size * dir_z
        x += mid_x + offset_x
        z += mid_z + offset_z
        x = max(0, min(x, draw_w - 1))
        z = max(0, min(z, draw_h - 1))
        return int(x), int(z)

    return to_px


def _is_birthday_drop(res_type: str, res_id: int) -> bool:
    """生日露滴：material id 174~199。"""
    return res_type == "material" and 174 <= res_id <= 199


async def _draw_single_site_map(
    site_map: dict,
    show_harvested: bool,
    name: str,
    mysekai_info: dict,
    pjsk_type: int = 0,
    bare: bool = False,
) -> Image.Image:
    """渲染单个区域地图。bare=True 时只返回地图本体和资源分布。"""
    sid = site_map.get("mysekaiSiteId")
    bg, info = await _site_background(sid, pjsk_type)

    title_h = 0 if bare else 40
    pad = 0 if bare else 14
    bg_w, bg_h = bg.size
    if bare:
        card = bg.copy().convert("RGBA")
    else:
        card_w = bg_w + pad * 2
        card_h = title_h + bg_h + pad
        card = _card(card_w, card_h)
        paste_alpha(card, bg, (pad, title_h))
    d = ImageDraw.Draw(card)
    if not bare:
        d.text((pad + 2, 10), name, fill=TEXT_COLOR, font=bold(20))

    # 资源点 fixture 图标偏移缓存（每张地图独立计算）
    pos_px = _build_pos_to_pixel(info, bg_w, bg_h)
    scale = info["scale"]

    # 收集掉落；同一地图的唯一资源图标批量加载，避免逐点串行等待。
    all_res: dict[str, dict[str, dict]] = {}
    prepared_drops: list[tuple[dict, str, int, int]] = []
    resource_keys: set[str] = set()
    for drop in site_map.get("userMysekaiSiteHarvestResourceDrops", []):
        res_type = drop.get("resourceType")
        res_id = drop.get("resourceId")
        res_status = drop.get("mysekaiSiteHarvestResourceDropStatus")
        if not show_harvested and res_status != "before_drop":
            continue
        res_key = f"{res_type}_{res_id}"
        px, py = pos_px(drop.get("positionX", 0), drop.get("positionZ", 0))
        prepared_drops.append((drop, res_key, px, py))
        resource_keys.add(res_key)

    icon_results = await asyncio.gather(
        *[get_res_icon(key, pjsk_type, (64, 64)) for key in sorted(resource_keys)],
        return_exceptions=True,
    )
    resource_icons = {
        key: icon if not isinstance(icon, Exception) and icon is not None else placeholder((64, 64))
        for key, icon in zip(sorted(resource_keys), icon_results)
    }
    for drop, res_key, px, py in prepared_drops:
        res_type = drop.get("resourceType")
        res_id = drop.get("resourceId")
        pkey = f"{px}_{py}"
        bucket = all_res.setdefault(pkey, {})
        if res_key not in bucket:
            bucket[res_key] = {
                "id": res_id, "type": res_type, "x": px, "z": py,
                "quantity": int(drop.get("quantity", 0)),
                "image": resource_icons[res_key],
                "small_icon": False, "del": False,
            }
        else:
            bucket[res_key]["quantity"] += int(drop.get("quantity", 0))

    # 删除固定数量常规掉落、生日伴生处理
    for pkey, bucket in all_res.items():
        is_birthday_sapling = False
        is_cotton_flower = False
        has_material_drop = False
        for res_key, item in bucket.items():
            if res_key in ("mysekai_material_1", "mysekai_material_6") and item["quantity"] == 6:
                item["del"] = True
            if res_key in ("mysekai_material_21", "mysekai_material_22"):
                is_cotton_flower = True
            if res_key.startswith("mysekai_material"):
                has_material_drop = True
            if _is_birthday_drop(item["type"], item["id"]) and item["quantity"] > 16:
                is_birthday_sapling = True
        for res_key, item in bucket.items():
            if not res_key.startswith("mysekai_material") and has_material_drop:
                item["small_icon"] = True
            if is_cotton_flower and res_key not in ("mysekai_material_21", "mysekai_material_22"):
                item["small_icon"] = True
            if is_birthday_sapling:
                item["small_icon"] = not _is_birthday_drop(item["type"], item["id"])
            elif _is_birthday_drop(item["type"], item["id"]):
                item["del"] = True

    # 资源点；唯一 fixture 图标批量加载。
    harvest_points: list[dict] = []
    fixture_ids: set[int] = set()
    for hp in site_map.get("userMysekaiSiteHarvestFixtures", []):
        fid = hp.get("mysekaiSiteHarvestFixtureId")
        st = hp.get("userMysekaiSiteHarvestFixtureStatus")
        if not show_harvested and st != "spawned":
            continue
        px, py = pos_px(hp.get("positionX", 0), hp.get("positionZ", 0))
        harvest_points.append({"id": fid, "x": px, "z": py})
        if fid is not None:
            fixture_ids.add(fid)

    fixture_results = await asyncio.gather(
        *[get_harvest_fixture_icon(fid, pjsk_type) for fid in sorted(fixture_ids)],
        return_exceptions=True,
    )
    fixture_icons = {
        fid: icon if not isinstance(icon, Exception) else None
        for fid, icon in zip(sorted(fixture_ids), fixture_results)
    }

    # 按 z, x 升序绘制
    harvest_points.sort(key=lambda p: (p["z"], p["x"]))
    point_meta_cache: dict[int, dict] = {}
    for hp in harvest_points:
        if hp["id"] in point_meta_cache:
            continue
        meta = get_by_id("mysekaiSiteHarvestFixtures.json", hp["id"], pjsk_type) or {}
        ftype = meta.get("mysekaiSiteHarvestFixtureType", "")
        if ftype == "birthday_plant":
            point_size = int(50 * scale)
            xoff = int(point_size * 0.15)
            zoff = 0
        else:
            point_size = int(160 * scale)
            xoff = 0
            zoff = int(-point_size * 0.3)
        img = fixture_icons.get(hp["id"])
        if img is not None:
            ratio = point_size / max(img.width, img.height)
            new_size = (max(8, int(img.width * ratio)), max(8, int(img.height * ratio)))
            img = img.resize(new_size, Image.Resampling.LANCZOS)
        point_meta_cache[hp["id"]] = {
            "img": img, "size": point_size,
            "xoff": int(-point_size * 0.5 + xoff),
            "zoff": int(-point_size * 0.5 + zoff),
        }
    for hp in harvest_points:
        meta = point_meta_cache.get(hp["id"]) or {}
        if not meta or not meta.get("img"):
            continue
        paste_alpha(card, meta["img"], (pad + hp["x"] + meta["xoff"], title_h + hp["z"] + meta["zoff"]))

    # 出生点
    spawn_x, spawn_z = pos_px(0, 0)
    mark = load_pic("mark.png", (max(8, int(20 * scale)), max(8, int(20 * scale))))
    paste_alpha(card, mark, (pad + spawn_x - mark.width // 2, title_h + spawn_z - mark.height // 2))

    # 资源掉落
    @dataclass
    class ResDrawCall:
        res_id: int = 0
        image: Optional[Image.Image] = None
        quantity: int = 0
        small_icon: bool = False
        size: int = 0
        x: int = 0
        z: int = 0
        draw_order: int = 0
        outline: Optional[tuple[tuple[int, int, int, int], int]] = None
        light_size: int = 0
        attachment: Optional[Image.Image] = None
        rarity: int = 0

    calls: list[ResDrawCall] = []
    for pkey, bucket in all_res.items():
        items = sorted(bucket.values(), key=lambda v: (-v["quantity"], v["id"]))
        small_total = sum(1 for it in items if not it["del"] and it["small_icon"])
        large_total = sum(1 for it in items if not it["del"] and not it["small_icon"])
        small_idx = large_idx = 0
        icon_zoffset = -160 * scale * 0.2
        large_size_default = 35 * scale
        small_size = max(6, int(17 * scale))
        for item in items:
            if item["del"] or not item["image"]:
                continue
            res_key = f"{item['type']}_{item['id']}"
            rarity = get_res_rarity(res_key)

            large_size = large_size_default
            attachment = None
            if item["type"] == "mysekai_material" and item["id"] == 24:
                large_size *= 1.5
            if item["type"] == "mysekai_music_record":
                large_size *= 1.5
                # 已有该唱片则在右下角叠 music_record.png
                user_records = (mysekai_info or {}).get("updatedResources", {}).get("userMysekaiMusicRecords", [])
                if find_by(user_records, "mysekaiMusicRecordId", item["id"]) is not None:
                    attachment = load_pic_optional("music_record.png")

            if item["small_icon"]:
                size_px = small_size
                cx = int(item["x"] + 0.5 * large_size_default * large_total - 0.6 * small_size)
                cy = int(item["z"] - 0.45 * large_size_default + 1.0 * small_size * small_idx + icon_zoffset)
                small_idx += 1
            else:
                size_px = max(8, int(large_size))
                cx = int(item["x"] - 0.5 * large_size * large_total + large_size * large_idx)
                cy = int(item["z"] - 0.5 * large_size + icon_zoffset)
                large_idx += 1
            if cy <= 0:
                cy += int(0.5 * large_size)

            order = item["z"] * 100 + item["x"]
            if item["small_icon"]:
                order += 1_000_000
            elif rarity == 2:
                order += 100_000

            outline = None
            if rarity == 2:
                outline = ((255, 50, 50, 200), 2)
            elif item["small_icon"]:
                outline = ((50, 50, 255, 130), 1)

            light_size = 0
            if rarity == 2 and not res_key.startswith("material"):
                base = RARE_RES_LIGHT_SMALL if item["small_icon"] else RARE_RES_LIGHT_LARGE
                light_size = max(0, int(45 * scale * base / 5.0))

            calls.append(ResDrawCall(
                res_id=item["id"], image=item["image"],
                quantity=item["quantity"], small_icon=item["small_icon"],
                size=size_px, x=cx, z=cy, draw_order=order,
                outline=outline, light_size=light_size, attachment=attachment,
                rarity=rarity,
            ))

    calls.sort(key=lambda c: c.draw_order)

    # 发光层
    light = load_pic_optional("light.png")
    for call in calls:
        if call.light_size and light is not None:
            ls = call.light_size
            li = light.resize((ls, ls), Image.Resampling.LANCZOS)
            paste_alpha(
                card, li,
                (pad + call.x + call.size // 2 - ls // 2, title_h + call.z + call.size // 2 - ls // 2),
            )

    for call in calls:
        if call.image is None:
            continue
        icon = call.image.resize((call.size, call.size), Image.Resampling.LANCZOS)
        paste_alpha(card, icon, (pad + call.x, title_h + call.z))
        if call.outline:
            stroke, sw = call.outline
            d.rounded_rectangle(
                (pad + call.x, title_h + call.z, pad + call.x + call.size, title_h + call.z + call.size),
                radius=4, outline=stroke, width=sw,
            )

    for call in calls:
        if call.attachment is not None:
            asz = max(10, int(call.size * 0.6))
            ai = call.attachment.resize((asz, asz), Image.Resampling.LANCZOS)
            paste_alpha(
                card, ai,
                (pad + call.x + call.size - int(asz * 0.7), title_h + call.z + call.size - int(asz * 0.7)),
            )

    # 数量文本
    for call in calls:
        if call.small_icon:
            continue
        font_size = max(10, int(11 * scale))
        if call.quantity == 2:
            color = (200, 20, 0)
            font_size = max(11, int(13 * scale))
        elif call.quantity > 2:
            color = (200, 20, 200)
            font_size = max(11, int(13 * scale))
        else:
            color = (50, 50, 50)
        f = bold(font_size)
        x_offset = -1
        z_offset = -1
        if call.quantity >= 10:
            x_offset = 1
            z_offset = call.size - font_size - 3
        d.text(
            (pad + call.x + x_offset, title_h + call.z + z_offset),
            str(call.quantity), fill=color, font=f,
        )

    return card


async def compose_map_image(
    profile: dict,
    is_private: bool,
    mysekai_info: dict,
    show_harvested: bool,
    pjsk_type: int = 0,
) -> Image.Image:
    """地图资源图：只展示四张地图的资源分布，不混入个人信息。"""
    maps = mysekai_info.get("updatedResources", {}).get("userMysekaiHarvestMaps", [])
    site_names = get_site_names(pjsk_type)
    map_tasks = []
    for sid in SITE_ID_ORDER:
        site_map = next((m for m in maps if m.get("mysekaiSiteId") == sid), None)
        if not site_map:
            continue
        map_tasks.append(_draw_single_site_map(
            site_map, show_harvested, site_names.get(sid, f"区域{sid}"),
            mysekai_info, pjsk_type, bare=True,
        ))
    map_imgs = list(await asyncio.gather(*map_tasks)) if map_tasks else []
    if not map_imgs:
        map_imgs = [placeholder((480, 320), "无地图")]

    rows_count = math.ceil(len(map_imgs) / 2)
    col_widths = [0, 0]
    row_heights = [0] * rows_count
    for idx, m in enumerate(map_imgs):
        col, row = idx % 2, idx // 2
        col_widths[col] = max(col_widths[col], m.width)
        row_heights[row] = max(row_heights[row], m.height)

    gap = 16
    pad = 16
    canvas_w = pad * 2 + col_widths[0] + (gap if len(map_imgs) > 1 else 0) + col_widths[1]
    canvas_h = pad * 2 + sum(row_heights) + gap * max(0, rows_count - 1)
    pic = Image.new("RGB", (canvas_w, canvas_h), (18, 30, 110))

    y = pad
    for row in range(rows_count):
        x = pad
        for col in range(2):
            idx = row * 2 + col
            if idx >= len(map_imgs):
                continue
            m = map_imgs[idx]
            paste_alpha(pic, m, (x, y))
            x += col_widths[col] + gap
        y += row_heights[row] + gap

    return pic

# 家具 / 蓝图列表

async def compose_fixture_list_image(
    profile: Optional[dict],
    is_private: bool,
    mysekai_info: Optional[dict],
    only_craftable: bool,
    pjsk_type: int = 0,
) -> Image.Image:
    groups, obtained, birthdays = build_fixture_collection(mysekai_info, pjsk_type, only_craftable)
    fixture_ids = [fid for main in groups.values() for sub in main.values() for fid, _ in sub]

    icons: dict[int, Image.Image] = {}
    icon_results = await asyncio.gather(
        *[get_fixture_icon(get_by_id("mysekaiFixtures.json", fid, pjsk_type), pjsk_type, 0, (34, 34))
          for fid in fixture_ids],
        return_exceptions=True,
    )
    for fid, icon in zip(fixture_ids, icon_results):
        icons[fid] = icon if not isinstance(icon, Exception) and icon is not None else placeholder((34, 34))

    # 收集进度（不含生日家具）
    total_all = total_obtained = 0
    if mysekai_info:
        for fid in fixture_ids:
            if fid in birthdays:
                continue
            total_all += 1
            if fid in obtained:
                total_obtained += 1

    total_rows = sum(math.ceil(len(v) / 18) + 1 for main in groups.values() for v in main.values())
    height = 90 + total_rows * 48 + len(groups) * 60
    if profile:
        height += 250
    pic = _make_canvas(max(600, height))
    y = PAD
    if profile:
        title = "MySekai 蓝图" if only_craftable else "MySekai 家具"
        sub = (
            f"{total_obtained}/{total_all} ({total_obtained / total_all * 100:.1f}%)"
            if mysekai_info and total_all else "收集视图"
        )
        header = await draw_player_header(profile, is_private, title, sub, pjsk_type)
        _paste_card(pic, header, PAD, y); y += header.height + 18
    else:
        d = ImageDraw.Draw(pic)
        d.text((PAD, y), "MySekai 家具列表", fill=ACCENT, font=bold(30)); y += 50

    for main_id in sorted(groups):
        subgroups = groups[main_id]
        count = sum(len(v) for v in subgroups.values())
        if count == 0:
            continue
        rows = sum(math.ceil(len(v) / 18) + 1 for v in subgroups.values())
        card_h = 50 + rows * 46
        card = _card(CANVAS_W - PAD * 2, card_h)
        cd = ImageDraw.Draw(card)
        cd.text((16, 12), fixture_genre_name(main_id, True, pjsk_type), fill=TEXT_COLOR, font=bold(21))
        yy = 50
        for sub_id in sorted(subgroups):
            if len(subgroups) > 1 and sub_id != -1:
                cd.text((22, yy), fixture_genre_name(sub_id, False, pjsk_type), fill=MUTED_COLOR, font=medium(15))
                yy += 24
            for idx, (fid, ok) in enumerate(subgroups[sub_id]):
                col, row = idx % 18, idx // 18
                x = 22 + col * 56
                fy = yy + row * 46
                paste_alpha(card, icons.get(fid, placeholder((34, 34))), (x, fy))
                if not ok:
                    overlay = Image.new("RGBA", (34, 34), (0, 0, 0, 95))
                    paste_alpha(card, overlay, (x, fy))
                cd.text((x, fy + 34), str(fid), fill=MUTED_COLOR, font=medium(9))
                if fid in birthdays:
                    ci = await get_character_icon(birthdays[fid], pjsk_type, (14, 14))
                    paste_alpha(card, ci, (x + 20, fy))
            yy += math.ceil(len(subgroups[sub_id]) / 18) * 46
        _paste_card(pic, card, PAD, y); y += card_h + 16
    draw_watermark(pic)
    return pic.crop((0, 0, CANVAS_W, y + 55))


# 家具详情

async def compose_fixture_detail_image(fids: list[int], pjsk_type: int = 0) -> Image.Image:
    cards: list[Image.Image] = []
    for fid in fids:
        fx = get_by_id("mysekaiFixtures.json", fid, pjsk_type)
        if not fx:
            continue
        cards.append(await _fixture_detail_card(fx, pjsk_type))
    if not cards:
        cards = [placeholder((700, 160), "无家具")]
    height = PAD * 2 + sum(c.height for c in cards) + 18 * len(cards) + 40
    pic = _make_canvas(height)
    y = PAD
    for c in cards:
        _paste_card(pic, c, PAD, y); y += c.height + 18
    draw_watermark(pic)
    return pic.crop((0, 0, CANVAS_W, y + 45))


async def _fixture_detail_card(fx: dict, pjsk_type: int = 0) -> Image.Image:
    w, h = CANVAS_W - PAD * 2, 320
    card = _card(w, h)
    d = ImageDraw.Draw(card)
    fid = fx.get("id")
    d.text((18, 16), f"【{server_name(pjsk_type).upper()}-{fid}】{fx.get('name', '')}", fill=TEXT_COLOR, font=bold(24))

    # 配色样张
    colors = [fx.get("colorCode")] + [c.get("colorCode") for c in (fx.get("mysekaiFixtureAnotherColors") or [])]
    color_count = len(colors)
    icons = await asyncio.gather(
        *[get_fixture_icon(fx, pjsk_type, idx, (96, 96)) for idx in range(min(5, color_count))],
        return_exceptions=True,
    )
    x = 22
    for idx, icon in enumerate(icons):
        if isinstance(icon, Exception) or icon is None:
            icon = placeholder((96, 96))
        paste_alpha(card, icon, (x, 68))
        cc = colors[idx]
        if cc:
            try:
                fill = tuple(int(cc.strip("#")[i:i+2], 16) for i in (0, 2, 4))
                d.rounded_rectangle((x, 172, x + 96, 188), radius=4, fill=fill, outline=(160, 160, 160), width=2)
            except Exception:
                pass
        x += 112

    info_x = 600
    text_lines = [
        f"类型：{fixture_genre_name(fx.get('mysekaiFixtureMainGenreId', -1), True, pjsk_type)}",
        f"大小：{(fx.get('gridSize') or {}).get('width', '?')}×{(fx.get('gridSize') or {}).get('depth', '?')}×{(fx.get('gridSize') or {}).get('height', '?')}",
        f"首次/重复放置消耗：{fx.get('firstPutCost', 0)} / {fx.get('secondPutCost', 0)}",
        f"可制作：{'是' if fx.get('isAssembled') else '否'}    可回收：{'是' if fx.get('isDisassembled') else '否'}",
        f"玩家交互：{'是' if fx.get('mysekaiFixturePlayerActionType', 'no_action') != 'no_action' else '否'}    角色交互：{'是' if fx.get('isGameCharacterAction') else '否'}",
    ]
    yy = 70
    for t in text_lines:
        d.text((info_x, yy), t, fill=MUTED_COLOR, font=medium(17)); yy += 30

    # 制作材料
    bps = [
        b for b in ensure_master("mysekaiBlueprints.json", pjsk_type)
        if isinstance(b, dict) and b.get("craftTargetId") == fid and b.get("mysekaiCraftType") == "mysekai_fixture"
    ]
    if bps:
        bp = bps[0]
        costs = [
            c for c in ensure_master("mysekaiBlueprintMaterialCosts.json", pjsk_type)
            if isinstance(c, dict) and c.get("mysekaiBlueprintId") == bp.get("id")
        ]
        d.text((18, 215), "制作材料", fill=TEXT_COLOR, font=bold(18))
        cx = 115
        for cst in costs[:14]:
            icon = await get_res_icon(f"mysekai_material_{cst.get('mysekaiMaterialId')}", pjsk_type, (36, 36))
            paste_alpha(card, icon, (cx, 208))
            d.text((cx, 244), f"x{cst.get('quantity')}", fill=TEXT_COLOR, font=medium(12))
            cx += 60
    return card


# 门升级材料

async def compose_gate_image(
    profile: Optional[dict],
    is_private: bool,
    suite_data: Optional[dict],
    spec_gate_id: Optional[int],
    pjsk_type: int = 0,
) -> Image.Image:
    user_mats = {m.get("mysekaiMaterialId"): m.get("quantity", 0) for m in (suite_data or {}).get("userMysekaiMaterials", [])}
    gates_lv = {g.get("mysekaiGateId"): g.get("mysekaiGateLevel", 0) for g in (suite_data or {}).get("userMysekaiGates", [])}

    raw_gate_data = ensure_master("mysekaiGateMaterialGroups.json", pjsk_type)
    gate_level_alt = bool(raw_gate_data and isinstance(raw_gate_data[0], dict) and "mysekaiGateMaterialGroupId" in raw_gate_data[0])

    groups: dict[int, dict[int, list[dict]]] = {}
    for item in raw_gate_data:
        if not isinstance(item, dict):
            continue
        if gate_level_alt:
            gid = item.get("mysekaiGateId")
            lv = item.get("level")
            mat_items: list[dict] = []
        else:
            gid = item.get("groupId", 0) // 1000
            lv = item.get("groupId", 0) % 1000
            mat_items = [item]
        if spec_gate_id and gid != spec_gate_id:
            continue
        groups.setdefault(gid, {}).setdefault(lv, []).extend(mat_items)

    height = 300 + len(groups) * 760
    pic = _make_canvas(max(700, height))
    y = PAD
    if profile:
        header = await draw_player_header(profile, is_private, "MySekai 门升级", "材料视图", pjsk_type)
        _paste_card(pic, header, PAD, y); y += header.height + 18

    for gid in sorted(groups):
        cur = gates_lv.get(gid, 0)
        if suite_data:
            lvs = [lv for lv in sorted(groups[gid]) if lv > cur][:8]
        else:
            lvs = sorted(groups[gid])[:12]
        card_h = 70 + max(1, len(lvs)) * 78
        card = _card(CANVAS_W - PAD * 2, card_h)
        d = ImageDraw.Draw(card)
        icon = load_pic(f"gate_icon/gate_{gid}.png", (48, 48))
        paste_alpha(card, icon, (18, 14))
        d.text(
            (78, 22),
            f"Gate {gid}" + (f"  当前 Lv.{cur}" if suite_data else ""),
            fill=ACCENT, font=bold(24),
        )
        yy = 70
        for lv in lvs:
            items = groups[gid][lv]
            ok = (
                all(user_mats.get(i.get("mysekaiMaterialId"), 0) >= i.get("quantity", 0) for i in items)
                if (suite_data and items) else False
            )
            d.text(
                (22, yy + 20), f"Lv.{lv}",
                fill=OK_COLOR if ok else WARN_COLOR if (suite_data and items) else TEXT_COLOR,
                font=bold(22),
            )
            x = 105
            if not items:
                d.text((x, yy + 20), "缺少升级材料表，仅显示等级", fill=MUTED_COLOR, font=medium(16))
            for it in items:
                mid, qty = it.get("mysekaiMaterialId"), it.get("quantity", 0)
                ic = await get_res_icon(f"mysekai_material_{mid}", pjsk_type, (46, 46))
                paste_alpha(card, ic, (x, yy + 4))
                own = user_mats.get(mid, 0)
                txt = f"{own}/{qty}" if suite_data else f"x{qty}"
                d.text(
                    (x + 52, yy + 14), txt,
                    fill=OK_COLOR if (own >= qty and suite_data) else WARN_COLOR if suite_data else TEXT_COLOR,
                    font=bold(16),
                )
                x += 155
            yy += 78
        _paste_card(pic, card, PAD, y); y += card_h + 18
    draw_watermark(pic)
    return pic.crop((0, 0, CANVAS_W, y + 50))


# 唱片列表

def _classify_music_unit(mid: int, pjsk_type: int) -> str:
    """返回 ``light_sound``/``idol``/``street``/``theme_park``/``school_refusal``/``vocaloid``/``other``。

    优先用 musicTags（若 master 提供），否则用 musicVocals.unit。
    所有 master 表读取失败统一降级为 ``other``，避免造成插件加载失败。
    """
    try:
        for t in listify(load_master_data("musicTags.json", pjsk_type)):
            if isinstance(t, dict) and t.get("musicId") == mid:
                tag = t.get("musicTag")
                if tag and tag != "all":
                    return tag
    except Exception:
        pass
    try:
        for v in listify(load_master_data("musicVocals.json", pjsk_type)):
            if isinstance(v, dict) and v.get("musicId") == mid:
                unit = v.get("unit")
                if unit:
                    return unit
    except Exception:
        pass
    return "other"


async def compose_musicrecord_image(
    profile: dict,
    is_private: bool,
    mysekai_info: dict,
    show_id: bool,
    pjsk_type: int = 0,
) -> Image.Image:
    user_records = {
        r.get("mysekaiMusicRecordId"): r
        for r in mysekai_info.get("updatedResources", {}).get("userMysekaiMusicRecords", [])
    }
    raw_records = ensure_master("mysekaiMusicRecords.json", pjsk_type)
    if raw_records and isinstance(raw_records[0], dict) and "mysekaiMusicTrackType" in raw_records[0]:
        records = [r for r in raw_records if isinstance(r, dict) and r.get("mysekaiMusicTrackType") == "music"]
    else:
        # 兜底：mysekaiMusicRecords 不带 trackType 时全量当作 music
        records = [{"id": r.get("id"), "externalId": r.get("id")} for r in raw_records if isinstance(r, dict)]

    # 按 unit 分类
    category: dict[str, list[dict]] = {tag: [] for tag in (
        "light_sound", "idol", "street", "theme_park", "school_refusal", "vocaloid", "other",
    )}
    for rec in records:
        rid = rec.get("id")
        mid = rec.get("externalId") or rid
        unit = _classify_music_unit(mid, pjsk_type)
        if unit not in category:
            unit = "other"
        category[unit].append({"rid": rid, "mid": mid, "rec": rec})

    total = sum(len(v) for v in category.values())
    got = sum(1 for v in category.values() for r in v if r["rid"] in user_records)

    section_rows = sum(math.ceil(max(1, len(v)) / 10) for v in category.values() if v)
    cell_h = 92 if show_id else 76
    height = 320 + section_rows * cell_h + len([v for v in category.values() if v]) * 50 + 130
    pic = _make_canvas(height)
    y = PAD
    header = await draw_player_header(
        profile, is_private, "MySekai 唱片",
        f"{got}/{total} ({got / total * 100 if total else 0:.1f}%)", pjsk_type,
    )
    _paste_card(pic, header, PAD, y); y += header.height + 18

    for unit, items in category.items():
        if not items:
            continue
        rows = math.ceil(len(items) / 10)
        card_h = 50 + rows * cell_h
        card = _card(CANVAS_W - PAD * 2, card_h)
        d = ImageDraw.Draw(card)
        title = {
            "light_sound": "Leo/need", "idol": "MORE MORE JUMP!",
            "street": "Vivid BAD SQUAD", "theme_park": "Wonderlands×Showtime",
            "school_refusal": "25時、ナイトコードで。", "vocaloid": "VIRTUAL SINGER",
            "other": "其他",
        }.get(unit, unit)
        sub_got = sum(1 for it in items if it["rid"] in user_records)
        d.text((18, 14), f"{title}  {sub_got}/{len(items)}", fill=TEXT_COLOR, font=bold(20))

        # 并行获取 jacket
        keys = [f"mysekai_music_record_{it['rid']}" for it in items]
        icons = await asyncio.gather(*[get_res_icon(k, pjsk_type, (58, 58)) for k in keys], return_exceptions=True)
        for idx, (it, icon) in enumerate(zip(items, icons)):
            if isinstance(icon, Exception) or icon is None:
                icon = placeholder((58, 58))
            col, row = idx % 10, idx // 10
            x = 22 + col * 100
            yy = 50 + row * cell_h
            paste_alpha(card, icon, (x, yy))
            if it["rid"] not in user_records:
                paste_alpha(card, Image.new("RGBA", (58, 58), (0, 0, 0, 145)), (x, yy))
            else:
                # 已收集角标
                badge = load_pic_optional("music_record.png")
                if badge is not None:
                    badge = badge.resize((22, 22), Image.Resampling.LANCZOS)
                    paste_alpha(card, badge, (x + 36, yy + 36))
            if show_id:
                d.text((x, yy + 60), str(it["mid"]), fill=MUTED_COLOR, font=medium(10))
        _paste_card(pic, card, PAD, y); y += card_h + 18
    draw_watermark(pic)
    return pic.crop((0, 0, CANVAS_W, y + 50))


# 烤森材料持有

async def compose_material_image(
    profile: dict,
    is_private: bool,
    suite_data: dict,
    show_all: bool,
    pjsk_type: int = 0,
) -> Image.Image:
    import re

    user_mats = suite_data.get("userMysekaiMaterials") or []
    items: list[dict] = []
    for um in user_mats:
        qty = um.get("quantity", 0)
        if qty == 0 and not show_all:
            continue
        mid = um.get("mysekaiMaterialId")
        mat = get_by_id("mysekaiMaterials.json", mid, pjsk_type) or {}
        name = mat.get("name", f"材料{mid}")
        desc = mat.get("description", "")
        if 35 <= mid <= 60 and desc:
            m = re.search(r"和(.*?)的(?:回忆|回憶)碎片", desc)
            if m:
                name = f"{m.group(1)}的记忆"
        items.append({
            "id": mid, "qty": qty, "seq": mat.get("seq", mid), "name": name,
        })
    items.sort(key=lambda x: (x["seq"], x["id"]))
    rows = math.ceil(max(1, len(items)) / 4)
    height = 310 + rows * 92 + 120
    pic = _make_canvas(height)
    y = PAD
    header = await draw_player_header(
        profile, is_private, "MySekai 材料",
        "全部记录" if show_all else "持有材料", pjsk_type,
    )
    _paste_card(pic, header, PAD, y); y += header.height + 18

    card = _card(CANVAS_W - PAD * 2, rows * 92 + 70)
    d = ImageDraw.Draw(card)
    d.text((18, 14), "材料列表" + ("（全部记录）" if show_all else ""), fill=TEXT_COLOR, font=bold(22))
    icons = await asyncio.gather(
        *[get_res_icon(f"mysekai_material_{it['id']}", pjsk_type, (54, 54)) for it in items],
        return_exceptions=True,
    )
    for idx, (it, icon) in enumerate(zip(items, icons)):
        col, row = idx % 4, idx // 4
        x, yy = 20 + col * 255, 60 + row * 92
        if isinstance(icon, Exception) or icon is None:
            icon = placeholder((54, 54))
        paste_alpha(card, icon, (x, yy + 8))
        d.text((x + 64, yy + 10), truncate_text(it["name"], medium(15), 160), fill=MUTED_COLOR, font=medium(15))
        d.text((x + 64, yy + 36), f"x{it['qty']}", fill=TEXT_COLOR, font=bold(22))
    if not items:
        d.text((24, 70), "没有可展示的材料记录", fill=WARN_COLOR, font=bold(20))
    _paste_card(pic, card, PAD, y); y += card.height + 18
    draw_watermark(pic)
    return pic.crop((0, 0, CANVAS_W, y + 50))


# 角色对话进度

async def compose_talk_list_image(
    profile: Optional[dict],
    is_private: bool,
    mysekai_info: dict,
    suite_data: Optional[dict],
    cuid: int,
    show_all_talks: bool,
    pjsk_type: int = 0,
) -> Image.Image:
    talk = build_talk_collection(mysekai_info, suite_data, cuid, pjsk_type, show_all_talks)
    if not talk.get("ok"):
        # master 表缺失，画一张提示图
        pic = _make_canvas(360)
        d = ImageDraw.Draw(pic)
        d.text((PAD, PAD), "MySekai 对话进度", fill=ACCENT, font=bold(28))
        d.text((PAD, PAD + 60), talk.get("msg") or "数据未就绪", fill=WARN_COLOR, font=medium(20))
        return pic

    fixture_dict = talk["fixture_dict"]
    obtained_fids = talk["obtained_fids"]
    fids_single = talk.get("single", {})
    fids_multi = talk.get("multi", {})

    # 进度统计
    total_talk_num = sum(it["total"] for it in fids_single.values()) + sum(it["total"] for it in fids_multi.values())
    total_read_num = sum(it["read"] for it in fids_single.values()) + sum(it["read"] for it in fids_multi.values())

    # 按主分类整理单人对话家具
    def find_main_genre(fid: int) -> int:
        fixture = fixture_dict.get(fid)
        return fixture.get("mysekaiFixtureMainGenreId", -1) if fixture else -1

    single_by_main: dict[int, list[tuple[list[int], dict]]] = {}
    for fids_str, item in fids_single.items():
        if not fids_str or item["total"] == item["read"]:
            continue
        fids = [int(x) for x in fids_str.split()]
        main = find_main_genre(fids[0])
        single_by_main.setdefault(main, []).append((fids, item))
    for v in single_by_main.values():
        v.sort(key=lambda kv: (-len(kv[0]), kv[0][0]))

    multi_unread = [(fids_str, it) for fids_str, it in fids_multi.items() if it["total"] != it["read"]]

    # 高度估算（粗略）
    h = 360
    for v in single_by_main.values():
        h += 80 + 110 * math.ceil(len(v) / 12)
    h += 80 + 80 * len(multi_unread)
    pic = _make_canvas(max(600, h))
    y = PAD
    if profile:
        header = await draw_player_header(
            profile, is_private,
            f"对话进度",
            f"{total_read_num}/{total_talk_num} ({total_read_num / total_talk_num * 100:.1f}%)" if total_talk_num else "全部已读",
            pjsk_type,
        )
        _paste_card(pic, header, PAD, y); y += header.height + 18

    # 单人对话
    title_card = _card(CANVAS_W - PAD * 2, 60)
    d = ImageDraw.Draw(title_card)
    d.text((18, 14), "单人对话家具（未读）", fill=TEXT_COLOR, font=bold(22))
    _paste_card(pic, title_card, PAD, y); y += 60 + 12

    for main in sorted(single_by_main):
        items = single_by_main[main]
        rows = math.ceil(len(items) / 12)
        card_h = 50 + rows * 110
        card = _card(CANVAS_W - PAD * 2, card_h)
        cd = ImageDraw.Draw(card)
        cd.text((18, 12), fixture_genre_name(main, True, pjsk_type), fill=TEXT_COLOR, font=bold(20))
        for idx, (fids, info) in enumerate(items):
            col, row = idx % 12, idx // 12
            xx = 22 + col * 88
            yy = 50 + row * 110
            # 家具组合
            for j, fid in enumerate(fids[:3]):
                fx = fixture_dict.get(fid)
                icon = await get_fixture_icon(fx, pjsk_type, 0, (44, 44))
                px = xx + j * 22
                paste_alpha(card, icon, (px, yy))
                if fid not in obtained_fids:
                    paste_alpha(card, Image.new("RGBA", (44, 44), (0, 0, 0, 100)), (px, yy))
            unread = info["total"] - info["read"]
            if unread > 1:
                cd.text((xx + 50, yy - 4), f"x{unread}", fill=WARN_COLOR, font=bold(14))
            cd.text((xx, yy + 50), " ".join(str(f) for f in fids[:2]), fill=MUTED_COLOR, font=medium(11))
        _paste_card(pic, card, PAD, y); y += card_h + 16

    if not single_by_main:
        empty = _card(CANVAS_W - PAD * 2, 60)
        ImageDraw.Draw(empty).text((18, 18), "全部已读", fill=OK_COLOR, font=bold(22))
        _paste_card(pic, empty, PAD, y); y += 60 + 12

    # 多人对话
    title_card = _card(CANVAS_W - PAD * 2, 60)
    d = ImageDraw.Draw(title_card)
    d.text((18, 14), "多人对话家具（未读）", fill=TEXT_COLOR, font=bold(22))
    _paste_card(pic, title_card, PAD, y); y += 60 + 12

    for fids_str, info in multi_unread:
        fids = [int(x) for x in fids_str.split()]
        card = _card(CANVAS_W - PAD * 2, 80)
        cd = ImageDraw.Draw(card)
        for j, fid in enumerate(fids[:5]):
            fx = fixture_dict.get(fid)
            icon = await get_fixture_icon(fx, pjsk_type, 0, (44, 44))
            paste_alpha(card, icon, (18 + j * 22, 16))
            if fid not in obtained_fids:
                paste_alpha(card, Image.new("RGBA", (44, 44), (0, 0, 0, 100)), (18 + j * 22, 16))
        unread = info["total"] - info["read"]
        cd.text((24 + len(fids[:5]) * 22 + 12, 26), f"未读 x{unread}", fill=WARN_COLOR, font=bold(20))
        # 角色组合
        cx = 360
        for cuids in info["cuids_set"]:
            for cu in cuids:
                ci = await get_chara_icon_by_chara_unit_id(cu, pjsk_type, (40, 40))
                paste_alpha(card, ci, (cx, 18))
                cx += 44
            cx += 12
        _paste_card(pic, card, PAD, y); y += 80 + 12

    if not multi_unread:
        empty = _card(CANVAS_W - PAD * 2, 60)
        ImageDraw.Draw(empty).text((18, 18), "全部已读", fill=OK_COLOR, font=bold(22))
        _paste_card(pic, empty, PAD, y); y += 60 + 12

    draw_watermark(pic)
    return pic.crop((0, 0, CANVAS_W, y + 30))
