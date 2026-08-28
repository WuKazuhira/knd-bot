from manager import Config
from utils.plugin_loader import load_sub_plugins

Config.add_plugin_config(
    "hook",
    "CHECK_NOTICE_INFO_CD",
    1800,
    name="基础hook配置",
    help_="群检测，个人权限检测等各种检测提示信息cd[单位：秒]",
    default_value=1800
)


load_sub_plugins(__name__, __file__)
