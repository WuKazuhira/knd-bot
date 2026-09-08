"""独立进程模式下的只读数据访问。

绘图服务作为单独进程跑时不能 import plugins 层，也不该自己去下载资源：
它和 bot 共享同一个 data/pjsk 目录（docker volume），只负责读。
bot 进程内运行时用的是插件注入的上下文（可自动补下载资源），不走这里。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, Dict, Optional

from PIL import Image

from utils.pjsk_paths import ONDEMAND_PATH

from .config import SERVER_MAP
from .context import PjskDrawContext, set_context

_CACHE: Dict[tuple, Any] = {}


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
    key = (str(path), stat.st_mtime_ns, stat.st_size)
    cached = _CACHE.get(key)
    if cached is not None:
        return cached
    with path.open("r", encoding="utf-8") as file:
        data = _unwrap(json.load(file))
    _CACHE.clear()
    _CACHE[key] = data
    return data


async def async_load_master_data(filename: str, pjsk_type: int = 0) -> Any:
    return await asyncio.to_thread(load_master_data, filename, pjsk_type)


def master_data_by_id(filename: str, pjsk_type: int = 0, key: str = "id") -> Dict[Any, Any]:
    items = load_master_data(filename, pjsk_type)
    if not isinstance(items, list):
        return {}
    return {item[key]: item for item in items if isinstance(item, dict) and key in item}


async def get_asset(
    path: str,
    raw: str,
    pjsk_type: int = 0,
    block: bool = False,
    download: bool = True,
) -> Optional[Image.Image]:
    """从共享目录读资源图；缺失就返回 None，由渲染器自行降级。"""
    file_path = _server_dir(pjsk_type) / path / raw
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
    """独立进程不下载资源，交给 bot 侧的资源管理器。"""
    return None


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
