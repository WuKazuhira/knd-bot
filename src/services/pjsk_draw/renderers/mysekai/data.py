from __future__ import annotations

import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from PIL import Image

from services.log import logger
from utils.pjsk_paths import ONDEMAND_PATH

from ...context import get_context
from .common import (
    MYSEKAI_HARVEST_FIXTURE_IMAGE_NAME,
    MYSEKAI_PICS_PATH,
    SITE_ID_ORDER,
    MySekaiError,
    find_all_by,
    find_by,
    get_by_id,
    get_refresh_hours,
    listify,
    load_mysekai_asset,
    load_pic_optional,
    placeholder,
    record_missing_asset,
    rip_img,
    server_name,
)
from .compact_schema import effective_drop_quantity, restore_compact_mysekai_data

data_path = ONDEMAND_PATH

# MasterData 工具

# 部分服务器把同一文件命名稍有差异；ensure_master 提供宽容回退。
_MASTER_ALIASES: dict[str, list[str]] = {
    "mysekaiBlueprintMaterialCosts.json": ["mysekaiBlueprintMaterialCost.json"],
    "mysekaiGateMaterialGroups.json": ["mysekaiGateLevels.json"],
    "mysekaiMusicRecords.json": ["musics.json"],  # 极端兜底
}


def ensure_master(filename: str, pjsk_type: int = 0) -> list:
    tried: list[str] = []
    for fn in [filename] + _MASTER_ALIASES.get(filename, []):
        if fn in tried:
            continue
        tried.append(fn)
        try:
            return listify(get_context().load_master_data(fn, pjsk_type))
        except Exception:
            continue
    raise MySekaiError(f"缺少或无法读取 MasterData：{filename}\n请先更新 pjsk masterdata。")


def item_name(filename: str, item_id: int, pjsk_type: int = 0, default: str = "未知") -> str:
    item = get_by_id(filename, item_id, pjsk_type)
    if item:
        return item.get("name") or f"{default}({item_id})"
    return f"{default}({item_id})"


# 家具与图标

async def _get_remote_asset(
    parent: str, name: str, pjsk_type: int = 0, *, remote_only: bool = False,
) -> Optional[Image.Image]:
    """资源缩略图可显式通过配置的 HTTP 源读取，其他展示用途保持本地优先。"""
    return await load_mysekai_asset(f"{parent}/{name}", pjsk_type, remote_only=remote_only)


async def get_fixture_icon(
    fixture: dict,
    pjsk_type: int = 0,
    color_idx: int = 0,
    size=(64, 64),
) -> Image.Image:
    """获取家具图标。"""
    if not fixture:
        record_missing_asset("fixture:unknown", pjsk_type, "master-missing")
        return placeholder(size)

    asset = fixture.get("assetbundleName")
    if not asset:
        record_missing_asset(f"fixture:{fixture.get('id')}", pjsk_type, "asset-name-missing")
        return placeholder(size)
    ftype = fixture.get("mysekaiFixtureType")
    layout = fixture.get("mysekaiSettableLayoutType")
    another = fixture.get("mysekaiFixtureAnotherColors") or []
    color_count = 1 + len(another)
    color_idx = max(0, min(color_idx, color_count - 1))

    # 1. 计算正确的远程路径（也作为本地查找键）。
    if ftype == "surface_appearance" and layout:
        suffix = "_1" if color_count == 1 else f"_{color_idx + 1}"
        rel_path = f"mysekai/thumbnail/surface_appearance/{asset}/tex_{asset}_{layout}{suffix}.png"
    else:
        suffix = f"_{color_idx + 1}"
        rel_path = f"mysekai/thumbnail/fixture/{asset}{suffix}.png"

    # 2. 本地查找（kndbot 旧布局下 harvest_fixture_icon/{rarity}/...）。
    #    对于 plant 类型的家具，本地有 ``mdl_non1001_*_{fid}.png`` 这种特殊命名，
    #    优先直接 asset 名 + .png。
    name = Path(rel_path).name
    local_candidates = [
        MYSEKAI_PICS_PATH / name,
        MYSEKAI_PICS_PATH / "harvest_fixture_icon" / "rarity_1" / name,
        MYSEKAI_PICS_PATH / "harvest_fixture_icon" / "rarity_2" / name,
        # plant 家具的 ``mdl_non1001_before_sapling1_<fid>.png`` 这种本地图。
        MYSEKAI_PICS_PATH / f"{asset}_{fixture.get('id')}.png",
    ]
    for lp in local_candidates:
        if lp.exists():
            try:
                img = Image.open(lp).convert("RGBA")
                return img.resize(size, Image.Resampling.LANCZOS)
            except Exception as e:
                logger.debug(f"本地家具图标 {lp} 加载失败: {e}")

    # 3. 本地优先、当前服务器远程源，使用统一资源缓存。
    parent = str(Path(rel_path).parent)
    img = await _get_remote_asset(parent, name, pjsk_type)
    if img is None:
        # surface_appearance 在某些 layout 缺失时退回 fixture 路径再试一次
        if ftype == "surface_appearance":
            img = await _get_remote_asset(
                f"mysekai/thumbnail/fixture", f"{asset}{suffix}.png", pjsk_type,
            )
    if img is None:
        record_missing_asset(rel_path, pjsk_type)
        return placeholder(size)
    return img.resize(size, Image.Resampling.LANCZOS)


