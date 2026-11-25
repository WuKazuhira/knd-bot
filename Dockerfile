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

COPY requirements.txt /tmp/requirements.txt

RUN --mount=type=cache,id=kndbot-pip-cache,target=/root/.cache/pip,sharing=locked \
    python -m pip install --prefer-binary -r /tmp/requirements.txt \
    && rm -f /tmp/requirements.txt

COPY bot.py /app/
COPY src /app/src
COPY example_config /app/example_config
COPY scripts/docker/entrypoint.sh /usr/local/bin/kndbot-entrypoint.sh

# 把内置静态素材放到 /opt/kndbot-seed，启动时补齐到宿主机挂载的 /app/data
RUN --mount=type=bind,source=.,target=/tmp/ctx \
    mkdir -p /opt/kndbot-seed/pjsk \
    && if [ -d /tmp/ctx/data/pjsk/static ]; then cp -a /tmp/ctx/data/pjsk/static /opt/kndbot-seed/pjsk/static && rm -f /opt/kndbot-seed/pjsk/static/cn_msr_allowed_groups.json; fi \
    && if [ -d /tmp/ctx/data/resources ]; then cp -a /tmp/ctx/data/resources /opt/kndbot-seed/resources; fi \
    && chmod -R a+rX /opt/kndbot-seed \
    && chmod +x /usr/local/bin/kndbot-entrypoint.sh

EXPOSE 8081

ENTRYPOINT ["/usr/local/bin/kndbot-entrypoint.sh"]
