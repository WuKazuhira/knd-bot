import json
import os
import time
from hashlib import md5

import uvicorn
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from http_contract import to_http_decks, translate_options_for_local
from init_data import _download_music_metas, _load_masterdata
from sekai_deck_recommend_cpp import (
    DeckRecommendCardConfig,
    DeckRecommendOptions,
    DeckRecommendResult,
    DeckRecommendSingleCardConfig,
    DeckRecommendUserData,
    SekaiDeckRecommend,
)
from worker import *

from config import *
from utils import *

try:
    import uvloop
    asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
except ImportError:
    pass


def update_data(
    region: str,
    masterdata_version: str,
    masterdata: dict[str, bytes] | None,
    musicmetas_update_ts: int,
    musicmetas: bytes | None,
):
    db = load_json(DB_PATH, default={})

    missing_data = set()

    current_masterdata_version = db.get('masterdata_version', {}).get(region)
    if current_masterdata_version != masterdata_version:
        if not masterdata:
            missing_data.add('masterdata')
        else:
            local_md_dir = pjoin(DATA_DIR, region)
            for name, md in masterdata.items():
                write_file(pjoin(local_md_dir, name), md)
            db.setdefault('masterdata_version', {})[region] = masterdata_version
            log(f"更新 {region} MasterData {current_masterdata_version} -> {masterdata_version}")

    current_musicmetas_update_ts = db.get('musicmetas_update_ts', {}).get(region)
    if current_musicmetas_update_ts != musicmetas_update_ts:
        if not musicmetas:
            missing_data.add('musicmetas')
        else:
            local_mm_path = pjoin(DATA_DIR, f'musicmetas_{region}.json')
            write_file(local_mm_path, musicmetas)
            db.setdefault('musicmetas_update_ts', {})[region] = musicmetas_update_ts
            current_ts_text = datetime.fromtimestamp(current_musicmetas_update_ts).strftime('%Y-%m-%d %H:%M:%S') if current_musicmetas_update_ts else 'None'
            local_ts_text = datetime.fromtimestamp(musicmetas_update_ts).strftime('%Y-%m-%d %H:%M:%S')
            log(f"更新 {region} MusicMetas {current_ts_text} -> {local_ts_text}")

    dump_json(db, DB_PATH)
    if missing_data:
        log(f"{region} 检测到数据更新不完整，缺少：{', '.join(missing_data)}")
        raise HTTPException(status_code=426, detail={
            'missing_data': list(missing_data),
            "message": "缺少必要的数据，请上传完整数据",
        })


async def extract_decompressed_payload(request: Request) -> list[bytes]:
    payload = decompress_zstd(await request.body())
    segments = []
    index = 0
    while index < len(payload):
        if index + 4 > len(payload):
            raise HTTPException(status_code=400, detail="数据格式错误")
        segment_size = int.from_bytes(payload[index:index+4], 'big')
        index += 4
        if index + segment_size > len(payload):
            raise HTTPException(status_code=400, detail="数据格式错误")
        segment = payload[index:index+segment_size]
        segments.append(segment)
        index += segment_size
    return segments


# =========================== AUTO DATA SYNC =========================== #

DECK_DATA_REGIONS = [
    region.strip()
    for region in os.getenv("DECK_RECOMMENDER_REGIONS", "jp,tw,cn").split(",")
    if region.strip()
]
DECK_DATA_REFRESH_INTERVAL = int(os.getenv("DECK_RECOMMENDER_REFRESH_INTERVAL", str(6 * 60 * 60)))


def _masterdata_version(masterdata: dict[str, bytes]) -> str:
    version_src = b"".join(name.encode("utf-8") + masterdata[name] for name in sorted(masterdata))
    return md5(version_src).hexdigest()[:12]


def sync_all_backend_data():
    musicmetas = _download_music_metas()
    musicmetas_update_ts = int(time.time())
    for region in DECK_DATA_REGIONS:
        masterdata = _load_masterdata(region)
        update_data(
            region=region,
            masterdata_version=_masterdata_version(masterdata),
            masterdata=masterdata,
            musicmetas_update_ts=musicmetas_update_ts,
            musicmetas=musicmetas,
        )
        log(f"自动同步 {region} 组卡数据完成 masterdata={len(masterdata)} musicmetas={len(musicmetas)}")


