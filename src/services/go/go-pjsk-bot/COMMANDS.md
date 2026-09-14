# go-pjsk-bot 指令覆盖对照表

本表记录 KNDBOT 各 pjsk 指令在 Go 微服务（go-pjsk-bot）中的迁移状态，
供 `KND_GO_OWNED_COMMANDS` 灰度配置与后续维护参考。

> 迁移原则：**请求-响应式**业务逻辑用 Go 重写；**绘图**统一委托 Python 的
> pjsk-draw 微服务；**框架治理 / 富媒体 / 定时后台 / 有状态会话**类功能保留
> Python。命令所有权（`KND_GO_OWNED_COMMANDS`）在两侧互斥，逐指令灰度。

## ✅ 已由 Go 接管（请求-响应，出图走 pjsk-draw）

| 指令（含别名） | 模块 | 说明 |
| --- | --- | --- |
| `bind` / `unbind` / `给看` / `查时间` | bind | 绑定/解绑/公开设置/账号创建时间 |
| `pjsk b30` | b30 | Best30 成绩图 |
| `pjsk抽卡`（正则） | gacha | 模拟抽卡 |
| `pjsk进度` | rop | 烧烤进度 |
| `逮捕` | arrest | 收歌统计 + 排位段（纯文本；见下方差异说明） |
| `rk` | rk | 排位查询 |
| `card` / `cncard` / `twcard` | card | 卡面原图缓存、PNG 转 JPG、特训前后图片 |
| `cardinfo` / `卡牌一览` | cardinfo/cardbox | 卡面信息 / 卡组一览 |
| `findcard` | findcard | 卡片检索 |
| `烧烤档案` | profile | 个人档案图 |
| `上传个人信息背景` / `上传个人背景` | profile | 下载图片、缩放转 JPG、写入共享 profile_bg |
| `清除个人信息背景` / `调整个人信息` | profile | 背景设置管理（清除自定义背景 / 调方向-模糊-透明，读写共享 settings.json） |
| `pjskupload` / `上传用户信息` | upload | Suite 文件解密并写入共享用户数据 |
| `生成难度csv` / `生成难度json` | maintenance | 由主数据生成可编辑难度 CSV，或将 CSV 回写实时难度 JSON（superuser） |
| `pjsk更新` / `cnpjsk更新` / `twpjsk更新` | maintenance | 通过 go-pjsk-helper 更新指定资源组（superuser） |
| `pjsk活动更新` | maintenance | 查询当前服务器活动号（superuser） |
| `pjsk数据去重` / `pjsk资源去重` | maintenance | CN/TW 资源去重预览或执行（superuser） |
| `查询uni分布式` / `添加uni分布式` / `删除uni分布式` | botcheck | 共享 unibot.json 管理（superuser） |
| `难度排行` | diffrank | AP/FC 难度排行 |
| `event` | event | 当前活动信息 |
| `findevent` / `查活动` / `活动图鉴` / `活动列表` | event | 活动图鉴筛选（类型/属性/组合/角色/箱活 → event_catalog） |
| `pjskinfo` / `查曲` / `查物量` / `pjskbpm` / `查bpm` | song | 歌曲信息 / 物量 / BPM 查询 |
| `谱面预览` / `谱面预览1/2/3` / `技能预览` | preview | 谱面/技能预览（参数编排与出图走 pjsk-draw） |
| `guess`：`pjsk猜卡面` / `pjsk猜曲` / `pjsk猜谱面`（含 cn/tw、普通/阴间/非人类图片题） | guess | 群内有状态猜题；最多 3 次猜测、发起者结束、90 秒超时结算 |
| `结束猜曲` / `结束猜卡面` / `结束猜谱面` | guess | 仅本轮发起者可提前结束并结算 |
| `pjskalias` / `pjskset`（正则） / `pjskdel` | song | 歌曲别名查询 / 添加 / 删除 |
| `msr` / `msg` / `msm` / `msmat` / `msb` / `msf` / `msd` / `msp` | mysekai | MySekai 资源/门/唱片/材料/蓝图/家具/抓包状态/照片 |
| `烤森材料` | mysekai | MySekai 材料图 |
| `cnmsr启用` / `cnmsr禁用` / `cnmsr白名单` | cnmsr | CN 服 MSR 群白名单管理（superuser） |
| `msr订阅` / `msr取消订阅` | mysekai | MySekai 数据更新推送订阅增删（读写共享 mysekai_msr_subscription.db；CN 服需白名单） |
| `sks` / `时速` / `skl` / `排名线` | sk | 时速/日速/半日速 与 排名线（支持 wl2/wl角色 单章节参数） |
| `wlsks` / `wlskl`（及 wl时速/wl排名线 等） | sk | WL 跨章节合并榜表（总榜+各章单榜时速/排名线 → sk_wl_rank_table） |
| `wlsk` / `wl查房` / `wlcsb` / `wl查水表` | sk | WL 查房/查水表（无参默认当前章节；支持 wl2/wl角色 指定章节） |
| `sk预测` / `活动预测` / `skp` | sk | 活动预测表格（读本地 forecast 缓存 JSON + 实时榜线 → sk_forecast） |
| `ycx曲线` / `sk预测曲线` / `活动预测曲线` | sk | 预测曲线图（历史榜线序列 + 预测缓存 → sk_forecast_curve） |
| `订阅sk` / `退订sk` / `清空sk订阅` | sk | sk 分数变动订阅增删（读写共享 sk_subscription.db；清空需 superuser） |
| `cf` / `查房` / `sk` | sk | 查房（范围/多排名 → sk_cf_range；单排名/ID/绑定账号 → sk_cf，含 WL 章节统计） |
| `csb` / `查水表` | sk | 查水表（逐时游玩次数 + 停车区间 → sk_csb；单排名/ID/绑定账号） |
| `skme` / `cnskme` / `twskme` / `sk我的曲线` | skme | 只读 `remote_live/{region}_{account}.db` 的 `live_records`，复用 `sk_me_curve_total`；World Bloom 且章节数据完整时追加 `sk_me_curve_wl`（superuser） |
| `虚拟live` / `vlive` | subscribe | 近期虚拟 Live 列表 |
| `pjsk开启/关闭新曲通知` / `pjsk开启/关闭live通知` | subscribe | 群订阅开关（管理员），关闭连带清理个人提醒 |
| `pjsk开启/关闭新卡通知` | subscribe | 仅日服群订阅开关（管理员） |
| `新卡速递` / `pjsk新卡速递` / `新卡情报` / `pjsk新卡情报` / `新卡` / `pjsk新卡` / `leak` / `pjskleak` | subscribe | 仅日服手动推送最新一批活动图与训练前后卡面原图（合并转发） |
| `pjsk新曲提醒` / `pjsklive提醒` 及取消 | subscribe | 个人 @ 提醒订阅/取消 |
| `pjsk订阅状态` | subscribe | 本群订阅状态 |
| `打歌分数` / `设置打歌分数` | remotescore | 远程打歌分数配置（superuser，调 sekai-api） |
| `remote on/off` / `live on/off` / `remote状态` | remote | superuser 控制 API 健康状态与可取消的 live 后台循环，状态落盘到 `data/pjsk/ondemand/remote/state.json` |
| `pjsktoken状态` | remote token | 查询指定区服 accessToken 状态（superuser，透传 sekai-api） |
| `pjsk上传token` | remote token | 私聊建立待上传状态，接收 OneBot `offline_file` 后下载并 multipart 上传（不影响 Suite 上传） |
| `ycm` | ycm | 云端选卡工具 |

