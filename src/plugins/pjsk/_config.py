"""PJSK 公开 YAML 配置与环境变量秘密加载器。"""

from __future__ import annotations

import os
from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml

from config.path_config import CONFIG_PATH
from ._paths import MASTERDATA_PATH, SUITE_PATH


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as file:
        return yaml.safe_load(file) or {}


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _split_urls(value: str | None, default: list[str] | None = None) -> list[dict[str, Any]]:
    urls = [item.strip() for item in (value or "").split(",") if item.strip()]
    if not urls:
        urls = list(default or [])
    return [{"url": url, "weight": 1} for url in urls]


_pjsk_config_dir = CONFIG_PATH / "pjsk"
_settings = _load_yaml(_pjsk_config_dir / "settings.yaml")
SERVER_CONFIG = _load_yaml(_pjsk_config_dir / "servers.yaml")
local_config = Path(os.getenv("PJSK_LOCAL_CONFIG", _pjsk_config_dir / "local.yaml"))
if local_config.exists():
    local_data = _load_yaml(local_config)
    _settings = _deep_merge(_settings, local_data.get("settings", {}))
    SERVER_CONFIG = _deep_merge(SERVER_CONFIG, local_data.get("servers", {}))

SERVER_MAP = {int(key): value for key, value in (_settings.get("server_map") or {0: "jp", 1: "tw", 2: "cn"}).items()}
api_base_url_list = list(_settings.get("api_base_urls") or [])
HARUKI_DRAWING_API_SERVERS = _split_urls(
    os.getenv("HARUKI_DRAWING_API_URLS"),
    _settings.get("haruki", {}).get("drawing_api_urls", []),
)
HARUKI_DECK_SERVICE_SERVERS = _split_urls(
    os.getenv("HARUKI_DECK_SERVICE_URLS"),
    _settings.get("haruki", {}).get("deck_service_urls", []),
)
GAMEAPI_TOKEN = (os.getenv("GAMEAPI_TOKEN") or "").strip()

_endpoints = _settings.get("endpoints", {})
MUSIC_METAS_BASE_URL = str(_endpoints.get("music_metas_base_url") or "").rstrip("/")
MUSIC_ALIAS_API_URL = str(_endpoints.get("music_alias_api_url") or "")
RANK_MATCH_API_BASE_URL = str(_endpoints.get("rank_match_api_base_url") or "").rstrip("/")
WORLDLINK_LATEST_API_URL = str(_endpoints.get("worldlink_latest_api_url") or "")
FORECAST_EVENTS_API_URL = str(_endpoints.get("forecast_events_api_url") or "")
FORECAST_LATEST_API_URL = str(_endpoints.get("forecast_latest_api_url") or "")
CHART_PREVIEW_BASE_URL = str(_endpoints.get("chart_preview_base_url") or "").rstrip("/")
GAMEAPI_AUTH_KEYWORDS = [
    str(keyword).lower()
    for keyword in (_endpoints.get("auth_url_keywords") or [])
    if str(keyword).strip()
]

_timeouts = _settings.get("timeouts", {})
DEFAULT_MASTERDATA_UPDATE_CHECK_TIMEOUT = int(_timeouts.get("masterdata_update_check", 3))
DEFAULT_MASTERDATA_DOWNLOAD_TIMEOUT = int(_timeouts.get("masterdata_download", 160))
DEFAULT_RIP_ASSET_DOWNLOAD_TIMEOUT = int(_timeouts.get("asset_download", 5))
RIP_IMG_CACHE_MAX_RES = 256 * 256
DEBUG_LOG_IMG_CACHE = False
MASTERDATA_FALLBACK = _settings.get("masterdata_fallback", {})

SUITE_API_KEYS = [
    "userCards", "userDecks", "userGamedata", "userMusics", "userMusicResults",
    "userMysekaiMaterials", "userAreas", "userChallengeLiveSoloDecks", "userCharacters",
    "userMysekaiCanvases", "userMysekaiFixtureGameCharacterPerformanceBonuses",
    "userMysekaiGates", "userWorldBloomSupportDecks", "userHonors",
    "userMysekaiCharacterTalks", "userChallengeLiveSoloResults", "userChallengeLiveSoloStages",
    "userChallengeLiveSoloHighScoreRewards", "userEvents", "userWorldBlooms",
    "userMusicAchievements", "userPlayerFrames", "userMaterials", "upload_time",
    "userCharacterMissionV2s", "userCharacterMissionV2Statuses", "userBonds", "userProfileHonors",
]

