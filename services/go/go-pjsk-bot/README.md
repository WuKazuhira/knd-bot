# go-pjsk-bot

`go-pjsk-bot` 是 KND-BOT 的 PJSK OneBot v11 服务，负责接收消息、解析命令、访问数据服务并发送文本或图片结果。

## 功能

- PJSK 活动、卡牌、歌曲、谱面、档案和 MySekai 查询
- 猜曲、抽卡、查榜、预测和订阅功能；组卡由 Python allium 插件负责
- `skme` 个人打歌曲线，从共享 SQLite 读取记录
- OneBot v11 正向 WebSocket 和反向 WebSocket 接入
- 命令别名、`cn`/`tw` 区服前缀、权限、冷却和防重入控制
- 与 Python 主进程共享 PostgreSQL、SQLite、主数据和缓存

完整命令列表见 [`COMMANDS.md`](./COMMANDS.md)。

## 运行结构

```text
OneBot / OneBotFilter
       ├──────────────► kndbot       非 PJSK + Python allium/上游 server 组卡
       │                   │
       │                   └─────────► pjsk-draw
       └──────────────► go-pjsk-bot  其它 Go PJSK 命令
                           ├────────── pjsk-draw      图片渲染
                           └────────── go-pjsk-helper 主数据、榜线、资源与预测
```

Go 服务负责已迁移命令的业务编排和消息收发；组卡由 Python deck 插件调用进程内 allium 或上游 `allium-deck-server`，图片渲染统一交给 `pjsk-draw`，主数据与后台同步交给 `go-pjsk-helper`。

## 配置

服务通过环境变量配置。常用变量如下：

| 变量 | 说明 | 默认值 |
| --- | --- | --- |
| `PJSKBOT_ONEBOT_MODE` | OneBot 接入模式：`forward` 或 `reverse` | `forward` |
| `PJSKBOT_STANDALONE` | `1` 时接管 Go 已注册的全部命令 | `0` |
| `PJSKBOT_ONEBOT_WS_URL` | 正向 WS 地址 | `ws://127.0.0.1:3001` |
| `PJSKBOT_ONEBOT_LISTEN_ADDR` | 反向 WS 监听地址 | `:3001` |
| `PJSKBOT_ONEBOT_PATH` | 反向 WS 路径 | `/onebot/v11/ws` |
| `PJSKBOT_ONEBOT_TOKEN` | OneBot access token，反向 WS 默认必填 | 空 |
| `PJSKBOT_ALLOW_UNAUTHENTICATED_ONEBOT` | 本地调试时设为 `1`，允许无 token 的反向 WS | `0` |
| `PJSKBOT_GROUP_MANAGER_PATH` | 共享群管理 JSON 文件 | `/app/data/manager/group_manager.json` |
| `PJSKBOT_LOG_MESSAGES` | 是否记录未命中普通消息的截断文本 | `0` |
| `PJSK_DRAW_SERVICE_URL` | `pjsk-draw` 地址 | `http://127.0.0.1:45560` |
| `PJSK_HELPER_URL` | `go-pjsk-helper` 地址 | `http://127.0.0.1:45558` |
| `SEKAI_REMOTE_ACCOUNT` | `skme` 默认账号 | 空 |
| `SEKAI_REMOTE_REGION` | `skme` 未指定账号时使用的区服 | `cn` |
| `DATABASE_URL` | 共享 PostgreSQL 连接串 | 空 |
| `PJSK_DATA_DIR` | PJSK 数据目录 | `/app/data/pjsk` |
| `PJSK_CONFIG_DIR` | PJSK 配置目录 | `/app/config` |
| `PJSK_STATIC_DIR` | PJSK 静态资源目录 | `/app/data/pjsk/static` |
| `PJSKBOT_SUPERUSERS` | 超级用户 QQ，逗号分隔 | 空 |

生产环境通常使用反向 WS 和 standalone 模式：

```dotenv
PJSKBOT_ONEBOT_MODE=reverse
PJSKBOT_STANDALONE=1
PJSKBOT_ONEBOT_LISTEN_ADDR=:3001
PJSKBOT_ONEBOT_PATH=/onebot/v11/ws
PJSKBOT_ONEBOT_TOKEN=<与 OneBot 客户端一致的 token>
```

反向 WS 接受 `Authorization: Bearer <token>` 或 `?access_token=<token>`。携带非空 `Origin` 的握手会被拒绝。普通用户请求会检查共享 PostgreSQL 的 `ban_info` 和群管理文件中的群黑名单与总开关。缺少群管理文件时，服务记录一次日志并继续处理请求。配置了 PostgreSQL 时，黑名单查询失败会暂时拦截普通用户请求。

`KND_GO_OWNED_COMMANDS` 可用于灰度部署或回滚。standalone 模式下该变量不会限制 Go 命令。

### 日志

默认日志会记录命中命令、规范命令名、区服、参数摘要、执行耗时、回复类型和 OneBot action；普通未命中消息只记录用户/群和未命中状态，不展开正文。

排查命令解析时可临时开启普通消息文本摘要：

```dotenv
PJSKBOT_LOG_MESSAGES=1
```

同时兼容 `true`、`yes` 和 `on`。日志会截断过长文本，并且不会输出 access token、图片 base64 或完整 action 参数。

## Docker Compose

从仓库根目录执行：

```bash
docker compose build go-pjsk-bot

docker compose up -d go-pjsk-bot
```

完整部署通常还需要同时运行 `postgres`、`pjsk-draw`、`pjsk-helper` 和 OneBotFilter。根目录的 `docker-compose.yml` 已配置这些服务之间的默认地址和共享目录。

## 本地开发

需要 Go 1.25 或兼容版本：

```bash
cd services/go/go-pjsk-bot
go mod download
go test ./...
go vet ./...
go build ./cmd/pjskbot
```

没有本机 Go 时，可以使用 Docker：

```bash
docker run --rm \
  -v "$PWD:/src" -w /src \
  -e GOPROXY=https://goproxy.cn,direct \
  -e GOSUMDB=off \
  golang:1.25-alpine \
  sh -c 'gofmt -d . && go test ./... && go build ./cmd/pjskbot'
```

## 目录结构

```text
cmd/pjskbot/main.go       服务入口和依赖装配
internal/onebot/          OneBot v11 WebSocket 客户端与服务端
internal/router/          命令、别名和区服前缀路由
internal/config/          环境变量配置
internal/pjsk/            PJSK 命令处理模块
internal/store/            PostgreSQL 共享数据访问
internal/skstore/          榜线 SQLite 查询
internal/remotelive/       skme 的 SQLite 记录查询
internal/subscription/     订阅调度与推送
internal/draw/             pjsk-draw HTTP 客户端
```

## 健康检查

反向模式启动后，服务提供：

```text
GET /healthz
```

返回 `ok` 表示 HTTP/WS 服务已启动。OneBot 连接是否成功还需要检查 OneBotFilter 或 OneBot 实现端的连接日志。