async def get_fixture_by_blueprint_id(blueprint_id: int, pjsk_type: int = 0) -> Optional[dict]:
    bp = get_by_id("mysekaiBlueprints.json", blueprint_id, pjsk_type)
    if bp and bp.get("mysekaiCraftType") == "mysekai_fixture":
        return get_by_id("mysekaiFixtures.json", bp.get("craftTargetId"), pjsk_type)
    return None


async def get_res_icon(res_key: str, pjsk_type: int = 0, size=(56, 56)) -> Image.Image:
    """根据当前服别主数据，通过配置的 HTTP 资产源获取资源缩略图。"""
    try:
        res_id = int(res_key.rsplit("_", 1)[-1])

        if res_key.startswith("mysekai_material"):
            mat = get_by_id("mysekaiMaterials.json", res_id, pjsk_type)
            if mat and mat.get("iconAssetbundleName"):
                asset = mat["iconAssetbundleName"]
                return await rip_img(
                    f"mysekai/thumbnail/material/{asset}.png",
                    pjsk_type, size=size, remote_only=True,
                )

        elif res_key.startswith("material"):
            return await rip_img(
                f"thumbnail/material_rip/material{res_id}.png", pjsk_type, size=size, remote_only=True,
            )

        elif res_key.startswith("mysekai_item"):
            item = get_by_id("mysekaiItems.json", res_id, pjsk_type)
            if item and item.get("iconAssetbundleName"):
                asset = item["iconAssetbundleName"]
                return await rip_img(
                    f"mysekai/thumbnail/item/{asset}.png", pjsk_type, size=size, remote_only=True,
                )

        elif res_key.startswith("mysekai_fixture"):
            fixture = get_by_id("mysekaiFixtures.json", res_id, pjsk_type)
            if fixture and fixture.get("assetbundleName"):
                asset = fixture["assetbundleName"]
                img = await _get_remote_asset(
                    "mysekai/thumbnail/fixture", f"{asset}_1.png", pjsk_type, remote_only=True,
                )
                if img is not None:
                    return img.resize(size, Image.Resampling.LANCZOS)
            record_missing_asset(res_key, pjsk_type, "resource-icon-missing")
            return placeholder(size)

        elif res_key.startswith("mysekai_music_record"):
            # 唱片资源使用对应歌曲封面。
            rec = get_by_id("mysekaiMusicRecords.json", res_id, pjsk_type)
            mid = rec.get("externalId") if rec else res_id
            music = get_by_id("musics.json", mid, pjsk_type)
            asset = music.get("assetbundleName") if music else f"jacket_s_{str(mid).zfill(3)}"

            # 唱片封面同样按当前服 HTTP 源顺序获取，不探测磁盘或跨服图片。
            for candidate in (
                f"music/jacket/{asset}_rip/{asset}.png",
                f"thumbnail/music_jacket/{asset}.png",
            ):
                img = await load_mysekai_asset(
                    candidate, pjsk_type, size=size, remote_only=True,
                )
                if img is not None:
                    return img
            record_missing_asset(res_key, pjsk_type, "remote-record-cover-missing")
            return placeholder(size)

        elif res_key.startswith("mysekai_blueprint"):
            fixture = await get_fixture_by_blueprint_id(res_id, pjsk_type)
            if fixture and fixture.get("assetbundleName"):
                asset = fixture["assetbundleName"]
                img = await _get_remote_asset(
                    "mysekai/thumbnail/fixture", f"{asset}_1.png", pjsk_type, remote_only=True,
                )
                if img is not None:
                    return img.resize(size, Image.Resampling.LANCZOS)

    except Exception as e:
        logger.debug(f"获取资源图标失败 {res_key}: {e}")
    record_missing_asset(res_key, pjsk_type, "resource-icon-missing")
    return placeholder(size)


