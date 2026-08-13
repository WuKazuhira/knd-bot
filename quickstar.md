# KanadeBot (kndbot)

基于绪山真寻 Bot 二次开发的宵崎奏同人 QQ 机器人，主打 Project Sekai（PJSK）查询功能，基于 NoneBot2 + OneBot V11。

## 项目来源与致谢

本项目基于 [cYanosora/kndbot](https://github.com/cYanosora/kndbot) 改编，感谢原作者及上游项目贡献者提供的机器人基础框架、插件实现和工程思路。本仓库不是上游项目的原版，而是在其基础上的二次开发版本。

本项目在上游基础上进行了 PJSK 日服（JP）、台服（TW）、国服（CN）的多服务器适配。



```

## 快速开始

### 1. 准备配置

```bash
cp .env.example .env      # 填写数据库口令、SUPERUSERS 及可选 token
cp -a example_config config
```

`config/` 是本机真实配置目录，已被 Git 整体忽略；请在其中填写 `pjsk/servers.yaml`、`pjsk/settings.yaml` 里的实际服务地址，以及 `local/` 下的 chat / LLM provider 配置。`example_config/` 只能放空值、占位符和可公开样板。

所有秘密（数据库口令、GAMEAPI_TOKEN、LLM key、非公开 API 地址等）只放 `.env` 或 `config/`，不要写入源码和 `example_config/`。

### 配置说明

#### `.env`：环境变量和秘密

`.env` 数据库口令、超级用户、机器人账号和 API token。常用字段如下：

| 变量 | 作用 |
| --- | --- |
| `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB` | PostgreSQL 初始化账号、密码和数据库名；其中 `POSTGRES_PASSWORD` 必填 |
| `DB_USER` / `DB_PASSWORD` / `DB_HOST` / `DB_PORT` / `DB_NAME` | bot 连接数据库的参数；Docker 部署时通常由 `POSTGRES_*` 注入 |
| `DATABASE_URL` | 可选，直接提供完整数据库连接串 |
| `SUPERUSERS` | NoneBot 超级用户列表，例如 `["123456789"]` |
| `GAMEAPI_TOKEN` | 需要鉴权的 PJSK API token |
| `KND_PORT` | Chromium/机器人对外端口，默认 `18081` |

#### `config/config.yaml`：插件默认配置

用于签到、商店、群管理、复读、天气等功能的默认开关和参数。群号黑名单、超级用户和 API key 。

#### `config/pjsk/servers.yaml`：各服 API 地址

按 `jp`、`cn`、`tw` 分区域配置 profile、suite、MySekai、排名等 API。


#### `config/pjsk/settings.yaml`：PJSK 多服务器和外部端点

- `server_map` 将服务器编号映射到 `jp`、`tw`、`cn`；
- `api_base_urls` 配置备用 API 基地址；
- `endpoints` 配置音乐元数据、歌曲别名、排位、WorldLink、谱面预览等可选服务；
- `deck` 配置组卡后端、地址、超时和返回数量；
- `masterdata_fallback` 配置 CN/TW 缺表时是否回退到 JP。

如果某个可选端点为空，对应功能会自动跳过或提示暂不可用，不影响其他功能启动。

#### `config/local/llm/`：聊天和 LLM

复制样板后，在 `config/local/llm/providers/` 新建或修改 provider 配置.

聊天提示词在 `config/local/chat/`，可通过 `AUTOCHAT_CONFIG_PATH`、`CHAT_SYSTEM_PROMPT_PATH` 等环境变量覆盖路径。

#### `data/`：运行时数据

`data/` 保存 PostgreSQL 之外的运行时文件，包括统计、缓存、PJSK MasterData、profile 和其他运行时资源。部署时请根据实际环境规划数据目录的备份策略。


### 2. 准备静态资源

字体、图片与 PJSK 固定绘图素材体积较大，不随 Git 分发。从本仓库 Release 下载资源包并在仓库根目录解压：

```bash
tar xzf kndbot-resources.tar.gz   # 展开 data/resources 与 data/pjsk/masterdata
```

各区域 MasterData 与解包素材会在运行时按需自动下载。维护者可用 `scripts/pack_resources.sh` 重新打包。

### 3. 启动

Docker（推荐，含 Postgres / Chromium / deck-service）：

```bash
docker compose up -d --build
```

本地运行（需 Python 3.14+ 与 PostgreSQL）：

```bash
pip install -r requirements.txt
python bot.py
```

再对接任意 OneBot V11 实现（如 NapCat / Lagrange），反向 WebSocket 指向 `ws://<host>:8081/onebot/v11/ws`。

详细部署说明见 [DOCKER.md](DOCKER.md)，功能文档见 `docs/`。

## 许可

AGPL-3.0，见 [LICENSE](LICENSE)。
