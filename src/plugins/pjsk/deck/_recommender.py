"""与 Rust deck-service 通信的 JSON 客户端模块。"""

import asyncio
import json
from typing import Dict, List, Optional, Tuple

import aiohttp

from services.log import logger
from .._config import DECK_RECOMMEND_BACKENDS
from ._allium_backend import recommend_with_allium, is_allium_available, get_allium_unavailable_reason

_SHARED_SESSION: Optional[aiohttp.ClientSession] = None
_SHARED_SESSION_LOCK = asyncio.Lock()
_request_id = 0


async def _get_shared_session() -> aiohttp.ClientSession:
    global _SHARED_SESSION
    if _SHARED_SESSION is not None and not _SHARED_SESSION.closed:
        return _SHARED_SESSION

    async with _SHARED_SESSION_LOCK:
        if _SHARED_SESSION is None or _SHARED_SESSION.closed:
            timeout = aiohttp.ClientTimeout(total=120)
            connector = aiohttp.TCPConnector(limit=100, limit_per_host=20, ttl_dns_cache=300)
            _SHARED_SESSION = aiohttp.ClientSession(timeout=timeout, connector=connector)
        return _SHARED_SESSION


async def _post_json(
    url: str,
    payload: dict,
    timeout: float = 120,
    session: Optional[aiohttp.ClientSession] = None,
) -> dict:
    """向 Rust deck-service 发送 JSON POST 请求并返回 JSON 响应。"""
    session = session or await _get_shared_session()
    async with session.post(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        timeout=aiohttp.ClientTimeout(total=timeout),
    ) as resp:
        if resp.status != 200:
            msg = f"{resp.status}: "
            try:
                err_data = await resp.json()
                detail = err_data.get("error") or err_data.get("detail") or ""
                msg += str(detail)
                logger.error(f"[deck] 后端返回错误 {resp.status}: {detail}")
            except Exception:
                try:
                    text = await resp.text()
                    msg += text
                    logger.error(f"[deck] 后端返回错误 {resp.status}: {text}")
                except Exception:
                    pass
            raise Exception(msg)
        return await resp.json()


async def do_recommend(
    server_urls: List[str],
    server_weights: List[int],
    region: str,
    options_list: List[dict],
    userdata_bytes: bytes,
    default_algs: List[str],
) -> List[Tuple[List[dict], List[str], Dict[str, float], Dict[str, float]]]:
    """执行组卡推荐请求。

    直接调用 Rust deck-service 的 JSON `/recommend` 接口。
    为了兼容上层调用，仍然返回 (decks_list, src_algs, cost_times, wait_times)。
    """
    global _request_id
    _request_id += 1

    enabled_backends = [backend for backend in DECK_RECOMMEND_BACKENDS if backend in {"http", "allium"}]
    if not enabled_backends:
        enabled_backends = ["http"]
    use_http = "http" in enabled_backends
    use_allium = "allium" in enabled_backends

    if use_http:
        if not server_urls:
            raise Exception("未配置可用的组卡服务")
        if sum(server_weights or []) <= 0:
            raise Exception("未配置可用的组卡服务")

    session = await _get_shared_session() if use_http else None
    userdata_str = userdata_bytes.decode("utf-8")

    async def _post_single_recommend(url: str, options: dict) -> tuple[dict, float]:
        payload = dict(options)
        payload["region"] = region
        payload["user_data_str"] = userdata_str
        payload.setdefault("algorithm", "dfs")
        payload.setdefault("timeout_ms", 15000)

        start = asyncio.get_running_loop().time()
        logger.info(f"[deck] 发送 Rust deck-service JSON 组卡请求到 {url}, alg={payload.get('algorithm')}")
        result = await _post_json(url.rstrip("/") + "/recommend", payload, timeout=120, session=session)
        elapsed = asyncio.get_running_loop().time() - start
        return result, elapsed

    async def _try_urls(options: dict) -> tuple[dict, float, str]:
        errors = []
        for url in server_urls:
            try:
                result, elapsed = await _post_single_recommend(url, options)
                return result, elapsed, url
            except Exception as e:
                logger.warning(f"[deck] 组卡请求 {url} 失败: {e}")
                errors.append(f"{url}: {e}")
        raise Exception("请求所有可用的组卡服务失败:\n" + "\n".join(errors))

    ret = []
    for index, base_options in enumerate(options_list):
        requested_alg = base_options.get("algorithm", "all")
        algs = default_algs if requested_alg == "all" else [requested_alg]

        all_decks = []
        deck_algs = {}
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
                else:
                    existing = deck_algs[deck_key]
                    if source not in existing.split("+"):
                        deck_algs[deck_key] += "+" + source

        if use_allium:
            if not is_allium_available():
                reason = get_allium_unavailable_reason()
                batch_errors.append(f"allium: {reason}")
                logger.warning(f"[deck] allium 后端不可用: {reason}")
            else:
                options = dict(base_options)
                if options.get("algorithm") == "all":
                    options["algorithm"] = "dfs"
                try:
                    decks, elapsed = await recommend_with_allium(region, options, userdata_bytes)
                    cost_times["allium"] = elapsed
                    wait_times["allium"] = 0.0
                    logger.info(f"[deck] allium 组卡成功: index={index} decks={len(decks)} cost={elapsed:.3f}s")
                    _add_decks(decks, "allium")
                except Exception as e:
                    err_text = str(e)
                    batch_errors.append(f"allium: {err_text}")
                    logger.warning(f"[deck] allium 组卡失败: {err_text}")

        if use_http:
            # 多个算法并发请求。原先是串行 for 循环，而 deck-service 上 dfs 要 5~6s、
            # ga 只要 0~1s，串起来等于白等一个 ga 的时间。并发后总耗时取最慢的那个。
            async def _run_alg(resolved_alg: str):
                options = dict(base_options)
                options["algorithm"] = resolved_alg
                return await _try_urls(options)

            alg_results = await asyncio.gather(
                *(_run_alg(alg) for alg in algs), return_exceptions=True
            )
            # 仍按 algs 原顺序落账：_add_decks 的去重靠插入顺序决定归属算法，
            # 顺序一乱同一套卡组的 alg 标注就会跟着变。
            for resolved_alg, outcome in zip(algs, alg_results):
                if isinstance(outcome, BaseException):
                    err_text = str(outcome)
                    batch_errors.append(f"{resolved_alg}: {err_text}")
                    logger.warning(f"[deck] 组卡项失败: alg={resolved_alg} error={err_text}")
                    continue
                result, elapsed, used_url = outcome
                decks = result.get("decks", []) if isinstance(result, dict) else []
                cost_times[resolved_alg] = elapsed
                wait_times[resolved_alg] = 0.0
                logger.info(
                    f"[deck] Rust deck-service 组卡成功: index={index} "
                    f"alg={resolved_alg} url={used_url} decks={len(decks)}"
                )
                _add_decks(decks, resolved_alg)

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