> 以上所有指令均支持 `cn`/`tw` 前缀（由 router 自动展开）；出图指令的图像由
> pjsk-draw 渲染，Go 侧只负责取数、组织载荷、发送。

> **出图载荷核对**：已交叉核对各 render task 的 Go/Python payload 字段。所有 Go
> 使用的 task 名都存在于 Python 侧（`scripts/check_draw_tasks.py` 守护）；字段级差异
> 均为合理的架构差异，非 bug——如 b30/rop/diffrank 的 `data_update_text` /
> `suite_update_text` 来自「本地 suite 缓存文件 mtime」，Go 走实时 API 无此概念。
> diffrank 的玩家档案 `header` 已补齐（复用 `Profile.HeaderPayload`，与 b30/rop 一致：
> 绑定且公开时显示头部，否则渲染器降级为「无数据」状态条）。这些可选时间文本的
> 缺省不影响主体出图。

> **arrest（逮捕）输出差异**：两侧主路径一致（纯文本回复）。Python 在纯文本发送
> 触发 `ActionFailed`（如被风控/过长）时会降级为 text2image 图片兜底；Go 侧仅发纯
> 文本，无图片兜底。这是边缘容错差异（依赖发送失败），非主功能缺失。

> **横切校验核对**：已逐指令对照 Python 的横切关注点。
> - **CN 服白名单**（`_ensure_cn_allowed`）：mysekai 全部查询/订阅/管理指令均已在 cn 服校验群白名单。
> - **隐私（给看/不给看）**：查他人档案时对方设「不给看」应拒绝——profile/b30/rop/arrest/profile_bg
>   走 `UserResolver.Resolve`（含隐私门 `isPrivate && qid≠自己 → 拒绝`），rk 内联实现同逻辑；
>   deck/diffrank/mysekai 只查发送者自己（无隐私问题），sk cf/查房查的是榜线公开数据（非私人档案）。
> - **cn/tw 前缀推断**：`Router.serverOfKey` 剥前缀后校验剩余是注册触发词，避免误剥命令名（cnmsr启用）。
> - **@目标 / 参数解析**：`AtTargets()` 跳过 @全体、取用点排除 @bot（对齐 `get_message_at` + `!= self_id`）；
>   `digitsOnly` 只保留数字，等价 Python `re.sub(r'\D','')`。均一致。
> - **时间/时区**：分场景且两侧一致——findcard 发布年份两侧都按 **UTC**（Go `.UTC().Year()` /
>   Python `fromtimestamp(tz=utc).year`，勿改）；event 活动时间 Go 用固定 UTC+8（cstZone）、
>   Python 用容器 localtime（TZ=Asia/Shanghai）；其它显示时间（注册/抓包/拍摄）两侧均用容器本地时区。
>   结果一致（容器已设 TZ=Asia/Shanghai）。
> - **数值/舍入**：b30 定数（fcrank/AP定数/前30均值/成绩映射/稳定排序）、diffrank 定数调整
>   （fp=ap−lv、fc=fcrank−lv、综合=fc·2/3+fp·1/3，浮点运算无整数除法陷阱，不预舍入）、
>   sk 时速（`(Δscore·period/elapsed)/10000`，不预舍入）、cf activity（avg_pt 取末10均值）逐点对齐。
>   `round2` 用银行家舍入（`math.RoundToEven`）对齐 Python `round(x,2)`——影响 b30 highest
>   与排位胜率（`WinCount/(win+lose)*100`）两处的 .xx5 边界显示。
> - **gacha 概率/保底**：普通抽 `rand.Intn(101)`=[0,100]、十连保底 `Intn((r4+r3)*2+1)/2`、
>   加权四星 `Intn(allWeight)` 累加 `acc≥target` 选卡——随机区间与累加判定均对齐 Python
>   `random.randint`（含两端）+ `nowweight≥rannum2`。

