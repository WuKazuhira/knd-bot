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
- [x] 档案基础设施：serverconfig(读 servers.yaml) / profile(getsuite+收歌进度统计,含测试) /
      UserResolver(uid 解析,对齐 get_userid_preprocess)
- [x] 业务模块 rop：收歌进度查询（profile → pjsk-draw "rop" 出图）
- [x] 业务模块 b30：按谱面定数算个人最佳30（constants.csv 定数表 + profile → "b30" 出图）
- [x] 业务模块 rk：排位赛查询（settings.yaml 取 API 基址 + 当前赛季 → 段位/胜负文本）
- [x] 业务模块 arrest：逮捕（收歌统计 + 当期排位成绩文本）
- [x] 业务模块 gacha：假抽卡模拟（概率/保底/权重算法 + 十连走 pjsk-draw "gacha"；正则触发）
- [x] router 正则触发支持（on_regex 型指令，如抽卡）
- 注：botcheck（uni 分布式检测）不迁移——它是 Python 主进程的插件开关治理 +
  多 bot 管理（run_preprocessor/group_manager/get_bots），非 pjsk 业务，保留 Python。
- 注：card(卡面大图) 依赖资源图下载+转码，偏资源管理，暂缓迁移。
- [x] 业务模块 findcard（卡面查询概览）：角色(内置缩写+昵称yaml)/团体 + 稀有度/属性/技能/
  限定/fes/年份/活动/leak 多维筛选 → "findcard" 出图。群自定义昵称DB作为增强暂缓。
  cards 底座新增 CharaAliasResolver（读 character_nicknames.yaml + 内置缩写）。
- [x] 业务模块 cardinfo（卡面详情）：解析卡面核心信息(字段/综合力JP+CN格式/技能/角色/限定)
  → "cardinfo" 出图。技能{{}}数值替换、关联event/music/gacha、CN翻译作为增强暂缓。
- [x] cards 底座：团体/角色映射 + CardType(限定判定)/IsFes/UnitVsChars（对齐 _card_utils）
- [x] 业务模块 cardbox（卡牌一览）：按团体/稀有度/属性/限定/fes 筛选 → "cardbox" 出图。
  单角色别名筛选、box 持卡、年份/活动卡筛选作为增强暂缓。
- [x] 业务模块 diffrank（难度排行）：定数调整(AP/FC/综合) + 难度/定数筛选分组 → "diffrank" 出图；
  绑定玩家成绩(getsuite MusicResult)增强。生成难度csv/json(下载 Sheets)保留 Python。
- [x] 业务模块 song（pjskinfo 子集）：查曲精确匹配(id/别名/标题) → "pjskinfo" 出图；查物量；
  别名管理 pjskalias(查别称) / pjskset(新 to 旧,含"to"歧义处理) / pjskdel(删别称)走共享别名库。
  模糊拼音评分匹配作为增强项暂缓；谱面/技能预览(依赖BPM谱面下载)保留 Python。
- [x] 业务模块 profile（个人档案）：GetProfile 解析档案 API → "profile" 出图。
  背景上传/调整指令涉及用户图片存储，保留 Python（绘图服务 profile_bg）。
- [x] 业务模块 event（活动信息）：当前活动定位(currentEventID) + 活动字段/加成/活动卡解析
  → "event_info" 出图。findevent 活动图鉴(复杂角色/团/属性筛选+别名DB)作为增强暂缓。
- [~] mysekai（MySekai）：msr 资源查询三图(取绑定uid→mysekaidata 拉数据→并发渲染
  summary/res_list/map 三图走 pjsk-draw→合并发送)已接通端到端。mysekaidata 获取器
  (API+本地缓存兜底、GetSuiteData、ProfileFromSuiteData、GetPhoto,含测试) + serverconfig.MysekaiURL。
  已补 msgate/msm/msmat/msb/msf/msd/msp 指令 handler 与 CN服白名单管理(cnmsr启用/禁用/白名单,
  superuser)。msr 订阅定时推送作为后续增量。
