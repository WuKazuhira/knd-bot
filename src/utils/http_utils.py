import asyncio
from asyncio.exceptions import TimeoutError
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Union

import aiofiles
import httpx
import rich
from httpx import ConnectTimeout, Response
from nonebot.adapters.onebot.v11 import MessageSegment
from playwright.async_api import Page

from services.log import logger
from utils.user_agent import get_user_agent

from .browser import get_browser
from .message_builder import image
from .utils import get_local_proxy

# ── 全局共享 AsyncClient ──────────────────────────────────────────────────────
# 复用连接池，避免每次请求重新 TCP/TLS 握手。
# 使用懒初始化，在第一次请求时创建，之后一直复用。
_shared_client: Optional[httpx.AsyncClient] = None
_shared_client_lock = asyncio.Lock()


def _normalize_proxy(proxy: Optional[Union[str, Dict[str, str]]]) -> Optional[str]:
    if not proxy:
        return None
    if isinstance(proxy, str):
        return proxy
    return proxy.get("https://") or proxy.get("http://") or next((v for v in proxy.values() if v), None)


async def _get_shared_client(proxy: Optional[Union[str, Dict[str, str]]] = None) -> httpx.AsyncClient:
    """获取全局共享的 AsyncClient，不存在或已关闭时重新创建。"""
    global _shared_client
    # 快速路径：client 存在且未关闭
    if _shared_client is not None and not _shared_client.is_closed:
        return _shared_client
    async with _shared_client_lock:
        # 双重检查
        if _shared_client is not None and not _shared_client.is_closed:
            return _shared_client
        proxy_url = _normalize_proxy(proxy)
        client_kwargs = dict(
            limits=httpx.Limits(
                max_connections=50,
                max_keepalive_connections=20,
                keepalive_expiry=30,
            ),
            timeout=httpx.Timeout(30.0, connect=5.0),
        )
        if proxy_url:
            client_kwargs["proxy"] = proxy_url
        _shared_client = httpx.AsyncClient(**client_kwargs)
        return _shared_client


