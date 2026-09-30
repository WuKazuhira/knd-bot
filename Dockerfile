FROM python:3.14-slim


ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src \
    PIP_DEFAULT_TIMEOUT=120 \
    PIP_RETRIES=10 \
    PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1

WORKDIR /app

RUN --mount=type=cache,id=kndbot-apt-cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,id=kndbot-apt-lists,target=/var/lib/apt/lists,sharing=locked \
    python -c "from pathlib import Path; source=Path('/etc/apt/sources.list.d/debian.sources'); source.write_text(source.read_text().replace('http://deb.debian.org', 'https://mirrors.tuna.tsinghua.edu.cn'))" \
    && rm -f /etc/apt/apt.conf.d/docker-clean \
    && apt-get update -o Acquire::Retries=5 -o Acquire::https::Proxy::mirrors.tuna.tsinghua.edu.cn=DIRECT \
    && apt-get install -y --no-install-recommends -o Acquire::Retries=5 -o Acquire::https::Proxy::mirrors.tuna.tsinghua.edu.cn=DIRECT --fix-missing \
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
        fonts-wqy-zenhei

ARG PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple
ARG PIP_EXTRA_INDEX_URL=https://pypi.org/simple
ARG PIP_TRUSTED_HOST=mirrors.aliyun.com
ENV PIP_INDEX_URL=${PIP_INDEX_URL} \
    PIP_EXTRA_INDEX_URL=${PIP_EXTRA_INDEX_URL} \
    PIP_TRUSTED_HOST=${PIP_TRUSTED_HOST}

RUN --mount=type=cache,id=kndbot-pip-cache,target=/root/.cache/pip,sharing=locked \
    python -m pip install --prefer-binary --upgrade pip setuptools wheel

COPY requirements.txt pyproject.toml /tmp/kndbot-deps/

RUN --mount=type=cache,id=kndbot-pip-cache,target=/root/.cache/pip,sharing=locked \
    python -c "from pathlib import Path; src=Path('/tmp/kndbot-deps/requirements.txt'); dst=Path('/tmp/kndbot-deps/requirements.docker.txt'); dst.write_text(src.read_text().replace('msgpack==1.0.5', 'msgpack==1.1.2'))" \
    && python -m pip install --prefer-binary -r /tmp/kndbot-deps/requirements.docker.txt \
    && rm -rf /tmp/kndbot-deps

COPY . /app
COPY scripts/entrypoint.sh /usr/local/bin/kndbot-entrypoint.sh

# 宿主对 /app/data 的 bind mount 会遮住镜像内置文件，
# 因此把固定素材移到 /opt/kndbot-seed，entrypoint 只补齐缺失文件。
RUN mkdir -p /opt/kndbot-seed/pjsk \
    && if [ -d /app/data/pjsk/static ]; then mv /app/data/pjsk/static /opt/kndbot-seed/pjsk/static; fi \
    && if [ -d /app/data/resources ]; then mv /app/data/resources /opt/kndbot-seed/resources; fi \
    && chmod +x /usr/local/bin/kndbot-entrypoint.sh

EXPOSE 8081

ENTRYPOINT ["/usr/local/bin/kndbot-entrypoint.sh"]