## 🐍 保留 Python（有明确技术依据）

### 富媒体 / 资产处理
- `pjskbpm` / `查bpm` 已由 Go 接管，读取现有本地谱面资产；谱面/技能预览的命令编排也已由 Go
  接管，绘图与资源生成仍由 Python `pjsk-draw` 完成。
- `card` / `cncard` / `twcard`：Go 负责资源缓存、PNG 转 JPG 与 OneBot 图片发送；资源下载仍复用 servers.yaml 配置的 rip 源。
- **上传个人信息背景**：Go 负责下载、尺寸限制、转 JPG 与共享 profile_bg 落盘。
- **pjskupload（上传用户信息）**：Go 接收私聊离线文件，解密 Suite 数据并写入共享 `ondemand/{jp,tw,cn}` 目录。
  （清除背景 / 调整个人信息 已由 Go 接管，读写共享 profile_bg/settings.json）
- **难度定数自动从 Google Sheets 更新**：go-pjsk-helper 定时拉取、匹配并生成共享 constants.csv；Python 逻辑仅作回滚保留。

### 有状态会话 / 框架治理
- **guess 全模式**：卡面、曲绘、谱面、听歌、倒放、歌词、提示、金币、排行榜与会话结算均由 Go 接管。
- **botcheck 自动群成员检测**：Go 负责 uni 分布式账号管理、群成员扫描与 PJSK 命令阻断，状态文件与 Python 基线兼容。