class AsyncHttpx:

    proxy = {"http://": get_local_proxy(), "https://": get_local_proxy()}

    @classmethod
    async def get(
        cls,
        url: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        cookies: Optional[Dict[str, str]] = None,
        use_proxy: bool = True,
        proxy: Dict[str, str] = None,
        timeout: Optional[int] = 30,
        retries: int = 3,
        **kwargs,
    ) -> Response:
        """
        说明：
            Get（使用共享连接池，支持 async 重试）
        参数：
            :param url: url
            :param params: params
            :param headers: 请求头
            :param cookies: cookies
            :param use_proxy: 使用默认代理
            :param proxy: 指定代理
            :param timeout: 超时时间
            :param retries: 最大重试次数（默认 3）
        """
        if not headers:
            headers = get_user_agent()
        _proxy = proxy if proxy else cls.proxy if use_proxy else None
        proxy_url = _normalize_proxy(_proxy)
        last_exc: Optional[Exception] = None
        for attempt in range(retries):
            try:
                client = await _get_shared_client(_proxy)
                return await client.get(
                    url,
                    params=params,
                    headers=headers,
                    cookies=cookies,
                    timeout=timeout,
                    **kwargs,
                )
            except (TimeoutError, ConnectTimeout) as e:
                last_exc = e
                logger.debug(f"AsyncHttpx.get 第 {attempt + 1} 次超时，url={url}")
            except httpx.RemoteProtocolError as e:
                # 共享 client 连接被服务端关闭，关闭旧 client 后重试
                last_exc = e
                logger.debug(f"AsyncHttpx.get 连接被重置，重建 client，url={url}")
                global _shared_client
                if _shared_client and not _shared_client.is_closed:
                    await _shared_client.aclose()
                _shared_client = None
            except Exception as e:
                last_exc = e
                break
        # 所有重试失败，回退到临时 client
        logger.debug(f"AsyncHttpx.get 共享 client 失败，回退临时 client，url={url}，原因：{last_exc}")
        client_kwargs = {}
        if proxy_url:
            client_kwargs["proxy"] = proxy_url
        async with httpx.AsyncClient(**client_kwargs) as client:
            return await client.get(
                url,
                params=params,
                headers=headers,
                cookies=cookies,
                timeout=timeout,
                **kwargs,
            )

    @classmethod
    async def post(
        cls,
        url: str,
        *,
        data: Optional[Dict[str, str]] = None,
        content: Any = None,
        files: Any = None,
        use_proxy: bool = True,
        proxy: Dict[str, str] = None,
        json: Optional[Dict[str, Union[Any]]] = None,
        params: Optional[Dict[str, str]] = None,
        headers: Optional[Dict[str, str]] = None,
        cookies: Optional[Dict[str, str]] = None,
        timeout: Optional[int] = 30,
        **kwargs,
    ) -> Response:
        """
        说明：
            Post
        参数：
            :param url: url
            :param data: data
            :param content: content
            :param files: files
            :param use_proxy: 是否默认代理
            :param proxy: 指定代理
            :param json: json
            :param params: params
            :param headers: 请求头
            :param cookies: cookies
            :param timeout: 超时时间
        """
        if not headers:
            headers = get_user_agent()
        proxy = proxy if proxy else cls.proxy if use_proxy else None
        proxy_url = _normalize_proxy(proxy)
        client_kwargs = {}
        if proxy_url:
            client_kwargs["proxy"] = proxy_url
        async with httpx.AsyncClient(**client_kwargs) as client:
            return await client.post(
                url,
                content=content,
                data=data,
                files=files,
                json=json,
                params=params,
                headers=headers,
                cookies=cookies,
                timeout=timeout,
                **kwargs,
            )

    @classmethod
    async def download_file(
        cls,
        url: str,
        path: Union[str, Path],
        *,
        params: Optional[Dict[str, str]] = None,
        use_proxy: bool = True,
        proxy: Dict[str, str] = None,
        headers: Optional[Dict[str, str]] = None,
        cookies: Optional[Dict[str, str]] = None,
        timeout: Optional[int] = 30,
        stream: bool = False,
        **kwargs,
    ) -> bool:
        """
        说明：
            下载文件
        参数：
            :param url: url
            :param path: 存储路径
            :param params: params
            :param use_proxy: 使用代理
            :param proxy: 指定代理
            :param headers: 请求头
            :param cookies: cookies
            :param timeout: 超时时间
            :param stream: 是否使用流式下载（流式写入+进度条，适用于下载大文件）
        """
        if isinstance(path, str):
            path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            for _ in range(3):
                if not stream:
                    try:
                        content = (
                            await cls.get(
                                url,
                                params=params,
                                headers=headers,
                                cookies=cookies,
                                use_proxy=use_proxy,
                                proxy=proxy,
                                timeout=timeout,
                                **kwargs,
                            )
                        ).content
                        async with aiofiles.open(path, "wb") as wf:
                            await wf.write(content)
                            logger.info(f"下载 {url} 成功.. Path：{path.absolute()}")
                        return True
                    except (TimeoutError, ConnectTimeout):
                        pass
                else:
                    if not headers:
                        headers = get_user_agent()
                    proxy = proxy if proxy else cls.proxy if use_proxy else None
                    proxy_url = _normalize_proxy(proxy)
                    try:
                        client_kwargs = {}
                        if proxy_url:
                            client_kwargs["proxy"] = proxy_url
                        async with httpx.AsyncClient(**client_kwargs) as client:
                            async with client.stream(
                                    "GET",
                                    url,
                                    params=params,
                                    headers=headers,
                                    cookies=cookies,
                                    timeout=timeout,
                                    **kwargs
                            ) as response:
                                logger.info(f"开始下载 {path.name}.. Path: {path.absolute()}")
                                async with aiofiles.open(path, "wb") as wf:
                                    total = int(response.headers["Content-Length"])
                                    with rich.progress.Progress(
                                            rich.progress.TextColumn(path.name),
                                            "[progress.percentage]{task.percentage:>3.0f}%",
                                            rich.progress.BarColumn(bar_width=None),
                                            rich.progress.DownloadColumn(),
                                            rich.progress.TransferSpeedColumn()
                                    ) as progress:
                                        download_task = progress.add_task("Download", total=total)
                                        async for chunk in response.aiter_bytes():
                                            await wf.write(chunk)
                                            await wf.flush()
                                            progress.update(download_task, completed=response.num_bytes_downloaded)
                                    logger.info(f"下载 {url} 成功.. Path：{path.absolute()}")
                        return True
                    except (TimeoutError, ConnectTimeout):
                        pass
            else:
                logger.error(f"下载 {url} 下载超时.. Path：{path.absolute()}")
        except Exception as e:
            logger.error(f"下载 {url} 未知错误 {type(e)}：{e}.. Path：{path.absolute()}")
        return False

    @classmethod
    async def gather_download_file(
        cls,
        url_list: List[str],
        path_list: List[Union[str, Path]],
        *,
        limit_async_number: Optional[int] = None,
        params: Optional[Dict[str, str]] = None,
        use_proxy: bool = True,
        proxy: Dict[str, str] = None,
        headers: Optional[Dict[str, str]] = None,
        cookies: Optional[Dict[str, str]] = None,
        timeout: Optional[int] = 30,
        **kwargs,
    ) -> List[bool]:
        """
        说明：
            分组同时下载文件
        参数：
            :param url_list: url列表
            :param path_list: 存储路径列表
            :param limit_async_number: 限制同时请求数量
            :param params: params
            :param use_proxy: 使用代理
            :param proxy: 指定代理
            :param headers: 请求头
            :param cookies: cookies
            :param timeout: 超时时间
        """
        if n := len(url_list) != len(path_list):
            raise UrlPathNumberNotEqual(
                f"Url数量与Path数量不对等，Url：{len(url_list)}，Path：{len(path_list)}"
            )
        if limit_async_number and n > limit_async_number:
            m = float(n) / limit_async_number
            x = 0
            j = limit_async_number
            _split_url_list = []
            _split_path_list = []
            for _ in range(int(m)):
                _split_url_list.append(url_list[x:j])
                _split_path_list.append(path_list[x:j])
                x += limit_async_number
                j += limit_async_number
            if int(m) < m:
                _split_url_list.append(url_list[j:])
                _split_path_list.append(path_list[j:])
        else:
            _split_url_list = [url_list]
            _split_path_list = [path_list]
        tasks = []
        result_ = []
        for x, y in zip(_split_url_list, _split_path_list):
            for url, path in zip(x, y):
                tasks.append(
                    asyncio.create_task(
                        cls.download_file(
                            url,
                            path,
                            params=params,
                            headers=headers,
                            cookies=cookies,
                            use_proxy=use_proxy,
                            timeout=timeout,
                            proxy=proxy,
                            ** kwargs,
                        )
                    )
                )
            _x = await asyncio.gather(*tasks)
            result_ = result_ + list(_x)
            tasks.clear()
        return result_


