"""Allium 本地组卡后端适配器。

allium-sekai-deck 是 Python 内嵌 Rust 引擎，不走 HTTP。这里把 kndbot 当前
HTTP deck-service 风格的 options/userdata 转换为 allium 的 LunaBot facade 对象。
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Dict, List, Tuple

from services.log import logger

from .._config import SERVER_MAP, data_path
from .._paths import DECKREC_PATH
from .._utils import async_load_master_data

_ENGINE_CACHE: Dict[str, object] = {}
_ENGINE_LOCKS: Dict[str, asyncio.Lock] = {}
_ALLIUM_AVAILABLE: bool | None = None
_ALLIUM_IMPORT_ERROR: Exception | None = None

UNSUPPORTED_ALLIUM_KEYS = {
    # allium facade 使用 forcedLeaderCharacterId；snake_case 字段在下面显式转换。
    "forced_leader_character_id",
    # kndbot / HTTP deck-service 侧字段；allium 0.0.2 对应 world_bloom_event_turn。
    "world_bloom_chapter_no",
}

ALLIUM_REQUIRED_MASTERDATA = [
    "gameCharacterUnits.json",
    "events.json",
    "cardRarities.json",
    "cardEpisodes.json",
    "masterLessons.json",
    "skills.json",
    "areaItemLevels.json",
    "characterRanks.json",
    "cardMysekaiCanvasBonuses.json",
    "eventCards.json",
    "eventDeckBonuses.json",
    "eventCardBonusLimits.json",
    "eventHonorBonuses.json",
    "worldBloomDifferentAttributeBonuses.json",
    "eventSkillScoreUpLimits.json",
    "eventRarityBonusRates.json",
    "cards.json",
]


def is_allium_available() -> bool:
    """检测 allium 包是否可导入。"""
    global _ALLIUM_AVAILABLE, _ALLIUM_IMPORT_ERROR
    if _ALLIUM_AVAILABLE is not None:
        return _ALLIUM_AVAILABLE
    try:
        import sekai_deck_recommend_cpp  # noqa: F401
    except Exception as e:  # pragma: no cover - 依赖缺失时只做降级
        _ALLIUM_AVAILABLE = False
        _ALLIUM_IMPORT_ERROR = e
        return False
    _ALLIUM_AVAILABLE = True
    _ALLIUM_IMPORT_ERROR = None
    return True


def get_allium_unavailable_reason() -> str:
    if _ALLIUM_IMPORT_ERROR is None:
        return "allium-sekai-deck 未安装或不可用"
    return str(_ALLIUM_IMPORT_ERROR)


def _musicmetas_path(region: str) -> Path:
    return DECKREC_PATH / f"musicmetas_{region}.json"


def _masterdata_base_dir(region: str) -> Path:
    # allium 需要直接指向 region 目录，例如 data/pjsk/masterdata/cn。
    region_dir = data_path / region
    return region_dir if region_dir.exists() else data_path


# allium(allium_sekai_deck 0.0.3) 的 cards.json schema 期望 Nuverse 风格的
# 紧凑 cardParameters：
#   {"param1": [lv1power, lv2power, ...], "param2": [...], "param3": [...]}
# CN/TW 的原始数据正好是这个形状，所以一直能用；JP 给的是展开的对象数组
#   [{"id":..., "cardId":1, "cardLevel":1, "cardParameterType":"param1", "power":1065}, ...]
# allium 直接报 "invalid type: map, expected a sequence"（报错文案与实际方向相反，
# 实为遇到 sequence 但期望 map），于是 JP 组卡整体失效。
# 这里在喂给 allium 前把 JP 压成紧凑格式，写到独立副本目录，
# 不改动原始 masterdata，避免影响其他读这些文件的模块。
_NORMALIZED_DIRNAME = "_allium_normalized"


def _compact_card_parameters(cards: list) -> bool:
    """把展开的 cardParameters 数组压成 allium 需要的 map，返回是否改写过。"""
    changed = False
    for card in cards:
        if not isinstance(card, dict):
            continue
        params = card.get("cardParameters")
        if not isinstance(params, list) or not params:
            continue
        grouped: Dict[str, List[Tuple[int, int]]] = {}
        ok = True
        for item in params:
            if not isinstance(item, dict):
                ok = False
                break
            ptype = item.get("cardParameterType")
            if not ptype:
                ok = False
                break
            grouped.setdefault(ptype, []).append((item.get("cardLevel", 0), item.get("power", 0)))
        if not ok or not grouped:
            continue
        # 按等级排序后只保留 power，还原成 CN 那种按等级递增的数组
        card["cardParameters"] = {
            ptype: [power for _, power in sorted(values)]
            for ptype, values in grouped.items()
        }
        changed = True
    return changed


def _normalized_masterdata_dir(region: str, source_dir: Path) -> Path:
    """返回可供 allium 使用的 masterdata 目录。

    仅当 cards.json 是展开格式（JP 系）时才生成副本，其余情况直接用原目录。
    副本里除 cards.json 外都用软链接复用原文件，避免复制几十 MB。
    """
    cards_path = source_dir / "cards.json"
    if not cards_path.exists():
        return source_dir

    target_dir = DECKREC_PATH / _NORMALIZED_DIRNAME / region
    target_cards = target_dir / "cards.json"
    marker = target_dir / ".source_mtime"
    src_stat = cards_path.stat()
    signature = f"{src_stat.st_mtime_ns}:{src_stat.st_size}"

    if target_cards.exists():
        try:
            if marker.read_text().strip() == signature:
                return target_dir
        except OSError:
            pass

    try:
        with open(cards_path, "r", encoding="utf-8") as f:
            cards = json.load(f)
    except Exception as e:
        logger.warning(f"[deck] 读取 cards.json 失败，直接使用原目录: {e}")
        return source_dir

    if not isinstance(cards, list) or not _compact_card_parameters(cards):
        return source_dir  # 已是 allium 可读的紧凑格式

    try:
        target_dir.mkdir(parents=True, exist_ok=True)
        for item in source_dir.iterdir():
            if item.name == "cards.json":
                continue
            link = target_dir / item.name
            if link.is_symlink() or link.exists():
                continue
            link.symlink_to(item)
        tmp = target_dir / "cards.json.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cards, f, ensure_ascii=False)
        tmp.replace(target_cards)
        marker.write_text(signature)
        logger.info(f"[deck] 已为 allium 生成兼容 masterdata: region={region} dir={target_dir}")
        return target_dir
    except Exception as e:
        logger.warning(f"[deck] 生成兼容 masterdata 失败，回退原目录: {e}")
        return source_dir


def _pjsk_type_for_region(region: str) -> int:
    for pjsk_type, server_name in SERVER_MAP.items():
        if server_name == region:
            return pjsk_type
    return 0


async def _ensure_allium_masterdata(region: str) -> None:
    """缺少 allium 必需表时复用 kndbot 现有 masterdata 自动拉取逻辑。"""
    base_dir = _masterdata_base_dir(region)
    missing = [filename for filename in ALLIUM_REQUIRED_MASTERDATA if not (base_dir / filename).exists()]
    if not missing:
        return

    pjsk_type = _pjsk_type_for_region(region)
    logger.info(f"[deck] allium masterdata 缺少 {missing}，尝试自动拉取 region={region}")
    failed = []
    for filename in missing:
        try:
            await async_load_master_data(filename, pjsk_type)
        except Exception as e:
            failed.append(f"{filename}: {e}")
    still_missing = [filename for filename in ALLIUM_REQUIRED_MASTERDATA if not (base_dir / filename).exists()]
    if still_missing:
        detail = "; ".join(failed) if failed else ", ".join(still_missing)
        raise FileNotFoundError(f"allium masterdata 仍缺少 {still_missing}: {detail}")


async def _get_engine(region: str):
    """获取并初始化指定服务器的 allium engine。"""
    if not is_allium_available():
        raise RuntimeError(get_allium_unavailable_reason())

    if region in _ENGINE_CACHE:
        return _ENGINE_CACHE[region]

    lock = _ENGINE_LOCKS.setdefault(region, asyncio.Lock())
    async with lock:
        if region in _ENGINE_CACHE:
            return _ENGINE_CACHE[region]

        from sekai_deck_recommend_cpp import SekaiDeckRecommend

        await _ensure_allium_masterdata(region)
        masterdata_dir = _masterdata_base_dir(region)
        masterdata_dir = await asyncio.to_thread(_normalized_masterdata_dir, region, masterdata_dir)
        musicmetas = _musicmetas_path(region)
        if not masterdata_dir.exists():
            raise FileNotFoundError(f"allium masterdata 目录不存在: {masterdata_dir}")
        if not musicmetas.exists():
            raise FileNotFoundError(f"allium musicmetas 文件不存在: {musicmetas}")

        engine = SekaiDeckRecommend()
        start = time.monotonic()
        await asyncio.to_thread(engine.update_masterdata, str(masterdata_dir), region)
        await asyncio.to_thread(engine.update_musicmetas, str(musicmetas), region)
        elapsed = time.monotonic() - start
        logger.info(
            f"[deck] allium 初始化完成: region={region} "
            f"masterdata={masterdata_dir} musicmetas={musicmetas} cost={elapsed:.3f}s"
        )
        _ENGINE_CACHE[region] = engine
        return engine


def _normalize_options_for_allium(options: dict, region: str) -> dict:
    """把 HTTP deck-service 风格 options 规范化为 allium facade 可接受格式。"""
    normalized = {
        key: value
        for key, value in options.items()
        if value is not None and key not in UNSUPPORTED_ALLIUM_KEYS
    }
    normalized["region"] = region

    # allium facade 使用 forcedLeaderCharacterId；旧 snake_case 需要显式转换。
    forced_leader = options.get("forced_leader_character_id")
    if forced_leader is not None:
        normalized["forcedLeaderCharacterId"] = forced_leader

    # allium 0.0.2 使用 world_bloom_event_turn 表示 WL 章节序号。
    world_bloom_chapter_no = options.get("world_bloom_chapter_no")
    if world_bloom_chapter_no is not None:
        normalized["world_bloom_event_turn"] = world_bloom_chapter_no

    # allium 目前只支持 5 人卡组；None 表示默认 5。
    member = normalized.get("member")
    if member not in (None, 5):
        raise ValueError(f"allium 仅支持 5 人组卡，当前 member={member}")

    # facade 会把所有旧算法名归一为 dfs；这里保持原值用于校验，结果来源另标 allium。
    normalized.setdefault("algorithm", "dfs")
    normalized.setdefault("target", "score")
    normalized.setdefault("live_type", "multi")
    normalized.setdefault("music_diff", "master")
    return normalized


def _result_to_decks(result) -> List[dict]:
    data = result.to_dict() if hasattr(result, "to_dict") else result
    if isinstance(data, dict):
        decks = data.get("decks") or []
    else:
        decks = []
    return decks if isinstance(decks, list) else []


async def recommend_with_allium(region: str, options: dict, userdata_bytes: bytes) -> Tuple[List[dict], float]:
    """执行 allium 本地组卡，返回 (decks, elapsed_seconds)。"""
    engine = await _get_engine(region)

    from sekai_deck_recommend_cpp import DeckRecommendOptions, DeckRecommendUserData

    normalized = _normalize_options_for_allium(options, region)

    def _run():
        user_data = DeckRecommendUserData()
        user_data.load_from_bytes(userdata_bytes)
        allium_options = DeckRecommendOptions.from_dict(normalized)
        allium_options.user_data = user_data
        result = engine.recommend(allium_options)
        return _result_to_decks(result)

    start = time.monotonic()
    decks = await asyncio.to_thread(_run)
    elapsed = time.monotonic() - start
    return decks, elapsed
