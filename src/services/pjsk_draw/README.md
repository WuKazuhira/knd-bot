# pjsk 绘图服务

pjsk 所有指令的「出图」都收在这里。指令侧只负责取数据，拿到数据后按
**任务名 + JSON 载荷** 调用本服务，收到图片字节直接发出去。

```python
from services.pjsk_draw import render

png = await render("pjskinfo", {"music_id": 74, "pjsk_type": 0})
await matcher.finish(image(png))
```

## 两种运行方式

`render()` 会看配置决定走哪条路，指令侧代码完全一样：

| 配置 | 行为 |
| --- | --- |
| `draw.service_urls` 留空（默认） | 在 bot 进程内执行同一个注册表里的渲染器 |
| 配了地址 | HTTP `POST {url}/render/{name}`，多地址轮转；失败按 `fallback_local` 回退进程内 |

配置在 `config/pjsk/settings.yaml` 的 `draw` 段，或环境变量
`PJSK_DRAW_SERVICE_URLS` / `PJSK_DRAW_SERVICE_TIMEOUT` / `PJSK_DRAW_SERVICE_FALLBACK_LOCAL`。

独立进程：

```bash
python -m services.pjsk_draw.serve            # 默认 0.0.0.0:45560
# 或 uvicorn services.pjsk_draw.serve:app --port 45560
```

对外接口：

- `POST /render/{name}` — JSON 载荷进，图片出
- `GET /renderers` — 可用任务名
- `GET /health`

独立进程与 bot 共享 `data/pjsk` 目录（只读资源与主数据，不负责下载），
所以要挂同一个 volume。

## 返回约定

渲染器可以返回三种形态，`registry.dispatch` 统一归一化成 `(图片列表, meta)`：

| 渲染器返回 | HTTP 响应 | 客户端取用 |
| --- | --- | --- |
| `bytes` | 原始图片字节 | `render()` |
| `[bytes, ...]` | JSON 信封 `{"images": [b64...]}` | `render_multi()` |
| `{"images": [...], "meta": {...}}` | JSON 信封（含 meta） | `render_with_meta()` |

多图用在猜曲这类「题面 + 答案由同一次随机裁剪产出」的场景；
meta 用在谱面预览这类需要顺带告知图源的场景。

## 数据接入（context）

服务本身不 import `plugins` 层。资源下载与主数据读取通过
`context.PjskDrawContext` 注入：

- bot 进程内：`plugins/pjsk/_draw_context.py` 在插件加载时注入
  （可自动补下载缺失资源）
- 独立进程：`local_data.install_local_context()` 注入只读实现
  （缺资源就降级，不下载）

未注入就调用渲染器会抛出明确的 `RuntimeError`。

## 目录

```
context.py          数据/资源接入点（协议 + 注册表）
config.py           服务地址、超时等配置
registry.py         任务名 -> 渲染器
client.py           render / render_multi / render_with_meta
serve.py            独立进程 FastAPI 入口
local_data.py       独立进程的只读数据实现
primitives.py       字体、图片缓存、渐变、编码等绘图原语
card.py             卡牌缩略图 / 大图 / 分组概览（渲染器内部复用）
honor.py            牌子绘制
profile_header.py   各出图共用的玩家信息 Header
profile_bg.py       个人信息图的自定义背景存储
event_data.py       活动出图用到的主数据解析
chart/              谱面 SVG 生成
renderers/          各指令的渲染器，导入即注册
```

新增一个出图任务：在 `renderers/` 下写好函数、用 `@register("任务名")` 标注，
再把模块名加进 `renderers/__init__.py` 的 `_MODULES`。
