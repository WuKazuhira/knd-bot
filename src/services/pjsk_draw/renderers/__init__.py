"""各 pjsk 指令的渲染器实现。

导入模块即完成注册（模块内用 @register("任务名") 声明）。
"""

from __future__ import annotations

from importlib import import_module

# 新增渲染器模块后在此登记
_MODULES = (
    "b30",
    "cardbox",
    "cardinfo",
    "deck",
    "diffrank",
    "event",
    "findcard",
    "gacha",
    "guess",
    "mappreview",
    "notify",
    "mysekai",
    "profile",
    "rop",
    "sk",
    "sk_me_curve",
    "song",
)

_loaded = False


def load_all() -> None:
    global _loaded
    if _loaded:
        return
    for name in _MODULES:
        import_module(f"{__name__}.{name}")
    _loaded = True
