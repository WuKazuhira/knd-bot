# KNDBOT 架构与命令接口审计

## 范围和边界

本轮检查 src/plugins、src/basic_plugins 的全部源码帮助声明与公开入口，重点交叉核对 Go PJSK 注册、所有权、权限、参数、绘图和订阅链路。审计不等于每个外部服务都完成线上实测；第三方 SDK、实时游戏 API、所有图片资产、3D runtime 的在线完整性不由静态代码证明。

首轮共发现 95 个声明插件名的模块，其中 82 个非 Hidden；79 个有普通 usage。补齐后 81 个非 Hidden 模块有普通 usage，广播仅提供超管 usage，避免公开管理命令。隐藏的入群条件公开查询保留帮助，但不强行加入全功能总览。

## 服务分层

| 层 | 责任 | 不应承担的工作 |
|---|---|---|
| Python NoneBot 主进程 | 全站帮助、权限/群开关、非 PJSK 插件、保留的 deck | 重复注册已归 Go 的 PJSK 业务 |
| Go PJSK bot | 业务参数解析、取数、订阅状态、OneBot 发送 | 代替全局 help 生成器、真实资源更新器 |
| Python pjsk-draw | JSON 业务载荷转图片、请求级资源策略 | 控制群订阅和 remote 打歌 |
| Go helper | 主数据与资产供给、榜线采集 | 对用户直接发送功能回复 |
| Sekai API | token 与游戏账号 API | 用路由前缀暗中切换当前打歌账号 |
| Haruki 3D engine | 服装 runtime 选模截图 | 影响普通卡面消息的成功与去重 |

PJSK 通知订阅库、新卡快照、SK 和 MySekai 订阅保持独立，未为了统一显示而合并数据库。`pjsk订阅状态` 明确是新曲/Live/新卡/服装的通知订阅；SK/MSR 用各自入口。

## 帮助契约

`knd xxx help` 是示例，不是唯一入口。帮助解析沿用驱动配置中的 nickname 和 command_start，保留无前缀、中文后缀、旧专用入口、大小写与末尾空白。昵称需有明确边界，避免将功能名的一部分误删。

- 总览示例：`knd help`、`kndhelp`。
- 单功能示例：`奏宝 查服装 help`、`kanade sk help`、`/sk help`、`.sk help`、`sk help`。
- 群聊和私聊均支持单功能帮助；管理和超管说明按请求者权限展示。
- 使用只读源码帮助索引合并 cmd、实际命令 alias、命令级用法与动态注册显式元数据；不重置持久配置、群开关、权限、冷却或等级。
- 类别帮助返回该类功能列表，不再返回碰巧第一个命中的插件。
- Go-owned World Bloom 的旧裸帮助有文本兜底；昵称入口由 Python 全局帮助回应，避免两个独立进程重复回复。

## 本轮明确调整

1. 补齐广播、管理员帮助、超级用户帮助、入群条件及全站旧命令的帮助索引；修复广播设置字段拼写。
2. 修复超管附加 usage 被普通 usage 覆盖；token、remote、MSR 管理说明不再混入普通用户帮助。
3. 新卡速报、新服装速报、查服装、SK/MSR 订阅补齐说明，World Bloom 统一归入烧烤相关&uni移植。
4. `skme` 数据库、events、worldBlooms 和绘图载荷使用同一有效区服。
5. World Bloom 参数支持名字键、引号和转义，保留原角色/卡号/花前花后；错误参数提供用法。
6. remote/live 明确拒绝与配置目标冲突的前缀，不自动换账号；token 支持显式区服并检测冲突。
7. Suite/token 上传采用互斥会话和原子消费，file 与 offline_file 走同一归属，保持私聊和超管约束。
8. 卡关联服装补图任务归属新卡所有权；独立服装通知任务归属服装所有权，与目标订阅 kind 一致。
9. 所有权校验加强为真实注册契约检查；同步猜题各模式、大小写、前缀及历史别名，并修正文档规范名与验证脚本。

## 验证结果

