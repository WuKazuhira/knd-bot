# Wrapper Dockerfile for third_party/allium-deck/server with repo-root build context.
# Avoids patching the upstream submodule in-place when benches/ is omitted or extra targets exist.

ARG RUST_VERSION=1.94
ARG LIBC=gnu
ARG RUNTIME=distroless

FROM rust:${RUST_VERSION}-alpine AS build-musl
RUN apk add --no-cache musl-dev build-base

FROM rust:${RUST_VERSION}-slim AS build-gnu
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

FROM build-${LIBC} AS build
ARG ALLOC=""
ENV CARGO_TERM_COLOR=always
WORKDIR /build

COPY third_party/allium-deck/Cargo.toml third_party/allium-deck/Cargo.lock ./
COPY third_party/allium-deck/src ./src
COPY third_party/allium-deck/data ./data
COPY third_party/allium-deck/server/Cargo.toml third_party/allium-deck/server/Cargo.lock ./server/
COPY third_party/allium-deck/server/src ./server/src
COPY third_party/allium-deck/server/openapi.json ./server/

RUN --mount=type=cache,target=/usr/local/cargo/registry \
    --mount=type=cache,target=/build/server/target \
    cd server \
    && cargo build --release --locked --bin allium-deck-server ${ALLOC:+--features ${ALLOC}} \
    && cp target/release/allium-deck-server /allium-deck-server

FROM scratch AS runtime-scratch
FROM gcr.io/distroless/cc-debian13 AS runtime-distroless

FROM runtime-${RUNTIME} AS runtime
COPY --from=build /allium-deck-server /allium-deck-server

USER 65532:65532
EXPOSE 8080

ENTRYPOINT ["/allium-deck-server"]
CMD ["--bind", "0.0.0.0:8080"]
