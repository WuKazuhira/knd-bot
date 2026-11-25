#!/usr/bin/env bash
set -Eeuo pipefail

cd /app
export PYTHONPATH="/app/src${PYTHONPATH:+:$PYTHONPATH}"

# 初始化运行时目录
mkdir -p \
    /app/data/log \
    /app/data/temp \
    /app/data/config \
    /app/data/what2eat \
    /app/data/pjsk/static \
    /app/data/pjsk/ondemand/profile \
    /app/data/pjsk/ondemand/deckrec \
    /app/data/pjsk/ondemand/forecast \
    /app/data/pjsk/ondemand/database \
    /app/data/pjsk/ondemand/temp \
    /app/data/pjsk/ondemand/suite \
    "${MEME_HOME:-/app/data/meme_generator}"

# 从内置 seed 补齐缺失素材，不覆盖宿主机已有文件
STATIC_SEED_DIR=${STATIC_SEED_DIR:-/opt/kndbot-seed/pjsk/static}
if [[ -d "$STATIC_SEED_DIR" ]]; then
    cp -r --no-preserve=ownership --update=none "$STATIC_SEED_DIR"/. /app/data/pjsk/static/
fi
RESOURCES_SEED_DIR=${RESOURCES_SEED_DIR:-/opt/kndbot-seed/resources}
if [[ -d "$RESOURCES_SEED_DIR" ]]; then
    mkdir -p /app/data/resources
    cp -r --no-preserve=ownership --update=none "$RESOURCES_SEED_DIR"/. /app/data/resources/
fi

# 后台校验/下载 meme-generator 素材，不阻塞启动
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

exec python bot.py