- 统一帮助解析、权限、旧设置兼容与群聊/私聊处理：18 项隔离测试通过。
- 帮助元数据、动态别名、权限级说明：10 项检查通过；本轮 74 个改动 Python 文件语法检查通过。
- Go 规范命令 94 个、触发词 258 个、正则 8 个：Python 所有权映射一致；593 个 Python 字面命令及别名的迁移矩阵未发现遗漏。
- Go 公开命令具体帮助定位无缺失、无歧义；39 个 Go 绘图任务全部存在于 Python 注册表。
- 完整 verify.sh 的 gofmt、build、vet、test 和上述契约检查通过。
- 对当前源码通过临时 overlay 运行参数、Suite/token 并发唯一消费及后台通知所有权测试，`go test -race ./internal/pjsk ./cmd/pjskbot` 通过；没有写入仓库测试文件或触发真实上传/打歌/推送。
- Compose 配置校验与 git diff --check 通过。此次验证仅编译/测试，不改变线上容器。

## 兼容与仍需关注

- 保留旧命令，不为架构美观删除接口、群开关或去重历史。
- 条件注册依赖数据库、settings、servers；依赖缺失不意味着自动回退到 Python。本轮说明可用性，不伪称外部依赖测试成功。
- 账号隐私、token 文件授权、角色别称动态内容和所有服务故障组合仍需在专用测试环境验证，不在生产群测试。
- 帮助依赖源码元数据，动态命令必须持续提供显式 alias/usage；静态覆盖脚本用于防止新注册漂移。
- 服装两处现有用户改动及其已有测试不属于此次实现，保留不覆盖。
- 本轮不重启服务、不创建提交、不推送；上线需另行发布。新增回归用例放临时目录，不将测试文件加入仓库。

## 逐模块源码帮助清单

下表通过 AST 读取生成，不导入业务插件或访问运行账号数据。隐藏事件型模块不等价于公开独立命令；动态注册覆盖与外部接口需另行测试。