def get_res_name(res_key: str, pjsk_type: int = 0) -> str:
    try:
        rid = int(res_key.rsplit("_", 1)[-1])
        if res_key.startswith("mysekai_material"):
            return item_name("mysekaiMaterials.json", rid, pjsk_type, "材料")
        if res_key.startswith("material"):
            return item_name("materials.json", rid, pjsk_type, "道具")
        if res_key.startswith("mysekai_item"):
            return item_name("mysekaiItems.json", rid, pjsk_type, "道具")
        if res_key.startswith("mysekai_fixture"):
            return item_name("mysekaiFixtures.json", rid, pjsk_type, "家具")
        if res_key.startswith("mysekai_music_record"):
            rec = get_by_id("mysekaiMusicRecords.json", rid, pjsk_type)
            mid = rec.get("externalId") if rec else rid
            music = get_by_id("musics.json", mid, pjsk_type)
            return (music or {}).get("title") or f"唱片({rid})"
        if res_key.startswith("mysekai_blueprint"):
            fixture = get_by_id("mysekaiFixtures.json", rid, pjsk_type)
            if fixture:
                return f"蓝图·{fixture.get('name', rid)}"
            return f"蓝图({rid})"
    except Exception:
        pass
    return res_key


# 资源汇总

def summarize_resources(mysekai_info: dict, show_harvested: bool = False) -> dict[int, dict[str, int]]:
    """把抓包数据按 site_id → res_key → 总数量聚合。"""
    mysekai_info = restore_compact_mysekai_data(mysekai_info)
    result: dict[int, dict[str, int]] = {sid: {} for sid in SITE_ID_ORDER}
    maps = (mysekai_info or {}).get("updatedResources", {}).get("userMysekaiHarvestMaps", [])
    for m in maps:
        sid = m.get("mysekaiSiteId")
        result.setdefault(sid, {})
        for drop in m.get("userMysekaiSiteHarvestResourceDrops", []):
            if not show_harvested and drop.get("mysekaiSiteHarvestResourceDropStatus") != "before_drop":
                continue
            key = f"{drop.get('resourceType')}_{drop.get('resourceId')}"
            result[sid][key] = result[sid].get(key, 0) + effective_drop_quantity(drop)
    return result


def get_site_names(pjsk_type: int = 0) -> dict[int, str]:
    try:
        return {
            int(i["id"]): i.get("name", f"区域{i['id']}")
            for i in listify(get_context().load_master_data("mysekaiSites.json", pjsk_type))
            if isinstance(i, dict)
        }
    except Exception:
        return {5: "森林", 7: "山丘", 6: "海边", 8: "遗迹"}


# 天气

def get_current_phenomena(mysekai_info: dict, pjsk_type: int = 0) -> tuple[Optional[int], list[int], list[datetime]]:
    """返回 (当前天气 id, 预报天气 id 列表, 各天气开始时间)。

    MySekai 天气按区服自然刷新时间切换：CN 为 05:00/17:00，其它服为 04:00/16:00。
    抓包中的 ``mysekaiPhenomenaSchedules`` 顺序与当前刷新日的两个自然刷新段一致。
    """
    schedule = (mysekai_info or {}).get("mysekaiPhenomenaSchedules", []) or []
    ids = [i.get("mysekaiPhenomenaId") for i in schedule if isinstance(i, dict) and i.get("mysekaiPhenomenaId") is not None]
    if not ids:
        return None, [], []

    upload_time = datetime.fromtimestamp(mysekai_info.get("upload_time", time.time()))
    h1, h2 = get_refresh_hours(pjsk_type)
    if h1 < h2:
        idx = 1 if upload_time.hour < h1 or upload_time.hour >= h2 else 0
        start = upload_time.replace(hour=h1, minute=0, second=0, microsecond=0)
        if upload_time.hour < h1:
            start -= timedelta(days=1)
    else:
        idx = 1 if h2 <= upload_time.hour < h1 else 0
        start = upload_time.replace(hour=h1, minute=0, second=0, microsecond=0)
        if h2 <= upload_time.hour < h1:
            start -= timedelta(days=1)
    idx = min(idx, len(ids) - 1)
    start_times = [start + timedelta(hours=12 * i) for i in range(len(ids))]
    return ids[idx], ids, start_times


