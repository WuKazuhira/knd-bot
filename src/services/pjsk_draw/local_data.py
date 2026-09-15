"""独立进程模式下的数据访问。

绘图服务作为单独进程运行时不能 import plugins 层；它和 bot 共享同一个
`data/pjsk` 目录，并通过 `pjsk-helper` 按需补齐缺失资源后再读取。
bot 进程内运行时用的是插件注入的上下文（同样可自动补下载资源），不走这里。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from collections import OrderedDict
from pathlib import Path
from threading import RLock
from typing import Any, Dict, Optional

import httpx
from PIL import Image

from utils.pjsk_paths import ONDEMAND_PATH

from .config import SERVER_MAP
from .context import PjskDrawContext, set_context

# 主数据缓存按文件独立失效，避免加载一个文件时清空其它热数据。
# 以文件体积和条目数双重限制，避免把大型 costume3ds 原始表无限常驻内存。
_MASTER_CACHE: "OrderedDict[str, tuple[int, int, Any]]" = OrderedDict()
_MASTER_CACHE_LIMIT = 24
_MASTER_CACHE_BYTES_LIMIT = 64 << 20
_MASTER_CACHE_BYTES = 0
_MASTER_CACHE_LOCK = RLock()
_UNCACHED_MASTER_FILES = {"costume3ds.json"}

_LOGGER = logging.getLogger(__name__)
_ASSET_FETCH_TIMEOUT = float(os.getenv("PJSK_DRAW_ASSET_FETCH_TIMEOUT", "90"))


# 常用 id 索引是派生数据，单独限长，且随主数据 mtime/size 变化失效。
_INDEX_CACHE: "OrderedDict[tuple, Dict[Any, Any]]" = OrderedDict()
_INDEX_CACHE_LIMIT = 64


def _server_dir(pjsk_type: int) -> Path:
    return ONDEMAND_PATH / SERVER_MAP.get(pjsk_type, "jp")


def _unwrap(data: Any) -> Any:
    """主数据有时被包一层字典，这里还原成列表，与插件侧行为保持一致。"""
    if not isinstance(data, dict):
        return data
    for key in ("data", "items", "list", "entries", "rankings", "masterData", "cards", "musics", "events", "skills"):
        value = data.get(key)
        if isinstance(value, (list, dict)):
            data = value
            break
    if isinstance(data, dict):
        keys = list(data.keys())
        if keys and all(str(key).isdigit() for key in keys):
            return list(data.values())
        filtered = [value for value in data.values() if isinstance(value, (dict, list))]
        if len(filtered) == 1 and len(keys) > 1:
            return filtered[0]
        return filtered
    return data


def load_master_data(filename: str, pjsk_type: int = 0) -> Any:
    path = _server_dir(pjsk_type) / filename
    if not path.exists():
        raise FileNotFoundError(f"MasterData {filename} 不存在: {path}")
    stat = path.stat()
    cache_key = str(path)
    use_cache = filename not in _UNCACHED_MASTER_FILES

    if use_cache:
        with _MASTER_CACHE_LOCK:
            cached = _MASTER_CACHE.get(cache_key)
            if cached is not None and cached[0] == stat.st_mtime_ns and cached[1] == stat.st_size:
                _MASTER_CACHE.move_to_end(cache_key)
                return cached[2]

    with path.open("r", encoding="utf-8") as file:
        data = _unwrap(json.load(file))
    if not use_cache:
        return data

    global _MASTER_CACHE_BYTES
    with _MASTER_CACHE_LOCK:
        old = _MASTER_CACHE.pop(cache_key, None)
        if old is not None:
            _MASTER_CACHE_BYTES -= old[1]
        _MASTER_CACHE[cache_key] = (stat.st_mtime_ns, stat.st_size, data)
        _MASTER_CACHE_BYTES += stat.st_size
        while _MASTER_CACHE and (
            len(_MASTER_CACHE) > _MASTER_CACHE_LIMIT or _MASTER_CACHE_BYTES > _MASTER_CACHE_BYTES_LIMIT
        ):
            _, evicted = _MASTER_CACHE.popitem(last=False)
            _MASTER_CACHE_BYTES -= evicted[1]
    return data


async def async_load_master_data(filename: str, pjsk_type: int = 0) -> Any:
    return await asyncio.to_thread(load_master_data, filename, pjsk_type)


def master_data_by_id(filename: str, pjsk_type: int = 0, key: str = "id") -> Dict[Any, Any]:
    path = _server_dir(pjsk_type) / filename
    stat = path.stat()
    cache_key = (str(path), stat.st_mtime_ns, stat.st_size, key)
    with _MASTER_CACHE_LOCK:
        cached = _INDEX_CACHE.get(cache_key)
        if cached is not None:
            _INDEX_CACHE.move_to_end(cache_key)
            return cached

    items = load_master_data(filename, pjsk_type)
    if not isinstance(items, list):
        return {}
    index = {item[key]: item for item in items if isinstance(item, dict) and key in item}
    with _MASTER_CACHE_LOCK:
        _INDEX_CACHE[cache_key] = index
        _INDEX_CACHE.move_to_end(cache_key)
        while len(_INDEX_CACHE) > _INDEX_CACHE_LIMIT:
            _INDEX_CACHE.popitem(last=False)
    return index


def _asset_helper_url() -> str:
    return os.getenv("PJSK_HELPER_URL", "http://host.docker.internal:45558").rstrip("/")


async def _request_asset_download(path: str, raw: str, pjsk_type: int) -> bool:
    helper_url = _asset_helper_url()
    if not helper_url:
        _LOGGER.warning("未配置 PJSK_HELPER_URL，无法下载资源 %s/%s", path, raw)
        return False

    params = {
        "region": SERVER_MAP.get(pjsk_type, "jp"),
        "path": path,
        "raw": raw,
    }
    try:
        async with httpx.AsyncClient(timeout=_ASSET_FETCH_TIMEOUT, trust_env=False) as client:
            response = await client.post(f"{helper_url}/assets/fetch", params=params)
        response.raise_for_status()
        payload = response.json()
        if not payload.get("ok"):
            _LOGGER.warning(
                "资源下载失败 %s/%s: %s",
                path,
                raw,
                payload.get("error", "helper returned ok=false"),
            )
            return False
        return True
    except (httpx.HTTPError, ValueError) as exc:
        _LOGGER.warning("请求 pjsk-helper 下载资源失败 %s/%s: %s", path, raw, exc)
        return False


async def get_asset(
    path: str,
    raw: str,
    pjsk_type: int = 0,
    block: bool = False,
    download: bool = True,
) -> Optional[Image.Image]:
    """从共享目录读取资源，缺失时通过 pjsk-helper 按需下载。"""
    file_path = _server_dir(pjsk_type) / path / raw
    if not file_path.exists() and download:
        await update_assets(path, raw, pjsk_type=pjsk_type, block=block)
    if not file_path.exists():
        return None
    return await asyncio.to_thread(_open, file_path)


def _open(file_path: Path) -> Optional[Image.Image]:
    try:
        with Image.open(file_path) as source:
            return source.convert("RGBA")
    except Exception:
        return None


async def update_assets(path: str, raw: str, pjsk_type: int = 0, block: bool = False) -> None:
    """通过 pjsk-helper 下载缺失资源到共享目录。"""
    file_path = _server_dir(pjsk_type) / path / raw
    if file_path.exists():
        return
    await _request_asset_download(path, raw, pjsk_type)


def _cardtype(cardid, cardCostume3ds, costume3ds) -> int:
    hair = {item.get("id") for item in costume3ds if isinstance(item, dict) and item.get("partType") == "hair"}
    limited = {item.get("cardId") for item in cardCostume3ds if isinstance(item, dict) and item.get("costume3dId") in hair}
    return 1 if cardid in limited else 0


def _getcharaname(characterid, gameCharacters=None, pjsk_type: int = 0):
    if gameCharacters is None:
        gameCharacters = load_master_data("gameCharacters.json", pjsk_type)
    for item in gameCharacters:
        if isinstance(item, dict) and item.get("id") == characterid:
            return (item.get("firstName") or "") + item.get("givenName", "")
    return None


def _is_fes_card(card, card_supplies=None, pjsk_type: int = 0) -> bool:
    try:
        if isinstance(card, int):
            card = master_data_by_id("cards.json", pjsk_type).get(card)
        if not card:
            return False
        supply_id = card.get("cardSupplyId")
        if not supply_id:
            return False
        if card_supplies is None:
            card_supplies = load_master_data("cardSupplies.json", pjsk_type)
        for supply in card_supplies:
            if isinstance(supply, dict) and supply.get("id") == supply_id:
                return supply.get("cardSupplyType", "") in (
                    "colorful_festival_limited",
                    "bloom_festival_limited",
                )
        return False
    except Exception:
        return False


def install_local_context() -> None:
    """独立进程启动时注册只读上下文。"""
    set_context(
        PjskDrawContext(
            get_asset=get_asset,
            update_assets=update_assets,
            load_master_data=load_master_data,
            async_load_master_data=async_load_master_data,
            master_data_by_id=master_data_by_id,
            server_map=SERVER_MAP,
            cardtype=_cardtype,
            is_fes_card=_is_fes_card,
            getcharaname=_getcharaname,
        )
    )
