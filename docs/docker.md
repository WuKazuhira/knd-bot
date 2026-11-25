# Docker 部署

想直接把 Kndbot 跑起来，Docker 是最省心的办法。下面先说清楚每个容器是干什么的，再按步骤启动；遇到问题也不用把所有日志都翻一遍，后面有常见问题可以对着看。

## 这些容器是干什么的

| 容器 | 作用 |
| --- | --- |
| `kndbot` | 普通聊天、群管理、娱乐功能、autochat 和 Python 组卡 |
| `go-pjsk-bot` | PJSK 查询、查分、订阅和个人历史曲线 |
| `pjsk-draw` | PJSK 图片生成（独立 Rust 容器，由 `services/rust-pjsk-draw/` 构建），Go 和 Python 都从这里出图 |
| `pjsk-helper` | PJSK 主数据、资源、Suite、榜线和预测数据（`services/go/go-pjsk-helper/`） |
| `postgres` | 数据库，数据放在 `./volumes/postgres` |
| `chromium` | 给 Bot 渲染网页和图片，`kndbot` 会和它共用网络 |

PJSK 的 Go 服务和 Python 主 Bot 会同时收到 OneBot 消息。组卡还是 Python 在处理，已经交给 Go 的 PJSK 指令则由 Go 回复，避免同一条消息回复两次。

## 开始部署

### 1. 初始化目录、配置与资源

新克隆仓库可直接运行一键初始化脚本（自动初始化 submodule、复制 `.env` 与 `config/`、建立 `data/` 与 `volumes/` 目录骨架，可选同时解包资源包）：

```bash
scripts/ops/bootstrap.sh
# 若已从 Release 下载静态资源包：
# scripts/ops/bootstrap.sh --resources /路径/kndbot-resources.tar.gz
```

也可以手动准备配置：

```bash
git submodule update --init --recursive
cp .env.example .env
cp -a example_config config
mkdir -p data/manager
chmod 600 .env
```

`.env` 和 `config/` 是你自己的配置，不要把真实密码和 token 提交到 Git。至少需要填写：

- `POSTGRES_PASSWORD`：数据库密码；
- `ONEBOT_ACCESS_TOKEN` 与 `PJSKBOT_ONEBOT_TOKEN`：分别与 OneBotFilter 的 Python 和 Go 连接配置一致，Go 入口默认要求令牌；
- 使用 OneBot v11 HTTP 事件上报时，将 `ONEBOT_SECRET` 配置为上报端使用的签名密钥；
- `SUPERUSERS`、机器人账号和你需要使用的 API token；
- `config/pjsk/servers.yaml`、`config/pjsk/settings.yaml`；
- 若宿主机外部 PJSK 数据目录不在默认空占位目录 `./volumes/haruki-3d-output` 与 `./volumes/local-jp`，可按需在 `.env` 覆盖 `HARUKI_3D_RUNTIME_HOST_DIR` 和 `LOCAL_JP_DATA_HOST_DIR`；
- 如需构建期走宿主代理，在 `.env` 中显式设置 `BUILD_HTTP_PROXY` / `BUILD_HTTPS_PROXY` / `BUILD_ALL_PROXY`（默认留空直连）。

升级旧部署时，按 `.env.example` 核对本地配置并移除已退役的变量和 profile。

字体、图片和部分 PJSK 固定资源比较大，不会完整放进 Git。准备好资源包后，可通过 `bootstrap.sh --resources ...` 或 `tar xzf kndbot-resources.tar.gz` 解压到仓库根目录的 `data/` 里。

### 2. 先检查一遍

```bash
python3 scripts/ops/preflight.py
```

这个检查会帮你看看 Docker、配置文件、submodule、端口和目录权限有没有明显问题。它只显示变量名，不会把密码打印出来。

如果要给脚本或监控使用 JSON：

```bash
python3 scripts/ops/preflight.py --json
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

先看各个容器是不是 `running`，再看健康状态。`pjsk-draw` 为独立 Rust 绘图容器（由 `./services/rust-pjsk-draw` 构建，默认镜像 `${KND_PJSK_DRAW_IMAGE:-kndbot-pjsk-draw:local}`，只读挂载 `./config`，读写挂载 `./data/pjsk` 与 `./data/resources`），可检查其就绪探针：

```bash
curl -fsS http://127.0.0.1:45560/readyz
```

如果启用了 `deck-http` profile，可直接检查 Allium HTTP 服务的 `/v1/regions` 返回内容：

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
Python  ws://127.0.0.1:18081/onebot/v11/ws
Go      ws://127.0.0.1:3001/onebot/v11/ws
```

