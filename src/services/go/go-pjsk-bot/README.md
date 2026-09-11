# go-pjsk-bot

PJSK 模块业务逻辑的独立 Go 微服务。**只做 pjsk，一件事做好。**

## 定位与边界（务必遵守）

- ✅ **只处理 pjsk 指令的业务逻辑**：查榜(sk)、猜曲(guess)、mysekai、绑定、活动、卡面等；
  组卡由 Python deck 插件专门处理。
- ✅ **出图一律委托 Python 的 pjsk-draw 微服务**（`internal/draw`，POST /render/{name}）。
  Go 侧只取数 + 组织 JSON payload，**绝不在 Go 里写任何绘图代码**。
- ✅ **数据复用现有 sidecar**：go-pjsk-helper（masterdata/ranking/assets/suite）、
  sekai-api（游戏 API）；组卡算法不在 Go 服务内实现。
- ✅ **与 Python 主进程通过命令所有权互斥**：`KND_GO_OWNED_COMMANDS` 命中的 pjsk
  指令由本服务接管，Python 侧 matcher 安静退场（`services/go_ownership.py`）。
- ❌ **不碰其他插件**（weather/roll/shop/petpet/llm…都留在 Python）。
- ❌ **不自建绘图层**（不重复 Go 分支 python-renderer 的错误）。
- ❌ **不做巨型单文件调度器**（不重复 Go 分支 6909 行 handler.go 的错误）。

## 结构

```
cmd/pjskbot/main.go     入口：接入 OneBot WS、装配路由、启动
internal/
  onebot/      OneBot v11 正向/反向 WS 接入（收事件、发 action）
  router/      指令路由：命令起始符 + 别名 + cn/tw 服务器前缀解析
  config/      环境变量配置
  draw/        pjsk-draw 出图微服务 HTTP 客户端
  remotelive/  remote_live SQLite 只读查询
  pjsk/        各指令业务逻辑（按模块分文件，禁止堆到一个文件）
```

## 配置（环境变量）

| 变量 | 说明 | 默认 |
| --- | --- | --- |
| `PJSKBOT_ONEBOT_MODE` | OneBot 接入模式：`forward` 或 `reverse` | `forward` |
| `PJSKBOT_STANDALONE` | `1` 时接管 Go 二进制注册的全部命令，忽略 `KND_GO_OWNED_COMMANDS` | `0` |
| `PJSKBOT_ONEBOT_WS_URL` | OneBot 正向 WS 地址（forward 模式） | `ws://127.0.0.1:3001` |
| `PJSKBOT_ONEBOT_LISTEN_ADDR` | 反向 WS 监听地址（reverse 模式） | `:3001` |
| `PJSKBOT_ONEBOT_PATH` | 反向 WS 路径（reverse 模式） | `/onebot/v11/ws` |
| `PJSKBOT_ONEBOT_TOKEN` | OneBot access_token（可选） | 空 |
| `PJSK_DRAW_SERVICE_URL` | pjsk-draw 出图服务 | `http://127.0.0.1:45560` |
| `PJSK_HELPER_URL` | go-pjsk-helper | `http://127.0.0.1:45558` |
| `SEKAI_API_URL` | sekai-api（remote/live/token/分数配置） | `http://127.0.0.1:9999` |
| `SEKAI_API_TOKEN` | sekai-api 鉴权 token | 空 |
| `SEKAI_CONTROL_URL` / `SEKAI_CONTROL_TOKEN` | 旧部署宿主 API 控制服务（可选） | 空 |
| `SEKAI_REMOTE_ACCOUNT` / `SEKAI_REMOTE_REGION` | remote 目标账号/区服 | 空 / `cn` |
| `SEKAI_LIVE_INTERVAL` / `SEKAI_LIVE_AUTO_STOP` | live 循环间隔秒数/每日停止时间 | `80` / `03:55` |
| `PJSK_DATA_DIR` | pjsk 数据目录（共享 volume） | `/app/data/pjsk` |

## 构建 / 测试

本机无 go，用 docker：

```bash
cd src/services/go/go-pjsk-bot
docker run --rm -v "$PWD":/w -w /w -e GOPROXY=https://goproxy.cn,direct -e GOSUMDB=off \
  golang:1.25-alpine sh -c 'go mod tidy && go build ./... && go vet ./... && go test ./...'
```

