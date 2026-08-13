"""SK 主榜线 API 的选择、解析与抓取。"""

from __future__ import annotations

from typing import Any, Awaitable, Callable, Dict, List, Literal

from services.log import logger

from .._gameapi import GameApiConfig, request_gameapi
from .._sk_sql import Ranking

SkApiMode = Literal["new", "old"]
RequestJson = Callable[..., Awaitable[Any]]


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
    """按排名去重；较早传入的数据优先，避免旧 API 的 T100 重复。"""
    merged: Dict[int, Ranking] = {}
    for group in groups:
        for ranking in group:
            merged.setdefault(ranking.rank, ranking)
    return [merged[rank] for rank in sorted(merged)]


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
        return rankings_from_items(data.get("rankings", []) if isinstance(data, dict) else [])

    top100: List[Ranking] = []
    borders: List[Ranking] = []
    configured = False

    if config.ranking_top100_api_url:
        configured = True
        url = config.ranking_top100_api_url.format(event_id=event_id)
        try:
            data = await request_json(url, "GET", "json")
            top100 = rankings_from_items(data.get("rankings", []) if isinstance(data, dict) else [])
        except Exception as exc:
            logger.warning(f"[SK API] {config.server_name} 旧 API 前百请求失败：{exc}")

    if config.ranking_border_api_url:
        configured = True
        url = config.ranking_border_api_url.format(event_id=event_id)
        try:
            data = await request_json(url, "GET", "json")
            borders = rankings_from_items(data.get("borderRankings", []) if isinstance(data, dict) else [])
        except Exception as exc:
            logger.warning(f"[SK API] {config.server_name} 旧 API 档线请求失败：{exc}")

    if not configured:
        logger.warning(f"[SK API] 服务器 {config.server_name} 未配置旧 API")
    return merge_rankings(top100, borders)