# 错误提示
NOT_BIND_ERROR = "出错了，可能是因为没有绑定"
ID_ERROR = "你这ID有问题啊"
TIMEOUT_ERROR = "出错了，可能是bot网不好"
BUG_ERROR = "出错了，可能是バグ捏"
REFUSED_ERROR = "查不到捏，可能是不给看"
NOT_PLAYER_ERROR = "未找到玩家"
NOT_IMAGE_ERROR = "部分资源加载失败，重新发送中..."
MAINTAIN_ERROR = "出错了，可能是游戏正在维护"
USER_BAN_ERROR = "出错了，可能是用户已被封禁"
NOT_SERVER_ERROR = "出错了，不支持此服务器"
QUERY_BAN_ERROR = "该用户已被拉黑，禁止使用此功能"
ONLY_TOP100_ERROR = "出错了，目前查分仅支持前百的玩家"



# 其它配置
event_id = 75
rank_levels = [
    1, 2, 3, 4, 5, 10, 20, 30, 40, 50, 100, 200, 300, 400, 500, 1000, 2000, 3000, 4000, 5000, 10000,
    20000, 30000, 40000, 50000, 100000
]

rankmatchgrades = {
    1: 'Beginner', 2: 'Bronze', 3: 'Silver', 4: 'Gold', 5: 'Platinum', 6: 'Diamond', 7: 'Master'
}

headers = {
    'accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.9',
    'accept-encoding': 'gzip, deflate, br',
    'accept-language': 'zh-CN,zh;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6,ja;q=0.5',
    'cache-control': 'max-age=0',
    'sec-ch-ua': '"Microsoft Edge";v="105", "Not)A;Brand";v="8", "Chromium";v="105"',
    'sec-ch-ua-mobile': '?0',
    'sec-ch-ua-platform': '"Windows"',
    'sec-fetch-dest': 'document',
    'sec-fetch-mode': 'navigate',
    'sec-fetch-site': 'none',
    'sec-fetch-user': '?1',
    'upgrade-insecure-requests': '1',
    'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/105.0.0.0 Safari/537.36 Edg/105.0.1343.42'
}

lab_headers = headers.copy()

# 兼容旧模块名称：主数据和用户 suite 已迁入 data/pjsk。
data_path = MASTERDATA_PATH
suite_path = SUITE_PATH

# 组卡服务配置

# 组卡后端列表：http、allium 或 allium,http
DECK_RECOMMEND_BACKENDS = [
    item.strip().lower()
    for item in os.getenv("DECK_BACKENDS", ",".join(_settings.get("deck", {}).get("backends", ["http"]))).split(",")
    if item.strip()
]
if not DECK_RECOMMEND_BACKENDS:
    DECK_RECOMMEND_BACKENDS = ["http"]

# Rust deck-service 地址列表（可配置多个做负载均衡）
DECK_RECOMMEND_SERVERS = _split_urls(
    os.getenv("DECK_SERVICE_URLS"),
    _settings.get("deck", {}).get("service_urls", ["http://127.0.0.1:45557"]),
)

# 组卡超时设置（秒）
DECK_RECOMMEND_TIMEOUT = int(_settings.get("deck", {}).get("timeout", 30))
DECK_RECOMMEND_TIMEOUT_NO_EVENT = int(_settings.get("deck", {}).get("timeout_no_event", 45))
DECK_RECOMMEND_TIMEOUT_SINGLE_ALG = int(_settings.get("deck", {}).get("timeout_single_algorithm", 15))
DECK_RECOMMEND_TIMEOUT_BONUS = int(_settings.get("deck", {}).get("timeout_bonus", 15))

# 组卡默认算法
DECK_RECOMMEND_DEFAULT_ALGS = list(_settings.get("deck", {}).get("default_algorithms", ["dfs", "ga"]))

# 组卡返回卡组数量
DECK_RETURN_NUM_MULTI = int(_settings.get("deck", {}).get("return_num_multi", 7))
DECK_RETURN_NUM_CHALLENGE = int(_settings.get("deck", {}).get("return_num_challenge", 3))
DECK_RETURN_NUM_BONUS = int(_settings.get("deck", {}).get("return_num_bonus", 7))

# 默认歌曲（music_id, difficulty）
DECK_DEFAULT_MUSIC_EVENT_MULTI = [(10000, 'master')]
DECK_DEFAULT_MUSIC_EVENT_SOLO = [(10000, 'master')]
DECK_DEFAULT_MUSIC_EVENT_AUTO = [(10000, 'master')]
DECK_DEFAULT_MUSIC_CHALLENGE = [(10000, 'master')]