# Docker 部署

`docker-compose.yml` 编排四个服务：

| 服务 | 说明 |
| --- | --- |
| `kndbot` | 机器人本体 + autochat 服务（同容器） |
| `postgres` | PostgreSQL 16，数据持久化在 `./volumes/postgres` |
| `chromium` | headless-shell，供 htmlrender 渲染；kndbot 共享其网络命名空间 |
| `deck-service` | Haruki 组卡后端，读取 `./data/pjsk/masterdata` 与 `./data/pjsk/deckrec` |

## 步骤

1. 复制并填写环境变量与本地配置（`POSTGRES_PASSWORD` 必填）：

   ```bash
   cp .env.example .env
   cp -a example_config config
   ```

   `config/` 整体被 Git 与 Docker build context 忽略；实际服务器地址、PJSK settings、LLM provider key 等只写在这里。公开仓库只提交脱敏后的 `example_config/`。

2. 解压 Release 资源包（提供 `data/resources` 与 `data/pjsk/masterdata` 固定素材；
   镜像内也带有种子副本，entrypoint 只补齐缺失文件，不覆盖已有数据）。

3. 构建并启动：

   ```bash
   docker compose up -d --build
   docker compose logs -f kndbot
   ```

## 挂载契约

- `./config -> /app/config`（只读）：本机私密配置，由 `example_config/` 复制后填写，整个目录不进入 Git 或镜像层。
- `./data -> /app/data`（读写）：全部运行时数据（日志、缓存、PJSK 数据、静态资源）。
- `./volumes/postgres`、`./volumes/deck-service`：服务自身持久化。

## 配置教程

### PostgreSQL

Docker Compose 会使用 `.env` 中的以下变量初始化数据库：

```dotenv
POSTGRES_USER=kndbot
POSTGRES_PASSWORD=请替换为强密码
POSTGRES_DB=kndbot
```

`POSTGRES_PASSWORD` 没有安全默认值，未填写时 Compose 会拒绝启动。已有数据库卷再次启动时不会重新初始化用户和密码；如果修改密码，需要同步处理 PostgreSQL 用户或使用新的数据卷。

### PJSK 多服务器

复制 `example_config` 后编辑：

```text
config/pjsk/servers.yaml   # JP/CN/TW 的 profile、suite、MySekai、排名 API
config/pjsk/settings.yaml  # server_map、endpoints、组卡、超时和回退策略
```

`servers.yaml` 中的 `{uid}` 和 `{event_id}` 是运行时占位符。不同服务器的接口可以分别配置；某个可选接口未配置时，对应功能会提示暂不可用，不影响其他服务器启动。

### LLM 与聊天

LLM provider 配置放在：

```text
config/local/llm/providers/
config/local/chat/
```

真实 API key 只写在 `config/local/llm/providers/` 或环境变量，不要放到 `example_config/`。修改后重启 `kndbot` 即可。

## 常见操作

- 查看状态：`docker compose ps`
- 查看实时日志：`docker compose logs -f kndbot`
- 只修改 `.env` 或 `config/` 后重启：`docker compose restart kndbot`
- 修改源码后重建：`docker compose build kndbot && docker compose up -d kndbot`
- 对外端口由 `.env` 中 `KND_PORT` 控制（默认 18081，映射容器内 8081）。
- 走代理构建：填写 `.env` 中 `BUILD_HTTP_PROXY` 等变量。
- 数据全部在宿主 `./data` 与 `./volumes`，容器可随时销毁重建。

## 备份与恢复

建议同时备份 PostgreSQL、运行时数据和本地配置：

```bash
mkdir -p backup/manual

docker exec kndbot-postgres pg_dump -U kndbot -d kndbot \
  > backup/manual/kndbot.sql

tar czf backup/manual/kndbot-data.tar.gz data volumes
```

恢复 PostgreSQL 前先停止 bot，避免迁移期间继续写入：

```bash
docker compose stop kndbot
docker exec -i kndbot-postgres psql -U kndbot -d kndbot \
  < backup/manual/kndbot.sql
docker compose start kndbot
```


## 常见问题

### 缺少 `.env`

执行：

```bash
cp .env.example .env
```

并至少填写 `POSTGRES_PASSWORD`。

### 找不到 `config.path_config` 或配置文件

确认首次部署时执行了：

```bash
cp -a example_config config
```

### LLM 不回复

检查 `config/local/llm/providers/` 是否有启用的 provider，API key 是否有效，并查看：

```bash
docker compose logs --tail=200 kndbot
```

### Bot 未连接

确认 OneBot 反向 WebSocket 指向：

```text
ws://宿主机地址:8081/onebot/v11/ws
```

然后查看 `docker compose logs -f kndbot` 是否出现 `Bot ... connected`。
