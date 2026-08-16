from __future__ import annotations

import asyncio
import io
import json
import time
from collections import OrderedDict
from datetime import datetime, timedelta
from pathlib import Path
from threading import RLock
from typing import Any, Optional

from PIL import Image

from services.log import logger

from .._autoask import pjsk_update_manager
from .._config import SUITE_API_KEYS, data_path
from .._gameapi import GameApiConfig, request_gameapi
from .._models import PjskBind, UserProfile
from .._utils import load_master_data, run_pjsk_thread
from ._utils import (
    CACHE_PATH,
    CN_MSR_GROUPS_FILE,
    MYSEKAI_HARVEST_FIXTURE_IMAGE_NAME,
    MYSEKAI_PICS_PATH,
    SITE_ID_ORDER,
    SITE_MAP_INFO,
    collect_by,
    find_all_by,
    find_by,
    get_by_id,
    get_chara_icon_by_chara_unit_id,
    get_character_icon,
    get_refresh_hours,
    get_res_rarity,
    listify,
    load_pic,
    load_pic_optional,
    placeholder,
    rip_img,
    server_name,
)

_REMOTE_ASSET_CACHE: OrderedDict[tuple[str, str, str], Image.Image] = OrderedDict()
_REMOTE_ASSET_CACHE_LIMIT = 512
_REMOTE_ASSET_CACHE_LOCK = RLock()
_REMOTE_ASSET_NEGATIVE_CACHE: OrderedDict[tuple[str, str, str], float] = OrderedDict()
_REMOTE_ASSET_NEGATIVE_TTL = 300.0
_REMOTE_ASSET_INFLIGHT: dict[tuple[str, str, str], asyncio.Task] = {}


# 异常
class MySekaiError(Exception):

    """MySekai 业务异常 — 在 matcher 里 finish 用纯文本回复。"""