class AsyncPlaywright:

    @classmethod
    async def _new_page(cls, user_agent: Optional[str] = None, **kwargs) -> Page:
        """
        说明：
            获取一个新页面
        参数：
            :param user_agent: 请求头
        """
        browser = await get_browser()
        if browser:
            return await browser.new_page(user_agent=user_agent, **kwargs)
        raise BrowserIsNone("获取Browser失败...")

    @classmethod
    async def goto(
        cls,
        url: str,
        *,
        timeout: Optional[float] = 100000,
        wait_until: Optional[
            Literal["domcontentloaded", "load", "networkidle"]
        ] = "networkidle",
        referer: str = None,
        **kwargs
    ) -> Optional[Page]:
        """
        说明：
            goto
        参数：
            :param url: 网址
            :param timeout: 超时限制
            :param wait_until: 等待类型
            :param referer:
        """
        page = None
        try:
            page = await cls._new_page(**kwargs)
            await page.goto(url, timeout=timeout, wait_until=wait_until, referer=referer)
            return page
        except Exception as e:
            logger.warning(f"Playwright 访问 url：{url} 发生错误 {type(e)}：{e}")
            if page:
                await page.close()
        return None

    @classmethod
    async def screenshot(
        cls,
        url: str,
        path: Union[Path, str],
        element: Union[str, List[str]],
        *,
        wait_time: Optional[int] = None,
        viewport_size: Dict[str, int] = None,
        wait_until: Optional[
            Literal["domcontentloaded", "load", "networkidle"]
        ] = "networkidle",
        timeout: float = None,
        type_: Literal["jpeg", "png"] = None,
        **kwargs
    ) -> Optional[MessageSegment]:
        """
        说明：
            截图，该方法仅用于简单快捷截图，复杂截图请操作 page
        参数：
            :param url: 网址
            :param path: 存储路径
            :param element: 元素选择
            :param wait_time: 等待截取超时时间
            :param viewport_size: 窗口大小
            :param wait_until: 等待类型
            :param timeout: 超时限制
            :param type_: 保存类型
        """
        page = None
        if viewport_size is None:
            viewport_size = dict(width=2560, height=1080)
        if isinstance(path, str):
            path = Path(path)
        try:
            page = await cls.goto(url, wait_until=wait_until, **kwargs)
            await page.set_viewport_size(viewport_size)
            if isinstance(element, str):
                if wait_time:
                    card = await page.wait_for_selector(element, timeout=wait_time)
                else:
                    card = await page.query_selector(element)
            else:
                card = page
                for e in element:
                    if wait_time:
                        card = await card.wait_for_selector(e, timeout=wait_time)
                    else:
                        card = await card.query_selector(e)
            await card.screenshot(path=path, timeout=timeout, type=type_)
            return image(path)
        except Exception as e:
            logger.warning(f"Playwright 截图 url：{url} element：{element} 发生错误 {type(e)}：{e}")
        finally:
            if page:
                await page.close()
        return None


class UrlPathNumberNotEqual(Exception):
    pass


class BrowserIsNone(Exception):
    pass
