import os

if os.getenv("KNDBOT_SKIP_PJSK_PLUGIN_AUTOLOAD") != "1":
    from utils.plugin_loader import load_sub_plugins

    load_sub_plugins(__name__, __file__)