### 定时 / 后台任务
- **新曲 / live / 新卡 / msr / sk 数据更新的定时检测与推送**：Go 调度器按各自订阅表轮询，统一经 OneBot 主动推送；新卡仅检测日服未发布活动卡并用合并转发推送，订阅增删与状态回写均由 Go 接管。
- **remote/live/token 控制与记录**：Go 负责控制、token 状态透传、离线文件上传与 live 记录，skme 从同一库查询曲线。
- **主数据/资源/翻译/难度表/预测自动更新**：go-pjsk-helper 统一负责主数据、资源、翻译、Google Sheets 难度/别名与多源预测缓存。
- **5v5人数**：Python 当前仅保留命令定义，暂无业务处理逻辑。

### 依赖榜线明细数据 / 复杂多模式出图（sk 家族增量）
- sk 家族已全部迁移：`sks`/`skl`/`cf`/`csb`（普通榜 + WL 单章节参数）、
  `wlsks`/`wlskl`（WL 合并榜表）、`wlsk`/`wlcsb`（WL 查房/查水表）、
  `sk预测`（预测表格）、`ycx曲线`（预测曲线）。预测数据由 go-pjsk-helper 统一生成，支持 local、33kit、Moesekai、SekaRun 缓存。
- **sk预测 / ycx / ycx曲线**：表格、历史曲线、WL 分榜预测和多源缓存刷新均由 Go 侧接管。
- **sk 分数变动的定时检测与推送**：轮询榜线 + OneBot 主动推送（读 sk_subscription.db）。
  订阅的增删查（订阅sk/退订sk/清空sk订阅）已由 Go 接管。
- **skme（自动打歌账号曲线）**：Go 读取 `data/pjsk/ondemand/database/remote_live/{region}_{account}.db`，账号取命令参数或 `SEKAI_REMOTE_ACCOUNT`，默认区服取 `SEKAI_REMOTE_REGION`（未带账号时）；曲线出图走 `sk_me_curve_total`，WL 分榜字段完整时追加 `sk_me_curve_wl`。

### 占位未实现
- **5v5人数**（含 `cn5v5人数` / `tw5v5人数`）：Python 侧当前仅有命令定义，暂无业务处理逻辑。

### Python 保留入口逐项验收矩阵

| Python 入口 | 保留原因 | Go 所有权行为 |
| --- | --- | --- |
| `card` / `cncard` / `twcard` | 卡面资源缓存、PNG 转 JPG 与图片发送 | Go 注册并处理 |
| `pjskupload` / `上传用户信息` | Suite 文件解密、用户信息落盘 | Go 注册并处理私聊离线文件 |
| `上传个人信息背景` | 图片下载、缩放、转 JPG 与共享目录落盘 | Go 注册并处理 |
| `guess` 全模式、`来点提示` | 群内有状态猜题、媒体题、提示、金币与排行榜 | Go 注册并处理 |
| `SKAPI切换` | 榜线 API 模式持久化切换 | Go 注册并与旧状态文件兼容 |
| `remote` / `live` / `remote状态` | Go remote 控制基础；需将 `pjsk_remote`/`pjsk_live`/`pjsk_remote_status` 加入 ownership | Go 注册并处理 |
| `pjsktoken状态` / `pjsk上传token` | Go token 状态透传与私聊离线文件上传；需加入对应 `pjsk_remote_token*` ownership | Go 注册并处理 |
| `活动组卡` / `挑战组卡` / `长草组卡` / `加成组卡`（含别名） | Python deck 插件负责参数编排，进程内 allium 计算并复用 pjsk-draw 出图 | Go 不注册；Python 处理 |
| `组卡后端` | 查看 allium 状态；HTTP/deck-service 已停用 | Go 不注册；Python 处理 |
| `skme` / `cnskme` / `twskme` / `sk我的曲线` | remote 记录查询与曲线出图；记录由 Go live 循环写入共享 `remote_live` | Go 注册；需将 `skme` 加入 `KND_GO_OWNED_COMMANDS`，Python 查询 matcher 退场 |
| `5v5人数` | 当前无业务实现，仅占位命令 | 不注册、不吞消息 |
| 新曲/live/msr/sk 分数定时推送 | Go 调度器轮询、去重、OneBot 主动推送与状态回写 | 订阅增删及推送均由 Go 接管 |

验证方式：Go router 仅对 `Register` 且出现在 `KND_GO_OWNED_COMMANDS` 的命令响应；Python
`go_ownership` 对未知保留入口返回 `False`，因此保留入口不会被任一侧误吞。可重复运行
`python3 src/services/go/go-pjsk-bot/scripts/check_command_coverage.py`，自动审计 Python
入口是否已迁移或列入本矩阵。

### 兼容性说明

