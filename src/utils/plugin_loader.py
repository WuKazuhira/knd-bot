import pkgutil
from pathlib import Path

import nonebot


def load_sub_plugins(pkg_name: str, pkg_file: str) -> None:
    # 容器里 cwd 不在 src，相对路径会扫空，改按包名显式加载
    nonebot.load_all_plugins(
        [
            f"{pkg_name}.{module.name}"
            for module in pkgutil.iter_modules([str(Path(pkg_file).parent)])
            if not module.name.startswith("_")
        ],
        [],
    )
