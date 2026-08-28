#!/usr/bin/env bash
set -Eeuo pipefail

cd /app
export PYTHONPATH="/app/src${PYTHONPATH:+:$PYTHONPATH}"

# 运行时可写目录（data 由宿主 bind mount 提供，缺失时自动创建）
mkdir -p \
    /app/data/log \
    /app/data/temp \
    /app/data/config \
    /app/data/what2eat \
    /app/data/pjsk/masterdata \
    /app/data/pjsk/assets \
    /app/data/pjsk/profile \
    /app/data/pjsk/deckrec \
    /app/data/pjsk/forecast \
    /app/data/pjsk/remote \
    /app/data/pjsk/database \
    /app/data/pjsk/temp \
    "${MEME_HOME:-/app/data/meme_generator}"

# data 的 bind mount 会遮住镜像内置的固定 PJSK 素材。
# 仅补齐缺失文件，绝不覆盖宿主已有数据。
MASTERDATA_SEED_DIR=${MASTERDATA_SEED_DIR:-/opt/kndbot-seed/masterdata}
if [[ -d "$MASTERDATA_SEED_DIR" ]]; then
    cp -a --update=none "$MASTERDATA_SEED_DIR"/. /app/data/pjsk/masterdata/
fi
RESOURCES_SEED_DIR=${RESOURCES_SEED_DIR:-/opt/kndbot-seed/resources}
if [[ -d "$RESOURCES_SEED_DIR" ]]; then
    mkdir -p /app/data/resources
    cp -a --update=none "$RESOURCES_SEED_DIR"/. /app/data/resources/
fi

# meme-generator 素材校验。已下载时是增量比对（实测约 5s）；
# 首次是全量下载（约 400MB / 20 分钟），所以放到后台，不挡 bot 启动。
if [[ "${MEME_CHECK_RESOURCES:-1}" == "1" ]]; then
    (
        if [[ -n "${MEME_DOWNLOAD_PROXY:-}" ]]; then
            export HTTP_PROXY="$MEME_DOWNLOAD_PROXY" HTTPS_PROXY="$MEME_DOWNLOAD_PROXY"
            export http_proxy="$MEME_DOWNLOAD_PROXY" https_proxy="$MEME_DOWNLOAD_PROXY"
        fi
        python -c 'import meme_generator; meme_generator.resources.check_resources()' \
            && echo "[entrypoint] meme-generator 素材就绪" \
            || echo "[entrypoint] meme-generator 素材校验失败，相关表情可能无法生成"
    ) &
fi

shutdown() {
    local code=$?
    if [[ -n "${BOT_PID:-}" ]] && kill -0 "$BOT_PID" 2>/dev/null; then
        kill "$BOT_PID" 2>/dev/null || true
    fi
    if [[ -n "${AUTOCHAT_PID:-}" ]] && kill -0 "$AUTOCHAT_PID" 2>/dev/null; then
        kill "$AUTOCHAT_PID" 2>/dev/null || true
    fi
    wait 2>/dev/null || true
    exit "$code"
}
trap shutdown EXIT INT TERM

python bot.py &
BOT_PID=$!

python src/services/autochat/serve.py &
AUTOCHAT_PID=$!

while true; do
    if ! kill -0 "$BOT_PID" 2>/dev/null; then
        wait "$BOT_PID"
        exit $?
    fi
    if ! kill -0 "$AUTOCHAT_PID" 2>/dev/null; then
        wait "$AUTOCHAT_PID"
        exit $?
    fi
    sleep 2
done
