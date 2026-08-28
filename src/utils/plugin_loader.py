"""嵌套子插件加载辅助。"""

import pkgutil
from pathlib import Path

import nonebot


def load_sub_plugins(pkg_name: str, pkg_file: str) -> None:
    """加载包内全部子插件（以 `_` 开头的模块除外）。

    nonebot.load_plugins 的目录搜索基于进程工作目录（容器内为 /app），
    不存在 plugins/、basic_plugins/ 相对目录时会静默搜到空。这里改为
    枚举包内子模块并按父包的绝对模块名显式加载，确保父子插件始终使用
    plugins.*、basic_plugins.* 这一套命名空间，避免同一文件被重复导入。
    """
    nonebot.load_all_plugins(
        [
            f"{pkg_name}.{module.name}"
            for module in pkgutil.iter_modules([str(Path(pkg_file).parent)])
            if not module.name.startswith("_")
        ],
        [],
    )