async def get_phenomena_icon(phenom_id: int, pjsk_type: int = 0, size=(50, 50)) -> Image.Image:
    p = get_by_id("mysekaiPhenomenas.json", phenom_id, pjsk_type)
    asset = p.get("iconAssetbundleName") if p else None
    if asset:
        return await rip_img(f"mysekai/thumbnail/phenomena/{asset}.png", pjsk_type, size=size)
    return placeholder(size, "☁")


# 家具集合

def build_fixture_collection(
    mysekai_info: Optional[dict],
    pjsk_type: int = 0,
    only_craftable: bool = False,
) -> tuple[dict, set[int], dict[int, int]]:
    fixtures = ensure_master("mysekaiFixtures.json", pjsk_type)

    obtained_fids: set[int] = set()
    if mysekai_info:
        for item in mysekai_info.get("updatedResources", {}).get("userMysekaiBlueprints", []):
            bp = get_by_id("mysekaiBlueprints.json", item.get("mysekaiBlueprintId"), pjsk_type)
            if bp and bp.get("mysekaiCraftType") == "mysekai_fixture":
                obtained_fids.add(bp.get("craftTargetId"))

    craftable_fids: Optional[set[int]] = None
    if only_craftable:
        craftable_fids = set()
        for bp in ensure_master("mysekaiBlueprints.json", pjsk_type):
            if isinstance(bp, dict) and bp.get("mysekaiCraftType") == "mysekai_fixture":
                craftable_fids.add(bp.get("craftTargetId"))

    groups: dict[int, dict[int, list[tuple[int, bool]]]] = {}
    birthday_fids: dict[int, int] = {}
    characters = ensure_master("gameCharacters.json", pjsk_type)
    for fx in fixtures:
        if not isinstance(fx, dict):
            continue
        fid = fx.get("id")
        if not fid or fx.get("mysekaiFixtureType") == "gate":
            continue
        if craftable_fids is not None and fid not in craftable_fids:
            continue
        fname = fx.get("name", "")
        for c in characters:
            if isinstance(c, dict) and fname.endswith(f"（{c.get('givenName', '')}）"):
                birthday_fids[fid] = c.get("id")
                break
        main = fx.get("mysekaiFixtureMainGenreId", -1)
        sub = fx.get("mysekaiFixtureSubGenreId", -1)
        if fid == 4:
            sub = 14
        if main in (4, 5, 7, 8, 9, 10, 11, 12, 13):
            sub = -1
        groups.setdefault(main, {}).setdefault(sub, []).append(
            (fid, (not mysekai_info) or fid in obtained_fids),
        )
    return groups, obtained_fids, birthday_fids


def fixture_genre_name(gid: int, main: bool, pjsk_type: int = 0) -> str:
    fn = "mysekaiFixtureMainGenres.json" if main else "mysekaiFixtureSubGenres.json"
    return item_name(fn, gid, pjsk_type, "分类")


# 资源点图标

async def get_harvest_fixture_icon(harvest_fid: int, pjsk_type: int = 0) -> Optional[Image.Image]:
    """返回 ``mysekaiSiteHarvestFixtures`` 上面的简化图标。

    优先按 master data 中 assetbundleName + rarity 路径加载，
    再退回 ``MYSEKAI_HARVEST_FIXTURE_IMAGE_NAME`` 简化映射。
    """
    fixture_meta = get_by_id("mysekaiSiteHarvestFixtures.json", harvest_fid, pjsk_type)
    if fixture_meta:
        asset = fixture_meta.get("assetbundleName")
        rarity = fixture_meta.get("mysekaiSiteHarvestFixtureRarityType")
        ftype = fixture_meta.get("mysekaiSiteHarvestFixtureType")
        if asset and rarity:
            local = MYSEKAI_PICS_PATH / "harvest_fixture_icon" / rarity / f"{asset}.png"
            if local.exists():
                try:
                    return Image.open(local).convert("RGBA")
                except Exception:
                    pass
            img = await _get_remote_asset(
                f"mysekai/harvest_fixture_icon/{rarity}", f"{asset}.png", pjsk_type,
            )
            if img is not None:
                return img
            record_missing_asset(f"mysekai/harvest_fixture_icon/{rarity}/{asset}.png", pjsk_type)
        # birthday_plant 类型：游戏里只有当年的角色资源点会有
        if ftype == "birthday_plant":
            return None
    # 兜底用简化图标
    fname = MYSEKAI_HARVEST_FIXTURE_IMAGE_NAME.get(harvest_fid)
    if fname:
        return load_pic_optional(fname)
    return None


