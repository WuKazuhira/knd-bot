from manager import Config
from utils.plugin_loader import load_sub_plugins

Config.add_plugin_config(
    "word_bank",
    "WORD_BANK_LEVEL [LEVEL]",
    6,
    name="词库问答",
    help_="设置增删词库的权限等级",
    default_value=6
)

load_sub_plugins(__name__, __file__)