Go 通过只读挂载读取 `data/manager/group_manager.json` 中的群开关和群黑名单。

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

## skme 个人历史曲线

`skme` 读取 `data/pjsk/ondemand/database/remote_live/{region}_{account}.db` 中的历史记录。
在 `.env` 中可通过 `SEKAI_REMOTE_ACCOUNT` 和 `SEKAI_REMOTE_REGION` 设置默认查询账号和区服（默认 `cn`）。

## 可选的 Haruki 3D 服装截图

`haruki-3d-engine` 使用官方镜像 `ghcr.io/team-haruki/haruki-3d-engine:main`，不使用不存在的 `latest`。本次核实本机已拉取的 digest 为 `sha256:0fdfeade9875e9e4a2cab09fa22272d0999c35455da55dc6cc3866d251c9f880`；`main` 是可变标签，后续更新前应再次核对镜像与导出资产版本。

默认宿主 3D runtime 占位目录为 `./volumes/haruki-3d-output`（可在 `.env` 通过 `HARUKI_3D_RUNTIME_HOST_DIR` 覆盖为宿主机外部 `Asset-Updater/Data/3d-output` 路径）：

| 宿主目录 | engine 容器目录 | 用途 |
| --- | --- | --- |
| `./volumes/haruki-3d-output`（或外部 `3d-output` 路径） | `/data/runtime`（engine 只读） | engine 官方 runtime 根；JP 为 `3d-output/jp` |
| `${HARUKI_3D_RUNTIME_HOST_DIR:-./volumes/haruki-3d-output}` | `/data/haruki-3d`（bot 只读） | bot 使用的 JP 根为 `/data/haruki-3d/jp` |
| `./volumes/haruki-3d-captures`（或自定义可写目录） | `/data/captures`（可写） | 截图结果，独立于 Bot 数据目录 |

engine 镜像以 `node` 用户运行。启动前需确认 runtime 可读、captures 对镜像用户可写，并确认官方包索引、模型、纹理与当前主数据版本匹配。普通 JP PNG 资源目录不能替代 3D runtime。

可在 `.env` 中覆盖：

```dotenv
# 只绑定宿主回环，默认 45561 已避开 updater 8080 和 assets HTTP 8082。
HARUKI_3D_ENGINE_PORT=45561
# 默认留空：关闭新增服装截图通知。资产验收后显式设置下一行 URL 才启用。
# Compose 内必须使用服务名，不能使用容器内的 localhost 或宿主映射端口。
PJSKBOT_HARUKI_3D_ENGINE_URL=
# 启用时：PJSKBOT_HARUKI_3D_ENGINE_URL=http://haruki-3d-engine:8080
```

URL 传给 `go-pjsk-bot`；`PJSKBOT_HARUKI_3D_RUNTIME_ROOT=/data/haruki-3d/jp` 指向只读 JP runtime。engine 使用镜像官方环境名 `PORT=8080`、`HARUKI_RUNTIME_ROOT=/data/runtime` 与 `HARUKI_CAPTURE_OUTPUT_DIR=/data/captures`。3D 服务名加入默认 `NO_PROXY`；若自己设置了 `NO_PROXY`，也需包含 `haruki-3d-engine`。engine 健康检查为 `/healthz`，服装路由为 `/regions/jp`；只证明 HTTP 服务存活，不代表所有资产均可渲染。

helper 的 `local_new_card_snapshot.json` 保留 `revision/cards/events/eventCards`，新增 `costume3ds/cardCostume3ds/character3ds/gameCharacters`。源目录必须包含这七张 JSON 表；新增四张表和 cards 均参与快速内容探测、两次完整读取与 revision 指纹。缺表、无效 JSON、错误数组结构或读取期间内容变化时保留上次成功快照；所有主数据成功发布后才原子替换单文件快照并记住源指纹。原有新卡消费者仍可忽略新增字段。

