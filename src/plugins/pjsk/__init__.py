import os

if os.getenv("KNDBOT_SKIP_PJSK_PLUGIN_AUTOLOAD") != "1":
    from utils.plugin_loader import load_sub_plugins

    from ._draw_context import install_draw_context

    # 指令模块加载前先给绘图服务注入数据上下文。
    install_draw_context()
    load_sub_plugins(__name__, __file__)
