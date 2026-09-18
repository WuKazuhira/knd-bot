# Docker 部署

想直接把 Kndbot 跑起来，Docker 是最省心的办法。下面先说清楚每个容器是干什么的，再按步骤启动；遇到问题也不用把所有日志都翻一遍，后面有常见问题可以对着看。

## 这些容器是干什么的

| 容器 | 作用 |
| --- | --- |
| `kndbot` | 普通聊天、群管理、娱乐功能、autochat 和 Python 组卡 |
| `go-pjsk-bot` | PJSK 查询、查分、订阅、remote 等 Go 指令 |
| `pjsk-draw` | PJSK 图片生成，Go 和 Python 都从这里出图 |
| `go-pjsk-helper` | PJSK 主数据、资源、Suite、榜线和预测数据 |
| `sekai-api` | 游戏 API 和 remote 后端 |
| `postgres` | 数据库，数据放在 `./volumes/postgres` |
| `chromium` | 给 Bot 渲染网页和图片，`kndbot` 会和它共用网络 |

PJSK 的 Go 服务和 Python 主 Bot 会同时收到 OneBot 消息。组卡还是 Python 在处理，已经交给 Go 的 PJSK 指令则由 Go 回复，避免同一条消息回复两次。

## 开始部署

### 1. 准备配置

```bash
cp .env.example .env
cp -a example_config config
chmod 600 .env
```

`.env` 和 `config/` 是你自己的配置，不要把真实密码和 token 提交到 Git。至少需要填写：

- `POSTGRES_PASSWORD`：数据库密码；
- `SEKAI_API_JWT_SECRET`：sekai-api 的 JWT 密钥；
- `SUPERUSERS`、机器人账号和你需要使用的 API token；
- `config/pjsk/servers.yaml`、`config/pjsk/settings.yaml`；
- `config/sekai-api/config.yaml`。

字体、图片和部分 PJSK 固定资源比较大，不会完整放进 Git。准备好 Release 资源包后，把它解压到仓库根目录的 `data/` 里。

### 2. 先检查一遍

```bash
python3 scripts/preflight.py
```

这个检查会帮你看看 Docker、配置文件、submodule、端口和目录权限有没有明显问题。它只显示变量名，不会把密码打印出来。

如果要给脚本或监控使用 JSON：

```bash
python3 scripts/preflight.py --json
```

### 3. 构建并启动

默认使用进程内的 Allium 组卡：

```bash
docker compose build
docker compose up -d
```

启动后看状态：

```bash
docker compose ps
docker compose logs -f kndbot
```

第一次启动可能需要下载主数据和资源，服务短时间显示 `starting` 不用慌。

### 4. 看看是不是都正常

```bash
docker compose ps
```

先看各个容器是不是 `running`，再看健康状态。Allium 如果只准备了部分区服，直接看它的日志和 `/v1/regions` 返回内容即可：

```bash
curl -fsS http://127.0.0.1:45557/v1/regions
```

## PJSK Go 常驻模式

Docker 部署一般使用：

```dotenv
KNDBOT_PJSK_RUNTIME=go
PJSKBOT_STANDALONE=1
PJSKBOT_ONEBOT_MODE=reverse
```

`kndbot` 继续处理普通功能和 Python 组卡，`go-pjsk-bot` 处理已经迁过去的 PJSK 指令。OneBotFilter 需要同时连接 Python 的入口和 Go 的反向 WebSocket：

```text
Python：宿主机 KND_PORT，默认 18081
Go：127.0.0.1:3001/onebot/v11/ws
```

`KND_GO_OWNED_COMMANDS` 主要给切换和回滚用。standalone 模式下，Go 会接管自己注册的指令。

## 可选的 Allium HTTP 组卡

默认不用额外容器，进程内 Allium 就能组卡。想单独启动 HTTP 服务时，在 `.env` 里填写：

```dotenv
DECK_BACKENDS=http
DECK_SERVICE_URLS=http://allium-deck-server:45557
DECK_SERVICE_API=v1
COMPOSE_PROFILES=deck-http
```

然后执行：

```bash
git submodule update --init --recursive
docker compose --profile deck-http up -d --build allium-deck-server
```

检查它有没有起来：

```bash
curl -fsS http://127.0.0.1:45557/healthz
curl -fsS http://127.0.0.1:45557/readyz
curl -fsS http://127.0.0.1:45557/v1/regions
```

如果只更新了主数据：

```bash
docker compose --profile deck-http run --rm allium-deck-data-init
docker compose restart allium-deck-server
```

如果 Rust 基础镜像因为代理拉不下来，也可以在宿主机编译：

```bash
cargo build --release --locked --manifest-path third_party/allium-deck/server/Cargo.toml
docker build -f docker/allium-deck-server-runtime.Dockerfile \
  -t kndbot-allium-deck:local \
  third_party/allium-deck/server/target/release
docker compose --profile deck-http up -d --no-build allium-deck-server
```

## 目录别删错

- `config/`：本机配置，容器里挂载到 `/app/config`，只读；
- `data/`：图片、缓存、PJSK 数据和运行时资源，容器里挂载到 `/app/data`；
- `volumes/postgres/`：PostgreSQL 数据；
- `volumes/sekai-api/Data/`：sekai-api 账号和缓存；
- `data/pjsk/ondemand/`：PJSK 运行时数据，Allium 也会用到。

容器删掉没关系，宿主机上的 `data/`、`config/` 和 `volumes/` 还在。不要直接删除这些目录，除非你已经确定不需要里面的数据。

## 常用操作

```bash
# 查看状态
docker compose ps

# 查看日志
docker compose logs -f kndbot
docker compose logs --tail=200 go-pjsk-bot

# 只改了配置，重启服务
docker compose restart kndbot go-pjsk-bot pjsk-draw

# 改了源码，重新构建
docker compose build
docker compose up -d
```

对外端口由 `.env` 里的 `KND_PORT` 控制，默认是 `18081`。Go 的 `3001` 默认只绑定在 `127.0.0.1`，不要直接把它暴露到公网。

## 备份和恢复

仓库里有现成脚本：

```bash
scripts/backup.sh
scripts/restore.sh --archive /安全路径/kndbot-backup.tar.gz --force
```

备份包括数据库逻辑备份、`data/`、`config/`、`.env` 和校验清单，不包括 `volumes/postgres/`。备份文件里有密码和业务数据，别直接丢到公开网盘。

备份脚本还支持 `--help`，需要确认参数时直接运行：

```bash
scripts/backup.sh --help
scripts/restore.sh --help
```

## 常见问题

### 缺少 `.env`

```bash
cp .env.example .env
chmod 600 .env
```

至少填写 `POSTGRES_PASSWORD` 和 `SEKAI_API_JWT_SECRET`。

### Bot 没有连上

确认 OneBotFilter 指向：

```text
ws://宿主机地址:18081/onebot/v11/ws
```

然后看：

```bash
docker compose logs -f kndbot go-pjsk-bot
```

日志出现 `Bot ... connected`，并且 Go 的 `/readyz` 返回 200，基本就说明消息链路接通了。

### 图片出不来

先看两个服务：

```bash
docker compose ps
docker compose logs --tail=200 pjsk-draw go-pjsk-helper
```

首次同步主数据和资源可能会慢；如果一直失败，优先检查 `data/pjsk` 权限、代理和 `config/pjsk/servers.yaml`。

### sekai-api 起不来

确认 `config/sekai-api/config.yaml` 存在、`volumes/sekai-api/Data` 可写、`SEKAI_API_JWT_SECRET` 已填写，并看日志：

```bash
docker compose logs --tail=200 sekai-api
```
