from pathlib import Path
from typing import Optional

from config.path_config import DATA_PATH
from manager import Config


def resolve_what2eat_path(configured_path: Optional[str]) -> Path:
    """解析资源目录；配置路径不可访问时回退到应用数据目录。"""
    default_path = DATA_PATH / "what2eat"
    if not configured_path:
        return default_path

    candidate = Path(configured_path).expanduser()
    return candidate if candidate.exists() else default_path


config = {
    "what2eat_path": str(
        resolve_what2eat_path(Config.get_config("what2eat", "WHAT2EAT_PATH"))
    ),
    "superusers": Config.get_config("what2eat", "SUPERUSERS"),
    "eating_limit": Config.get_config("what2eat", "EATING_LIMIT"),
}
