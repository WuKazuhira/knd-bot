import json
import re
import time
from datetime import datetime, timedelta
from pathlib import Path
from threading import RLock
from typing import Any, Dict, List, Optional, Tuple

import yaml
from nonebot.adapters.onebot.v11 import Message, MessageEvent
from nonebot.params import CommandArg

from services import logger

# 绘图相关实现已迁至绘图服务（services.pjsk_draw）。
# run_pjsk_thread 是通用的限流线程池入口，非绘图代码也在用，这里再导出。
from services.pjsk_draw import run_pjsk_thread
from utils.http_utils import AsyncHttpx
from utils.utils import get_message_at

from ._autoask import pjsk_update_manager
from ._common_utils import callapi, timeremain
from ._config import (
    ID_ERROR,
    MASTERDATA_FALLBACK,
    REFUSED_ERROR,
    SERVER_MAP,
    TIMEOUT_ERROR,
    api_base_url_list,
    data_path,
    rank_levels,
)
from ._paths import STATIC_PATH

_MASTER_DATA_CACHE: Dict[Tuple[int, str, str, int, int], Any] = {}
_MASTER_DATA_PATH_CACHE: Dict[Tuple[int, str], Tuple[str, int, int]] = {}
_MASTER_DATA_INDEX_CACHE: Dict[Tuple[int, str, str, int, int, str], Dict[Any, Any]] = {}
_CHARA_ALIAS_CACHE: Dict[str, Any] = {"path": None, "mtime": None, "size": None, "data": None}

_CACHE_LOCK = RLock()


# 服务器数据目录
def get_server_data_path(pjsk_type: int) -> Path:
    server_name = SERVER_MAP.get(pjsk_type, 'jp')
    path = data_path / server_name
    if not path.exists():
        path.mkdir(parents=True, exist_ok=True)
    return path

def get_chara_alias_map():
    yaml_path = STATIC_PATH / "character_nicknames.yaml"

    if not yaml_path.exists():
        return {}

    stat = yaml_path.stat()
    path_key = str(yaml_path)
    with _CACHE_LOCK:
        if (
            _CHARA_ALIAS_CACHE.get("path") == path_key
            and _CHARA_ALIAS_CACHE.get("mtime") == stat.st_mtime_ns
            and _CHARA_ALIAS_CACHE.get("size") == stat.st_size
        ):
            return _CHARA_ALIAS_CACHE.get("data") or {}

    alias_map = {}
    try:
        with open(yaml_path, 'r', encoding='utf-8') as f:
            data = yaml.safe_load(f) or {}
            for item in data.get('nicknames', []):
                char_id = item['id']
                for nickname in item.get('nicknames', []):
                    alias_map[str(nickname).lower()] = char_id
    except Exception as e:
        logger.warning(f"加载 character_nicknames.yaml 失败: {e}")

    with _CACHE_LOCK:
        _CHARA_ALIAS_CACHE.update({
            "path": path_key,
            "mtime": stat.st_mtime_ns,
            "size": stat.st_size,
            "data": alias_map,
        })
    return alias_map

def _set_master_data_path_cache(
    pjsk_type: int,
    filename: str,
    path_key: str,
    mtime_ns: int,
    size: int,
) -> None:
    """记录文件版本，并清理同服同文件的旧版本缓存。"""
    path_cache_key = (pjsk_type, filename)
    version = (path_key, mtime_ns, size)
    with _CACHE_LOCK:
        previous = _MASTER_DATA_PATH_CACHE.get(path_cache_key)
        if previous != version:
            stale_data_keys = [
                key for key in _MASTER_DATA_CACHE
                if key[0] == pjsk_type and key[1] == filename
            ]
            for key in stale_data_keys:
                del _MASTER_DATA_CACHE[key]
            stale_index_keys = [
                key for key in _MASTER_DATA_INDEX_CACHE
                if key[0] == pjsk_type and key[1] == filename
            ]
            for key in stale_index_keys:
                del _MASTER_DATA_INDEX_CACHE[key]
        _MASTER_DATA_PATH_CACHE[path_cache_key] = version


