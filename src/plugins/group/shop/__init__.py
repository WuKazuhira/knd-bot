from manager import Config
from utils.plugin_loader import load_sub_plugins

Config.add_plugin_config(
    "shop",
    "IMPORT_DEFAULT_SHOP_GOODS",
    True,
    help_="导入商店自带的六个商品",
    default_value=True
)


load_sub_plugins(__name__, __file__)
