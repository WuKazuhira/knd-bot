# syntax=docker/dockerfile:1.7
FROM golang:1.25-alpine AS builder
WORKDIR /src
COPY go.mod ./
COPY go.sum* ./
ENV GOPROXY=https://goproxy.cn,direct
ENV GOSUMDB=off
RUN go mod download || true
COPY . .
RUN go mod tidy && CGO_ENABLED=0 go build -trimpath -ldflags='-s -w' -o /out/pjsk-bot ./cmd/pjskbot

FROM alpine:3.20
RUN apk add --no-cache ca-certificates tzdata
ENV TZ=Asia/Shanghai
COPY --from=builder /out/pjsk-bot /usr/local/bin/pjsk-bot
ENTRYPOINT ["/usr/local/bin/pjsk-bot"]
