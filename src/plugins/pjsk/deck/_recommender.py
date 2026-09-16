"""组卡后端编排：进程内 allium、PR #39 HTTP 及兼容的双后端模式。"""

from __future__ import annotations

import asyncio
from typing import Dict, List, Tuple

from services.log import logger

from .._config import DECK_RECOMMEND_SERVERS
from ._allium_backend import get_allium_unavailable_reason, is_allium_available, recommend_with_allium
from ._allium_http import (
    recommend_challenge_all_with_allium_http,
    recommend_with_allium_http,
)
from ._backend_state import active_backends


def _configured_servers() -> tuple[list[str], list[int]]:
    urls = []
    weights = []
    for item in DECK_RECOMMEND_SERVERS:
        if isinstance(item, dict):
            url = str(item.get("url") or "").strip()
            if url:
                urls.append(url)
                try:
                    weights.append(int(item.get("weight", 1)))
                except (TypeError, ValueError):
                    weights.append(1)
        elif str(item).strip():
            urls.append(str(item).strip())
            weights.append(1)
    return urls, weights


def _card_id(card: dict) -> int:
    try:
        return int(card.get("card_id", card.get("cardId", 0)) or 0)
    except (TypeError, ValueError):
        return 0


async def do_recommend(
    server_urls: List[str],
    server_weights: List[int],
    region: str,
    options_list: List[dict],
    userdata_bytes: bytes,
    default_algs: List[str],
) -> List[Tuple[List[dict], List[str], Dict[str, float], Dict[str, float]]]:
    """执行组卡推荐，保持旧返回结构并支持 PR #39 HTTP 服务。"""
    if not server_urls:
        server_urls, configured_weights = _configured_servers()
        if not server_weights:
            server_weights = configured_weights

    enabled_backends = [backend for backend in active_backends() if backend in {"http", "allium"}]
    if not enabled_backends:
        enabled_backends = ["allium"]
    use_http = "http" in enabled_backends
    use_allium = "allium" in enabled_backends

    if use_http and not server_urls and not use_allium:
        raise RuntimeError("未配置可用的 Allium HTTP 组卡服务")
    if use_http and not server_urls:
        logger.warning("[deck] HTTP 后端已启用但未配置服务地址，将继续使用进程内 allium")

    # PR #39 的 challenge-all 一次构建候选池并返回 26 个角色结果；HTTP-only
    # 模式优先使用该端点。both 模式仍按旧的逐角色编排，便于与本地结果逐项去重。
    challenge_all_batch = (
        use_http
        and not use_allium
        and len(options_list) > 1
        and all(
            isinstance(item, dict)
            and item.get("live_type") in {"challenge", "challenge_auto"}
            and item.get("challenge_live_character_id")
            for item in options_list
        )
    )
    if challenge_all_batch:
        base_options = dict(options_list[0])
        base_options.pop("challenge_live_character_id", None)
        try:
            decks, elapsed, used_url = await recommend_challenge_all_with_allium_http(
                server_urls,
                server_weights,
                region,
                base_options,
                userdata_bytes,
            )
            logger.info(
                f"[deck] Allium HTTP challenge-all 成功: url={used_url} "
                f"decks={len(decks)} cost={elapsed:.3f}s"
            )
            return [(decks, ["http"] * len(decks), {"http": elapsed}, {"http": 0.0})]
        except Exception as exc:
            logger.warning(f"[deck] challenge-all 端点不可用，回退逐角色请求: {exc}")

    # PR #39 自带 Top-K 搜索；default_algs 仅为旧调用方保留，不会改变 v1 请求。
    del default_algs
    ret = []
    for index, base_options in enumerate(options_list):
        all_decks: List[dict] = []
        deck_algs: Dict[str, str] = {}
        cost_times: Dict[str, float] = {}
        wait_times: Dict[str, float] = {}
        batch_errors: list[str] = []

        def _add_decks(decks: List[dict], source: str):
            for deck in decks:
                if not isinstance(deck, dict):
                    continue
                cards = deck.get("cards") or []
                first_card_id = _card_id(cards[0]) if cards and isinstance(cards[0], dict) else 0
                card_ids = tuple(
                    _card_id(card) for card in cards if isinstance(card, dict)
                )
                deck_key = (
                    deck.get("score", 0),
                    deck.get("total_power", 0),
                    card_ids,
                    first_card_id,
                )
                if deck_key not in deck_algs:
                    deck_algs[deck_key] = source
                    all_decks.append(deck)
                elif source not in deck_algs[deck_key].split("+"):
                    deck_algs[deck_key] += "+" + source

        async def _run_local() -> tuple[List[dict], float]:
            if not is_allium_available():
                raise RuntimeError(get_allium_unavailable_reason())
            options = dict(base_options)
            if options.get("algorithm", "all") == "all":
                options["algorithm"] = "dfs"
            return await recommend_with_allium(region, options, userdata_bytes)

        async def _run_http() -> tuple[List[dict], float, str]:
            return await recommend_with_allium_http(
                server_urls,
                server_weights,
                region,
                dict(base_options),
                userdata_bytes,
            )

        jobs: list[tuple[str, asyncio.Future | asyncio.Task]] = []
        if use_allium:
            jobs.append(("allium", asyncio.create_task(_run_local())))
        if use_http and server_urls:
            jobs.append(("http", asyncio.create_task(_run_http())))

        outcomes = await asyncio.gather(*(job for _, job in jobs), return_exceptions=True)
        for (source, _), outcome in zip(jobs, outcomes):
            if isinstance(outcome, BaseException):
                err_text = str(outcome)
                batch_errors.append(f"{source}: {err_text}")
                logger.warning(f"[deck] {source} 组卡失败: {err_text}")
                continue
            if source == "allium":
                decks, elapsed = outcome
                cost_times["allium"] = elapsed
                wait_times["allium"] = 0.0
                logger.info(
                    f"[deck] allium 组卡成功: index={index} "
                    f"decks={len(decks)} cost={elapsed:.3f}s"
                )
            else:
                decks, elapsed, used_url = outcome
                cost_times["http"] = elapsed
                wait_times["http"] = 0.0
                logger.info(
                    f"[deck] Allium HTTP 组卡成功: index={index} url={used_url} "
                    f"decks={len(decks)} cost={elapsed:.3f}s"
                )
            _add_decks(decks, source)

        if not all_decks:
            raise RuntimeError("组卡服务未返回可用结果: " + " | ".join(batch_errors))

        all_decks.sort(key=lambda deck: deck.get("score", 0), reverse=True)
        src_algs = []
        for deck in all_decks:
            cards = deck.get("cards") or []
            card_ids = tuple(_card_id(card) for card in cards if isinstance(card, dict))
            first_card_id = _card_id(cards[0]) if cards and isinstance(cards[0], dict) else 0
            deck_key = (
                deck.get("score", 0),
                deck.get("total_power", 0),
                card_ids,
                first_card_id,
            )
            src_algs.append(deck_algs.get(deck_key, ""))

        ret.append((all_decks, src_algs, cost_times, wait_times))

    return ret