async def deck_data_sync_loop():
    while True:
        try:
            await asyncio.to_thread(sync_all_backend_data)
        except Exception as e:
            error(f"自动同步组卡数据失败: {get_exc_desc(e)}")
        await asyncio.sleep(DECK_DATA_REFRESH_INTERVAL)


# =========================== API =========================== #

app = FastAPI()


@app.on_event("startup")
async def _start_deck_data_sync_loop():
    asyncio.create_task(deck_data_sync_loop())


def _workers_ready() -> bool:
    return (
        bool(WorkerContext.all_workers)
        and bool(WorkerContext.all_processes)
        and len(WorkerContext.all_workers) == len(WorkerContext.all_processes)
        and all(process.is_alive() for process in WorkerContext.all_processes.values())
    )


def _data_state() -> tuple[dict, dict]:
    db = load_json(DB_PATH, default={})
    if not isinstance(db, dict):
        return {}, {}
    versions = db.get("masterdata_version", {})
    metas = db.get("musicmetas_update_ts", {})
    return (
        versions if isinstance(versions, dict) else {},
        metas if isinstance(metas, dict) else {},
    )


def _region_ready(region: str) -> bool:
    versions, metas = _data_state()
    return bool(versions.get(region) and metas.get(region))


def _decode_http_user(value: object) -> bytes:
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=400, detail=f"user must contain JSON text: {exc}") from exc
    elif isinstance(value, dict):
        parsed = value
    else:
        raise HTTPException(status_code=400, detail="user must be a JSON object or JSON text")
    return json.dumps(parsed, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _http_region(payload: dict) -> str:
    region = payload.get("region")
    if region:
        return str(region)
    if DECK_DATA_REGIONS:
        return DECK_DATA_REGIONS[0]
    return "jp"


async def _run_v1_recommend(payload: dict) -> dict:
    if not _workers_ready():
        raise HTTPException(status_code=503, detail="组卡服务尚未初始化")
    user_bytes = _decode_http_user(payload.get("user"))
    params = payload.get("params") or {}
    if not isinstance(params, dict):
        raise HTTPException(status_code=400, detail="params must be an object")
    params = translate_options_for_local(params)
    region = _http_region(payload)
    if region not in DECK_DATA_REGIONS:
        raise HTTPException(status_code=404, detail=f"unknown region: {region}")
    if not _region_ready(region):
        raise HTTPException(status_code=503, detail=f"region {region} data is not ready")
    params["region"] = region

    started = time.monotonic()
    async with WorkerContext(task_timeout=max(5.0, float(params.get("timeout_ms", 120000)) / 1000 + 5)) as ctx:
        cached = await ctx.cache_userdata(user_bytes)
        if cached.get("status") != "success":
            raise HTTPException(status_code=500, detail=cached.get("message", "缓存用户数据失败"))
        result = await ctx.recommend(region, params, cached["userdata_hash"])
    total_ms = (time.monotonic() - started) * 1000

    if result.get("status") != "success":
        raise HTTPException(status_code=500, detail=result.get("message", "组卡失败"))
    result_data = result.get("result") if isinstance(result.get("result"), dict) else {}
    cost_ms = float(result.get("cost_time", 0)) * 1000
    return {
        "region": region,
        "decks": to_http_decks(result_data.get("decks")),
        "diagnostics": {},
        "timing": {
            "queueWaitMs": max(0.0, total_ms - cost_ms),
            "buildPoolMs": 0.0,
            "searchMs": cost_ms,
            "totalMs": total_ms,
        },
        "timedOut": False,
    }


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}


@app.get("/readyz")
async def readyz():
    if not _workers_ready():
        raise HTTPException(status_code=503, detail="组卡 worker 尚未启动")
    if not DECK_DATA_REGIONS or not all(_region_ready(region) for region in DECK_DATA_REGIONS):
        raise HTTPException(status_code=503, detail="组卡数据尚未初始化")
    return {"status": "ready"}


