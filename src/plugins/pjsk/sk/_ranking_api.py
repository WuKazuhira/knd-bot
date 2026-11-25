from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Literal

from services.log import logger

from .._gameapi import GameApiConfig, request_gameapi
from .._sk_sql import Ranking

SkApiMode = Literal["new", "old"]
RequestJson = Callable[..., Awaitable[Any]]


@dataclass
class HarukiRankingSnapshot:
    main_rankings: List[Ranking] = field(default_factory=list)
    world_bloom_rankings: Dict[int, List[Ranking]] = field(default_factory=dict)
    configured: bool = False


def rankings_from_items(items: Any) -> List[Ranking]:
    if not isinstance(items, list):
        return []
    rankings: List[Ranking] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        try:
            rankings.append(Ranking.from_sk(item))
        except (KeyError, TypeError, ValueError):
            continue
    return rankings


def merge_rankings(*groups: List[Ranking]) -> List[Ranking]:
    merged: Dict[int, Ranking] = {}
    for group in groups:
        for ranking in group:
            merged.setdefault(ranking.rank, ranking)
    return [merged[rank] for rank in sorted(merged)]


def _payload(data: Any) -> Dict[str, Any]:
    if not isinstance(data, dict):
        return {}
    nested = data.get("data")
    if isinstance(nested, dict):
        return nested
    return data


def _world_bloom_groups(
    data: Any,
    group_keys: tuple[str, ...],
    ranking_keys: tuple[str, ...],
) -> Dict[int, List[Ranking]]:
    payload = _payload(data)
    result: Dict[int, List[Ranking]] = {}
    for group_key in group_keys:
        groups = payload.get(group_key)
        if not isinstance(groups, list):
            continue
        for group in groups:
            if not isinstance(group, dict):
                continue
            try:
                character_id = int(
                    group.get("gameCharacterId")
                    or group.get("game_character_id")
                    or 0
                )
            except (TypeError, ValueError):
                continue
            if not character_id:
                continue

            items: Any = []
            for ranking_key in ranking_keys:
                value = group.get(ranking_key)
                if isinstance(value, list):
                    items = value
                    break
            rankings = rankings_from_items(items)
            if rankings:
                result[character_id] = merge_rankings(
                    result.get(character_id, []),
                    rankings,
                )
    return result


def world_bloom_rankings_from_payloads(
    top100_data: Any,
    border_data: Any,
) -> Dict[int, List[Ranking]]:
    top100 = _world_bloom_groups(
        top100_data,
        (
            "userWorldBloomChapterRankings",
            "worldBloomChapterRankings",
            "groups",
        ),
        ("rankings", "ranking"),
    )
    borders = _world_bloom_groups(
        border_data,
        (
            "userWorldBloomChapterRankingBorders",
            "worldBloomChapterRankingBorders",
            "groups",
        ),
        ("borderRankings", "rankings", "ranking"),
    )

    result: Dict[int, List[Ranking]] = {}
    for character_id in set(top100) | set(borders):
        result[character_id] = merge_rankings(
            top100.get(character_id, []),
            borders.get(character_id, []),
        )
    return result


async def fetch_haruki_ranking_snapshot(
    pjsk_type: int,
    event_id: int,
    request_json: RequestJson = request_gameapi,
) -> HarukiRankingSnapshot:
    config = GameApiConfig(pjsk_type)
    top100_data: Any = {}
    border_data: Any = {}
    configured = False

    if config.ranking_top100_api_url:
        configured = True
        url = config.ranking_top100_api_url.format(event_id=event_id)
        try:
            top100_data = await request_json(url, "GET", "json")
        except Exception as exc:
            logger.warning(f"[SK API] {config.server_name} Haruki 前百请求失败：{exc}")

    if config.ranking_border_api_url:
        configured = True
        url = config.ranking_border_api_url.format(event_id=event_id)
        try:
            border_data = await request_json(url, "GET", "json")
        except Exception as exc:
            logger.warning(f"[SK API] {config.server_name} Haruki 档线请求失败：{exc}")

    if not configured:
        logger.warning(f"[SK API] 服务器 {config.server_name} 未配置 Haruki 榜线 API")

    top100_payload = _payload(top100_data)
    border_payload = _payload(border_data)
    main_rankings = merge_rankings(
        rankings_from_items(top100_payload.get("rankings", [])),
        rankings_from_items(border_payload.get("borderRankings", [])),
    )
    return HarukiRankingSnapshot(
        main_rankings=main_rankings,
        world_bloom_rankings=world_bloom_rankings_from_payloads(
            top100_data,
            border_data,
        ),
        configured=configured,
    )


async def fetch_main_rankings(
    pjsk_type: int,
    event_id: int,
    mode: SkApiMode,
    request_json: RequestJson = request_gameapi,
) -> List[Ranking]:
    config = GameApiConfig(pjsk_type)

    if mode == "new":
        url = config.ranking_top100_new_api_url
        if not url:
            logger.warning(f"[SK API] 服务器 {config.server_name} 未配置新 API")
            return []
        data = await request_json(url, "GET", "json")
        payload = _payload(data)
        return rankings_from_items(payload.get("rankings", []))

    snapshot = await fetch_haruki_ranking_snapshot(
        pjsk_type,
        event_id,
        request_json=request_json,
    )
    return snapshot.main_rankings