def _read_json_file(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json_file(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


# 绑定与抓包数据

async def get_bound_uid(user_qq: int, pjsk_type: int = 0) -> tuple[str, bool]:
    uid, is_private = await PjskBind.get_user_bind(user_qq, pjsk_type)
    if not uid:
        s = {0: "日服", 1: "台服", 2: "国服"}.get(pjsk_type, "烧烤")
        raise MySekaiError(f"你还没有绑定{s}账号哦")
    return str(uid), bool(is_private)


def _cache_file(uid: str, pjsk_type: int) -> Path:
    path = CACHE_PATH / server_name(pjsk_type)
    path.mkdir(parents=True, exist_ok=True)
    return path / f"{uid}.json"


async def get_mysekai_info(
    uid: str,
    pjsk_type: int = 0,
    mode: str = "latest",
    filters: Optional[list[str]] = None,
    use_cache: bool = True,
) -> tuple[dict, str]:
    """获取 MySekai 数据，失败时使用本地缓存。"""
    cfg = GameApiConfig(pjsk_type)
    if not cfg.mysekai_api_url:
        raise MySekaiError(f"暂不支持 {server_name(pjsk_type).upper()} 的 MySekai 查询")
    url = cfg.mysekai_api_url.format(uid=uid)
    sep = "&" if "?" in url else "?"
    url += f"{sep}mode={mode}"
    if filters:
        url += "&filter=" + ",".join(filters)

    cache_path = _cache_file(uid, pjsk_type)
    try:
        data = await request_gameapi(url, method="GET", data_type="json")
        if not isinstance(data, dict) or not data:
            raise MySekaiError("接口没有返回有效 MySekai 数据")
        await run_pjsk_thread(_write_json_file, cache_path, data)
        return data, ""
    except Exception as e:
        logger.warning(f"获取 MySekai 数据失败 uid={uid} server={server_name(pjsk_type)}: {e}")
        if use_cache and cache_path.exists():
            try:
                data = await run_pjsk_thread(_read_json_file, cache_path)
                return data, f"接口获取失败，已使用本地缓存：{e}"
            except Exception:
                pass
        raise MySekaiError(f"获取 MySekai 数据失败：{e}")


async def get_suite_data(
    uid: str,
    pjsk_type: int = 0,
    keys: Optional[list[str]] = None,
) -> tuple[Optional[dict], str]:
    cfg = GameApiConfig(pjsk_type)
    if not cfg.suite_api_url:
        return None, "此区服不支持 Suite 数据"
    if keys is None:
        # 与 UserProfile.getsuite 共用 Suite API 字段列表。
        keys = SUITE_API_KEYS or ['userGamedata']
    url = cfg.suite_api_url.format(uid=uid) + "?mode=latest&key=" + ",".join(keys)
    try:
        data = await request_gameapi(url, method="GET", data_type="json")
        return (data if isinstance(data, dict) else None), ""
    except Exception as e:
        logger.warning(f"获取 Suite 数据失败 uid={uid}: {e}")
        return None, f"Suite 数据获取失败：{e}"


def profile_from_suite_data(uid: str, data: Optional[dict]) -> dict:
    """从已有 Suite 响应提取 MSR 头部资料，避免再次请求和完整解析。"""
    data = data if isinstance(data, dict) else {}
    gamedata = data.get("userGamedata", {})
    if not isinstance(gamedata, dict):
        gamedata = {}
    root = data
    profile_data = gamedata or root
    decks = data.get("userDecks") or profile_data.get("userDecks") or []
    cards = data.get("userCards") or profile_data.get("userCards") or []
    deck_num = profile_data.get("deck", 1)
    user_decks = [0, 0, 0, 0, 0]
    special_training = [False, False, False, False, False]
    selected_deck = next((d for d in decks if d.get("deckId") == deck_num), None)
    if isinstance(selected_deck, dict):
        for i in range(5):
            card_id = selected_deck.get(f"member{i + 1}", 0)
            user_decks[i] = card_id
            card = next((c for c in cards if c.get("cardId") == card_id), None)
            special_training[i] = isinstance(card, dict) and card.get("defaultImage") == "special_training"
    return {
        "userid": uid,
        "name": root.get("name") or profile_data.get("name") or "???",
        "rank": root.get("rank") or profile_data.get("rank", 0),
        "userDecks": user_decks,
        "special_training": special_training,
        "userProfileHonors": root.get("userProfileHonors") or profile_data.get("userProfileHonors") or [],
        "userHonorMissions": root.get("userHonorMissions") or profile_data.get("userHonorMissions") or [],
        "suite_update_time": root.get("upload_time") or int(time.time()),
    }


async def get_profile_for_header(uid: str, pjsk_type: int = 0) -> dict:
    """获取绘图头部所需的玩家资料。"""
    profile = UserProfile()
    try:
        data = await profile.getsuite(uid, pjsk_type=pjsk_type)
        return {
            "userid": uid,
            "name": profile.name or data.get("userGamedata", {}).get("name", "???"),
            "rank": profile.rank or data.get("userGamedata", {}).get("rank", 0),
            "userDecks": profile.userDecks,
            "special_training": profile.special_training,
            "userProfileHonors": profile.userProfileHonors or [],
            "userHonorMissions": profile.userHonorMissions or [],
            "suite_update_time": data.get("upload_time") or int(time.time()),
        }
    except Exception as e:
        logger.warning(f"获取 MySekai 顶部档案失败: {e}")
        return {
            "userid": uid, "name": "MySekai User", "rank": 0,
            "userDecks": [], "special_training": [],
            "userProfileHonors": [], "userHonorMissions": [],
            "suite_update_time": None,
        }


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
            return listify(load_master_data(fn, pjsk_type))
        except Exception:
            continue
    raise MySekaiError(f"缺少或无法读取 MasterData：{filename}\n请先更新 pjsk masterdata。")


def item_name(filename: str, item_id: int, pjsk_type: int = 0, default: str = "未知") -> str:
    item = get_by_id(filename, item_id, pjsk_type)
    if item:
        return item.get("name") or f"{default}({item_id})"
    return f"{default}({item_id})"


# 家具与图标

async def _fetch_remote_asset(parent: str, name: str, pjsk_type: int) -> Optional[Image.Image]:
    key = (str(pjsk_type), parent, name)
    try:
        img = await pjsk_update_manager.get_asset(parent, name, pjsk_type=pjsk_type)
        if img is not None:
            if img.mode != "RGBA":
                img = img.convert("RGBA")
            with _REMOTE_ASSET_CACHE_LOCK:
                _REMOTE_ASSET_CACHE[key] = img.copy()
                _REMOTE_ASSET_CACHE.move_to_end(key)
                _REMOTE_ASSET_NEGATIVE_CACHE.pop(key, None)
                while len(_REMOTE_ASSET_CACHE) > _REMOTE_ASSET_CACHE_LIMIT:
                    _REMOTE_ASSET_CACHE.popitem(last=False)
            return img.copy()
    except Exception as e:
        logger.debug(f"远程资源 {parent}/{name} 获取失败: {e}")
    with _REMOTE_ASSET_CACHE_LOCK:
        _REMOTE_ASSET_NEGATIVE_CACHE[key] = time.monotonic() + _REMOTE_ASSET_NEGATIVE_TTL
        _REMOTE_ASSET_NEGATIVE_CACHE.move_to_end(key)
        while len(_REMOTE_ASSET_NEGATIVE_CACHE) > _REMOTE_ASSET_CACHE_LIMIT:
            _REMOTE_ASSET_NEGATIVE_CACHE.popitem(last=False)
    return None


async def _get_remote_asset(parent: str, name: str, pjsk_type: int = 0) -> Optional[Image.Image]:
    """获取远程资源，复用成功/失败缓存并合并并发请求。"""
    key = (str(pjsk_type), parent, name)
    now = time.monotonic()
    with _REMOTE_ASSET_CACHE_LOCK:
        cached = _REMOTE_ASSET_CACHE.get(key)
        if cached is not None:
            _REMOTE_ASSET_CACHE.move_to_end(key)
            return cached.copy()
        negative_until = _REMOTE_ASSET_NEGATIVE_CACHE.get(key)
        if negative_until is not None:
            if negative_until > now:
                _REMOTE_ASSET_NEGATIVE_CACHE.move_to_end(key)
                return None
            _REMOTE_ASSET_NEGATIVE_CACHE.pop(key, None)

    task = _REMOTE_ASSET_INFLIGHT.get(key)
    if task is None:
        task = asyncio.create_task(_fetch_remote_asset(parent, name, pjsk_type))
        _REMOTE_ASSET_INFLIGHT[key] = task
    try:
        img = await asyncio.shield(task)
        return img.copy() if img is not None else None
    finally:
        if task.done() and _REMOTE_ASSET_INFLIGHT.get(key) is task:
            _REMOTE_ASSET_INFLIGHT.pop(key, None)


async def get_fixture_icon(
    fixture: dict,
    pjsk_type: int = 0,
    color_idx: int = 0,
    size=(64, 64),
) -> Image.Image:
    """获取家具图标。"""
    if not fixture:
        return placeholder(size)

    asset = fixture.get("assetbundleName")
    if not asset:
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

    # 3. 远程拉取（强制日服资源源）。
    parent = str(Path(rel_path).parent)
    img = await _get_remote_asset(parent, name, pjsk_type)
    if img is None:
        # surface_appearance 在某些 layout 缺失时退回 fixture 路径再试一次
        if ftype == "surface_appearance":
            img = await _get_remote_asset(
                f"mysekai/thumbnail/fixture", f"{asset}{suffix}.png", pjsk_type,
            )
    if img is None:
        return placeholder(size)
    return img.resize(size, Image.Resampling.LANCZOS)


async def get_fixture_by_blueprint_id(blueprint_id: int, pjsk_type: int = 0) -> Optional[dict]:
    bp = get_by_id("mysekaiBlueprints.json", blueprint_id, pjsk_type)
    if bp and bp.get("mysekaiCraftType") == "mysekai_fixture":
        return get_by_id("mysekaiFixtures.json", bp.get("craftTargetId"), pjsk_type)
    return None


async def get_res_icon(res_key: str, pjsk_type: int = 0, size=(56, 56)) -> Image.Image:
    """根据资源 key 返回图标。"""
    try:
        res_id = int(res_key.rsplit("_", 1)[-1])

        if res_key.startswith("mysekai_material"):
            mat = get_by_id("mysekaiMaterials.json", res_id, pjsk_type)
            if mat and mat.get("iconAssetbundleName"):
                asset = mat["iconAssetbundleName"]
                return await rip_img(
                    f"mysekai/thumbnail/material/{asset}.png",
                    pjsk_type, size=size, skip_local=True,
                )

        elif res_key.startswith("material"):
            return await rip_img(
                f"thumbnail/material_rip/material{res_id}.png", pjsk_type, size=size,
            )

        elif res_key.startswith("mysekai_item"):
            item = get_by_id("mysekaiItems.json", res_id, pjsk_type)
            if item and item.get("iconAssetbundleName"):
                asset = item["iconAssetbundleName"]
                return await rip_img(
                    f"mysekai/thumbnail/item/{asset}.png", pjsk_type, size=size, skip_local=True,
                )

        elif res_key.startswith("mysekai_fixture"):
            fixture = get_by_id("mysekaiFixtures.json", res_id, pjsk_type)
            if fixture and fixture.get("assetbundleName"):
                asset = fixture["assetbundleName"]
                for filename in (f"{asset}_{res_id}_1.png", f"{asset}_1.png"):
                    img = await _get_remote_asset("mysekai/thumbnail/fixture", filename, pjsk_type)
                    if img is not None:
                        return img.resize(size, Image.Resampling.LANCZOS)
            return placeholder(size)

        elif res_key.startswith("mysekai_music_record"):
            # 唱片资源使用对应歌曲封面。
            rec = get_by_id("mysekaiMusicRecords.json", res_id, pjsk_type)
            mid = rec.get("externalId") if rec else res_id
            music = get_by_id("musics.json", mid, pjsk_type)
            asset = music.get("assetbundleName") if music else f"jacket_s_{str(mid).zfill(3)}"

            # 多个本地候选路径：cn 服特有的简化路径 + 完整 *_rip 路径
            candidates = [
                # cn 服简化路径：<region>/startapp/thumbnail/music_jacket/<asset>.png
                f"thumbnail/music_jacket/{asset}.png",
                # 完整路径
                f"music/jacket/{asset}_rip/{asset}.png",
            ]
            for cand in candidates:
                img = await rip_img(cand, pjsk_type, size=size, skip_remote=True)
                # rip_img 在彻底失败时返回 placeholder，无法判断"是否命中"。
                # 改为直接检查本地 path 是否存在。
                if _local_resource_exists(cand, pjsk_type):
                    return img
            # 都不命中再走远程
            return await rip_img(
                f"music/jacket/{asset}_rip/{asset}.png", pjsk_type, size=size,
            )

        elif res_key.startswith("mysekai_blueprint"):
            fixture = await get_fixture_by_blueprint_id(res_id, pjsk_type)
            if fixture and fixture.get("assetbundleName"):
                asset = fixture["assetbundleName"]
                img = await _get_remote_asset("mysekai/thumbnail/fixture", f"{asset}_1.png", pjsk_type)
                if img is not None:
                    return img.resize(size, Image.Resampling.LANCZOS)

    except Exception as e:
        logger.debug(f"获取资源图标失败 {res_key}: {e}")
    return placeholder(size)


def _local_resource_exists(rel_path: str, pjsk_type: int = 0) -> bool:
    """检查 rel_path 是否在 kndbot 已知本地路径中存在。

    与 ``rip_img`` 的本地查找候选保持一致。
    """
    clean = rel_path.replace("startapp/", "")
    if clean.startswith("mysekai/"):
        inner = clean[len("mysekai/"):]
        name = Path(inner).name
        if (MYSEKAI_PICS_PATH / inner).exists():
            return True
        if (MYSEKAI_PICS_PATH / name).exists():
            return True
    name = Path(clean).name
    for region in (server_name(pjsk_type), "jp", "cn"):
        if (data_path / region / clean).exists():
            return True
        if (data_path / region / "startapp" / clean).exists():
            return True
    return False


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
    result: dict[int, dict[str, int]] = {sid: {} for sid in SITE_ID_ORDER}
    maps = (mysekai_info or {}).get("updatedResources", {}).get("userMysekaiHarvestMaps", [])
    for m in maps:
        sid = m.get("mysekaiSiteId")
        result.setdefault(sid, {})
        for drop in m.get("userMysekaiSiteHarvestResourceDrops", []):
            if not show_harvested and drop.get("mysekaiSiteHarvestResourceDropStatus") != "before_drop":
                continue
            key = f"{drop.get('resourceType')}_{drop.get('resourceId')}"
            result[sid][key] = result[sid].get(key, 0) + int(drop.get("quantity", 0))
    return result


def get_site_names(pjsk_type: int = 0) -> dict[int, str]:
    try:
        return {
            int(i["id"]): i.get("name", f"区域{i['id']}")
            for i in listify(load_master_data("mysekaiSites.json", pjsk_type))
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
        # birthday_plant 类型：游戏里只有当年的角色资源点会有
        if ftype == "birthday_plant":
            return None
    # 兜底用简化图标
    fname = MYSEKAI_HARVEST_FIXTURE_IMAGE_NAME.get(harvest_fid)
    if fname:
        return load_pic_optional(fname)
    return None


# 照片

async def get_photo(uid: str, seq: int, pjsk_type: int = 0) -> tuple[Image.Image, datetime]:
    info, _ = await get_mysekai_info(uid, pjsk_type)
    photos = info.get("updatedResources", {}).get("userMysekaiPhotos", [])
    if not photos:
        raise MySekaiError("没有查询到 MySekai 照片数据")
    if seq == 0:
        raise MySekaiError("照片编号从 1 或 -1 开始")
    if seq < 0:
        seq = len(photos) + seq + 1
    if seq < 1 or seq > len(photos):
        raise MySekaiError(f"照片编号超出范围，共 {len(photos)} 张")
    photo = photos[seq - 1]
    cfg = GameApiConfig(pjsk_type)
    if not cfg.mysekai_photo_api_url or cfg.mysekai_photo_api_url == "https://xxx":
        raise MySekaiError("当前未配置 MySekai 照片 API")
    raw = await request_gameapi(
        cfg.mysekai_photo_api_url, method="POST", data_type="bytes", json=photo,
    )
    return (
        Image.open(io.BytesIO(raw)).convert("RGB"),
        datetime.fromtimestamp(photo.get("obtainedAt", time.time())),
    )


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
            archive_groups = listify(load_master_data(fname, pjsk_type))
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


# CN 服群白名单

def load_cn_allowed_groups() -> set[int]:
    if not CN_MSR_GROUPS_FILE.exists():
        return set()
    try:
        data = _read_json_file(CN_MSR_GROUPS_FILE)
        if isinstance(data, list):
            return {int(x) for x in data}
        if isinstance(data, dict) and "groups" in data:
            return {int(x) for x in data["groups"]}
    except Exception as e:
        logger.warning(f"读取 cn_msr 白名单失败: {e}")
    return set()


async def async_load_cn_allowed_groups() -> set[int]:
    return await run_pjsk_thread(load_cn_allowed_groups)


def save_cn_allowed_groups(groups: set[int]) -> None:
    _write_json_file(CN_MSR_GROUPS_FILE, {"groups": sorted(groups)})


async def async_save_cn_allowed_groups(groups: set[int]) -> None:
    await run_pjsk_thread(save_cn_allowed_groups, groups)


def assert_cn_msr_allowed(group_id: Optional[int], pjsk_type: int) -> None:
    if pjsk_type != 2:  # 仅 cn 服需要白名单
        return
    if not group_id:
        raise MySekaiError("CN 服 MSR 系列指令仅在已加入白名单的群内可用")
    if int(group_id) not in load_cn_allowed_groups():
        raise MySekaiError("当前群暂未加入 CN 服 MSR 白名单，请联系管理员开通")