# 来访角色

def get_visit_info(mysekai_info: dict, pjsk_type: int = 0) -> dict:
    visit = (mysekai_info or {}).get("userMysekaiGateCharacterVisit") or {}
    gate = visit.get("userMysekaiGate") or {}
    chars = visit.get("userMysekaiGateCharacters") or []
    visit_cgids: list[int] = []
    seen: set[int] = set()
    reservation_cgid: Optional[int] = None
    for item in chars:
        cgid = item.get("mysekaiGameCharacterUnitGroupId")
        if not cgid or cgid in seen:
            continue
        cuids = get_unit_group_chars(cgid, pjsk_type)
        # 到访列表只展示单人来访。多人组合是对话组合数据，直接画会产生大量“重复角色”。
        if len(cuids) != 1:
            continue
        seen.add(cgid)
        visit_cgids.append(cgid)
        if item.get("isReservation"):
            reservation_cgid = cgid
    return {
        "gate_id": gate.get("mysekaiGateId"),
        "gate_level": gate.get("mysekaiGateLevel"),
        "visit_cgids": visit_cgids,
        "reservation_cgid": reservation_cgid,
    }


def get_unit_group_chars(cgid: int, pjsk_type: int = 0) -> list[int]:
    """从 mysekaiGameCharacterUnitGroups 拆出该组合的全部 chara_unit_id。"""
    group = get_by_id("mysekaiGameCharacterUnitGroups.json", cgid, pjsk_type) or {}
    cuids: list[int] = []
    for i in range(1, 10):
        key = f"gameCharacterUnitId{i}"
        if key in group:
            cuids.append(group[key])
    return cuids


# 角色对话进度

