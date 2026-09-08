"""MySekai 数据获取与权限校验。

绘图所需的主数据整理与图标加载已迁至
services/pjsk_draw/renderers/mysekai/data.py，这里只保留
抓包接口调用、绑定查询、照片获取和 CN 服白名单。
"""

from __future__ import annotations

import io
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from PIL import Image

from services.log import logger
from services.pjsk_draw.renderers.mysekai.common import (
    CACHE_PATH,
    CN_MSR_GROUPS_FILE,
    MySekaiError,
    server_name,
)

from .._config import SUITE_API_KEYS
from .._gameapi import GameApiConfig, request_gameapi
from .._models import PjskBind, UserProfile
from .._utils import run_pjsk_thread

__all__ = [
    "MySekaiError",
    "assert_cn_msr_allowed",
    "async_load_cn_allowed_groups",
    "async_save_cn_allowed_groups",
    "get_bound_uid",
    "get_mysekai_info",
    "get_photo",
    "get_profile_for_header",
    "get_suite_data",
    "load_cn_allowed_groups",
    "profile_from_suite_data",
    "save_cn_allowed_groups",
]


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
