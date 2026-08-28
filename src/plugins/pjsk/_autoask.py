import asyncio
import hashlib
import os
import tempfile
import time
from pathlib import Path
from typing import Optional, Union

import httpx
import requests
import yaml
from PIL import Image
from zhconv import convert

from services import logger
from utils.user_agent import get_user_agent

if os.getenv("KNDBOT_SKIP_PJSK_PLUGIN_AUTOLOAD") == "1":
    class _NoopScheduler:
        def scheduled_job(self, *args, **kwargs):
            def decorator(func):
                return func
            return decorator

    scheduler = _NoopScheduler()
    AsyncHttpx = None
else:
    from utils.http_utils import AsyncHttpx
    from utils.utils import scheduler

from ._config import (
    DEFAULT_MASTERDATA_DOWNLOAD_TIMEOUT,
    DEFAULT_MASTERDATA_UPDATE_CHECK_TIMEOUT,
    MUSIC_METAS_BASE_URL,
    SERVER_CONFIG,
    SERVER_MAP,
    data_path,
    lab_headers,
)


def _iter_masterdata_urls(base_url: str, raw: str):
    url = base_url + raw
    ghfast_prefix = "https://ghfast.top/https://"
    if url.startswith(ghfast_prefix):
        yield url[len("https://ghfast.top/"):]
    yield url


_RIP_ONDEMAND_PREFIXES = ("event", "gacha", "music/long", "mysekai", "virtual_live")
_RIP_STARTAPP_PREFIXES = (
    "bonds_honor", "honor", "thumbnail", "character", "music", "rank_live",
    "stamp", "home/banner", "player_frame", "areaitem",
)


def _iter_rip_asset_urls(source: dict, path: str, raw: str):
    """按 Nanami-Bot 的资源映射规则生成候选下载地址。"""
    base_url = str(source["base_url"]).rstrip("/") + "/"
    rel_path = f"{path.strip('/')}/{raw.lstrip('/')}".replace("_rip", "")
    name = str(source.get("name", ""))
    urls = []
    if name == "haruki":
        if rel_path.startswith(_RIP_ONDEMAND_PREFIXES):
            urls.append(base_url + "ondemand/" + rel_path)
        elif rel_path.startswith(_RIP_STARTAPP_PREFIXES):
            urls.append(base_url + "startapp/" + rel_path)
    elif name == "sekai.best":
        urls.append(base_url + rel_path)
    urls.append(base_url + f"{path.strip('/')}/{raw.lstrip('/')}")
    yield from dict.fromkeys(urls)




