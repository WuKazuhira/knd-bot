FROM golang:1.23-alpine AS builder
WORKDIR /src
COPY go.mod ./
COPY go.sum* ./
ENV GOPROXY=https://goproxy.cn,direct
RUN --mount=type=cache,id=kndbot-go-pjsk-helper-mod,target=/go/pkg/mod,sharing=locked \
    go mod download || true
COPY . .
RUN --mount=type=cache,id=kndbot-go-pjsk-helper-mod,target=/go/pkg/mod,sharing=locked \
    --mount=type=cache,id=kndbot-go-pjsk-helper-build,target=/root/.cache/go-build,sharing=locked \
    go mod tidy && CGO_ENABLED=0 go build -trimpath -ldflags='-s -w' -o /out/pjsk-helper .

FROM alpine:3.20
RUN apk add --no-cache ca-certificates tzdata
ENV TZ=Asia/Shanghai
COPY --from=builder /out/pjsk-helper /usr/local/bin/pjsk-helper
EXPOSE 8000
ENTRYPOINT ["/usr/local/bin/pjsk-helper"]
