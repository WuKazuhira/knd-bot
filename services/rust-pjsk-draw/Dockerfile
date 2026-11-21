ARG RUST_VERSION=1.95

FROM rust:${RUST_VERSION}-slim-bookworm AS builder

RUN --mount=type=cache,id=kndbot-pjsk-draw-apt-cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,id=kndbot-pjsk-draw-apt-lists,target=/var/lib/apt/lists,sharing=locked \
    rm -f /etc/apt/apt.conf.d/docker-clean \
    && if [ -z "${http_proxy:-}" ] && [ -n "${HTTP_PROXY:-}" ]; then export http_proxy="$HTTP_PROXY"; fi \
    && if [ -z "${https_proxy:-}" ] && [ -n "${HTTPS_PROXY:-}" ]; then export https_proxy="$HTTPS_PROXY"; fi \
    && apt-get update -o Acquire::Retries=5 \
    && apt-get install -y --no-install-recommends -o Acquire::Retries=5 build-essential

WORKDIR /src
ENV CARGO_TERM_COLOR=always

COPY Cargo.toml Cargo.lock ./
COPY src ./src

RUN --mount=type=cache,id=kndbot-rust-cargo-registry,target=/usr/local/cargo/registry,sharing=locked \
    --mount=type=cache,id=kndbot-rust-cargo-git,target=/usr/local/cargo/git,sharing=locked \
    --mount=type=cache,id=kndbot-rust-pjsk-draw-target,target=/src/target,sharing=locked \
    cargo build --release --locked \
    && mkdir -p /out \
    && strip -s target/release/pjsk-draw-server -o /out/pjsk-draw-server

FROM debian:bookworm-slim AS runtime

RUN --mount=type=cache,id=kndbot-pjsk-draw-apt-cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,id=kndbot-pjsk-draw-apt-lists,target=/var/lib/apt/lists,sharing=locked \
    rm -f /etc/apt/apt.conf.d/docker-clean \
    && if [ -z "${http_proxy:-}" ] && [ -n "${HTTP_PROXY:-}" ]; then export http_proxy="$HTTP_PROXY"; fi \
    && if [ -z "${https_proxy:-}" ] && [ -n "${HTTPS_PROXY:-}" ]; then export https_proxy="$HTTPS_PROXY"; fi \
    && apt-get update -o Acquire::Retries=5 \
    && apt-get install -y --no-install-recommends -o Acquire::Retries=5 \
        ca-certificates \
        tzdata \
        curl \
        fonts-dejavu-core

ENV TZ=Asia/Shanghai
WORKDIR /app

COPY --from=builder /out/pjsk-draw-server /usr/local/bin/pjsk-draw-server

EXPOSE 45560
ENTRYPOINT ["/usr/local/bin/pjsk-draw-server"]