### 一键全量验证

`scripts/verify.sh` 串联全部检查，一条命令验证整个交付（CI / 提交前自检）：

```bash
bash src/services/go/go-pjsk-bot/scripts/verify.sh
```

依次执行：① gofmt ② go build ③ go vet ④ go test（Go 步骤默认在 golang:1.25
容器里跑；本机装了 go 可设 `USE_LOCAL_GO=1`）⑤ check_ownership_sync（命令所有权
Go/Python 映射一致，防双回复漂移）⑥ check_draw_tasks（Go 出图 task 名都存在于
Python 侧）⑦ Python go_ownership 单元测试。任一失败则整体非零退出。

## 迁移进度

- [x] 骨架：onebot 接入 / router / draw 客户端 / config / 入口，编译+测试通过
- [x] 命令所有权：`KND_GO_OWNED_COMMANDS` 解析与路由过滤（router/ownership）
- [x] store：PostgreSQL 连接池 + pjsk_bind CRUD（与 Python 共享库表）
- [x] 业务模块 bind：绑定 / 解绑 / 给看 / 查时间（含 uid 校验、at 解析、隐私）
- [x] 业务模块 ycm：烧烤推车查询（抓取站点数据 → pjsk-draw 出图，验证 draw 闭环）
- [x] 基础设施续：masterdata(本地JSON读取+解包+索引+缓存) / gameapi(token+错误映射) /
      limiter(CD+防重入)，均带 go test。（早期的 helper sidecar 客户端因 Go 选择直连
      sekai-api（profile.Fetcher）+ 本地 sqlite（skstore），功能重叠无调用方，已移除）
- [x] 指令限流接线：分发层套用 RateLimiter（per-user/group 冷却 + 防重入，对齐 Python
      __plugin_cd_limit__，superuser 豁免，含测试）——各指令按规范名配 count_limit(默认60s/5次)。
- [x] 档案基础设施：serverconfig(读 servers.yaml) / profile(getsuite+收歌进度统计,含测试) /
      UserResolver(uid 解析,对齐 get_userid_preprocess)
- [x] 业务模块 rop：收歌进度查询（profile → pjsk-draw "rop" 出图）
- [x] 业务模块 b30：按谱面定数算个人最佳30（constants.csv 定数表 + profile → "b30" 出图）
- [x] 业务模块 rk：排位赛查询（settings.yaml 取 API 基址 + 当前赛季 → 段位/胜负文本）
- [x] 业务模块 arrest：逮捕（收歌统计 + 当期排位成绩文本）
- [x] 业务模块 gacha：假抽卡模拟（概率/保底/权重算法 + 十连走 pjsk-draw "gacha"；正则触发）
- [x] router 正则触发支持（on_regex 型指令，如抽卡）
- botcheck 自动群成员检测与插件开关治理仍保留 Python；`查询/添加/删除uni分布式` 管理命令及共享 `unibot.json` 已迁移到 Go。
- [x] 业务模块 card（卡面大图）：复用 servers.yaml rip 源下载、资源缓存、PNG 转 JPG，并通过 OneBot 发送特训前后图片。
- [x] 业务模块 findcard（卡面查询概览）：角色(内置缩写+昵称yaml)/团体 + 稀有度/属性/技能/
  限定/fes/年份/活动/leak 多维筛选 → "findcard" 出图；`ena7` 箱活短写已接入。
  群自定义昵称DB作为增强暂缓。cards 底座新增 CharaAliasResolver（读 character_nicknames.yaml + 内置缩写）。
- [x] 业务模块 cardinfo（卡面详情）：解析卡面核心信息(字段/综合力JP+CN格式/技能/角色/限定)
  → "cardinfo" 出图。技能{{}}数值替换、关联event/music/gacha、CN翻译作为增强暂缓。
- [x] cards 底座：团体/角色映射 + CardType(限定判定)/IsFes/UnitVsChars（对齐 _card_utils）
- [x] 业务模块 cardbox（卡牌一览）：按团体/稀有度/属性/限定/fes/年份/活动卡/leak 筛选 →
  "cardbox" 出图；`box` 持卡模式与单角色别名筛选已接入共享绑定库/Suite/角色别名解析。
