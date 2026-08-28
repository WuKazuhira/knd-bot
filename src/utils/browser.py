from typing import Optional
from playwright.async_api import Browser, async_playwright
import nonebot
from nonebot import Driver
from services.log import logger


driver: Driver = nonebot.get_driver()


_browser: Optional[Browser] = None


async def _acquire(**kwargs) -> Browser:
    """获取浏览器实例。

    容器部署时本镜像不带 Chromium（Dockerfile 设了
    PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD），浏览器跑在 chromium 边车里，
    只能通过 CDP 连接。优先复用 htmlrender 已经建立的连接，
    这样整个项目共用同一个浏览器进程；htmlrender 不可用时再自己启动。
    """
    try:
        from nonebot_plugin_htmlrender.browser import get_browser as htmlrender_browser

        return await htmlrender_browser(**kwargs)
    except Exception as e:
        logger.debug(f"htmlrender 浏览器不可用，回退本地启动：{type(e).__name__}: {e}")

    playwright = await async_playwright().start()
    return await playwright.chromium.launch(**kwargs)


async def init(**kwargs) -> Optional[Browser]:
    global _browser
    try:
        _browser = await _acquire(**kwargs)
        return _browser
    except NotImplementedError:
        logger.warning("win环境下 初始化playwright失败，相关功能将被限制....")
    except Exception as e:
        logger.warning(f"启动chromium发生错误 {type(e)}：{e}")
    _browser = None
    return None


async def get_browser(**kwargs) -> Browser:
    if _browser and _browser.is_connected():
        return _browser
    return await init(**kwargs)


# @driver.on_startup
def install():
    """自动安装、更新 Chromium"""
    logger.info("正在检查 Chromium 更新")
    import sys
    from playwright.__main__ import main

    sys.argv = ["", "install", "chromium"]
    try:
        main()
    except SystemExit:
        pass
