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
- [ ] 业务模块续（中等）：card(卡面大图)、findcard(角色概览)、event
- [x] 业务模块 cardinfo（卡面详情）：解析卡面核心信息(字段/综合力JP+CN格式/技能/角色/限定)
  → "cardinfo" 出图。技能{{}}数值替换、关联event/music/gacha、CN翻译作为增强暂缓。
- [x] cards 底座：团体/角色映射 + CardType(限定判定)/IsFes/UnitVsChars（对齐 _card_utils）
- [x] 业务模块 cardbox（卡牌一览）：按团体/稀有度/属性/限定/fes 筛选 → "cardbox" 出图。
  单角色别名筛选、box 持卡、年份/活动卡筛选作为增强暂缓。
- [x] 业务模块 diffrank（难度排行）：定数调整(AP/FC/综合) + 难度/定数筛选分组 → "diffrank" 出图；
  绑定玩家成绩(getsuite MusicResult)增强。生成难度csv/json(下载 Sheets)保留 Python。
- [x] 业务模块 song（pjskinfo 子集）：查曲精确匹配(id/别名/标题) → "pjskinfo" 出图；查物量。
  模糊拼音评分匹配作为增强项暂缓；别名 set/del 待补。
- [x] 业务模块 profile（个人档案）：GetProfile 解析档案 API → "profile" 出图。
  背景上传/调整指令涉及用户图片存储，保留 Python（绘图服务 profile_bg）。
- [ ] 业务模块续（复杂）：mysekai、deck、guess、subscribe、sk