- [x] 业务模块 diffrank（难度排行）：定数调整(AP/FC/综合) + 难度/定数筛选分组 → "diffrank" 出图；
  绑定玩家成绩(getsuite MusicResult) + 档案头部(HeaderPayload)增强。手动生成难度 csv/json 已迁移到 maintenance，Google Sheets 定时拉取仍保留 Python。
- [x] 业务模块 song（pjskinfo 子集）：查曲精确匹配(id/别名/标题) → "pjskinfo" 出图；查物量；
  别名管理 pjskalias(查别称) / pjskset(新 to 旧,含"to"歧义处理) / pjskdel(删别称)走共享别名库；
  pjskbpm/查bpm 及谱面/技能预览命令编排已迁移并读取/复用本地谱面资产；图片生成仍由
  Python pjsk-draw 完成。模糊拼音评分匹配与资源下载器增强仍保留 Python。
- [x] 业务模块 profile（个人档案）：GetProfile 解析档案 API → "profile" 出图。
  上传/清除个人信息背景、调整个人信息(方向/模糊/透明)已迁(读写共享 profile_bg/settings.json，含图片尺寸/JPG 单测)。
- [x] maintenance 维护模块：难度表 CSV/JSON 互转、superuser `pjsk更新`/`pjsk活动更新`、CN/TW 资源去重预览/执行，更新复用 go-pjsk-helper。
- [x] botcheck 管理命令：`查询/添加/删除uni分布式` 与 Python 共享 `ondemand/database/unibot.json`；群成员自动检测仍由 Python 负责。
- [x] 业务模块 event（活动信息）：当前活动定位(currentEventID) + 活动字段/加成/活动卡解析
  → "event_info" 出图。findevent 活动图鉴已迁(类型/属性/组合/角色/箱活多维参数解析→传全量
  events+params 给 "event_catalog" 渲染端筛选出图，复用 CharaAliasResolver，含测试)。
- [x] mysekai（MySekai）：msr 资源查询三图(取绑定uid→mysekaidata 拉数据→并发渲染
  summary/res_list/map 三图走 pjsk-draw→合并发送)已接通端到端。mysekaidata 获取器
  (API+本地缓存兜底、GetSuiteData、ProfileFromSuiteData、GetPhoto,含测试) + serverconfig.MysekaiURL。
  已补 msgate/msm/msmat/msb/msf/msd/msp 指令 handler 与 CN服白名单管理(cnmsr启用/禁用/白名单,
  superuser)。msr订阅/取消订阅与定时主动推送已迁(msrsub 读写共享 mysekai_msr_subscription.db，按服批量查询
  upload_time、三图出图缓存与发送后状态回写，CN服白名单校验，含测试)。
- [x] remote 控制基础（superuser）：`remote on/off`、`live on/off`、`remote状态` 复用 sekai-api `/echo`
  与可选旧控制服务，状态落盘 `data/pjsk/ondemand/remote/state.json`；live 维护可取消后台循环并可调用
  `/api/{region}/user/%user_id%/live/auto`，不改 guess/订阅逻辑。
- [x] remote token 基础：`pjsktoken状态` 透传 `/token/{region}/status`；`pjsk上传token` 私聊等待
  OneBot `offline_file`、下载后 multipart POST `/token/{region}/upload`，与 Suite 上传 notice 串联。
- [x] remote 打歌分数配置（superuser）：打歌分数(查询) / 设置打歌分数(auto|clear 的 base_score/life,
  含"重置"与字段名/位置兜底解析) 调用 sekai-api /config/score。