@app.get("/v1/regions")
async def v1_regions():
    versions, metas = _data_state()
    return {
        "regions": [
            {
                "region": region,
                "masterdataVersion": versions.get(region),
                "musicMetasUpdateTs": metas.get(region),
                "ready": bool(versions.get(region) and metas.get(region)),
            }
            for region in DECK_DATA_REGIONS
        ]
    }


async def _run_v1_challenge_all(payload: dict) -> dict:
    """兼容 PR #39 的 challenge-all；共享一次用户缓存，逐角色复用 worker。"""
    if not _workers_ready():
        raise HTTPException(status_code=503, detail="组卡服务尚未初始化")
    user_bytes = _decode_http_user(payload.get("user"))
    params = payload.get("params") or {}
    if not isinstance(params, dict):
        raise HTTPException(status_code=400, detail="params must be an object")
    params = translate_options_for_local(params)
    region = _http_region(payload)
    if region not in DECK_DATA_REGIONS:
        raise HTTPException(status_code=404, detail=f"unknown region: {region}")
    if not _region_ready(region):
        raise HTTPException(status_code=503, detail=f"region {region} data is not ready")
    params["region"] = region

    params["live_type"] = (
        params.get("live_type")
        if params.get("live_type") in {"challenge", "challenge_auto"}
        else "challenge"
    )
    params.pop("challenge_live_character_id", None)
    params.setdefault("music_id", 104)
    params.setdefault("music_diff", "master")
    params["limit"] = 1

    started = time.monotonic()
    characters: list[dict] = []
    total_search_ms = 0.0
    timeout_seconds = max(5.0, float(params.get("timeout_ms", 120000)) / 1000 + 5)
    async with WorkerContext(task_timeout=timeout_seconds) as ctx:
        cached = await ctx.cache_userdata(user_bytes)
        if cached.get("status") != "success":
            raise HTTPException(status_code=500, detail=cached.get("message", "缓存用户数据失败"))
        for character_id in range(1, 27):
            one = dict(params)
            one["challenge_live_character_id"] = character_id
            character_started = time.monotonic()
            result = await ctx.recommend(region, one, cached["userdata_hash"])
            search_ms = (time.monotonic() - character_started) * 1000
            total_search_ms += search_ms
            decks: list[dict] = []
            if result.get("status") == "success":
                result_data = result.get("result") if isinstance(result.get("result"), dict) else {}
                decks = to_http_decks(result_data.get("decks"))
            characters.append(
                {
                    "rank": None,
                    "characterId": character_id,
                    "candidateCount": 0,
                    "searchMs": search_ms,
                    "deck": decks[0] if decks else None,
                }
            )

    ranked = sorted(
        (item for item in characters if item.get("deck")),
        key=lambda item: item["deck"].get("targetValue", 0),
        reverse=True,
    )
    for rank, item in enumerate(ranked, 1):
        item["rank"] = rank
    total_ms = (time.monotonic() - started) * 1000
    return {
        "region": region,
        "characters": characters,
        "diagnostics": {},
        "timing": {
            "queueWaitMs": max(0.0, total_ms - total_search_ms),
            "buildPoolMs": 0.0,
            "searchMs": total_search_ms,
            "totalMs": total_ms,
        },
        "timedOut": False,
    }


def _v1_error(status_code: int, message: str) -> JSONResponse:
    code = {
        400: "invalid_request",
        404: "unknown_region",
        503: "overloaded",
        504: "queue_timeout",
    }.get(status_code, "internal")
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": message}},
    )


@app.post("/v1/recommend")
async def v1_recommend(request: Request):
    try:
        payload = await request.json()
        if not isinstance(payload, dict):
            raise HTTPException(status_code=400, detail="request body must be an object")
        return await _run_v1_recommend(payload)
    except HTTPException as exc:
        return _v1_error(exc.status_code, str(exc.detail))
    except (json.JSONDecodeError, ValueError) as exc:
        return _v1_error(400, str(exc))
    except Exception as exc:
        error("PR39 HTTP 组卡请求处理失败", get_exc_desc(exc))
        return _v1_error(500, get_exc_desc(exc))