- [x] remote 打歌分数配置（superuser）：打歌分数(查询) / 设置打歌分数(auto|clear 的 base_score/life,
  含"重置"与字段名/位置兜底解析) 调用 sekai-api /config/score。互斥 key 与 Python go_owns 对齐为命令名。
  remote on/off、live 循环打歌、token 上传等有状态后台任务保留 Python。
- card(卡面大图,依赖资源下载)、botcheck(框架治理) 明确不迁。
- [x] sk 时速/排名线端到端：skranking 榜线解析(Ranking + FromSK/FromItems/Merge) + 时速计算
  (CalculateSpeed/BuildRankTableData) + ParseRankArgs 档位解析 + skstore 只读时序 sqlite(纯Go)。
  sks/时速/日速/半日速 与 skl/排名线 已接通(取活动→读时序库→算时速→"sk_rank_table"出图,含测试)。
  sk预测/活动预测/skp 表格模式已迁(skforecast 读本地 forecast 缓存 JSON + 实时榜线 → "sk_forecast",含测试)。
  cf/查房/sk 查房已迁(范围/多排名→"sk_cf_range"；单排名/ID/绑定账号→"sk_cf"含WL章节统计；
  QueryRankingByUID + BuildActivityStats 含测试)。csb/查水表已迁(逐时游玩次数+停车区间→"sk_csb",含测试)。
  sks/skl/cf/csb 支持显式 WL 单章节参数(wl2/wl角色/-c,resolveWLFromChapters 含测试)。
  榜线采集由 go-pjsk-helper 承担；WL快捷指令(wlsks 等无参数默认跨章合并榜)、ycx曲线与预测数据生成(GRU)作为后续增量。
- [~] guess（猜曲）基础：guessgame 并发安全游戏状态管理器(开局/查询/结束/答题计数,含并发测试)
  + store 排行榜(pjsk_guess_rank add/get)。完整交互游戏(on_message 捕获任意群消息模糊匹配答案、
  超时调度结算、音频裁切/倒放、多渲染器出题 guess_card/jacket/chart/lyrics、init_rank 排行榜出图)
  深度耦合框架与富媒体处理，合理保留 Python；请求-响应骨架(状态机+排行榜库)已在 Go 就位。
- [x] subscribe（订阅）：虚拟live 列表(过滤 virtualLives.json → "vlive_cards" 出图)已迁；
  群订阅开关(pjsk开启/关闭新曲|live通知,管理员) + 个人@提醒(pjsk新曲|live提醒/取消) + pjsk订阅状态，
  走独立读写 sqlite 订阅库 notifysub(与 Python 共享 notify_subscription.db,纯Go,含测试)。
  定时推送检测(新曲/live轮询+OneBot主动推送)仍由 Python 承担。
- [~] deck（组卡）基础设施：deckservice HTTP 客户端(对齐 do_recommend 的 /recommend 契约,
  多地址故障转移,含 httptest 单测) + settings 读 deck 配置。算法在 Rust deck-service，
  Go 只做 options 组装 + 调用。挑战组卡 options 解析已迁(deckopts.BuildChallengeOptions:
  live_type/target/各稀有度卡配置/角色/algorithm/timeout,含测试)。活动/长草/加成 options
  与 handler 接线(取suite→build→recommend→render)作为后续增量。
- [x] deck 挑战组卡端到端：取绑定uid→拉suite→BuildChallengeOptions→deckservice 组卡
  (多算法合并去重)→"deck" 出图已接通。活动/长草/加成组卡 options 作为后续增量。
- [x] docker-compose 接入：新增 go-pjsk-bot 服务(profiles:["go-pjsk"]，默认不启动)，
  连 OneBot 正向 WS + 共享 postgres/config/data，出图指向 pjsk-draw、数据指向 helper/sekai-api/
  deck-service，命令所有权 KND_GO_OWNED_COMMANDS 逐指令灰度。镜像构建验证通过(多阶段静态二进制)。
  灰度启用：docker compose --profile go-pjsk --profile pjsk-draw up -d。