- cardbox 的 `box` 持卡模式、单角色别名筛选、活动卡/年份/leak 过滤已由 Go 接管；群自定义昵称数据库增强仍由 Python 保留。
- deck 的挑战/活动/长草/加成模式及 WL 章节、歌曲/难度、区域道具、排除卡牌、队友参数、顶配/次顶配和当前卡组参数均由 Python deck 插件编排并调用进程内 allium；图片继续走 pjsk-draw。
- 指定箱活查询（`ena7` 短写）已由 Go 的 event / findcard / pjskinfo 接管，依赖本地主数据活动 banner 与活动卡集合。
- 仅因资源管理、富媒体交互、后台调度或框架治理而保留 Python 的入口，均列于上方保留清单。
- skme 参数范围明确为 `skme [remote账号]`：不带参数使用 `SEKAI_REMOTE_ACCOUNT`，且仅查询当前活动；数据库缺失、schema 不兼容、记录没有有效总榜排名时返回明确错误，不伪造曲线。WL 分榜仅在 `worldBlooms.json` 与记录字段可可靠配对时绘制。

## 当前全量接管配置

```bash
# 与当前部署 .env 一致：接管本表中已由 Go 完成的全部规范命令。
KND_GO_OWNED_COMMANDS='["逮捕","pjsk b30","bind","unbind","给看","查时间","查询uni分布式","添加uni分布式","card","卡牌一览","cardinfo","cnmsr启用","cnmsr禁用","cnmsr白名单","难度排行","event","findevent","findcard","pjsk抽卡","guess","结束猜曲","生成难度csv","pjsk更新","pjsk活动更新","pjsk数据去重","msr","msg","msm","烤森材料","msb","msf","msd","msp","msr订阅","msr取消订阅","谱面预览","技能预览","烧烤档案","上传个人信息背景","清除个人信息背景","调整个人信息","pjsk_remote","pjsk_live","pjsk_remote_status","pjsk_remote_token","pjsk_remote_token_upload","打歌分数","设置打歌分数","rk","pjsk进度","sks","skl","sk预测","ycx曲线","cf","sk","csb","wlsk","wlcsb","wlsks","wlskl","订阅sk","退订sk","清空sk订阅","skme","pjskinfo","查物量","pjskbpm","查bpm","pjskalias","pjskdel","pjskset","虚拟live","pjsk开启新曲通知","pjsk关闭新曲通知","pjsk开启live通知","pjsk关闭live通知","pjsk开启新卡通知","pjsk关闭新卡通知","新卡速递","pjsk新曲提醒","pjsk取消新曲提醒","pjsklive提醒","pjsk取消live提醒","pjsk订阅状态","pjskupload","ycm"]'
```

命令名需与 Go `Register` 的规范名一致（见上表）；`cn`/`tw` 前缀变体在 **Go 侧**由
router 自动展开，无需在清单中重复列出。

## 灰度互斥机制（已双向生效）

命令所有权的**双向互斥**：Go 接管某命令时，Python 侧对应 matcher 会安静退场，避免双回复。

- **Go 侧**：只处理 `KND_GO_OWNED_COMMANDS` 中列出的命令（router ownership 过滤）。
- **Python 侧**：`plugins/pjsk/__init__.py` 注册了统一的 `run_preprocessor`——对每条 pjsk
  指令，取 nonebot 匹配到的命令名，经 `services/go_ownership.py` 的
  `pjsk_command_owned_by_go()` 归一化（别名 + cn/tw 前缀 → 规范名）后查 owned 列表，
  命中则 `IgnoredException` 让 Python 退场。

安全特性：
- 默认 `KND_GO_OWNED_COMMANDS` 为空 → preprocessor 全部返回 False，**现有部署零影响**。
- 归一化失败（无法识别为已迁移命令）→ 默认由 Python 处理，**绝不误吞**非 owned 指令。
- 别名/前缀映射镜像 go-pjsk-bot 的 `r.Register(name, aliases)`，两侧判定一致。
- remote 系指令仍走各自的 `go_owns()` 钩子（更细粒度），与本统一钩子并存。

> **维护要求**：Go 侧新增/修改命令（`r.Register` / `RegisterRegex`）时，必须同步
> `src/services/go_ownership.py` 的 `_PJSK_ALIAS_TO_CANON`。用
> `python3 scripts/check_ownership_sync.py` 校验两侧一致（不一致时非零退出，
> 适合接入 CI 或提交前检查）。
