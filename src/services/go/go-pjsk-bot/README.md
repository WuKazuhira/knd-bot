# go-pjsk-bot

PJSK 模块业务逻辑的独立 Go 微服务。**只做 pjsk，一件事做好。**

## 定位与边界（务必遵守）

- ✅ **只处理 pjsk 指令的业务逻辑**：查榜(sk)、组卡(deck)、猜曲(guess)、
  mysekai、绑定、活动、卡面等。
- ✅ **出图一律委托 Python 的 pjsk-draw 微服务**（`internal/draw`，POST /render/{name}）。
  Go 侧只取数 + 组织 JSON payload，**绝不在 Go 里写任何绘图代码**。
- ✅ **数据复用现有 sidecar**：go-pjsk-helper（masterdata/ranking/assets/suite）、
  sekai-api（游戏 API）、deck-service（Rust 组卡引擎）。
- ✅ **与 Python 主进程通过命令所有权互斥**：`KND_GO_OWNED_COMMANDS` 命中的 pjsk
  指令由本服务接管，Python 侧 matcher 安静退场（`services/go_ownership.py`）。
- ❌ **不碰其他插件**（weather/roll/shop/petpet/llm…都留在 Python）。
- ❌ **不自建绘图层**（不重复 Go 分支 python-renderer 的错误）。
- ❌ **不做巨型单文件调度器**（不重复 Go 分支 6909 行 handler.go 的错误）。

## 结构

```
cmd/pjskbot/main.go     入口：连 OneBot WS、装配路由、启动
internal/
  onebot/   OneBot v11 正向 WS 接入（收 message 事件、发 action）
  router/   指令路由：命令起始符 + 别名 + cn/tw 服务器前缀解析
  config/   环境变量配置
  draw/     pjsk-draw 出图微服务 HTTP 客户端
  pjsk/     各指令业务逻辑（按模块分文件，禁止堆到一个文件）
```

## 配置（环境变量）

| 变量 | 说明 | 默认 |
| --- | --- | --- |
| `PJSKBOT_ONEBOT_WS_URL` | OneBot 正向 WS 地址 | `ws://127.0.0.1:3001` |
| `PJSKBOT_ONEBOT_TOKEN` | OneBot access_token | 空 |
| `PJSK_DRAW_SERVICE_URL` | pjsk-draw 出图服务 | `http://127.0.0.1:45560` |
| `PJSK_HELPER_URL` | go-pjsk-helper | `http://127.0.0.1:45558` |
| `SEKAI_API_URL` | sekai-api | `http://127.0.0.1:9999` |
| `DECK_SERVICE_URL` | deck-service | `http://127.0.0.1:45557` |
| `PJSK_DATA_DIR` | pjsk 数据目录（共享 volume） | `/app/data/pjsk` |

## 构建 / 测试

本机无 go，用 docker：

```bash
cd src/services/go/go-pjsk-bot
docker run --rm -v "$PWD":/w -w /w -e GOPROXY=https://goproxy.cn,direct -e GOSUMDB=off \
  golang:1.25-alpine sh -c 'go mod tidy && go build ./... && go vet ./... && go test ./...'
```

## 迁移进度

- [x] 骨架：onebot 接入 / router / draw 客户端 / config / 入口，编译+测试通过
- [x] 命令所有权：`KND_GO_OWNED_COMMANDS` 解析与路由过滤（router/ownership）
- [x] store：PostgreSQL 连接池 + pjsk_bind CRUD（与 Python 共享库表）
- [x] 业务模块 bind：绑定 / 解绑 / 给看 / 查时间（含 uid 校验、at 解析、隐私）
- [x] 业务模块 ycm：烧烤推车查询（抓取站点数据 → pjsk-draw 出图，验证 draw 闭环）
- [x] 基础设施续：masterdata(本地JSON读取+解包+索引+缓存) / gameapi(token+错误映射) /
      helper(suite/b30/ranking) / limiter(CD+防重入)，均带 go test
- [ ] 业务模块续：b30、gacha、rop、arrest、botcheck、rk、card、profile、
      diffrank、event、pjskinfo、mysekai、deck、guess、subscribe、sk
