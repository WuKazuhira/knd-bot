"""绘图服务独立进程入口。

    python -m services.pjsk_draw.serve            # 或
    uvicorn services.pjsk_draw.serve:app --port 45560

对外接口：
    POST /render/{name}   JSON 载荷 -> image/png
    GET  /renderers       可用渲染任务名
    GET  /health

与 bot 共享 data/pjsk 目录，缺失资源通过 pjsk-helper 按需下载。
"""

from __future__ import annotations

import base64
import os

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse

from .local_data import install_local_context
from .primitives import clear_runtime_caches, guess_image_media_type
from .registry import dispatch, load_all_renderers, renderer_names

app = FastAPI(title="pjsk-draw", docs_url=None, redoc_url=None)


@app.on_event("startup")
async def _startup() -> None:
    install_local_context()
    load_all_renderers()


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "renderers": len(renderer_names())}


@app.get("/renderers")
async def list_renderers() -> dict:
    return {"renderers": renderer_names()}


@app.post("/cache/clear")
async def clear_cache() -> dict:
    await clear_runtime_caches()
    return {"status": "ok"}


@app.post("/render/{name}")
async def render_endpoint(name: str, request: Request) -> Response:
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="载荷必须是 JSON 对象")
    try:
        images, meta = await dispatch(name, payload)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"{type(exc).__name__}: {exc}") from exc
    if len(images) == 1 and not meta:
        data = images[0]
        return Response(content=data, media_type=guess_image_media_type(data))
    # 多图 / 带元信息的任务用 JSON 信封，客户端按顺序取用
    return JSONResponse({
        "images": [base64.b64encode(item).decode() for item in images],
        "meta": meta,
    })


def main() -> None:
    import uvicorn

    uvicorn.run(
        app,
        host=os.getenv("PJSK_DRAW_HOST", "0.0.0.0"),
        port=int(os.getenv("PJSK_DRAW_PORT", "45560")),
    )


if __name__ == "__main__":
    main()