def build_talk_collection(
    mysekai_info: dict,
    suite_data: Optional[dict],
    cuid: int,
    pjsk_type: int = 0,
    show_all_talks: bool = False,
) -> dict:
    """返回单人/多人对话已读进度结构。

    - ``cuid``：要查询的 ``gameCharacterUnits.id``。
    - ``show_all_talks=False`` 时按 suite 中 ``userMysekaiCharacterTalks`` 计算未读。
    - ``show_all_talks=True`` 时仅统计总条数，全部视为未读，用于"全部对话"视图。

    由于 master 表庞大，函数内部尽量惰性 ``find_by``；某张表缺失时降级为空表，保证插件不崩。
    """
    mysekai_info = restore_compact_mysekai_data(mysekai_info)
    suite_data = restore_compact_mysekai_data(suite_data)

    obtained_fids: set[int] = set()
    for item in (mysekai_info or {}).get("updatedResources", {}).get("userMysekaiBlueprints", []):
        bp = get_by_id("mysekaiBlueprints.json", item.get("mysekaiBlueprintId"), pjsk_type)
        if bp and bp.get("mysekaiCraftType") == "mysekai_fixture":
            obtained_fids.add(bp.get("craftTargetId"))

    fixtures = ensure_master("mysekaiFixtures.json", pjsk_type)
    fixture_dict = {f.get("id"): f for f in fixtures if isinstance(f, dict)}

    user_talks: list[dict] = []
    if not show_all_talks and suite_data:
        user_talks = suite_data.get("userMysekaiCharacterTalks") or []

    # 加载 4 张 master 表，缺失时降级。
    try:
        fixture_conds = find_all_by(
            ensure_master("mysekaiCharacterTalkConditions.json", pjsk_type),
            "mysekaiCharacterTalkConditionType", "mysekai_fixture_id",
        )
    except MySekaiError:
        return {
            "ok": False,
            "msg": "缺少 mysekaiCharacterTalkConditions 数据，请等待远程同步后重试",
            "fixture_dict": fixture_dict,
            "obtained_fids": obtained_fids,
        }
    try:
        cond_groups = ensure_master("mysekaiCharacterTalkConditionGroups.json", pjsk_type)
    except MySekaiError:
        return {
            "ok": False,
            "msg": "缺少 mysekaiCharacterTalkConditionGroups 数据，请等待远程同步后重试",
            "fixture_dict": fixture_dict,
            "obtained_fids": obtained_fids,
        }
    try:
        talks = ensure_master("mysekaiCharacterTalks.json", pjsk_type)
    except MySekaiError:
        return {
            "ok": False,
            "msg": "缺少 mysekaiCharacterTalks 数据，请等待远程同步后重试",
            "fixture_dict": fixture_dict,
            "obtained_fids": obtained_fids,
        }

    archive_groups = []
    for fname in [
        "characterArchiveMysekaiCharacterTalkGroups.json",
        "mysekaiCharacterArchiveTalkGroups.json",
    ]:
        try:
            archive_groups = listify(get_context().load_master_data(fname, pjsk_type))
            if archive_groups:
                break
        except Exception:
            continue

    # 预索引以提速
    fid_to_cond_ids: dict[int, set[int]] = {}
    for c in fixture_conds:
        fid = c.get("mysekaiCharacterTalkConditionTypeValue")
        if fid is None:
            continue
        fid_to_cond_ids.setdefault(fid, set()).add(c.get("id"))

    cond_to_groups: dict[int, list[int]] = {}
    for g in cond_groups:
        if not isinstance(g, dict):
            continue
        gid = g.get("id")
        cid_ = g.get("mysekaiCharacterTalkConditionId")
        if cid_ is None or gid is None:
            continue
        cond_to_groups.setdefault(cid_, []).append(gid)

    group_to_talks: dict[int, list[dict]] = {}
    for t in talks:
        if not isinstance(t, dict):
            continue
        gid = t.get("mysekaiCharacterTalkConditionGroupId")
        if gid is None:
            continue
        group_to_talks.setdefault(gid, []).append(t)

    archive_id_to_display: dict[int, str] = {
        a.get("id"): a.get("archiveDisplayType", "normal")
        for a in archive_groups
        if isinstance(a, dict)
    }

    aid_reads: dict[int, dict] = {}
    for fid, fixture in fixture_dict.items():
        if fixture.get("mysekaiFixtureType") == "gate":
            continue
        cond_ids = fid_to_cond_ids.get(fid, set())
        if not cond_ids:
            continue
        group_ids: list[int] = []
        for cid_ in cond_ids:
            group_ids.extend(cond_to_groups.get(cid_, []))
        for gid in group_ids:
            for t in group_to_talks.get(gid, []):
                ucug = get_by_id(
                    "mysekaiGameCharacterUnitGroups.json",
                    t.get("mysekaiGameCharacterUnitGroupId"), pjsk_type,
                ) or {}
                group_cuids: list[int] = []
                for i in range(1, 10):
                    if f"gameCharacterUnitId{i}" in ucug:
                        group_cuids.append(ucug[f"gameCharacterUnitId{i}"])
                tid = t.get("id")
                aid = t.get("characterArchiveMysekaiCharacterTalkGroupId")
                display = archive_id_to_display.get(aid, "normal") == "normal" if archive_id_to_display else True
                if cuid in group_cuids and display:
                    user_talk = find_by(user_talks, "mysekaiCharacterTalkId", tid)
                    has_read = bool(user_talk is not None and user_talk.get("isRead"))
                    rec = aid_reads.setdefault(aid, {"fids": set(), "has_read": False, "cuids": group_cuids})
                    rec["fids"].add(fid)
                    rec["has_read"] = rec["has_read"] or has_read

    fids_single_reads: dict[str, dict] = {}
    fids_multi_reads: dict[str, dict] = {}
    for aid, item in aid_reads.items():
        cuids_ = item["cuids"]
        fids_key = " ".join(str(f) for f in sorted(item["fids"]))
        target = fids_single_reads if len(cuids_) == 1 else fids_multi_reads
        rec = target.setdefault(fids_key, {"total": 0, "read": 0, "cuids_set": set()})
        rec["total"] += 1
        rec["read"] += int(item["has_read"])
        if not item["has_read"]:
            rec["cuids_set"].add(tuple(cuids_))

    return {
        "ok": True,
        "msg": "",
        "fixture_dict": fixture_dict,
        "obtained_fids": obtained_fids,
        "single": fids_single_reads,
        "multi": fids_multi_reads,
    }

