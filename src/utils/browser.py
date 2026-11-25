import asyncio
from typing import Optional

import nonebot
from nonebot import Driver
from playwright.async_api import Browser, Playwright, async_playwright

from services.log import logger

driver: Driver = nonebot.get_driver()


_browser: Optional[Browser] = None
_playwright: Optional[Playwright] = None
_browser_lock = asyncio.Lock()


async def _close_local_browser():
    global _browser, _playwright
    browser, playwright = _browser, _playwright
    _browser = _playwright = None
    if playwright is not None:
        try:
            if browser is not None and browser.is_connected():
                await browser.close()
        finally:
            await playwright.stop()


@driver.on_shutdown
async def _shutdown_browser():
    async with _browser_lock:
        await _close_local_browser()


async def _acquire(**kwargs) -> Browser:
    # 优先复用 htmlrender 的浏览器（容器里靠它连 chromium），不行再本地拉起
    try:
        from nonebot_plugin_htmlrender.browser import get_browser as htmlrender_browser

        return await htmlrender_browser(**kwargs)
    except Exception as e:
        logger.debug(f"htmlrender 浏览器不可用，回退本地启动：{type(e).__name__}: {e}")

    global _playwright
    playwright = await async_playwright().start()
    try:
        browser = await playwright.chromium.launch(**kwargs)
    except BaseException:
        await playwright.stop()
        raise
    _playwright = playwright
    return browser


async def init(**kwargs) -> Optional[Browser]:
    global _browser
    async with _browser_lock:
        if _browser and _browser.is_connected():
            return _browser
        try:
            await _close_local_browser()
            _browser = await _acquire(**kwargs)
            return _browser
        except NotImplementedError:
            logger.warning("win环境下 初始化playwright失败，相关功能将被限制....")
        except Exception as e:
            logger.warning(f"启动chromium发生错误 {type(e)}：{e}")
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
