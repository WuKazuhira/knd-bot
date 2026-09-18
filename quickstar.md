# KanadeBot (kndbot)

这是一只基于绪山真寻 Bot 二次开发的宵崎奏同人 QQ 机器人，主要提供 PJSK 查询和各种群聊娱乐功能。想自己搭一只的话，按下面的顺序来就好，不需要一上来把所有东西都研究透。

本项目基于 [cYanosora/kndbot](https://github.com/cYanosora/kndbot) 改编，感谢原作者及上游项目贡献者提供的机器人基础框架、插件实现和工程思路。本仓库不是上游项目的原版，而是在其基础上的二次开发版本。

本项目在上游基础上进行了 PJSK 日服（JP）、台服（TW）、国服（CN）的多服务器适配。

## 1. 准备配置

```bash
cp .env.example .env
cp -a example_config config
chmod 600 .env
```

先改这些：

- `POSTGRES_PASSWORD`：数据库密码，必须换成自己的；
- `SEKAI_API_JWT_SECRET`：sekai-api 的 JWT 密钥，使用随机长字符串；
- `SUPERUSERS`：需要管理权限的 QQ 号；
- `GAMEAPI_TOKEN`、LLM key 等你实际会用到的 token；
- `config/pjsk/servers.yaml`、`config/pjsk/settings.yaml`：PJSK 各服地址和开关；
- `config/sekai-api/config.yaml`：sekai-api 配置。

`.env` 和 `config/` 是本机配置，真实密码、token、私有 API 地址不要写入源码或 `example_config/`。

## 配置文件简单说明

### `.env`

数据库、超级用户、Bot 账号和 API token 都可以放这里。常用字段：

| 变量 | 用途 |
| --- | --- |
| `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB` | PostgreSQL 账号、密码和数据库名 |
| `DATABASE_URL` | 完整数据库连接串，可选 |
| `SUPERUSERS` | NoneBot 超级用户，例如 `["123456789"]` |
| `GAMEAPI_TOKEN` | PJSK API token |
| `KND_PORT` | 机器人对外端口，默认 `18081` |

### `config/config.yaml`

签到、商店、群管理、复读、天气等普通功能的配置。

### `config/pjsk/servers.yaml`

按 `jp`、`cn`、`tw` 填写 Suite、MySekai、排名等 PJSK 地址。

### `config/pjsk/settings.yaml`

这里可以改服务器映射、备用接口、歌曲和谱面数据、组卡后端、超时，以及 CN/TW 缺数据时是否回退到 JP。

某个可选接口没填时，对应功能会提示暂不可用，不会影响其他功能启动。

### `config/llm/` 和 `config/chat/`

LLM provider 放在 `config/llm/providers/`，聊天配置和提示词放在 `config/chat/`。真实 key 不要放进公开样板。

### `data/`

这里保存统计、缓存、PJSK MasterData、图片、字体和其他运行时资源。这个目录会不断变化，记得一起备份。

## 2. 准备资源

字体、图片和 PJSK 固定素材体积比较大，不会随 Git 完整分发。从 Release 下载资源包后，在仓库根目录解压：

```bash
tar xzf kndbot-resources.tar.gz
```

资源包一般会展开 `data/resources` 和 `data/pjsk/masterdata`。各区 MasterData 也会在运行时按需下载。

## 3. Docker 启动（推荐）

```bash
docker compose up -d --build
```

生产 Docker 默认由 Go 处理已经迁移的 PJSK 指令，Python 主进程继续处理普通功能和组卡。OneBotFilter 需要同时连 Python 入口和 Go 入口，详细说明看 [DOCKER.md](DOCKER.md)。

启动后先看：

```bash
docker compose ps
docker compose logs --tail=100 kndbot go-pjsk-bot
```

## 4. 本地运行

本地运行适合改代码和排查问题，需要 Python 3.14+、PostgreSQL 以及完整配置：

```bash
pip install -r requirements.txt
python bot.py
```

本地模式主要启动 Python 主进程。Go PJSK、helper、绘图和 sekai-api 还是建议用 Docker Compose 一起启动。

对接 OneBot V11 时，反向 WebSocket 指向：

```text
ws://<host>:18081/onebot/v11/ws
```

## 5. 遇到问题先看什么

先跑：

```bash
python3 scripts/preflight.py
docker compose ps
```

再看对应服务的日志：

```bash
docker compose logs --tail=200 kndbot go-pjsk-bot pjsk-draw pjsk-helper sekai-api
```

一般来说，配置文件缺失、端口被占用、`data/pjsk` 没权限，或者首次下载资源还没结束，是最常见的几个原因。

## 许可

AGPL-3.0，见 [LICENSE](LICENSE)。
