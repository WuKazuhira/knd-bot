import os
import time
from hashlib import md5

import uvicorn
from fastapi import FastAPI, HTTPException, Request, Response
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
            local_md_dir = pjoin(DATA_DIR, 'masterdata', region)
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
