import asyncio
import json
import urllib.parse
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import httpx
import Levenshtein as lev
import requests

from utils.http_utils import AsyncHttpx

from ._config import (
    GAMEAPI_AUTH_KEYWORDS,
    ONLY_TOP100_ERROR,
    SERVER_CONFIG,
    SERVER_MAP,
    data_path,
    suite_path,
)
from ._errors import QueryBanned, apiCallError, maintenanceIn, userIdBan



def _pjsk_helper_url() -> str:
    import os

    return os.getenv('PJSK_HELPER_URL', 'http://host.docker.internal:45558').rstrip('/')


def _suite_backend() -> str:
    import os

    return os.getenv('PJSK_SUITE_BACKEND', 'python').strip().lower()

_LOCAL_RANKING_CACHE: Dict[Tuple[str, int, int], Dict[str, Any]] = {}
_LOCAL_SUITE_CACHE: Dict[Tuple[str, int, int], Dict[str, Any]] = {}


def _load_local_json(path: Path, cache: Dict[Tuple[str, int, int], Dict[str, Any]]) -> Tuple[Dict[str, Any], float]:
    stat = path.stat()
    key = (str(path), stat.st_mtime_ns, stat.st_size)
    cached = cache.get(key)
    if cached is None:
        with open(path, 'r', encoding='utf-8') as f:
            cached = json.load(f)
        cache[key] = cached
    return cached, stat.st_mtime


def _load_local_ranking(path: Path) -> Tuple[Dict[str, Any], float]:
    return _load_local_json(path, _LOCAL_RANKING_CACHE)


def _load_local_suite(path: Path) -> Dict[str, Any]:
    data, _ = _load_local_json(path, _LOCAL_SUITE_CACHE)
    return data


def _blocking_get_json(url: str, headers: Optional[Dict[str, str]] = None, timeout: int = 5) -> Dict[str, Any]:
    if headers:
        return requests.get(url, headers=headers, timeout=timeout).json()
    return requests.get(url, timeout=timeout).json()


# 通用api查询
async def callapi(
        url: str,
        param: Optional[Dict] = None,
        query_type: str = 'unknown',
        is_force_update: bool = False,
        pjsk_type: int = 0
) -> Dict[str, Any]:
    if param is not None:
        q = urllib.parse.urlencode(param)
        url = url + '?' + q
    
    server_name = SERVER_MAP.get(pjsk_type, 'jp')
    server_data_path = data_path / server_name
    if not server_data_path.exists():
        server_data_path.mkdir(parents=True, exist_ok=True)

    # 处理sk和rk的api
    json_path = None
    if r'/event/' in url:
        json_path = server_data_path / 'sktop100.json'
    elif r'/rank-match-season/' in url:
        json_path = server_data_path / 'rktop100.json'
    
    if 'targetRank' in url and json_path and json_path.exists():
        targetRank = int(url[url.find('targetRank=') + len('targetRank='):])
        top100, updatetime = _load_local_ranking(json_path)
        for single in top100["rankings"]:
            if single["rank"] == targetRank:
                return {
                    "rankings": [single],
                    'updateTime': datetime.fromtimestamp(updatetime).strftime("%m-%d %H:%M:%S")
                }
        else:
            raise apiCallError(ONLY_TOP100_ERROR)
    elif 'targetUserId' in url and json_path and json_path.exists():
        targetUserId = int(url[url.find('targetUserId=') + len('targetUserId='):])
        jptop100, updatetime = _load_local_ranking(json_path)
        for single in jptop100["rankings"]:
            if single["userId"] == targetUserId:
                return {
                    "rankings": [single],
                    'updateTime': datetime.fromtimestamp(updatetime).strftime("%m-%d %H:%M:%S")
                }
        else:
            raise apiCallError(ONLY_TOP100_ERROR)
    
    # 处理逮捕、b30、profile、进度
    # 逮捕仍然实时查询
    if '/profile' in url and query_type != 'arrest' and not is_force_update:
        # url 格式可能是 /user/{userid}/profile
        try:
            userid = url[url.find('user/') + 5:url.find('/profile')]
        except:
            userid = None
            
        if userid:
            # 先尝试取本地结果
            user_suite_file = suite_path / server_name / f'{userid}.json'
            if user_suite_file.exists():
                return _load_local_suite(user_suite_file)
            # go sidecar 代理模式（PJSK_SUITE_BACKEND=go）：走 pjsk-helper 的多级缓存
            elif _suite_backend() == 'go':
                helper_url = f'{_pjsk_helper_url()}/suite/{server_name}/{userid}'
                try:
                    resp = await AsyncHttpx.get(helper_url, timeout=15)
                    if resp.status_code == 200:
                        return resp.json()
                except (httpx.HTTPError, OSError, ValueError):
                    pass  # 回落到直连 Haruki
                api_url = SERVER_CONFIG[server_name]['api']['profile_api_url'].format(uid=userid)

                from ._gameapi import request_gameapi

                try:
                    return await request_gameapi(api_url)
                except apiCallError:
                    if query_type in ['b30', 'rop', 'rank']:
                        raise QueryBanned("无法查询到用户信息，可能是没有上传")
                    raise
            # 拿不到再访问 Haruki 接口
            else:
                # 尝试从 SERVER_CONFIG 获取 URL 模板并格式化
                api_url = SERVER_CONFIG[server_name]['api']['profile_api_url'].format(uid=userid)
                
                from ._gameapi import request_gameapi

                try:
                    return await request_gameapi(api_url)
                except apiCallError:
                    if query_type in ['b30', 'rop', 'rank']:
                        raise QueryBanned("无法查询到用户信息，可能是没有上传")
                    raise

    # 需要鉴权的游戏 API 统一走共享会话，避免发送 Bearer None。
    if any(keyword in url.lower() for keyword in GAMEAPI_AUTH_KEYWORDS):
        from ._gameapi import request_gameapi

        return await request_gameapi(url)

    try:
        data = (await AsyncHttpx.get(url, timeout=5)).json()
    except (httpx.HTTPError, OSError, ValueError):
        try:
            data = await asyncio.to_thread(_blocking_get_json, url, None, 5)
        except (OSError, ValueError, requests.RequestException) as exc:
            raise apiCallError from exc

    if isinstance(data, dict):
        if data.get('status') == 'maintenance_in':
            raise maintenanceIn
        elif data.get('status') == 'user_id_ban':
            raise userIdBan
    return data

# 时间戳格式化
def timeremain(time):
    if time < 60:
        return f'{int(time)}秒'
    elif time < 60*60:
        return f'{int(time / 60)}分{int(time % 60)}秒'
    elif time < 60*60*24:
        hours = int(time / 60 / 60)
        remain = time - 3600 * hours
        return f'{int(time / 60 / 60)}小时{int(remain / 60)}分{int(remain % 60)}秒'
    else:
        days = int(time / 3600 / 24)
        remain = time - 3600 * 24 * days
        return f'{int(days)}天{timeremain(remain)}'


# 字符串相似度
def string_similar(s1: str, s2: str) -> float:
    # 使用Levenshtein库计算两个字符串之间的距离
    distance = lev.distance(s1, s2)
    # 计算最大可能的距离
    max_len = max(len(s1), len(s2))
    if max_len == 0:
        return 1.0
    # 计算相似度，并返回。距离越小，相似度越高，所以我们用1减去它们的比值
    return 1 - (distance / max_len)