- botcheck 的自动群成员检测/插件开关治理仍由 Python 负责；管理命令已由 Go 接管。card 资源命令已由 Go 接管，资源下载仍按配置复用现有 rip 源。
- [x] sk 时速/排名线端到端：skranking 榜线解析(Ranking + FromSK/FromItems/Merge) + 时速计算
  (CalculateSpeed/BuildRankTableData) + ParseRankArgs 档位解析 + skstore 只读时序 sqlite(纯Go)。
  sks/时速/日速/半日速 与 skl/排名线 已接通(取活动→读时序库→算时速→"sk_rank_table"出图,含测试)。
  sk预测/活动预测/skp 表格模式已迁(skforecast 读本地 forecast 缓存 JSON + 实时榜线 → "sk_forecast",含测试)。
  cf/查房/sk 查房已迁(范围/多排名→"sk_cf_range"；单排名/ID/绑定账号→"sk_cf"含WL章节统计；
  QueryRankingByUID + BuildActivityStats 含测试)。csb/查水表已迁(逐时游玩次数+停车区间→"sk_csb",含测试)。
  sks/skl/cf/csb 支持显式 WL 单章节参数(wl2/wl角色/-c,resolveWLFromChapters 含测试)。
  wlsks/wlskl WL 合并榜表已迁(总榜+各章单榜聚合→"sk_wl_rank_table")。
  wlsk/wlcsb WL查房/查水表已迁(无参默认当前章节,复用 cf/csb + 当前章节注入)。
  ycx曲线/sk预测曲线已迁(历史榜线序列+预测缓存→"sk_forecast_curve",含测试)。
  订阅sk/退订sk/清空sk订阅与定时推送已迁(sksub 读写共享 sk_subscription.db，按活动/UID读取榜线、分数变化时出 sk_cf、失败自动清理，含测试)。
  sk 家族查询已全部迁移。榜线采集与预测数据生成(GRU)由 go-pjsk-helper/Python 承担；Go 只读缓存展示。
- [x] remote/live/token：superuser `remote on/off`、`live on/off`、`remote状态`、token 状态/上传和打歌分数配置已迁；live 循环调用 sekai-api 后递归解析 `endResponse/endStatus`，将总榜/WL 记录写入兼容 Python 的 `remote_live` SQLite。
- [x] skme/cnskme/twskme/sk我的曲线：读取 `ondemand/database/remote_live/{region}_{account}.db` 的 `live_records`，复用 `sk_me_curve_total`；World Bloom 且章节字段完整时追加 `sk_me_curve_wl`。账号支持命令参数或 `SEKAI_REMOTE_ACCOUNT`，未带账号时取 `SEKAI_REMOTE_REGION`；schema/记录不满足绘图前提时返回明确错误。
  共享表 schema 与 Python `_remote_sql.py` 对齐：`id, ts, event_id, account_id, live_id, event_rank, event_point, wl_chapter_no, wl_chapter_rank, wl_chapter_point, score`；查询路径只读，remote 写入路径使用 WAL 并保留 NULL 字段。
- [保留Python] guess（猜曲）：完整交互游戏(on_message 捕获任意群消息模糊匹配答案、
  超时调度结算、音频裁切/倒放、多渲染器出题 guess_card/jacket/chart/lyrics、init_rank 排行榜出图)
  深度耦合框架与富媒体处理，明确保留 Python。早期预留的 guessgame 状态机 + guess_rank 库骨架
  因无调用方（本服务不接管 guess）已作为死代码移除，将来若迁移可从 git 历史找回。
- [x] subscribe（订阅）：虚拟live 列表(过滤 virtualLives.json → "vlive_cards" 出图)已迁；
  群订阅开关(pjsk开启/关闭新曲|live通知,管理员) + 个人@提醒(pjsk新曲|live提醒/取消) + pjsk订阅状态，
  走独立读写 sqlite 订阅库 notifysub(与 Python 共享 notify_subscription.db,纯Go,含测试)。
  定时推送检测(新曲/live轮询+MSR/SK 变更检测+OneBot主动推送)已由 Go 调度器闭环，
  无数据源/网络失败时保留订阅并安全重试。
- [x] deck 组卡继续由 Python `src/plugins/pjsk/deck` 处理：挑战/活动/长草/加成参数解析、
  World Bloom 当前章节默认选择及 `world_bloom_chapter_no` 转换均在 Python 侧完成；推荐引擎默认使用
  `allium-sekai-deck`（`sekai_deck_recommend_cpp`），结果继续交给 `pjsk-draw` 的 `deck` 任务渲染。
  Go 不注册组卡命令，也不调用 Rust deck-service。
- [x] docker-compose 接入：新增 go-pjsk-bot 服务(profiles:["go-pjsk"]，默认不启动)，
  默认以反向 WS 接入 OneBotFilter（同时保留正向模式兼容）+ 共享 postgres/config/data，
  出图指向 pjsk-draw、数据指向 helper/sekai-api，命令所有权
  KND_GO_OWNED_COMMANDS 与 Python 双向互斥。镜像构建验证通过(多阶段静态二进制)。
  启用：docker compose --profile go-pjsk --profile pjsk-draw up -d。
