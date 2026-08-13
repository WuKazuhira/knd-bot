# syntax=docker/dockerfile:1.7

FROM python:3.14-slim

ARG PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple
ARG PIP_EXTRA_INDEX_URL=https://pypi.org/simple
ARG PIP_TRUSTED_HOST=mirrors.aliyun.com
ARG HTTP_PROXY=
ARG HTTPS_PROXY=
ARG ALL_PROXY=
ARG NO_PROXY=localhost,127.0.0.1

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src \
    PIP_NO_CACHE_DIR=1 \
    PIP_DEFAULT_TIMEOUT=120 \
    PIP_RETRIES=10 \
    PIP_INDEX_URL=${PIP_INDEX_URL} \
    PIP_EXTRA_INDEX_URL=${PIP_EXTRA_INDEX_URL} \
    PIP_TRUSTED_HOST=${PIP_TRUSTED_HOST} \
    PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1

WORKDIR /app

RUN env -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY -u http_proxy -u https_proxy -u all_proxy \
        apt-get update -o Acquire::Retries=5 \
    && env -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY -u http_proxy -u https_proxy -u all_proxy \
        apt-get install -y --no-install-recommends -o Acquire::Retries=5 --fix-missing \
        build-essential \
        ca-certificates \
        curl \
        ffmpeg \
        git \
        libffi-dev \
        libpq-dev \
        libcairo2 \
        libcairo2-dev \
        libfontconfig1 \
        libfreetype6 \
        libjpeg62-turbo \
        liblcms2-2 \
        libopenjp2-7 \
        libtiff6 \
        libwebp7 \
        fonts-freefont-ttf \
        fonts-ipafont-gothic \
        fonts-liberation \
        fonts-noto-cjk \
        fonts-noto-color-emoji \
        fonts-tlwg-loma-otf \
        fonts-unifont \
        fonts-wqy-zenhei \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt pyproject.toml /tmp/kndbot-deps/

RUN python -c "from pathlib import Path; src=Path('/tmp/kndbot-deps/requirements.txt'); dst=Path('/tmp/kndbot-deps/requirements.docker.txt'); dst.write_text(src.read_text().replace('msgpack==1.0.5', 'msgpack==1.1.2'))" \
    && HTTP_PROXY=${HTTP_PROXY} HTTPS_PROXY=${HTTPS_PROXY} ALL_PROXY=${ALL_PROXY} NO_PROXY=${NO_PROXY} \
        python -m pip install --prefer-binary --upgrade pip setuptools wheel \
    && HTTP_PROXY=${HTTP_PROXY} HTTPS_PROXY=${HTTPS_PROXY} ALL_PROXY=${ALL_PROXY} NO_PROXY=${NO_PROXY} \
        python -m pip install --prefer-binary -r /tmp/kndbot-deps/requirements.docker.txt \
    && rm -rf /root/.cache/pip /tmp/kndbot-deps

COPY . /app
COPY scripts/entrypoint.sh /usr/local/bin/kndbot-entrypoint.sh

# 宿主对 /app/data 的 bind mount 会遮住镜像内置文件，
# 因此把固定素材移到 /opt/kndbot-seed，entrypoint 只补齐缺失文件。
RUN mkdir -p /opt/kndbot-seed \
    && if [ -d /app/data/pjsk/masterdata ]; then mv /app/data/pjsk/masterdata /opt/kndbot-seed/masterdata; fi \
    && if [ -d /app/data/resources ]; then mv /app/data/resources /opt/kndbot-seed/resources; fi \
    && chmod +x /usr/local/bin/kndbot-entrypoint.sh

EXPOSE 8081

ENTRYPOINT ["/usr/local/bin/kndbot-entrypoint.sh"]