def _atomic_write(path: Path, data: bytes) -> None:
    """在目标目录内写临时文件，再原子替换目标文件。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}.", delete=False) as temp_file:
            temp_path = Path(temp_file.name)
            temp_file.write(data)
            temp_file.flush()
            os.fsync(temp_file.fileno())
        os.replace(temp_path, path)
    except OSError:
        if temp_path is not None:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass
        raise


def _write_if_changed(path: Path, data: bytes) -> bool:
    try:
        old_data = path.read_bytes()
    except FileNotFoundError:
        old_data = None
    if old_data is not None and hashlib.md5(old_data).digest() == hashlib.md5(data).digest():
        return False
    _atomic_write(path, data)
    return True


def _masterdata_target(filepath: Path) -> Optional[tuple[str, int]]:
    """把 masterdata 文件路径还原成 (文件名, pjsk_type)，不是 masterdata 就返回 None。

    目录形如 <data_path>/<server>/xxx.json，部分文件在 <server>/realtime/ 下。
    """
    if filepath.suffix != ".json":
        return None
    parent = filepath.parent
    if parent.name == "realtime":
        parent = parent.parent
    if parent.parent != data_path:
        return None
    pjsk_type = next((k for k, v in SERVER_MAP.items() if v == parent.name), None)
    if pjsk_type is None:
        return None
    return filepath.name, pjsk_type


async def _rewarm_master_data(filepath: Path) -> None:
    """masterdata 落盘后立刻重建内存缓存，别把大 JSON 的解析留给下一条用户指令。"""
    target = _masterdata_target(filepath)
    if not target:
        return
    filename, pjsk_type = target
    try:
        # _utils 在模块级 import 了本模块，这里只能延迟导入。
        from ._utils import refresh_master_data_cache

        await refresh_master_data_cache(filename, pjsk_type)
    except Exception as e:
        logger.debug(f"重建 MasterData 缓存失败 {filepath}: {e}")


class PjskDataUpdate:
    def __init__(self, path: Union[str, Path]):
        if isinstance(path, str):
            self.path = Path(path)
        else:
            self.path = path
        if not self.path.exists():
            self.path.mkdir(parents=True, exist_ok=True)
        self.download_semaphore = asyncio.Semaphore(10)
        self._file_locks: dict[Path, asyncio.Lock] = {}
        self._lock_creation_lock = asyncio.Lock()

    async def get_file_lock(self, path: Path):
        async with self._lock_creation_lock:
            if path not in self._file_locks:
                self._file_locks[path] = asyncio.Lock()
            return self._file_locks[path]

    async def _get_response(self, url: str, headers=None, block: bool = False):
        async with self.download_semaphore:
            if block:
                return await asyncio.to_thread(
                    requests.get,
                    url,
                    headers=headers,
                    timeout=DEFAULT_MASTERDATA_DOWNLOAD_TIMEOUT,
                )
            return await AsyncHttpx.get(
                url,
                headers=headers,
                timeout=DEFAULT_MASTERDATA_DOWNLOAD_TIMEOUT,
            )

    async def _store_download(self, filepath: Path, data: bytes, raw: str, server_name: str = "") -> None:
        prefix = f"[{server_name}] " if server_name else ""
        existed = filepath.exists()
        if await asyncio.to_thread(_write_if_changed, filepath, data):
            logger.info(f'{prefix}{"更新" if existed else "初次创建"}{raw}')
            await _rewarm_master_data(filepath)
        else:
            logger.info(f'{prefix}无需更新{raw}')

    async def update_music_lyrics(self, pjsk_type: int = 0, block: bool = False):
        # 歌词下载源（watagashi-uni/Unibot 及 raw.fastgit.org）已不可用，暂时停用。
        # 原逻辑：
        #   1. 从 GitHub API 获取 watagashi-uni/Unibot 仓库中的歌词文件列表
        #   2. 通过 raw.fastgit.org 逐个下载缺失的 .txt 歌词文件
        # 待有可用源后再恢复。
        return

    async def update_music_data(self, raw: str, pjsk_type: int = 0, block: bool = False):
        server_name = SERVER_MAP.get(pjsk_type, 'jp')
        sources = SERVER_CONFIG.get(server_name, {}).get('masterdata', {}).get('sources', [])
        filepath = data_path / server_name / raw
        lock = await self.get_file_lock(filepath)
        async with lock:
            for source in sources:
                for url in _iter_masterdata_urls(source['base_url'], raw):
                    try:
                        resp = await self._get_response(url, headers=lab_headers, block=block)
                    except (requests.RequestException, httpx.HTTPError, asyncio.TimeoutError) as e:
                        logger.warning(f'[{server_name}] {raw} 从 {source["name"]} 下载失败: {e}: {url}')
                        continue
                    if resp.status_code != 200:
                        logger.warning(f'[{server_name}] {raw} 从 {source["name"]} 下载返回状态码 {resp.status_code}: {url}')
                        continue
                    logger.info(f'[{server_name}] {raw}下载成功 (from {source["name"]}: {url})')
                    try:
                        await self._store_download(filepath, resp.content, raw, server_name)
                    except OSError as e:
                        logger.warning(f'[{server_name}] {raw}写入失败: {e}')
                    return
        logger.warning(f"[{server_name}] {raw}所有源下载均失败")

    def sync_update_music_data(self, raw: str, pjsk_type: int = 0):
        """同步更新MasterData数据，参考 update_server_game_data 模式（带MD5校验和多源支持）"""
        server_name = SERVER_MAP.get(pjsk_type, 'jp')
        sources = SERVER_CONFIG.get(server_name, {}).get('masterdata', {}).get('sources', [])
        
        for source in sources:
            for url in _iter_masterdata_urls(source['base_url'], raw):
                try:
                    resp = requests.get(url, headers=lab_headers, timeout=DEFAULT_MASTERDATA_DOWNLOAD_TIMEOUT)
                    if resp.status_code == 200:
                        jsondata = resp.content
                        logger.info(f'[{server_name}] {raw} 同步下载成功 (from {source["name"]}: {url})')
                        
                        filepath = data_path / server_name / raw
                        existed = filepath.exists()
                        if _write_if_changed(filepath, jsondata):
                            logger.info(f'[{server_name}] {"更新" if existed else "初次创建"}{raw}')
                        else:
                            logger.info(f'[{server_name}] {raw} 内容未变化，无需更新')
                        return  # 只要有一个源成功就返回
                    logger.warning(f"[{server_name}] 从 {source['name']} 同步下载 {raw} 返回状态码 {resp.status_code}: {url}")
                except (requests.RequestException, OSError) as e:
                    logger.warning(f"[{server_name}] 从 {source['name']} 同步下载 {raw} 失败: {e}: {url}")
                    continue
        
        logger.warning(f"[{server_name}] {raw} 所有源同步下载均失败")

    async def update_music_meta_data(self, raw: str, block: bool = False):
        if not MUSIC_METAS_BASE_URL:
            logger.warning("未配置 endpoints.music_metas_base_url，跳过音乐元数据更新")
            return
        url = f"{MUSIC_METAS_BASE_URL}/{raw}"
        filepath = self.path / 'realtime' / raw
        lock = await self.get_file_lock(filepath)
        async with lock:
            try:
                resp = await self._get_response(url, headers=lab_headers, block=block)
                resp.raise_for_status()
                logger.info(f'{raw}下载成功')
                await self._store_download(filepath, resp.content, raw)
            except (requests.RequestException, httpx.HTTPError, asyncio.TimeoutError, OSError) as e:
                logger.warning(f'{raw}下载失败, 错误原因:{e}')

    async def update_translate_data(self, raw: str, pjsk_type: int = 0, block: bool = False):
        server_name = SERVER_MAP.get(pjsk_type, 'jp')
        filepath = data_path / server_name / 'translate.yaml'
        lock = await self.get_file_lock(filepath)
        async with lock:
            try:
                if filepath.exists():
                    translation = yaml.load(filepath.read_text(encoding='utf-8'), Loader=yaml.FullLoader) or {}
                else:
                    translation = {}
                    logger.info(f'[{server_name}] 首次创建{raw}翻译')
            except (OSError, yaml.YAMLError) as e:
                logger.warning(f'[{server_name}] 读取翻译文件失败，错误原因:{e}')
                return

            translation.setdefault(raw, {})
            url = f'https://raw.fastgit.org/Sekai-World/sekai-i18n/main/zh-TW/{raw}.json'
            try:
                resp = await self._get_response(url, block=block)
                resp.raise_for_status()
                data = resp.json()
                logger.info(f'[{server_name}] {raw}翻译下载成功')
            except (requests.RequestException, httpx.HTTPError, asyncio.TimeoutError, ValueError) as e:
                logger.warning(f'[{server_name}] {raw}翻译下载失败，错误原因:{e}')
                return

            for i in data:
                try:
                    translation[raw][int(i)]
                except (KeyError, ValueError, TypeError):
                    zhhan = convert(data[i], 'zh-cn')
                    translation[raw][int(i)] = zhhan
                    logger.info(f'[{server_name}] 更新翻译{raw} {i} {zhhan}')

            try:
                yaml_data = yaml.dump(translation, allow_unicode=True).encode('utf-8')
                await asyncio.to_thread(_atomic_write, filepath, yaml_data)
            except (OSError, yaml.YAMLError) as e:
                logger.warning(f'[{server_name}] 写入翻译文件失败，错误原因:{e}')

    async def update_server_game_data(self, raw: str, pjsk_type: int = 0, block: bool = False):
        server_name = SERVER_MAP.get(pjsk_type, 'jp')
        sources = SERVER_CONFIG.get(server_name, {}).get('masterdata', {}).get('sources', [])
        filepath = data_path / server_name / raw
        lock = await self.get_file_lock(filepath)
        async with lock:
            for source in sources:
                for url in _iter_masterdata_urls(source['base_url'], raw):
                    try:
                        resp = await self._get_response(url, headers=lab_headers, block=block)
                    except (requests.RequestException, httpx.HTTPError, asyncio.TimeoutError) as e:
                        logger.warning(f'[{server_name}] {raw} 从 {source["name"]} 下载失败, 错误原因:{e}: {url}')
                        continue
                    if resp.status_code != 200:
                        logger.warning(f'[{server_name}] {raw} 从 {source["name"]} 下载返回状态码 {resp.status_code}: {url}')
                        continue
                    logger.info(f'[{server_name}] {raw}下载成功 (from {source["name"]}: {url})')
                    try:
                        await self._store_download(filepath, resp.content, raw, server_name)
                    except OSError as e:
                        logger.warning(f'[{server_name}] {raw}写入失败, 错误原因:{e}')
                    return  # 只要有一个源成功就退出

    async def update_server_assets(self, path: str, raw: str, pjsk_type: int = 0, block: bool = False):
        server_name = SERVER_MAP.get(pjsk_type, 'jp')
        path = path.replace('\\', '/')
        raw = raw.replace('\\', '/')
        
        sources = SERVER_CONFIG.get(server_name, {}).get('rip', {}).get('sources', [])
        
        filepath = data_path / server_name / path
        if not filepath.exists():
            filepath.mkdir(parents=True, exist_ok=True)
        filepath = filepath / raw
        
        if not filepath.exists():
            for source in sources:
                # 检查是否有前缀限制
                prefixes = source.get('prefixes', [])
                if prefixes:
                    match = False
                    for p in prefixes:
                        if path.startswith(p):
                            match = True
                            break
                    if not match:
                        continue
                
                for url in _iter_rip_asset_urls(source, path, raw):
                    if await self._download_file(url, path=filepath, block=block):
                        logger.info(
                            f'[{server_name}] {path}/{raw}下载成功 '
                            f'(from {source["name"]}: {url})'
                        )
                        return

    # 为兼容性保留此名称
    async def update_assets(self, path: str, raw: str, pjsk_type: int = 0, block: bool = False):
        await self.update_server_assets(path, raw, pjsk_type=pjsk_type, block=block)

    async def _download_file(self, url: str, path: Path, headers=None, block: bool = False):
        if headers is None:
            headers = get_user_agent()
        lock = await self.get_file_lock(path)
        async with lock:
            # block 和 non-block 共用路径锁、信号量、超时与原子写路径。
            if path.exists():
                return True
            try:
                resp = await self._get_response(url, headers=headers, block=block)
                if resp.status_code != 200:
                    return False
                await asyncio.to_thread(_atomic_write, path, resp.content)
                return True
            except (requests.RequestException, httpx.HTTPError, asyncio.TimeoutError, OSError) as e:
                logger.debug(f'下载失败: {url}, 错误信息：{e}')
                return False

    async def get_asset(self, path: str, raw: str, pjsk_type: int = 0, block: bool = False, download: bool = True) -> Optional[Image.Image]:
        server_name = SERVER_MAP.get(pjsk_type, 'jp')
        asset_path = data_path / server_name / path / raw
        if not asset_path.exists() and download:
            logger.warning(f'[{server_name}] 缺失资源{path}/{raw}，尝试下载此资源中...')
            await self.update_server_assets(path, raw, pjsk_type=pjsk_type, block=block)
        try:
            if raw.endswith('.png') or raw.endswith('.jpg') or raw.endswith('.jpeg'):
                pic = Image.open(asset_path)
                return pic
        except FileNotFoundError:
            logger.warning(f'[{server_name}] 找不到资源{path}/{raw}')
            return None
        except Exception as e:
            logger.warning(f'[{server_name}] 资源调用失败，错误信息：{e}')
            return None

pjsk_update_manager = PjskDataUpdate(data_path)


# 分组自动更新烧烤数据
async def check_event_resources(block: bool = False, iswait: bool = True, pjsk_type: Optional[int] = None):
    logger.info("[定时任务]:开始自动更新pjsk游戏数据！（活动）")
    st = time.time()
    wait_time = 5 if iswait else 0
    pjsk_types = [pjsk_type] if pjsk_type is not None else list(SERVER_MAP.keys())
    for p_type in pjsk_types:
        await pjsk_update_manager.update_server_game_data('events.json', p_type, block=block)
        await asyncio.sleep(wait_time)
        await pjsk_update_manager.update_server_game_data('worldBlooms.json', p_type, block=block)
        await asyncio.sleep(wait_time)
        await pjsk_update_manager.update_server_game_data('rankMatchSeasons.json', p_type, block=block)
        await asyncio.sleep(wait_time)
        await pjsk_update_manager.update_server_game_data('cheerfulCarnivalTeams.json', p_type, block=block)
        await asyncio.sleep(wait_time)
        await pjsk_update_manager.update_server_game_data('bondsHonors.json', p_type, block=block)
    logger.info(f"[定时任务]:pjsk游戏数据更新完毕,耗时{int(time.time() - st)}秒！")

@scheduler.scheduled_job('cron', minute=3)
async def _check_event_resources_task():
    await check_event_resources()

# 其他资源更新任务类似重构...
async def check_eventinfo_resources(block: bool = False, iswait: bool = True, pjsk_type: Optional[int] = None):
    logger.info("[定时任务]:开始自动更新pjsk游戏数据！（活动查询）")
    st = time.time()
    wait_time = 5 if iswait else 0
    pjsk_types = [pjsk_type] if pjsk_type is not None else list(SERVER_MAP.keys())
    for p_type in pjsk_types:
        await pjsk_update_manager.update_server_game_data('eventCards.json', p_type, block=block)
        await asyncio.sleep(wait_time)
        await pjsk_update_manager.update_server_game_data('eventDeckBonuses.json', p_type, block=block)
        await asyncio.sleep(wait_time)
        await pjsk_update_manager.update_server_game_data('gameCharacterUnits.json', p_type, block=block)
    logger.info(f"[定时任务]:pjsk游戏数据更新完毕,耗时{int(time.time() - st)}秒！")

@scheduler.scheduled_job('cron', minute=4)
async def _check_eventinfo_resources_task():
    await check_eventinfo_resources()

async def check_cards_resources(block: bool = False, iswait: bool = True, pjsk_type: Optional[int] = None):
    logger.info("[定时任务]:开始自动更新pjsk游戏数据！（卡面查询）")
    st = time.time()
    wait_time = 5 if iswait else 0
    pjsk_types = [pjsk_type] if pjsk_type is not None else list(SERVER_MAP.keys())
    for p_type in pjsk_types:
        await pjsk_update_manager.update_server_game_data('cardCostume3ds.json', p_type, block=block)
        await asyncio.sleep(wait_time)
        await pjsk_update_manager.update_server_game_data('costume3ds.json', p_type, block=block)
        await asyncio.sleep(wait_time)
        await pjsk_update_manager.update_server_game_data('gameCharacters.json', p_type, block=block)
        await asyncio.sleep(wait_time)
        await pjsk_update_manager.update_server_game_data('cards.json', p_type, block=block)
        await asyncio.sleep(wait_time)
        await pjsk_update_manager.update_server_game_data('cardEpisodes.json', p_type, block=block)
    logger.info(f"[定时任务]:pjsk游戏数据更新完毕,耗时{int(time.time() - st)}秒！")

@scheduler.scheduled_job('cron', minute=2)
async def _check_cards_resources_task():
    await check_cards_resources()

async def check_profile_resources(block: bool = False, iswait: bool = True, pjsk_type: Optional[int] = None):
    logger.info("[定时任务]:开始自动更新pjsk游戏数据！（档案）")
    st = time.time()
    wait_time = 5 if iswait else 0
    pjsk_types = [pjsk_type] if pjsk_type is not None else list(SERVER_MAP.keys())
    for p_type in pjsk_types:
        await pjsk_update_manager.update_server_game_data('honors.json', p_type, block=block)
        await asyncio.sleep(wait_time)
        await pjsk_update_manager.update_server_game_data('honorGroups.json', p_type, block=block)
    logger.info(f"[定时任务]:pjsk游戏数据更新完毕,耗时{int(time.time() - st)}秒！")

@scheduler.scheduled_job('cron', minute=1)
async def _check_profile_resources_task():
    await check_profile_resources()

async def check_pjskinfo_resources(block: bool = False, iswait: bool = True, pjsk_type: Optional[int] = None):
    logger.info("[定时任务]:开始自动更新pjsk游戏数据！（谱面&歌词）")
    st = time.time()
    wait_time = 5 if iswait else 0
    pjsk_types = [pjsk_type] if pjsk_type is not None else list(SERVER_MAP.keys())
    for p_type in pjsk_types:
        await pjsk_update_manager.update_server_game_data('musicVocals.json', p_type, block=block)
        await asyncio.sleep(wait_time)
        await pjsk_update_manager.update_server_game_data('outsideCharacters.json', p_type, block=block)
        await asyncio.sleep(wait_time)
        await pjsk_update_manager.update_music_lyrics(p_type, block=block)
    logger.info(f"[定时任务]:pjsk游戏数据更新完毕,耗时{int(time.time() - st)}秒！")

@scheduler.scheduled_job('cron', minute=2)
async def _check_pjskinfo_resources_task():
    await check_pjskinfo_resources()

async def check_songs_resources(block: bool = False, iswait: bool = True, pjsk_type: Optional[int] = None):
    logger.info("[定时任务]:开始自动更新pjsk游戏数据！（歌曲）")
    st = time.time()
    wait_time = 5 if iswait else 0
    pjsk_types = [pjsk_type] if pjsk_type is not None else list(SERVER_MAP.keys())
    # music_metas 来源和存储位置均不区分服务器，每轮只更新一次。
    await pjsk_update_manager.update_music_meta_data('music_metas.json', block=block)
    for p_type in pjsk_types:
        await pjsk_update_manager.update_server_game_data('musicDifficulties.json', p_type, block=block)
        await asyncio.sleep(wait_time)
        await pjsk_update_manager.update_server_game_data('musics.json', p_type, block=block)
    logger.info(f"[定时任务]:pjsk游戏数据更新完毕,耗时{int(time.time() - st)}秒！")

@scheduler.scheduled_job('cron', minute=5)
async def _check_songs_resources_task():
    await check_songs_resources()

async def check_trans_resources(block: bool = False, iswait: bool = True, pjsk_type: Optional[int] = None):
    logger.info("[定时任务]:开始自动更新pjsk游戏数据！（翻译）")
    st = time.time()
    wait_time = 5 if iswait else 0
    pjsk_types = [pjsk_type] if pjsk_type is not None else list(SERVER_MAP.keys())
    for p_type in pjsk_types:
        await pjsk_update_manager.update_translate_data('music_titles', p_type, block=block)
        await asyncio.sleep(wait_time)
        await pjsk_update_manager.update_translate_data('event_name', p_type, block=block)
        await asyncio.sleep(wait_time)
        await pjsk_update_manager.update_translate_data('card_prefix', p_type, block=block)
        await asyncio.sleep(wait_time)
        await pjsk_update_manager.update_translate_data('cheerful_carnival_teams', p_type, block=block)
    logger.info(f"[定时任务]:pjsk游戏数据更新完毕,耗时{int(time.time() - st)}秒！")

@scheduler.scheduled_job('cron', minute=6)
async def _check_trans_resources_task():
    await check_trans_resources()

async def check_card_info_resources(block: bool = False, iswait: bool = True, pjsk_type: Optional[int] = None):
    logger.info("[定时任务]:开始自动更新pjsk游戏数据！（卡面补全）")
    st = time.time()
    wait_time = 5 if iswait else 0
    pjsk_types = [pjsk_type] if pjsk_type is not None else list(SERVER_MAP.keys())
    for p_type in pjsk_types:
        await pjsk_update_manager.update_server_game_data('eventMusics.json', p_type, block=block)
        await asyncio.sleep(wait_time)
        await pjsk_update_manager.update_server_game_data('gachas.json', p_type, block=block)
        await asyncio.sleep(wait_time)
        await pjsk_update_manager.update_server_game_data('skills.json', p_type, block=block)
    logger.info(f"[定时任务]:pjsk游戏数据更新完毕,耗时{int(time.time() - st)}秒！")

@scheduler.scheduled_job('cron', minute=7)
async def _check_card_info_resources_task():
    await check_card_info_resources()

async def check_pjsk_all_resources(block: bool = False, iswait: bool = True, pjsk_type: Optional[int] = None):
    await check_event_resources(block, iswait, pjsk_type)
    await check_eventinfo_resources(block, iswait, pjsk_type)
    await check_cards_resources(block, iswait, pjsk_type)
    await check_profile_resources(block, iswait, pjsk_type)
    await check_pjskinfo_resources(block, iswait, pjsk_type)
    await check_songs_resources(block, iswait, pjsk_type)
    await check_trans_resources(block, iswait, pjsk_type)
    await check_card_info_resources(block, iswait, pjsk_type)

@scheduler.scheduled_job(
    'cron',
    hour=4,
    minute=30
)
async def _cron_sync_haruki_music_aliases():
    try:
        from ._song_utils import sync_haruki_music_aliases
        # Sync JP by default, you can add loops for CN/TW if needed
        await sync_haruki_music_aliases(0)
    except Exception as e:
        logger.warning(f"定时同步 Haruki 歌曲别称失败: {e}")
