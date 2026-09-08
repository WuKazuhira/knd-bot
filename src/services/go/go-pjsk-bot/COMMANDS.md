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
| `逮捕` | arrest | 收歌统计 + 排位段 |
| `rk` | rk | 排位查询 |
| `cardinfo` / `卡牌一览` | cardinfo/cardbox | 卡面信息 / 卡组一览 |
| `findcard` | findcard | 卡片检索 |
| `烧烤档案` | profile | 个人档案图 |
| `难度排行` | diffrank | AP/FC 难度排行 |
| `event` | event | 当前活动信息 |
| `pjskinfo` / `查曲` / `查物量` | song | 歌曲信息 / 物量查询 |
| `pjskalias` / `pjskset`（正则） / `pjskdel` | song | 歌曲别名查询 / 添加 / 删除 |
| `msr` / `msg` / `msm` / `msmat` / `msb` / `msf` / `msd` / `msp` | mysekai | MySekai 资源/门/唱片/材料/蓝图/家具/抓包状态/照片 |
| `烤森材料` | mysekai | MySekai 材料图 |
| `cnmsr启用` / `cnmsr禁用` / `cnmsr白名单` | cnmsr | CN 服 MSR 群白名单管理（superuser） |
| `挑战组卡` | deck | 挑战组卡推荐 |
| `sks` / `时速` / `skl` / `排名线` | sk | 时速/日速/半日速 与 排名线 |
| `虚拟live` / `vlive` | subscribe | 近期虚拟 Live 列表 |
| `pjsk开启/关闭新曲通知` / `pjsk开启/关闭live通知` | subscribe | 群订阅开关（管理员），关闭连带清理个人提醒 |
| `pjsk新曲提醒` / `pjsklive提醒` 及取消 | subscribe | 个人 @ 提醒订阅/取消 |
| `pjsk订阅状态` | subscribe | 本群订阅状态 |
| `打歌分数` / `设置打歌分数` | remotescore | 远程打歌分数配置（superuser，调 sekai-api） |
| `ycm` | ycm | 云端选卡工具 |

> 以上所有指令均支持 `cn`/`tw` 前缀（由 router 自动展开）；出图指令的图像由
> pjsk-draw 渲染，Go 侧只负责取数、组织载荷、发送。

## 🐍 保留 Python（有明确技术依据）

### 富媒体 / 资产处理
- **谱面预览 / 技能预览 / pjskbpm / 查bpm**：依赖谱面文件下载与 BPM 解析
  （资产下载器），Go 侧无对应基础设施。
- **card（卡面大图）**：依赖大体积资源下载与合成。
- **上传/调整/清除个人信息背景**：涉及用户图片存储与处理。
- **生成难度csv / 生成难度json**：依赖 Google Sheets 下载与格式转换。

### 有状态会话 / 框架治理
- **guess（猜曲/猜卡面/猜谱面 全套）**：`on_message` 捕获任意群消息模糊匹配
  答案、超时调度结算、音频裁切/倒放、多渲染器出题、`init_rank` 排行榜出图，
  深度耦合 nonebot 会话与富媒体。（Go 侧已备并发游戏状态机 + 排行榜库骨架）
- **botcheck（uni 分布式检测）**：耦合 `run_preprocessor`、`group_manager`、
  跨 bot 实例与群成员列表，属框架治理层。

### 定时 / 后台任务
- **新曲 / live / msr 订阅推送**：定时轮询 + OneBot 主动推送。
- **remote on/off、live 循环打歌、token 上传**：sekai-api 生命周期管理、
  定时循环、文件上传等有状态后台流程。
- **pjsk更新 / pjsk活动更新**：主数据/资产更新调度。

### 依赖榜线明细数据 / 复杂多模式出图（sk 家族增量）
- **sk（查排名/查房主指令）、cf/查房、csb/查水表**：依赖 go-pjsk-helper 采集的
  榜线明细，含排名/id/范围/@qq 多态参数与复杂出图。
- **wlsk / wlskl / wlsks / wlcsb（WL 分榜系列）**：依赖 WL 章节分榜数据。
- **sk预测 / ycx / ycx曲线**：多源预测合并 + GRU 模型 + 实时榜线合并 + 曲线
  历史累积；预测数据生成为 Python 定时任务。
- **订阅sk / 退订sk / 清空sk订阅**：sk 榜线订阅推送（定时后台）。
- **skme（自动打歌账号曲线）**：绑定 remote 后台账号。

### 占位未实现
- **5v5人数**：Python 侧当前仅有命令定义、无处理逻辑。

## 灰度配置示例

```bash
# 让 Go 接管已迁移的稳定指令（示例，逐步扩大）
KND_GO_OWNED_COMMANDS='["bind","unbind","给看","查时间","pjsk b30","pjskinfo","难度排行","event","烧烤档案","msr","sks","skl","虚拟live"]'
```

命令名需与 Go `Register` 的规范名一致（见上表）；`cn`/`tw` 前缀变体由两侧自动处理，
无需在清单中重复列出。
