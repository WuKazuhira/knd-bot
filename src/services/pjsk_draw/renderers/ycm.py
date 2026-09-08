"""烧烤推车查询出图。

指令侧只负责抓取推车数据（room/des/time 列表），把数据发过来，
这里用 HTML 模板 + 浏览器截图渲染成图片。模板与静态资源在
``ycm_templates/``（``ycm.html`` / ``style.css`` / ``bg.png``）。

注意：``nonebot_plugin_htmlrender`` 依赖 nonebot 的浏览器环境，
放在函数内延迟导入，避免独立绘图进程加载渲染器模块时因缺依赖而整体失败。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

from ..registry import register

_TEMPLATE_DIR = Path(__file__).parent / "ycm_templates"
_TEMPLATE_NAME = "ycm.html"


@register("ycm")
async def render_ycm(payload: dict) -> bytes:
    """烧烤推车列表出图。载荷：cars=[{room, des, time}, ...]。"""
    from nonebot_plugin_htmlrender import html_to_pic, template_to_html

    cars: List[Dict[str, Any]] = payload.get("cars") or []
    if not cars:
        raise ValueError("没有找到车！建议检查网页结构是否变化")

    template_path = str(_TEMPLATE_DIR)
    html = await template_to_html(
        template_path=template_path,
        template_name=_TEMPLATE_NAME,
        cars=cars,
    )
    return await html_to_pic(
        html=html,
        template_path=f"file://{template_path}/{_TEMPLATE_NAME}",
        viewport={"width": 1180, "height": 300},
        wait=0,
    )
