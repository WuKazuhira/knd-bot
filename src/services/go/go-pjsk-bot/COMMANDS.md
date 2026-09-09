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
| `cardinfo` / `卡牌一览` | cardinfo/cardbox | 卡面信息 / 卡组一览 |
| `findcard` | findcard | 卡片检索 |
| `烧烤档案` | profile | 个人档案图 |
| `清除个人信息背景` / `调整个人信息` | profile | 背景设置管理（清除自定义背景 / 调方向-模糊-透明，读写共享 settings.json） |
| `难度排行` | diffrank | AP/FC 难度排行 |
| `event` | event | 当前活动信息 |
| `findevent` / `查活动` / `活动图鉴` / `活动列表` | event | 活动图鉴筛选（类型/属性/组合/角色/箱活 → event_catalog） |
| `pjskinfo` / `查曲` / `查物量` | song | 歌曲信息 / 物量查询 |
| `pjskalias` / `pjskset`（正则） / `pjskdel` | song | 歌曲别名查询 / 添加 / 删除 |
| `msr` / `msg` / `msm` / `msmat` / `msb` / `msf` / `msd` / `msp` | mysekai | MySekai 资源/门/唱片/材料/蓝图/家具/抓包状态/照片 |
| `烤森材料` | mysekai | MySekai 材料图 |
| `cnmsr启用` / `cnmsr禁用` / `cnmsr白名单` | cnmsr | CN 服 MSR 群白名单管理（superuser） |
| `msr订阅` / `msr取消订阅` | mysekai | MySekai 数据更新推送订阅增删（读写共享 mysekai_msr_subscription.db；CN 服需白名单） |
| `挑战组卡` | deck | 挑战组卡推荐 |
| `sks` / `时速` / `skl` / `排名线` | sk | 时速/日速/半日速 与 排名线（支持 wl2/wl角色 单章节参数） |
| `wlsks` / `wlskl`（及 wl时速/wl排名线 等） | sk | WL 跨章节合并榜表（总榜+各章单榜时速/排名线 → sk_wl_rank_table） |
| `wlsk` / `wl查房` / `wlcsb` / `wl查水表` | sk | WL 查房/查水表（无参默认当前章节；支持 wl2/wl角色 指定章节） |
| `sk预测` / `活动预测` / `skp` | sk | 活动预测表格（读本地 forecast 缓存 JSON + 实时榜线 → sk_forecast） |
| `ycx曲线` / `sk预测曲线` / `活动预测曲线` | sk | 预测曲线图（历史榜线序列 + 预测缓存 → sk_forecast_curve） |
| `订阅sk` / `退订sk` / `清空sk订阅` | sk | sk 分数变动订阅增删（读写共享 sk_subscription.db；清空需 superuser） |
| `cf` / `查房` / `sk` | sk | 查房（范围/多排名 → sk_cf_range；单排名/ID/绑定账号 → sk_cf，含 WL 章节统计） |
| `csb` / `查水表` | sk | 查水表（逐时游玩次数 + 停车区间 → sk_csb；单排名/ID/绑定账号） |
| `虚拟live` / `vlive` | subscribe | 近期虚拟 Live 列表 |
| `pjsk开启/关闭新曲通知` / `pjsk开启/关闭live通知` | subscribe | 群订阅开关（管理员），关闭连带清理个人提醒 |
| `pjsk新曲提醒` / `pjsklive提醒` 及取消 | subscribe | 个人 @ 提醒订阅/取消 |
| `pjsk订阅状态` | subscribe | 本群订阅状态 |
| `打歌分数` / `设置打歌分数` | remotescore | 远程打歌分数配置（superuser，调 sekai-api） |
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
- **谱面预览 / 技能预览 / pjskbpm / 查bpm**：依赖谱面文件下载与 BPM 解析
  （资产下载器），Go 侧无对应基础设施。
- **card（卡面大图）**：依赖大体积资源下载与合成。
- **上传个人信息背景**：接收用户图片、缩放并保存为 jpg，涉及图像处理，保留 Python。
  （清除背景 / 调整个人信息 已由 Go 接管，读写共享 profile_bg/settings.json）
- **生成难度csv / 生成难度json**：依赖 Google Sheets 下载与格式转换。

### 有状态会话 / 框架治理
- **guess（猜曲/猜卡面/猜谱面 全套）**：`on_message` 捕获任意群消息模糊匹配
  答案、超时调度结算、音频裁切/倒放、多渲染器出题、`init_rank` 排行榜出图，
  深度耦合 nonebot 会话与富媒体。（早期预留的 Go 状态机/排行榜库骨架因无调用方已移除）
- **botcheck（uni 分布式检测）**：耦合 `run_preprocessor`、`group_manager`、
  跨 bot 实例与群成员列表，属框架治理层。

### 定时 / 后台任务
- **新曲 / live / msr 数据更新的定时检测与推送**：定时轮询 + OneBot 主动推送
  （读各自的订阅表）。订阅的增删（subscribe 开关、sk 订阅、msr订阅）均已由 Go 接管。
- **remote on/off、live 循环打歌、token 上传**：sekai-api 生命周期管理、
  定时循环、文件上传等有状态后台流程。
- **pjsk更新 / pjsk活动更新**：主数据/资产更新调度。

### 依赖榜线明细数据 / 复杂多模式出图（sk 家族增量）
- sk 家族已全部迁移：`sks`/`skl`/`cf`/`csb`（普通榜 + WL 单章节参数）、
  `wlsks`/`wlskl`（WL 合并榜表）、`wlsk`/`wlcsb`（WL 查房/查水表）、
  `sk预测`（预测表格）、`ycx曲线`（预测曲线）。预测数据的**生成**（多源合并 +
  GRU 模型 + 定时任务）仍由 Python 承担；Go 只读缓存展示。
- **sk预测 / ycx / ycx曲线**：`sk预测/活动预测/skp` 的**表格模式已由 Go 接管**
  （读本地 forecast 缓存 JSON + 实时榜线出图）；`ycx曲线`（历史曲线）与 WL 分榜
  预测、以及预测数据的**生成**（多源合并 + GRU 模型 + 定时任务）仍在 Python。
- **sk 分数变动的定时检测与推送**：轮询榜线 + OneBot 主动推送（读 sk_subscription.db）。
  订阅的增删查（订阅sk/退订sk/清空sk订阅）已由 Go 接管。
- **skme（自动打歌账号曲线）**：绑定 remote 后台账号。

### 占位未实现
- **5v5人数**：Python 侧当前仅有命令定义、无处理逻辑。

## 灰度配置示例

```bash
# 让 Go 接管已迁移的稳定指令（示例，逐步扩大）
KND_GO_OWNED_COMMANDS='["bind","unbind","给看","查时间","pjsk b30","pjskinfo","难度排行","event","烧烤档案","msr","sks","skl","虚拟live"]'
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