**本阶段仅落地配置，不能启动 updater 的 `haruki_3d`，不能重启现有服务。** engine 放在 `haruki-3d` profile，不影响普通 `docker compose up -d`；先完成资产版本、目录权限和截图接口验收，再由部署维护者批准启用。以下命令仅供批准后执行：

```bash
docker compose --profile haruki-3d up -d haruki-3d-engine
curl -fsS http://127.0.0.1:45561/healthz
```

部署还需重新构建包含扩展的 helper，并在业务侧完成接口集成后更新 `go-pjsk-bot`；配置 URL 本身不代表功能已上线。不要仅为引擎增加强制 `depends_on`，以免资产未就绪阻塞现有 PJSK 服务。

## PJSK 新服装速报与 3D 截图行为

仅支持日服。部署全局启用后，群管理员可用：

- `pjsk新服装速报`：查询最新服装速报（别名 `新服装速报`、`新服装`、`pjsk新服装`）。
- `pjsk开启新服装通知`：开启本群通知（别名 `pjsk新服装通知开启`）。
- `pjsk关闭新服装通知`：关闭本群通知（别名 `pjsk新服装通知关闭`）。

群开关与部署 URL 开关是两层控制：URL 未配置时，群内开启命令会提示功能未启用；查询遇到资产未就绪会提示原因，不会伪造成功图片。

新卡本地快照同步成功后，Go PJSK 会把服装数据作为独立的速报来源处理。首张符合条件的色卡优先选择其关联角色作为展示对象；这不是“固定男女各一名”的规则，系统不会强行凑一男一女。若一次更新包含多个候选，会按主数据关联与稳定顺序选择，结果应视为首色卡/角色优先，而不是性别配额。

首次启用或首次建立服装状态时，速报会先建立初始 baseline：baseline 只用于记录已观察版本，避免把启用前历史服装全部重新通知；它不是 PNG 或 3D 资源已就绪的证明。部署切换、清空状态或更换数据集时，应预期 baseline 重新建立并核对通知范围。

服装截图是独立的最佳努力步骤：3D engine 不可用、JP runtime 缺文件、单个角色渲染失败或临时超时时，不应阻塞原卡图/新卡速报；该服装任务会独立重试，成功后再补发或更新服装结果。不要把 `PJSKBOT_HARUKI_3D_ENGINE_URL` 非空理解为 PNG ready，也不要在普通 JP PNG 资源验收前宣称截图可用。

## 服装速报开关与临时回滚

默认关闭服装截图通知，保持以下配置为空：

```dotenv
PJSKBOT_HARUKI_3D_ENGINE_URL=
PJSKBOT_HARUKI_3D_RUNTIME_ROOT=/data/haruki-3d/jp
```

准备启用时，先确认官方 engine、`/regions/jp` 路由、runtime 版本和权限，再只在 `.env` 中设置：

```dotenv
PJSKBOT_HARUKI_3D_ENGINE_URL=http://haruki-3d-engine:8080
PJSKBOT_HARUKI_3D_RUNTIME_ROOT=/data/haruki-3d/jp
```

临时关闭/回滚只需将 `PJSKBOT_HARUKI_3D_ENGINE_URL` 恢复为空，并按既有发布流程重建或重启 `go-pjsk-bot`；不要删除 snapshot、baseline 或主数据。这样会停止新的 3D 服装尝试，原卡图和其他 PJSK 通知继续工作。重新启用时应保留并复核 baseline，避免重复速报。

以下命令属于部署维护者，本文档不执行；本阶段不要启动服务：

```bash
docker compose --profile haruki-3d config --quiet
# 获批准后才可启动：
# docker compose --profile haruki-3d up -d haruki-3d-engine
```

## 目录别删错


- `config/`：本机配置，容器里挂载到 `/app/config`，只读；
- `data/`：图片、缓存、PJSK 数据和运行时资源，容器里挂载到 `/app/data`；
- `volumes/postgres/`：PostgreSQL 数据；
- `data/pjsk/ondemand/`：PJSK 运行时数据，Allium 也会用到。

容器删掉没关系，宿主机上的 `data/`、`config/` 和 `volumes/` 还在。不要直接删除这些目录，除非你已经确定不需要里面的数据。

## 常用操作