def _master_data_cache_key(pjsk_type: int, filename: str) -> Optional[Tuple[int, str, str, int, int]]:
    primary_path = get_server_data_path(pjsk_type) / filename
    with _CACHE_LOCK:
        cached = _MASTER_DATA_PATH_CACHE.get((pjsk_type, filename))
    if cached:
        cached_path, cached_mtime, cached_size = cached
        path = Path(cached_path)
        # 首选文件恢复后，优先切回首选文件。
        if not (str(path) != str(primary_path) and primary_path.exists()) and path.exists():
            stat = path.stat()
            if stat.st_mtime_ns == cached_mtime and stat.st_size == cached_size:
                return pjsk_type, filename, cached_path, cached_mtime, cached_size

    server_name = SERVER_MAP.get(pjsk_type, 'jp')
    candidates = [primary_path]
    fallback_cfg = MASTERDATA_FALLBACK.get(server_name)
    if fallback_cfg and fallback_cfg.get('enabled'):
        basename = filename.split('.')[0]
        fallback_names = fallback_cfg.get('names', [])
        if "*" in fallback_names or basename in fallback_names:
            fallback_region = fallback_cfg.get('region', 'jp')
            fallback_type = next((k for k, v in SERVER_MAP.items() if v == fallback_region), 0)
            candidates.append(get_server_data_path(fallback_type) / filename)

    for path in candidates:
        if path.exists():
            stat = path.stat()
            path_key = str(path)
            _set_master_data_path_cache(
                pjsk_type, filename, path_key, stat.st_mtime_ns, stat.st_size
            )
            return pjsk_type, filename, path_key, stat.st_mtime_ns, stat.st_size
    return None


