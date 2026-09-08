"""绘图服务的数据接入点。

绘图服务只负责「数据进、图片出」，本身不下载资源、不读主数据，
这些能力由 pjsk 插件在加载时通过 :func:`set_context` 注入。
这样 services 层不必反向 import plugins 层，也方便日后把绘图搬到独立进程。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, Optional

from PIL import Image


@dataclass(frozen=True)
class PjskDrawContext:
    """绘图服务运行所需的外部能力集合。"""

    # 资源（图片）获取：对应 pjsk_update_manager.get_asset
    get_asset: Callable[..., Awaitable[Optional[Image.Image]]]
    # 资源批量下载：对应 pjsk_update_manager.update_assets
    update_assets: Callable[..., Awaitable[Any]]
    # 主数据读取
    load_master_data: Callable[..., Any]
    async_load_master_data: Callable[..., Awaitable[Any]]
    master_data_by_id: Callable[..., Dict[Any, Any]]
    # pjsk_type -> 服务器代号（jp/tw/cn）
    server_map: Dict[int, str]
    # 卡面类型、fes 判定与角色名，卡牌绘制需要
    cardtype: Callable[..., Any]
    is_fes_card: Callable[..., Any]
    getcharaname: Callable[..., Any]

    def server_name(self, pjsk_type: int) -> str:
        return self.server_map.get(pjsk_type, "jp")


_context: Optional[PjskDrawContext] = None


def set_context(context: PjskDrawContext) -> None:
    global _context
    _context = context


def get_context() -> PjskDrawContext:
    if _context is None:
        raise RuntimeError("pjsk 绘图服务尚未注入数据上下文（services.pjsk_draw.set_context）")
    return _context


def has_context() -> bool:
    return _context is not None
