"""调用进程内 allium 引擎执行组卡推荐。"""

from __future__ import annotations

import asyncio
from typing import Dict, List, Tuple

from services.log import logger

from ._allium_backend import get_allium_unavailable_reason, is_allium_available, recommend_with_allium
from ._backend_state import active_backends


async def do_recommend(
    server_urls: List[str],
    server_weights: List[int],
    region: str,
    options_list: List[dict],
    userdata_bytes: bytes,
    default_algs: List[str],
) -> List[Tuple[List[dict], List[str], Dict[str, float], Dict[str, float]]]:
    """执行组卡推荐，兼容旧调用签名但只使用 allium。

    `server_urls`、`server_weights`、`default_algs` 保留在签名中，避免上层参数
    编排发生无关变化；实际计算固定走进程内 allium，不会发起 HTTP 请求。
    """
    del server_urls, server_weights, default_algs

    if "allium" not in active_backends():
        raise RuntimeError("组卡后端已固定为 allium")
    if not is_allium_available():
        raise RuntimeError(f"allium 后端不可用: {get_allium_unavailable_reason()}")

    ret = []
    for index, base_options in enumerate(options_list):
        all_decks: List[dict] = []
        deck_algs: Dict[str, str] = {}
        cost_times: Dict[str, float] = {}
        wait_times: Dict[str, float] = {}
        batch_errors = []

        def _add_decks(decks: List[dict], source: str):
            for deck in decks:
                cards = deck.get("cards") or []
                first_card_id = cards[0].get("card_id", 0) if cards else 0
                deck_key = f"{deck.get('score', 0)}_{deck.get('total_power', 0)}_{first_card_id}"
                if deck_key not in deck_algs:
                    deck_algs[deck_key] = source
                    all_decks.append(deck)
                elif source not in deck_algs[deck_key].split("+"):
                    deck_algs[deck_key] += "+" + source

        options = dict(base_options)
        if options.get("algorithm", "all") == "all":
            options["algorithm"] = "dfs"
        try:
            decks, elapsed = await recommend_with_allium(region, options, userdata_bytes)
            cost_times["allium"] = elapsed
            wait_times["allium"] = 0.0
            logger.info(f"[deck] allium 组卡成功: index={index} decks={len(decks)} cost={elapsed:.3f}s")
            _add_decks(decks, "allium")
        except Exception as exc:
            err_text = str(exc)
            batch_errors.append(f"allium: {err_text}")
            logger.warning(f"[deck] allium 组卡失败: {err_text}")

        if not all_decks:
            raise Exception("组卡服务未返回可用结果: " + " | ".join(batch_errors))

        all_decks.sort(key=lambda d: d.get("score", 0), reverse=True)
        src_algs = []
        for deck in all_decks:
            cards = deck.get("cards") or []
            first_card_id = cards[0].get("card_id", 0) if cards else 0
            deck_key = f"{deck.get('score', 0)}_{deck.get('total_power', 0)}_{first_card_id}"
            src_algs.append(deck_algs.get(deck_key, ""))

        ret.append((all_decks, src_algs, cost_times, wait_times))

    return ret