@app.post("/v1/recommend/challenge-all")
async def v1_challenge_all(request: Request):
    try:
        payload = await request.json()
        if not isinstance(payload, dict):
            raise HTTPException(status_code=400, detail="request body must be an object")
        return await _run_v1_challenge_all(payload)
    except HTTPException as exc:
        return _v1_error(exc.status_code, str(exc.detail))
    except (json.JSONDecodeError, ValueError) as exc:
        return _v1_error(400, str(exc))
    except Exception as exc:
        error("PR39 HTTP challenge-all 请求处理失败", get_exc_desc(exc))
        return _v1_error(500, get_exc_desc(exc))


@app.post("/update_data")
async def _(request: Request):
    try:
        segments = await extract_decompressed_payload(request)

        data = loads_json(segments[0])
        region = data['region']
        masterdata_version = data['masterdata_version']
        musicmetas_update_ts = data['musicmetas_update_ts']

        masterdatas: dict[str, bytes] = {}
        musicmetas: bytes = None
        for i in range(1, len(segments), 2):
            key = segments[i].decode('utf-8')
            value = segments[i+1]
            if key == 'musicmetas':
                musicmetas = value
            else:
                masterdatas[key] = value

        update_data(region, masterdata_version, masterdatas, musicmetas_update_ts, musicmetas)

    except HTTPException as he:
        raise he
    except Exception as e:
        error("更新数据失败")
        raise HTTPException(
            status_code=500,
            detail=get_exc_desc(e),
        )

@app.post("/cache_userdata")
async def _(request: Request):
    try:
        segments = await extract_decompressed_payload(request)
        userdata_bytes = segments[0]

        t = datetime.now()
        all_result = await asyncio.gather(*[ctx.cache_userdata(userdata_bytes) for ctx in WorkerContext.workers()])
        elapsed = (datetime.now() - t).total_seconds()

        for result in all_result:
            if result['status'] != 'success':
                raise HTTPException(
                    status_code=500,
                    detail=result.get('message', '内部错误'),
                )

        userdata_hash = all_result[0]['userdata_hash']
        log(f"缓存用户数据 {userdata_hash} 成功，耗时 {elapsed:.3f} 秒")

        return {"userdata_hash": userdata_hash}

    except Exception as e:
        if isinstance(e, HTTPException):
            raise e
        error("缓存用户数据失败")
        raise HTTPException(
            status_code=500,
            detail=get_exc_desc(e),
        )

@app.post("/recommend")
async def _(request: Request):
    try:
        segments = await extract_decompressed_payload(request)

        data = loads_json(segments[0])
        region = data['region']
        batch_options = data['batch_options']
        userdata_hash = data['userdata_hash']

        async def do_recommend(options):
            start_time = datetime.now()
            async with WorkerContext() as ctx:
                result = await ctx.recommend(region, options, userdata_hash)

            if result['status'] != 'success':
                raise HTTPException(
                    status_code=500,
                    detail=result.get('message', '内部错误'),
                )

            total_time = (datetime.now() - start_time).total_seconds()
            wait_time = total_time - result['cost_time']

            return {
                "result": result['result'],
                "alg": options['algorithm'],
                "cost_time": result['cost_time'],
                "wait_time": wait_time,
            }

        return await asyncio.gather(*[do_recommend(options) for options in batch_options])

    except Exception as e:
        if isinstance(e, HTTPException):
            raise e
        error("组卡请求处理失败")
        raise HTTPException(
            status_code=500,
            detail=get_exc_desc(e),
        )


if __name__ == "__main__":
    WorkerContext.init_workers(WORKER_NUM)
    log(f"组卡服务初始化 worker_num={WORKER_NUM} data_dir={DATA_DIR}")

    uvicorn.run(
        "serve:app",
        host=HOST,
        port=PORT,
        log_level="warning",
        workers=None,
        timeout_keep_alive=60,
    )
