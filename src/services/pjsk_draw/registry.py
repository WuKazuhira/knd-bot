"""绘图任务注册表。

绘图服务对外只有一个概念：**渲染任务名 + JSON 载荷 -> 图片字节**。
每个 pjsk 指令对应一个（或几个）任务名，指令侧只管把收集好的数据发过来。
个别任务（如猜曲的题面 + 答案）需要一次返回多张图，渲染器返回 bytes 列表即可。
"""

from __future__ import annotations

import inspect
from typing import Any, Awaitable, Callable, Dict, List, Sequence, Tuple, Union

# 渲染器签名：接收 JSON 可序列化的载荷，返回
#   bytes                                  单张图
#   Sequence[bytes]                        多张图
#   {"images": [...], "meta": {...}}       多张图 + 附带元信息（如谱面图源）
RenderResult = Union[bytes, Sequence[bytes], Dict[str, Any]]
Renderer = Callable[[Dict[str, Any]], Union[RenderResult, Awaitable[RenderResult]]]

_RENDERERS: Dict[str, Renderer] = {}


def register(name: str) -> Callable[[Renderer], Renderer]:
    """把一个渲染函数登记到任务名上。"""

    def decorator(func: Renderer) -> Renderer:
        if name in _RENDERERS:
            raise ValueError(f"绘图任务名重复注册: {name}")
        _RENDERERS[name] = func
        return func

    return decorator


def get_renderer(name: str) -> Renderer:
    renderer = _RENDERERS.get(name)
    if renderer is None:
        raise KeyError(f"未知的绘图任务: {name}")
    return renderer


def renderer_names() -> List[str]:
    return sorted(_RENDERERS)


def _coerce_images(name: str, value: Any) -> List[bytes]:
    if isinstance(value, (bytes, bytearray)):
        return [bytes(value)]
    if isinstance(value, Sequence):
        images = []
        for item in value:
            if not isinstance(item, (bytes, bytearray)):
                raise TypeError(f"绘图任务 {name} 返回的列表里出现了 {type(item).__name__}")
            images.append(bytes(item))
        return images
    raise TypeError(f"绘图任务 {name} 返回了 {type(value).__name__}，应为 bytes 或 bytes 列表")


async def dispatch(name: str, payload: Dict[str, Any]) -> Tuple[List[bytes], Dict[str, Any]]:
    """在当前进程内执行渲染任务，返回 (图片字节列表, 元信息)。"""
    renderer = get_renderer(name)
    result = renderer(payload or {})
    if inspect.isawaitable(result):
        result = await result
    if isinstance(result, dict):
        images = _coerce_images(name, result.get("images") or [])
        meta = result.get("meta") or {}
        if not isinstance(meta, dict):
            raise TypeError(f"绘图任务 {name} 的 meta 必须是 dict")
        return images, meta
    return _coerce_images(name, result), {}


def load_all_renderers() -> None:
    """导入全部渲染器模块，完成注册（幂等）。"""
    from . import renderers  # noqa: F401  导入即注册

    renderers.load_all()