| 模块源码 | 功能名称 | 源码帮助声明 |
|---|---|---|
| [src/plugins/abstract/__init__.py](../src/plugins/abstract/__init__.py) | 语句抽象化 | 普通说明 |
| [src/plugins/airi_status/__init__.py](../src/plugins/airi_status/__init__.py) | 运行状态 | 普通说明 |
| [src/plugins/chat/__init__.py](../src/plugins/chat/__init__.py) | 大预言模型/Chat | 普通说明 + 超管说明 |
| [src/plugins/crazy_thursday/__init__.py](../src/plugins/crazy_thursday/__init__.py) | 疯狂星期四 | 普通说明 |
| [src/plugins/emojimix/__init__.py](../src/plugins/emojimix/__init__.py) | 表情合成 | 普通说明 |
| [src/plugins/fudu/__init__.py](../src/plugins/fudu/__init__.py) | 复读 [Hidden] | 无普通说明；Hidden |
| [src/plugins/image_management/delete_image/__init__.py](../src/plugins/image_management/delete_image/__init__.py) | 删除图片 [Admin] | 普通说明 |
| [src/plugins/image_management/move_image/__init__.py](../src/plugins/image_management/move_image/__init__.py) | 移动图片 [Admin] | 普通说明 |
| [src/plugins/image_management/pjsk_images/__init__.py](../src/plugins/image_management/pjsk_images/__init__.py) | 烧烤角色称呼 | 普通说明 |
| [src/plugins/image_management/send_image/__init__.py](../src/plugins/image_management/send_image/__init__.py) | 烧烤同人图库 | 普通说明 |
| [src/plugins/image_management/upload_image/__init__.py](../src/plugins/image_management/upload_image/__init__.py) | 上传图片 | 普通说明 |
| [src/plugins/logos/__init__.py](../src/plugins/logos/__init__.py) | logo制作 | 普通说明 |
| [src/plugins/meme_extra/__init__.py](../src/plugins/meme_extra/__init__.py) | 更多表情包 | 普通说明 |
| [src/plugins/memes/__init__.py](../src/plugins/memes/__init__.py) | 表情包制作 | 普通说明 |
| [src/plugins/music/__init__.py](../src/plugins/music/__init__.py) | 点歌 | 普通说明 |
| [src/plugins/mute/__init__.py](../src/plugins/mute/__init__.py) | 刷屏禁言/拉黑 [Admin] | 普通说明 |
| [src/plugins/parse_bilibili_json/__init__.py](../src/plugins/parse_bilibili_json/__init__.py) | b站转发解析 [Hidden] | 无普通说明；Hidden |
| [src/plugins/petpet/__init__.py](../src/plugins/petpet/__init__.py) | 头像表情包/图片操作 | 普通说明 |
| [src/plugins/pjsk/arrest/__init__.py](../src/plugins/pjsk/arrest/__init__.py) | 逮捕 | 普通说明 |
| [src/plugins/pjsk/b30/__init__.py](../src/plugins/pjsk/b30/__init__.py) | 烧烤b30/pjskb30 | 普通说明 |
| [src/plugins/pjsk/bind/__init__.py](../src/plugins/pjsk/bind/__init__.py) | 绑定账号 | 普通说明 |
| [src/plugins/pjsk/botcheck/__init__.py](../src/plugins/pjsk/botcheck/__init__.py) | uni分布式检测 [Superuser] | 普通说明 |
| [src/plugins/pjsk/cardbox/__init__.py](../src/plugins/pjsk/cardbox/__init__.py) | 卡牌一览/cardbox | 普通说明 |
| [src/plugins/pjsk/deck/__init__.py](../src/plugins/pjsk/deck/__init__.py) | 烧烤组卡/pjsk deck | 普通说明 |
| [src/plugins/pjsk/diffrank/__init__.py](../src/plugins/pjsk/diffrank/__init__.py) | 难度排行 | 普通说明 + 超管说明 |
| [src/plugins/pjsk/event/__init__.py](../src/plugins/pjsk/event/__init__.py) | 活动查询/event | 普通说明 |
| [src/plugins/pjsk/findcard/__init__.py](../src/plugins/pjsk/findcard/__init__.py) | 卡面查询/findcard | 普通说明 |
| [src/plugins/pjsk/gacha/__init__.py](../src/plugins/pjsk/gacha/__init__.py) | pjsk抽卡 | 普通说明 |
| [src/plugins/pjsk/guess/__init__.py](../src/plugins/pjsk/guess/__init__.py) | pjsk猜卡面/猜曲/猜谱面 | 普通说明 |
| [src/plugins/pjsk/mappreview/__init__.py](../src/plugins/pjsk/mappreview/__init__.py) | 谱面预览/技能预览 | 普通说明 |
| [src/plugins/pjsk/mysekai/__init__.py](../src/plugins/pjsk/mysekai/__init__.py) | MySekai/烤森查询 | 普通说明 + 超管说明 |
| [src/plugins/pjsk/pjsk_update/__init__.py](../src/plugins/pjsk/pjsk_update/__init__.py) | pjsk数据更新 [Superuser] | 普通说明 |
| [src/plugins/pjsk/pjskinfo/__init__.py](../src/plugins/pjsk/pjskinfo/__init__.py) | 歌曲查询/pjskinfo | 普通说明 |
| [src/plugins/pjsk/profile/__init__.py](../src/plugins/pjsk/profile/__init__.py) | 烧烤档案/pjskprofile | 普通说明 |
| [src/plugins/pjsk/remote/__init__.py](../src/plugins/pjsk/remote/__init__.py) | 远程打歌控制/remote [Superuser] | 普通说明 + 超管说明 |
| [src/plugins/pjsk/rk/__init__.py](../src/plugins/pjsk/rk/__init__.py) | 排位查询/rk | 普通说明 |
| [src/plugins/pjsk/rop/__init__.py](../src/plugins/pjsk/rop/__init__.py) | 烧烤进度/pjsk进度 | 普通说明 |
| [src/plugins/pjsk/sk/__init__.py](../src/plugins/pjsk/sk/__init__.py) | 活动查分/sk | 普通说明 + 超管说明 |
| [src/plugins/pjsk/subscribe/__init__.py](../src/plugins/pjsk/subscribe/__init__.py) | 订阅通知 | 普通说明 |
| [src/plugins/pjsk/upload_file/__init__.py](../src/plugins/pjsk/upload_file/__init__.py) | 上传用户信息/pjskupload | 普通说明 |
| [src/plugins/pjsk/world_bloom/__init__.py](../src/plugins/pjsk/world_bloom/__init__.py) | World Bloom 自定义边框 | 普通说明 |
| [src/plugins/pjsk/ycm/__init__.py](../src/plugins/pjsk/ycm/__init__.py) | 烧烤推车查询 | 普通说明 |
| [src/plugins/plate_picker/__init__.py](../src/plugins/plate_picker/__init__.py) | 捡车牌 | 普通说明 |
| [src/plugins/record/__init__.py](../src/plugins/record/__init__.py) | 聊天记录记录器 | 普通说明 |
| [src/plugins/roll/__init__.py](../src/plugins/roll/__init__.py) | roll | 普通说明 |
| [src/plugins/statistics/statistics_handle.py](../src/plugins/statistics/statistics_handle.py) | 功能调用统计 | 普通说明 + 超管说明 |
| [src/plugins/statistics/statistics_hook.py](../src/plugins/statistics/statistics_hook.py) | 功能调用统计 [Hidden] | 无普通说明；Hidden |
| [src/plugins/turtle_soup/__init__.py](../src/plugins/turtle_soup/__init__.py) | 海龟汤 | 普通说明 |
| [src/plugins/wbtop/__init__.py](../src/plugins/wbtop/__init__.py) | 微博热搜 | 普通说明 |
| [src/plugins/weather/__init__.py](../src/plugins/weather/__init__.py) | 天气查询 | 普通说明 |
| [src/plugins/what/__init__.py](../src/plugins/what/__init__.py) | 缩写查询/梗百科 | 普通说明 |
| [src/plugins/what2eat/eat.py](../src/plugins/what2eat/eat.py) | 今天吃什么 | 普通说明 |
| [src/plugins/what2eat/eat_memu.py](../src/plugins/what2eat/eat_memu.py) | 菜单管理 [Admin] | 普通说明 + 超管说明 |
| [src/plugins/withdraw/withdraw_handle.py](../src/plugins/withdraw/withdraw_handle.py) | 自助撤回 [Admin] | 普通说明 |
| [src/plugins/withdraw/withdraw_message_handler.py](../src/plugins/withdraw/withdraw_message_handler.py) | 自助撤回词条检测 [Hidden] | 无普通说明；Hidden |
| [src/plugins/word_bank/message_handle.py](../src/plugins/word_bank/message_handle.py) | 词库问答回复操作 [Hidden] | 无普通说明；Hidden |
| [src/plugins/word_bank/word_handle.py](../src/plugins/word_bank/word_handle.py) | 词库问答 [Admin] | 普通说明 + 超管说明 |
| [src/plugins/youthstudy/__init__.py](../src/plugins/youthstudy/__init__.py) | 青年大学习 | 普通说明 |
| [src/basic_plugins/about.py](../src/basic_plugins/about.py) | 关于<配置值> | 普通说明 |
| [src/basic_plugins/admin_bot_manage/admin_config.py](../src/basic_plugins/admin_bot_manage/admin_config.py) | 群管理员变动监测 [Hidden] | 无普通说明；Hidden |
| [src/basic_plugins/admin_bot_manage/custom_welcome_message.py](../src/basic_plugins/admin_bot_manage/custom_welcome_message.py) | 设置群欢迎消息 [Admin] | 普通说明 |
| [src/basic_plugins/admin_bot_manage/switch_rule.py](../src/basic_plugins/admin_bot_manage/switch_rule.py) | 群功能开关 [Admin] | 普通说明 + 超管说明 |
| [src/basic_plugins/admin_bot_manage/timing_task.py](../src/basic_plugins/admin_bot_manage/timing_task.py) | 定时任务相关 [Hidden] | 无普通说明；Hidden |
| [src/basic_plugins/admin_bot_manage/update_group_member_info.py](../src/basic_plugins/admin_bot_manage/update_group_member_info.py) | 更新群组成员列表 [Admin] | 普通说明 + 超管说明 |
| [src/basic_plugins/admin_help/__init__.py](../src/basic_plugins/admin_help/__init__.py) | 管理帮助 [Admin] | 普通说明 |
| [src/basic_plugins/ban/__init__.py](../src/basic_plugins/ban/__init__.py) | 拉黑用户 [Admin] | 普通说明 + 超管说明 |
| [src/basic_plugins/broadcast/__init__.py](../src/basic_plugins/broadcast/__init__.py) | 超管广播 [Superuser] | 无普通说明 + 超管说明 |
| [src/basic_plugins/check_level/__init__.py](../src/basic_plugins/check_level/__init__.py) | 群权限查看 | 普通说明 + 超管说明 |
| [src/basic_plugins/dialogue/__init__.py](../src/basic_plugins/dialogue/__init__.py) | 联系master | 普通说明 + 超管说明 |
| [src/basic_plugins/group_handle/__init__.py](../src/basic_plugins/group_handle/__init__.py) | 群事件处理 [Hidden] | 无普通说明；Hidden |
| [src/basic_plugins/group_welcome_msg.py](../src/basic_plugins/group_welcome_msg.py) | 查看群欢迎消息 | 普通说明 |
| [src/basic_plugins/help/__init__.py](../src/basic_plugins/help/__init__.py) | 帮助 [Hidden] | 无普通说明；Hidden |
| [src/basic_plugins/init_plugin_config/__init__.py](../src/basic_plugins/init_plugin_config/__init__.py) | 初始化插件数据 [Hidden] | 无普通说明；Hidden |
| [src/basic_plugins/invite_manager/__init__.py](../src/basic_plugins/invite_manager/__init__.py) | 好友群聊处理请求 [Hidden] | 普通说明；Hidden |
| [src/basic_plugins/nickname.py](../src/basic_plugins/nickname.py) | 昵称系统 | 普通说明 |
| [src/basic_plugins/random_reply/__init__.py](../src/basic_plugins/random_reply/__init__.py) | 互动 | 普通说明 |
| [src/basic_plugins/shop/buy.py](../src/basic_plugins/shop/buy.py) | 购买道具 | 普通说明 |
| [src/basic_plugins/shop/gold.py](../src/basic_plugins/shop/gold.py) | 我的金币 | 普通说明 |
| [src/basic_plugins/shop/my_props/__init__.py](../src/basic_plugins/shop/my_props/__init__.py) | 我的道具 | 普通说明 |
| [src/basic_plugins/shop/my_props.py](../src/basic_plugins/shop/my_props.py) | 我的道具 | 普通说明 |
| [src/basic_plugins/shop/shop_handle/__init__.py](../src/basic_plugins/shop/shop_handle/__init__.py) | 查看商店 | 普通说明 + 超管说明 |
| [src/basic_plugins/shop/shop_register.py](../src/basic_plugins/shop/shop_register.py) | 商品使用函数注册 [Hidden] | 无普通说明；Hidden |
| [src/basic_plugins/shop/shop_scheduler.py](../src/basic_plugins/shop/shop_scheduler.py) | 商店定时任务 [Hidden] | 无普通说明；Hidden |
| [src/basic_plugins/shop/tlmt_event.py](../src/basic_plugins/shop/tlmt_event.py) | 限时活动 | 普通说明 |
| [src/basic_plugins/shop/use/__init__.py](../src/basic_plugins/shop/use/__init__.py) | 使用道具 | 普通说明 |
| [src/basic_plugins/sign_in/__init__.py](../src/basic_plugins/sign_in/__init__.py) | 签到 | 普通说明 |
| [src/basic_plugins/super_cmd/bot_friend_group.py](../src/basic_plugins/super_cmd/bot_friend_group.py) | 好友群组管理 [Superuser] | 普通说明 |
| [src/basic_plugins/super_cmd/check/__init__.py](../src/basic_plugins/super_cmd/check/__init__.py) | 服务器自我检查 [Superuser] | 普通说明 |
| [src/basic_plugins/super_cmd/clear_data.py](../src/basic_plugins/super_cmd/clear_data.py) | 清理临时数据 [Superuser] | 普通说明 |
| [src/basic_plugins/super_cmd/exec_sql.py](../src/basic_plugins/super_cmd/exec_sql.py) | 执行sql [Superuser] | 普通说明 |
| [src/basic_plugins/super_cmd/manager_group.py](../src/basic_plugins/super_cmd/manager_group.py) | 群权限管理 [Superuser] | 普通说明 |
| [src/basic_plugins/super_cmd/reload_setting.py](../src/basic_plugins/super_cmd/reload_setting.py) | 重载插件配置 [Superuser] | 普通说明 |
| [src/basic_plugins/super_cmd/set_admin_permissions.py](../src/basic_plugins/super_cmd/set_admin_permissions.py) | 用户权限管理 [Superuser] | 普通说明 |
| [src/basic_plugins/super_cmd/update_friend_group_info.py](../src/basic_plugins/super_cmd/update_friend_group_info.py) | 更新群组好友信息 [Superuser] | 普通说明 |
| [src/basic_plugins/super_help/__init__.py](../src/basic_plugins/super_help/__init__.py) | 超级用户帮助 [Superuser] | 普通说明 |