# 加载主数据，支持回退
def load_master_data(filename: str, pjsk_type: int = 0) -> Any:
    server_name = SERVER_MAP.get(pjsk_type, 'jp')
    primary_path = get_server_data_path(pjsk_type) / filename
    cache_key = _master_data_cache_key(pjsk_type, filename)
    if cache_key:
        with _CACHE_LOCK:
            cached_data = _MASTER_DATA_CACHE.get(cache_key)
        if cached_data is not None:
            return cached_data
    
    data = None
    load_path = Path(cache_key[2]) if cache_key else None
    if load_path and load_path.exists():
        with open(load_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    
    if data is None:
        # 本地缺失时自动拉取
        logger.info(f"[{server_name}] MasterData {filename} 缺失，尝试自动拉取...")
        pjsk_update_manager.sync_update_music_data(filename, pjsk_type)
        cache_key = _master_data_cache_key(pjsk_type, filename)
        load_path = Path(cache_key[2]) if cache_key else primary_path
        
        # 再次尝试读取
        if load_path.exists():
            with open(load_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
    
    if data is None:
        raise FileNotFoundError(f"MasterData {filename} not found for server {server_name} and automatic pull failed.")

    # 确保数据类型正确，避免读到损坏内容
    if isinstance(data, str):
        logger.error(f"MasterData {filename} 加载失败: 预期 JSON 对象/列表，但得到的是字符串。内容可能已损坏。")
        raise ValueError(f"MasterData {filename} is a string, expected dict or list.")

    # 若外层是字典包裹，则尝试解包
    if isinstance(data, dict):
        # 常见包裹键
        wrapper_keys = ['data', 'items', 'list', 'entries', 'rankings', 'masterData', 'cards', 'musics', 'events', 'skills']
        unwrapped = False
        for key in wrapper_keys:
            if key in data and isinstance(data[key], (list, dict)):
                logger.info(f"[{server_name}] 主数据 {filename} 命中包裹键 '{key}'，正在解包...")
                data = data[key]
                unwrapped = True
                break
        
        # 仍是字典时，尝试转换为列表
        if isinstance(data, dict):
            # 数字键字典直接取 values
            # 否则过滤掉杂项字段
            keys = list(data.keys())
            is_numeric_map = all(str(k).isdigit() for k in keys)
            
            if is_numeric_map:
                data = list(data.values())
            else:
                # 过滤掉元数据类字段
                filtered_data = [v for v in data.values() if isinstance(v, (dict, list))]
                
                # 如果只剩一个容器字段，且原字典有多个键，则自动解包
                if len(filtered_data) == 1 and len(data) > 1:
                    logger.info(f"[{server_name}] 主数据 {filename} 自动解包单一容器字段。")
                    data = filtered_data[0]
                else:
                    if len(filtered_data) != len(data):
                        logger.info(f"[{server_name}] 主数据 {filename} 已过滤 {len(data) - len(filtered_data)} 个元数据字段。")
                    data = filtered_data
        
    cache_key = _master_data_cache_key(pjsk_type, filename)
    if cache_key:
        with _CACHE_LOCK:
            _MASTER_DATA_CACHE[cache_key] = data
    return data


async def async_load_master_data(filename: str, pjsk_type: int = 0) -> Any:
    """异步加载 MasterData，避免大 JSON 解析阻塞事件循环。"""
    return await run_pjsk_thread(load_master_data, filename, pjsk_type)


async def refresh_master_data_cache(filename: str, pjsk_type: int) -> bool:
    """MasterData 文件被改写后，在线程池里提前重建缓存。

    改写会让缓存按 mtime 失效，之后第一条用到它的指令就得在事件循环上现做
    一次大 JSON 解析（实测 cards.json 约 0.55s、gachas.json 约 0.78s、
    costume3ds.json 约 0.99s，期间整个 bot 卡住）。更新任务本来就在后台跑，
    把这笔开销挪到这里是免费的。

    只刷新已经缓存过的条目：没人用过的主数据不该被读进内存（costume3ds.json
    这类文件光原文就有 50MB+）。
    """
    with _CACHE_LOCK:
        was_cached = any(
            key[0] == pjsk_type and key[1] == filename for key in _MASTER_DATA_CACHE
        )
    if not was_cached:
        return False
    try:
        await async_load_master_data(filename, pjsk_type)
        return True
    except Exception as e:
        logger.debug(f"重建 MasterData 缓存失败 {filename}(server={pjsk_type}): {e}")
        return False


def index_by_id(items: Any, key: str = 'id') -> Dict[Any, Any]:
    if not isinstance(items, list):
        return {}
    return {item[key]: item for item in items if isinstance(item, dict) and key in item}


def master_data_by_id(filename: str, pjsk_type: int = 0, key: str = 'id') -> Dict[Any, Any]:
    cache_key = _master_data_cache_key(pjsk_type, filename)
    if not cache_key:
        data = load_master_data(filename, pjsk_type)
        return index_by_id(data, key)
    index_key = (*cache_key, key)
    with _CACHE_LOCK:
        indexed = _MASTER_DATA_INDEX_CACHE.get(index_key)
    if indexed is None:
        indexed = index_by_id(load_master_data(filename, pjsk_type), key)
        with _CACHE_LOCK:
            _MASTER_DATA_INDEX_CACHE[index_key] = indexed
    return indexed

# 烧烤uid预处理
async def get_userid_preprocess(event: MessageEvent, msg: Message = CommandArg(), pjsk_type: int = 0):
    from ._models import PjskBind
    arg = re.sub(r'\D', "", msg.extract_plain_text().strip())
    reply = ""
    isprivate = False
    if not arg:
        qq_ls = get_message_at(event.raw_message)
        qid = qq_ls[0] if qq_ls and qq_ls[0] != event.self_id else event.user_id
        arg, isprivate = await PjskBind.get_user_bind(qid, pjsk_type)
        if not arg:
            server_display = "日服" if pjsk_type == 0 else ("台服" if pjsk_type == 1 else "国服")
            reply = f"{'你' if event.user_id == qid else '用户'}还没有绑定{server_display}哦，国服/台服指令请加cn/tw前缀，日服无需前缀"
        elif isprivate and qid != event.user_id:
            reply = REFUSED_ERROR
    elif arg.isdigit() and verifyid(arg, pjsk_type):
        pass
    else:
        reply = ID_ERROR
    return {
        'error': reply,
        'private': isprivate,
        'userid': str(arg) if arg is not None else arg
    }


# 当前排位赛季
def currentrankmatch(pjsk_type: int = 0):
    try:
        data = load_master_data('rankMatchSeasons.json', pjsk_type)
        for i in range(0, len(data)):
            startAt = data[i]['startAt']
            endAt = data[i]['closedAt']
            now = int(round(time.time() * 1000))
            if not startAt < now < endAt:
                continue
            return data[i]['id']
        return data[len(data) - 1]['id']
    except:
        return 0

# 当期活动
def currentevent(pjsk_type: int = 0) -> dict:
    try:
        data = load_master_data('events.json', pjsk_type)
        now = int(round(time.time() * 1000))
        
        # 首先查找当前进行中或刚进入结算中的活动。
        # closedAt 可能会晚于实际活动结束很久；若已过结算缓冲期，则不再把上一期视为默认活动。
        for i in range(0, len(data)):
            startAt = data[i]['startAt']
            aggregateAt = data[i]['aggregateAt']
            assetbundleName = data[i]['assetbundleName']
            
            if not startAt < now < aggregateAt + 600000:
                continue
            if startAt < now < aggregateAt:
                status = 'going'
                remain = timeremain((aggregateAt - now) / 1000)
            else:
                status = 'counting'
                remain = ''
            return {'id': data[i]['id'], 'status': status, 'remain': remain, 'assetbundleName': assetbundleName}
        
        # 如果没有进行中的活动，查找下一期尚未开始的活动
        next_event = None
        min_start_time = float('inf')
        for i in range(0, len(data)):
            startAt = data[i]['startAt']
            if startAt > now and startAt < min_start_time:
                min_start_time = startAt
                next_event = data[i]
        
        if next_event:
            assetbundleName = next_event['assetbundleName']
            remain = timeremain((next_event['startAt'] - now) / 1000)
            return {'id': next_event['id'], 'status': 'upcoming', 'remain': remain, 'assetbundleName': assetbundleName}
        
        # 没有未来活动时才回退到最后一期，避免极端情况下默认活动为空。
        last_event = max(
            (event for event in data if isinstance(event, dict) and event.get('id')),
            key=lambda event: event.get('startAt', 0),
            default=None,
        )
        if last_event:
            return {
                'id': last_event['id'],
                'status': 'end',
                'remain': '0',
                'assetbundleName': last_event.get('assetbundleName', '')
            }
        return {'id': 0, 'status': 'end', 'remain': '0', 'assetbundleName': ''}
    except:
        return {'id': 0, 'status': 'end', 'remain': '0', 'assetbundleName': ''}


# 从指令名获取pjsk服务器类型
def get_pjsk_type(cmd_name: str) -> int:
    if cmd_name.startswith('cn'):
        return 2
    if cmd_name.startswith('tw'):
        return 1
    return 0


# 烧烤 UID 创建时间
def gettime(userid: str, pjsk_type: int) -> int:
    try:
        uid_val = int(userid)
        if pjsk_type == 0: #cp服 (JP)
            # JP IDs: (timestamp_ms - 1600218000000) << 22
            # Divided by 1000 to get seconds, then >> 22
            passtime = (uid_val // 1000) // 4194304  # 4194304 = 2^22
            return 1600218000 + passtime
        elif pjsk_type in [1, 2]: #字节服 (TW/CN)
            # CN/TW IDs: (timestamp_s) << 32
            # Divided by 2^32 to get seconds
            passtime = uid_val // 4294967296  # 4294967296 = 2^32
            return passtime
    except ValueError:
        return 0
    return 0

# 烧烤 ID 合规性
def verifyid(userid: str, pjsk_type: int = 0) -> bool:
    userid = str(userid)
    if not (13 <= len(userid) <= 20) or not userid.isdigit():
        return False
    
    registertime = gettime(userid, pjsk_type)
    # 验证时间范围（当前设置为从JP开服时间2020-09-16到当前，增加1天缓冲）
    if not registertime:
        return False
    
    dt = datetime.fromtimestamp(registertime)
    start_dt = datetime.strptime("2020-09-16", "%Y-%m-%d")
    now_dt = datetime.now() + timedelta(days=1) # 增加一天缓冲以防时区或微小误差
    
    if not (start_dt <= dt <= now_dt):
        return False
    return True


# 排名附近档线
def near_rank(rank: int) -> List:
    tmp = []
    if rank == rank_levels[0]:
        return [{'tag': '↓', 'index': 1, 'rank': rank_levels[1]}]
    if rank >= rank_levels[-1]:
        return [{'tag': '↑', 'index': len(rank_levels) - 1, 'rank': rank_levels[-1]}]
    for i in range(len(rank_levels)):
        if rank <= rank_levels[i]:
            tmp.append({'tag': '↑', 'index': i - 1, 'rank': rank_levels[i - 1]})
            if rank == rank_levels[i]:
                tmp.append({'tag': '↓', 'index': i + 1, 'rank': rank_levels[i + 1]})
            else:
                tmp.append({'tag': '↓', 'index': i, 'rank': rank_levels[i]})
            break
    return tmp



# 用户当期活动信息
async def getUserData(event_id: int, param: dict, pjsk_type: int = 0) -> Dict:
    from ._config import SERVER_MAP
    from ._sk_sql import query_latest_ranking
    server_name = SERVER_MAP.get(pjsk_type, 'jp')

    # 将传来的 param 解析 (targetRank 或 targetUserId)
    ranks_to_query = None
    if 'targetRank' in param:
        ranks_to_query = param['targetRank']
        if isinstance(ranks_to_query, list):
            ranks_to_query = [int(v) for v in ranks_to_query]
        else:
            ranks_to_query = [int(ranks_to_query)]
    
    # 获取最新的排名
    latest_ranks = await query_latest_ranking(server_name, event_id, ranks=ranks_to_query)
    
    # 如果指定了 UID 搜索
    if 'targetUserId' in param:
        uid = str(param['targetUserId'])
        found = [r for r in latest_ranks if r.uid == uid]
    elif ranks_to_query and len(ranks_to_query) == 1:
        found = [r for r in latest_ranks if r.rank == ranks_to_query[0]]
    else:
        # 这个函数主要用于单人查询，所以取满足条件的第一个
        found = latest_ranks

    if not found:
        from ._config import ONLY_TOP100_ERROR
        from ._errors import apiCallError
        raise apiCallError(ONLY_TOP100_ERROR)

    user_rank = found[0]
    
    userdata = {
        'id': user_rank.uid,
        'name': user_rank.name,
        'score': user_rank.score,
        'rank': user_rank.rank,
        'teaminfo': None,
        'assetbundleName': None,
        'updateTime': user_rank.time.strftime("%m-%d %H:%M:%S")
    }
    
    # 5v5 活动暂不扩展 teaminfo，避免逻辑过重
    return userdata



# 活动 ID
async def getEventId(url: str):
    data_json = (await AsyncHttpx.get(url)).json()
    return data_json