```bash
# 查看状态
docker compose ps

# 查看日志
docker compose logs -f kndbot
docker compose logs --tail=200 go-pjsk-bot

# 更新 Compose 配置或 .env 后应用变更
docker compose up -d kndbot go-pjsk-bot pjsk-draw

# 改了源码，重新构建
docker compose build
docker compose up -d
```

Python 和 Go 的 OneBot 入口均绑定宿主回环地址 `127.0.0.1`。Python 宿主端口由 `KND_PORT` 控制，默认 `18081`。Go 使用 `3001`。管理命令中的 SQL 执行默认由 `KNDBOT_ENABLE_EXEC_SQL=0` 关闭。

## 备份和恢复

仓库里有现成脚本：

```bash
scripts/ops/backup.sh
scripts/ops/restore.sh --archive /安全路径/kndbot-backup.tar.gz --force
```

备份包括 PostgreSQL 逻辑备份、`data/`、`config/`、`.env` 和校验清单。`skme` 的 `remote_live` SQLite 历史记录随 `data/` 一起备份和恢复。备份文件里有密码和业务数据，别直接丢到公开网盘。

备份脚本还支持 `--help`，需要确认参数时直接运行：

```bash
scripts/ops/backup.sh --help
scripts/ops/restore.sh --help
```

## 常见问题

### 缺少 `.env`

```bash
cp .env.example .env
chmod 600 .env
```

填写 `POSTGRES_PASSWORD` 和 OneBot 连接使用的令牌。

### Bot 没有连上

确认 OneBotFilter 指向：

```text
ws://127.0.0.1:18081/onebot/v11/ws
ws://127.0.0.1:3001/onebot/v11/ws
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
docker compose logs --tail=200 pjsk-draw pjsk-helper
```

首次同步主数据和资源可能会慢；如果一直失败，优先检查 `data/pjsk` 权限、代理和 `config/pjsk/servers.yaml`。

## 迁移与私有资源/镜像分发（GitHub Release vs GitHub Packages）

仓库默认遵循“源码公开、机密与大体积版权资产外置”的原则：

| 资产类型 | 推荐分发载体 | 说明 |
| --- | --- | --- |
| 静态字体、图片、PJSK 固定素材（`data/resources` + `data/pjsk/static`，约 550MB） | **私有 GitHub Release**（`.tar.gz` + `.sha256`） | 适合大体积零散文件包；单文件上限 2GB，不污染镜像层，新机器用 `gh release download` + `scripts/ops/bootstrap.sh --resources ...` 一步校验解包。 |
| 预编译服务镜像（如 `pjsk-draw`、`allium-deck-server`） | **GitHub Packages (`ghcr.io`) Private 镜像**（或 `docker save` 离线包） | 直接对应 Compose 的 `KND_PJSK_DRAW_IMAGE` / `ALLIUM_DECK_IMAGE`；目标机 `docker login ghcr.io` 后免编译拉取即可。 |
| 密钥配置与运行时状态（`.env`、`config/`、PostgreSQL 数据库、`volumes/`） | **`scripts/ops/backup.sh` 加密传输 + `restore.sh`** | 含口令与线上历史数据，**禁止**传到公开仓库、公开 Release 或公开 Package。 |

### 在原机器打包私有资源 / 推送私有镜像

```bash
# 1. 打包静态素材（自动生成 .sha256）
scripts/ops/pack_resources.sh /tmp/kndbot-resources.tar.gz

# 2. 上传到私有仓库（或私有 Release）
# gh release upload assets-v1 /tmp/kndbot-resources.tar.gz /tmp/kndbot-resources.tar.gz.sha256 --clobber

# 3. 若需向新机器分发预编译镜像（GitHub Packages / GHCR）
GHCR_IMAGE=ghcr.io/your-github-account/kndbot-pjsk-draw:latest
docker tag kndbot-pjsk-draw:local "$GHCR_IMAGE"
docker push "$GHCR_IMAGE"
```

### 在新机器迁移启动

```bash
git clone --recursive git@github.com:<你的账号>/knd-bot.git
cd knd-bot
scripts/ops/bootstrap.sh --resources /路径/kndbot-resources.tar.gz
# 编辑 .env 与 config/（或使用 scripts/ops/restore.sh 恢复线上数据）
python3 scripts/ops/preflight.py
docker compose up -d --build
```
