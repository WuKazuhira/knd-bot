from __future__ import annotations

import asyncio
import ipaddress
import math
import socket
import time
from collections import OrderedDict
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from io import BytesIO
from pathlib import Path
from threading import RLock
from typing import Any, Iterable, Optional
from urllib.parse import quote, urlsplit
from weakref import WeakKeyDictionary

import httpx
from PIL import Image, ImageDraw, ImageFont

from config.path_config import FONT_PATH
from services.log import logger
from utils.pjsk_paths import ONDEMAND_PATH, PROFILE_PATH, STATIC_PATH

from ...context import get_context
from ...primitives import (
    PJSK_WATERMARK_TEXT,
    open_pjsk_image,
    run_pjsk_thread,
)

data_path = ONDEMAND_PATH


class MySekaiError(Exception):

    """MySekai 业务异常 — 在 matcher 里 finish 用纯文本回复。"""

# 路径常量

# pics 已随「PJSK static asset path migration」整体迁到 static/pics 下（bg、notes、
# pjskinfo、mysekai 同级）。这里必须指向 static，否则 site 底图等自带静态图加载失败——
# 尤其是 msr 第三张地图，其 SITE_MAP_INFO 坐标是针对这套自定义底图校准的，
# 底图缺失会回退到游戏原始 texture 或占位图导致显示异常。
MYSEKAI_PICS_PATH = STATIC_PATH / "pics" / "mysekai"
CACHE_PATH = PROFILE_PATH / "mysekai"
CN_MSR_GROUPS_FILE = STATIC_PATH / "cn_msr_allowed_groups.json"

# Both policies share an LRU bound, but remote-only keys have a separate namespace:
# neither real images nor negative entries can cross the local/remote boundary.
_MYSEKAI_IMAGE_CACHE: OrderedDict[tuple, Image.Image] = OrderedDict()
_MYSEKAI_IMAGE_CACHE_LIMIT = 512
_MYSEKAI_IMAGE_CACHE_LOCK = RLock()
_MYSEKAI_IMAGE_NEGATIVE_CACHE: OrderedDict[tuple, float] = OrderedDict()
_MYSEKAI_IMAGE_NEGATIVE_TTL = 15.0
# Flight key includes both I/O policies and the event loop; probe misses never
# suppress downloads, and tasks cannot be awaited from another loop.
_MYSEKAI_IMAGE_INFLIGHT: dict[tuple, asyncio.Task] = {}
_MYSEKAI_FAST_RENDER: ContextVar[bool] = ContextVar("mysekai_fast_render", default=False)
_MYSEKAI_ASSET_REQUEST: ContextVar[Optional["MySekaiAssetRequest"]] = ContextVar("mysekai_asset_request", default=None)
_MYSEKAI_REMOTE_LIMITS: WeakKeyDictionary = WeakKeyDictionary()
_MYSEKAI_REMOTE_TIMEOUT = 10.0


@dataclass
class MySekaiAssetRequest:
    fast: bool
    deadline: float
    max_concurrency: int = 8
    missing_assets: dict[str, dict] = field(default_factory=dict)
    semaphore: asyncio.Semaphore = field(init=False)

    def __post_init__(self):
        self.semaphore = asyncio.Semaphore(self.max_concurrency)

    def metadata(self) -> dict:
        return {
            "assets_complete": not self.missing_assets,
            "missing_assets": [self.missing_assets[key].copy() for key in sorted(self.missing_assets)],
        }


@contextmanager
def mysekai_asset_request(fast: bool = False, budget_seconds: float = 5.0, max_concurrency: int = 8):
    """Wrap all images in one render request; children share its deadline/meta.

    A missing asset remains recorded even if a later panel succeeds: the earlier
    panel already contains a fallback and must not enter a persistent image cache.
    """
    try:
        budget = float(budget_seconds)
        if not math.isfinite(budget):
            budget = 5.0
    except (TypeError, ValueError, OverflowError):
        budget = 5.0
    try:
        concurrency = int(max_concurrency)
    except (TypeError, ValueError, OverflowError):
        concurrency = 8
    state = MySekaiAssetRequest(bool(fast), time.monotonic() + max(0.0, min(5.0, budget)), max(1, min(8, concurrency)))
    token = _MYSEKAI_ASSET_REQUEST.set(state)
    fast_token = _MYSEKAI_FAST_RENDER.set(bool(fast))
    try:
        yield state
    finally:
        _MYSEKAI_FAST_RENDER.reset(fast_token)
        _MYSEKAI_ASSET_REQUEST.reset(token)


@contextmanager
def mysekai_fast_render(enabled: bool = True):
    """Compatibility wrapper; nested panels retain the request's shared deadline."""
    if _MYSEKAI_ASSET_REQUEST.get() is not None:
        token = _MYSEKAI_FAST_RENDER.set(bool(enabled))
        try:
            yield _MYSEKAI_ASSET_REQUEST.get()
        finally:
            _MYSEKAI_FAST_RENDER.reset(token)
    else:
        with mysekai_asset_request(fast=enabled) as state:
            yield state


def mysekai_asset_metadata() -> dict:
    state = _MYSEKAI_ASSET_REQUEST.get()
    return state.metadata() if state else {"assets_complete": True, "missing_assets": []}


def record_missing_asset(path: str, pjsk_type: int = 0, reason: str = "missing") -> None:
    state = _MYSEKAI_ASSET_REQUEST.get()
    if state is not None:
        key = f"{pjsk_type}:{path}"
        state.missing_assets[key] = {"key": key, "region": server_name(pjsk_type), "path": path, "reason": reason}
    logger.debug(f"MySekai asset region={server_name(pjsk_type)} path={path} layer=fallback reason={reason}")


def clear_mysekai_asset_caches() -> None:
    """Clear memory only, including flights so old loads cannot repopulate caches."""
    with _MYSEKAI_IMAGE_CACHE_LOCK:
        _MYSEKAI_IMAGE_CACHE.clear()
        _MYSEKAI_IMAGE_NEGATIVE_CACHE.clear()
    for task in tuple(_MYSEKAI_IMAGE_INFLIGHT.values()):
        if not task.done():
            task.get_loop().call_soon_threadsafe(task.cancel)
    _MYSEKAI_IMAGE_INFLIGHT.clear()


def is_mysekai_fast_render() -> bool:
    return _MYSEKAI_FAST_RENDER.get()


def _negative_cache_get(
    path: str,
    pjsk_type: int,
    size: Optional[tuple[int, int]],
    *, remote_only: bool = False,
) -> bool:
    key = (path, pjsk_type, size, "remote-only") if remote_only else (path, pjsk_type, size)
    now = time.monotonic()
    with _MYSEKAI_IMAGE_CACHE_LOCK:
        expires = _MYSEKAI_IMAGE_NEGATIVE_CACHE.get(key)
        if expires is None:
            return False
        if expires <= now:
            _MYSEKAI_IMAGE_NEGATIVE_CACHE.pop(key, None)
            return False
        _MYSEKAI_IMAGE_NEGATIVE_CACHE.move_to_end(key)
        return True


def _negative_cache_put(
    path: str,
    pjsk_type: int,
    size: Optional[tuple[int, int]],
    *, remote_only: bool = False,
) -> None:
    key = (path, pjsk_type, size, "remote-only") if remote_only else (path, pjsk_type, size)
    with _MYSEKAI_IMAGE_CACHE_LOCK:
        _MYSEKAI_IMAGE_NEGATIVE_CACHE[key] = time.monotonic() + _MYSEKAI_IMAGE_NEGATIVE_TTL
        _MYSEKAI_IMAGE_NEGATIVE_CACHE.move_to_end(key)
        while len(_MYSEKAI_IMAGE_NEGATIVE_CACHE) > _MYSEKAI_IMAGE_CACHE_LIMIT:
            _MYSEKAI_IMAGE_NEGATIVE_CACHE.popitem(last=False)


def _image_cache_get(
    path: str,
    pjsk_type: int,
    size: Optional[tuple[int, int]],
    *, remote_only: bool = False,
) -> Optional[Image.Image]:
    key = (path, pjsk_type, size, "remote-only") if remote_only else (path, pjsk_type, size)
    with _MYSEKAI_IMAGE_CACHE_LOCK:
        image = _MYSEKAI_IMAGE_CACHE.get(key)
        if image is None:
            return None
        _MYSEKAI_IMAGE_CACHE.move_to_end(key)
        return image.copy()


def _image_cache_put(
    path: str,
    pjsk_type: int,
    size: Optional[tuple[int, int]],
    image: Image.Image,
    *, remote_only: bool = False,
) -> Image.Image:
    key = (path, pjsk_type, size, "remote-only") if remote_only else (path, pjsk_type, size)
    cached = image.copy()
    with _MYSEKAI_IMAGE_CACHE_LOCK:
        _MYSEKAI_IMAGE_CACHE[key] = cached
        _MYSEKAI_IMAGE_CACHE.move_to_end(key)
        while len(_MYSEKAI_IMAGE_CACHE) > _MYSEKAI_IMAGE_CACHE_LIMIT:
            _MYSEKAI_IMAGE_CACHE.popitem(last=False)
    return cached.copy()


# UI 配色

BG_COLOR = (245, 245, 250)
CARD_BG = (255, 255, 255)
HEADER_BG = (255, 255, 255, 210)
ACCENT = (88, 92, 118)
SCORE_COLOR = (61, 74, 162)
TIP_COLOR = (0, 204, 187)
WARN_COLOR = (220, 50, 50)
OK_COLOR = (0, 170, 80)
TEXT_COLOR = (45, 45, 55)
MUTED_COLOR = (110, 110, 120)


# 业务常量

UNIT_GATEID_MAP = {
    "light_sound": 1,
    "idol": 2,
    "street": 3,
    "theme_park": 4,
    "school_refusal": 5,
}

UNIT_COLORS = [
    (68, 85, 221, 255),    # light_sound
    (136, 221, 68, 255),   # idol
    (238, 17, 102, 255),   # street
    (255, 153, 0, 255),    # theme_park
    (136, 68, 153, 255),   # school_refusal
]

UNIT_ALIASES = {
    "ln": "light_sound", "leo": "light_sound", "l/n": "light_sound", "星星": "light_sound",
    "mmj": "idol", "mm": "idol", "偶像": "idol",
    "vbs": "street", "街头": "street",
    "ws": "theme_park", "wxs": "theme_park", "游乐园": "theme_park",
    "25": "school_refusal", "n25": "school_refusal", "ニーゴ": "school_refusal", "25时": "school_refusal",
    "vs": "piapro", "vocaloid": "piapro", "piapro": "piapro",
}

MUSIC_TAG_UNIT_MAP = {
    "light_sound": "light_sound",
    "idol": "idol",
    "street": "street",
    "theme_park": "theme_park",
    "school_refusal": "school_refusal",
    "vocaloid": "piapro",
    "other": None,
}

# 抓包数据中 4 个采集区域 ID。其它 ID 都是住宅，没有掉落。
SITE_ID_ORDER = (5, 7, 6, 8)


# 稀有资源规则
RARE_RES_KEYS: dict[int, list[str]] = {
    1: [
        "mysekai_material_32", "mysekai_material_33", "mysekai_material_34",
        "mysekai_material_61", "mysekai_material_64", "mysekai_material_65",
        "mysekai_material_66", "mysekai_material_93", "mysekai_material_94",
        "mysekai_music_record_0~9999",
    ],
    2: [
        "mysekai_material_5", "mysekai_material_12", "mysekai_material_20",
        "mysekai_material_24", "mysekai_fixture_121",
        "material_17", "material_170", "material_173",
        "mysekai_material_67~92", "material_174~203",
    ],
}

# 资源点 fixture id → 简化图标文件名，用于图标缺失时兜底。
MYSEKAI_HARVEST_FIXTURE_IMAGE_NAME = {
    1001: "oak.png", 1002: "pine.png", 1003: "palm.png", 1004: "luxury.png",
    2001: "stone.png", 2002: "copper.png", 2003: "glass.png", 2004: "iron.png",
    2005: "crystal.png", 2006: "diamond.png", 3001: "toolbox.png", 6001: "barrel.png",
    5001: "junk.png", 5002: "junk.png", 5003: "junk.png", 5004: "junk.png",
    5101: "junk.png", 5102: "junk.png", 5103: "junk.png", 5104: "junk.png",
}


# 角色生日（cid → (month, day)）
CHARACTER_BIRTHDAYS: dict[int, tuple[int, int]] = {
    1: (8, 11), 2: (5, 9), 3: (10, 27), 4: (1, 8), 5: (4, 14), 6: (10, 5),
    7: (3, 19), 8: (12, 6), 9: (3, 2), 10: (7, 26), 11: (11, 12), 12: (5, 25),
    13: (5, 17), 14: (9, 9), 15: (7, 20), 16: (6, 24), 17: (2, 10), 18: (1, 27),
    19: (4, 30), 20: (8, 27), 21: (8, 31), 22: (12, 27), 23: (12, 27), 24: (1, 30),
    25: (11, 5), 26: (2, 17),
}


# 五周年区服（生日掉落机制启用）
FIFTH_ANNIV_REGIONS = {"jp"}


# 地图坐标配置

SITE_MAP_INFO: dict[int, dict[str, Any]] = {
    5: {
        "image": "site/grassland.png",
        "grid_size": 33.333,
        "offset_x": 0.0, "offset_z": -40.0,
        "dir_x": -1, "dir_z": -1,
        "rev_xz": True,
    },
    7: {
        "image": "site/flowergarden.png",
        "grid_size": 24.806,
        "offset_x": -62.015, "offset_z": 20.672,
        "dir_x": -1, "dir_z": -1,
        "rev_xz": True,
    },
    6: {
        "image": "site/beach.png",
        "grid_size": 20.513,
        "offset_x": 0.0, "offset_z": 80.0,
        "dir_x": 1, "dir_z": -1,
        "rev_xz": False,
    },
    8: {
        "image": "site/memorialplace.png",
        "grid_size": 21.333,
        "offset_x": 0.0, "offset_z": -106.667,
        "dir_x": 1, "dir_z": -1,
        "rev_xz": False,
    },
}


# 地图整体缩放（决定单张地图卡片的像素尺寸；原图 1920×1080 太大，0.4 后 768×432）
MYSEKAI_HARVEST_MAP_IMAGE_SCALE = 0.4
# 稀有资源发光大小相对资源大图标尺寸的倍数
RARE_RES_LIGHT_LARGE = 7.0
RARE_RES_LIGHT_SMALL = 5.0


# 天气配色

PHENOMENA_COLORS = {
    1: {"ground": (255, 255, 255, 255), "sky1": (150, 210, 255, 255), "sky2": (230, 248, 255, 255)},
    2: {"ground": (230, 235, 245, 255), "sky1": (120, 150, 190, 255), "sky2": (205, 215, 235, 255)},
    3: {"ground": (210, 225, 255, 255), "sky1": (85, 125, 200, 255), "sky2": (170, 210, 255, 255)},
    4: {"ground": (245, 235, 220, 255), "sky1": (255, 160, 95, 255), "sky2": (255, 230, 180, 255)},
    5: {"ground": (215, 215, 230, 255), "sky1": (70, 70, 120, 255), "sky2": (160, 160, 200, 255)},
}


# 字体助手

def font(name: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONT_PATH / name), size)


def bold(size: int) -> ImageFont.FreeTypeFont:
    return font("SourceHanSansCN-Bold.otf", size)


def medium(size: int) -> ImageFont.FreeTypeFont:
    return font("SourceHanSansCN-Medium.otf", size)


def rodin(size: int) -> ImageFont.FreeTypeFont:
    return font("FOT-RodinNTLGPro-DB.ttf", size)


def text_width(font_obj: ImageFont.FreeTypeFont, text: str) -> int:
    try:
        box = font_obj.getbbox(str(text))
        return box[2] - box[0]
    except Exception:
        return font_obj.getsize(str(text))[0]


def truncate_text(text: str, font_obj: ImageFont.FreeTypeFont, max_w: int) -> str:
    text = str(text or "")
    if text_width(font_obj, text) <= max_w:
        return text
    while text and text_width(font_obj, text + "…") > max_w:
        text = text[:-1]
    return text + "…"


# PIL 绘图小工具

def rounded_rect(draw: ImageDraw.ImageDraw, xy, fill, radius=12, outline=None, width=1) -> None:
    draw.rounded_rectangle(xy, radius=radius, fill=fill, outline=outline, width=width)


def paste_alpha(base: Image.Image, img: Image.Image, xy: tuple[int, int]) -> None:
    if img is None:
        return
    if img.mode != "RGBA":
        img = img.convert("RGBA")
    base.paste(img, xy, img.split()[-1])


def placeholder(size=(64, 64), text="?") -> Image.Image:
    img = Image.new("RGBA", size, (230, 230, 235, 255))
    d = ImageDraw.Draw(img)
    rounded_rect(
        d, (0, 0, size[0] - 1, size[1] - 1), (230, 230, 235, 255),
        radius=max(4, size[0] // 10), outline=(180, 180, 190), width=2,
    )
    f = bold(max(12, size[1] // 2))
    tw = text_width(f, text)
    d.text(((size[0] - tw) // 2, (size[1] - f.size) // 2 - 2), text, fill=(130, 130, 140), font=f)
    return img


def _load_image_rgba_sync(path: Path, size: Optional[tuple[int, int]] = None) -> Image.Image:
    return open_pjsk_image(path, mode="RGBA", size=size)


async def _load_image_rgba(path: Path, size: Optional[tuple[int, int]] = None) -> Image.Image:
    return await run_pjsk_thread(_load_image_rgba_sync, path, size)


def load_pic(rel: str, size: Optional[tuple[int, int]] = None) -> Image.Image:
    """加载本地 ``MYSEKAI_PICS_PATH/<rel>``；不存在则返回占位。"""
    path = MYSEKAI_PICS_PATH / rel
    if not path.exists():
        return placeholder(size or (64, 64))
    return _load_image_rgba_sync(path, size)


def load_pic_optional(rel: str) -> Optional[Image.Image]:
    """与 :func:`load_pic` 类似，但本地缺失时返回 ``None``。"""
    path = MYSEKAI_PICS_PATH / rel
    if not path.exists():
        return None
    return _load_image_rgba_sync(path)


# 杂项

def server_name(pjsk_type: int) -> str:
    return get_context().server_name(pjsk_type)


def listify(data: Any) -> list:
    if data is None:
        return []
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        return list(data.values())
    return []


def find_by(items: Iterable[dict], key: str, value: Any) -> Optional[dict]:
    for item in items or []:
        if isinstance(item, dict) and item.get(key) == value:
            return item
    return None


def find_all_by(items: Iterable[dict], key: str, value: Any) -> list[dict]:
    return [i for i in (items or []) if isinstance(i, dict) and i.get(key) == value]


def get_by_id(filename: str, item_id: int, pjsk_type: int = 0) -> Optional[dict]:
    try:
        return get_context().master_data_by_id(filename, pjsk_type).get(item_id)
    except Exception as e:
        logger.warning(f"读取 {filename} 失败: {e}")
        return None


def collect_by(filename: str, key: str, value: Any, pjsk_type: int = 0) -> list[dict]:
    try:
        return [
            i for i in listify(get_context().load_master_data(filename, pjsk_type))
            if isinstance(i, dict) and i.get(key) == value
        ]
    except Exception as e:
        logger.warning(f"读取 {filename} 失败: {e}")
        return []


def get_res_rarity(res_key: str) -> int:
    typ, sid = res_key.rsplit("_", 1)
    try:
        iid = int(sid)
    except Exception:
        iid = 0
    for rare, keys in RARE_RES_KEYS.items():
        for k in keys:
            if "~" in k:
                t2, rng = k.rsplit("_", 1)
                lo, hi = map(int, rng.split("~"))
                if typ == t2 and lo <= iid <= hi:
                    return rare
            elif k == res_key:
                return rare
    return 0


# 时间相关

def get_refresh_hours(pjsk_type: int) -> tuple[int, int]:
    """返回 MySekai 刷新小时。"""
    return (5, 17) if server_name(pjsk_type) == "cn" else (4, 16)


def get_last_refresh_time(pjsk_type: int, now: Optional[datetime] = None) -> datetime:
    h1, h2 = get_refresh_hours(pjsk_type)
    if h1 > h2:
        h1, h2 = h2, h1
    now = now or datetime.now()
    if now.hour < h1:
        return now.replace(hour=h2, minute=0, second=0, microsecond=0) - timedelta(days=1)
    if now.hour < h2:
        return now.replace(hour=h1, minute=0, second=0, microsecond=0)
    return now.replace(hour=h2, minute=0, second=0, microsecond=0)


def get_character_next_birthday_dt(cid: int, now: Optional[datetime] = None) -> Optional[datetime]:
    """从给定时间起算，下一个该角色生日的 0 点。"""
    bd = CHARACTER_BIRTHDAYS.get(cid)
    if not bd:
        return None
    month, day = bd
    now = now or datetime.now()
    candidate = datetime(now.year, month, day)
    if candidate < now:
        candidate = datetime(now.year + 1, month, day)
    return candidate


def is_fifth_anniversary(pjsk_type: int) -> bool:
    return server_name(pjsk_type) in FIFTH_ANNIV_REGIONS


def get_last_refresh_time_and_reason(
    pjsk_type: int, now: Optional[datetime] = None,
) -> tuple[datetime, str]:
    """返回 (上次刷新时间, 'natural' | 'bdstart_{cid}' | 'bdend_{cid}')。"""
    last_natural = get_last_refresh_time(pjsk_type, now)
    now = now or datetime.now()
    reason = "natural"
    if not is_fifth_anniversary(pjsk_type):
        return last_natural, reason
    # 生日掉落区间：生日前 3 天 ~ 生日当天结束
    for cid in CHARACTER_BIRTHDAYS:
        bd_dt = get_character_next_birthday_dt(cid, now - timedelta(days=1))
        if not bd_dt:
            continue
        start = bd_dt - timedelta(days=3)
        end = bd_dt
        if last_natural < start <= now:
            return start, f"bdstart_{cid}"
        if last_natural < end <= now:
            return end, f"bdend_{cid}"
    return last_natural, reason


# 单元/角色解析

def parse_unit_arg(args: str) -> tuple[Optional[str], str]:
    parts = args.strip().split()
    unit = None
    rest: list[str] = []
    for p in parts:
        mapped = UNIT_ALIASES.get(p.lower())
        if mapped and unit is None:
            unit = mapped
        else:
            rest.append(p)
    return unit, " ".join(rest).strip()


def get_cid_by_nickname(name: str, pjsk_type: int = 0) -> Optional[int]:
    name = (name or "").strip().lower()
    if not name:
        return None
    if name.isdigit():
        cid = int(name)
        if 1 <= cid <= 26:
            return cid
    alias = {
        "miku": 1, "初音": 1, "一歌": 2, "ichika": 2, "咲希": 3, "saki": 3,
        "穗波": 4, "志步": 5, "みのり": 6, "实乃理": 6, "遥": 7, "爱莉": 8, "雫": 9,
        "こはね": 10, "心羽": 10, "杏": 11, "彰人": 12, "冬弥": 13,
        "司": 14, "笑梦": 15, "emu": 15, "寧々": 16, "宁宁": 16, "类": 17,
        "奏": 18, "真冬": 19, "绘名": 20, "瑞希": 21,
        "rin": 22, "铃": 22, "len": 23, "连": 23, "luka": 24, "流歌": 24,
        "meiko": 25, "kaito": 26,
    }
    if name in alias:
        return alias[name]
    try:
        for chara in listify(get_context().load_master_data("gameCharacters.json", pjsk_type)):
            if not isinstance(chara, dict):
                continue
            names = [
                chara.get("firstName", ""), chara.get("givenName", ""),
                chara.get("firstNameRuby", ""), chara.get("givenNameRuby", ""),
                chara.get("firstNameEnglish", ""), chara.get("givenNameEnglish", ""),
            ]
            if any(name and name in str(n).lower() for n in names):
                return chara.get("id")
    except Exception:
        pass
    return None


# 远程资源拉取

def normalize_mysekai_asset_path(path: str) -> str:
    parts = path.replace("\\", "/").strip("/").split("/")
    while parts and parts[0] in ("startapp", "ondemand"):
        parts.pop(0)
    if not parts or any(part in (".", "..", "") for part in parts):
        raise ValueError("invalid asset path")
    return "/".join(parts)


def mysekai_local_candidates(path: str, pjsk_type: int = 0) -> list[Path]:
    """Reuse only the exact asset filename, never a foreign numeric master ID."""
    clean = normalize_mysekai_asset_path(path)
    variants = [clean]
    parent, name = str(Path(clean).parent), Path(clean).name
    if parent.endswith("_rip"):
        variants.append(f"{parent[:-4]}/{name}")
    elif parent != ".":
        variants.append(f"{parent}_rip/{name}")
    if clean.startswith("music/jacket/") or clean.startswith("thumbnail/music_jacket/"):
        asset = Path(name).stem
        variants.extend([f"thumbnail/music_jacket/{name}", f"music/jacket/{asset}_rip/{name}"])
    candidates = []
    # Legacy material<ID> names were synthesized from server-local master IDs;
    # unlike assetbundleName they are not evidence of cross-server equivalence.
    numeric_material = name.startswith("material") and Path(name).stem[len("material"):].isdigit()
    regions = (server_name(pjsk_type),) if numeric_material else (server_name(pjsk_type), "jp", "cn")
    for variant in dict.fromkeys(variants):
        if variant.startswith("mysekai/"):
            candidates.extend([MYSEKAI_PICS_PATH / variant[len("mysekai/"):], MYSEKAI_PICS_PATH / name])
        # Current updater layout and old data/<region>/... are both supported.
        for root in (data_path / "assets", data_path):
            for region in dict.fromkeys(regions):
                for prefix in ("", "startapp", "ondemand"):
                    candidates.append(root / region / prefix / variant)
            if not numeric_material:
                for prefix in ("", "startapp", "ondemand"):
                    candidates.append(root / prefix / variant)
    return list(dict.fromkeys(candidates))


async def probe_mysekai_asset(path: str, pjsk_type: int = 0, size=None) -> Optional[Image.Image]:
    """Decode-only local probe. A miss never writes negative cache or metadata."""
    clean = normalize_mysekai_asset_path(path)
    cached = _image_cache_get(clean, pjsk_type, size)
    if cached is not None:
        return cached
    raw = _image_cache_get(clean, pjsk_type, None)
    if raw is None:
        for candidate in mysekai_local_candidates(clean, pjsk_type):
            if candidate.is_file():
                try:
                    raw = await _load_image_rgba(candidate)
                    _image_cache_put(clean, pjsk_type, None, raw)
                    logger.debug(f"MySekai asset region={server_name(pjsk_type)} path={clean} layer=local")
                    break
                except Exception as exc:
                    logger.debug(f"MySekai asset region={server_name(pjsk_type)} path={clean} layer=local error={type(exc).__name__}")
    if raw is None:
        return None
    if size is not None and raw.size != size:
        raw = await run_pjsk_thread(raw.resize, size, Image.Resampling.LANCZOS)
    return _image_cache_put(clean, pjsk_type, size, raw)


_MYSEKAI_REMOTE_BODY_LIMIT = 16 * 1024 * 1024
_MYSEKAI_ONDEMAND_PREFIXES = ("event", "gacha", "music/long", "mysekai", "virtual_live")
_MYSEKAI_STARTAPP_PREFIXES = (
    "bonds_honor", "honor", "thumbnail", "character", "music", "rank_live",
    "stamp", "home/banner", "player_frame", "areaitem",
)


def _mysekai_external_base_url(source: dict) -> Optional[str]:
    """允许外部 HTTP 源，以及显式配置的本机 8082 updater 文件服务。"""
    is_local_updater = str(source.get("name", "")).lower() == "local-updater"
    base = str(source.get("base_url") or "").strip().rstrip("/")
    try:
        parsed = urlsplit(base)
        host = (parsed.hostname or "").lower().rstrip(".")
        if (parsed.scheme not in ("http", "https") or not host
                or parsed.username or parsed.password or parsed.query or parsed.fragment):
            return None
        if is_local_updater:
            # 仅放行用户明确配置的本机资产服务，不开放任意内网 URL。
            if parsed.port == 8082 and host in {
                "localhost", "host.docker.internal", "gateway.docker.internal", "127.0.0.1", "::1",
            }:
                return base + "/"
            return None
        if parsed.port == 8082:
            return None
        if (host in {"localhost", "host.docker.internal", "gateway.docker.internal",
                     socket.gethostname().lower()}
                or host.endswith((".localhost", ".local", ".internal"))):
            return None
        try:
            if not ipaddress.ip_address(host).is_global:
                return None
        except ValueError:
            if "." not in host:
                return None
    except ValueError:
        return None
    return base + "/"


def _mysekai_remote_urls(path: str, pjsk_type: int):
    # Read the current service configuration, not the helper's disk-first chain.
    from ...config import SERVER_CONFIG, SERVER_MAP

    clean = normalize_mysekai_asset_path(path)
    directory = str(Path(clean).parent)
    sources = SERVER_CONFIG.get(SERVER_MAP.get(pjsk_type, "jp"), {}).get("rip", {}).get("sources", [])
    seen = set()
    for source in sources:
        base = _mysekai_external_base_url(source)
        if base is None:
            continue
        prefixes = source.get("prefixes") or []
        if prefixes and not any(directory.startswith(prefix) for prefix in prefixes):
            continue
        relative = clean.replace("_rip", "")
        variants = []
        if source.get("name") == "haruki":
            if relative.startswith(_MYSEKAI_ONDEMAND_PREFIXES):
                variants.append("ondemand/" + relative)
            elif relative.startswith(_MYSEKAI_STARTAPP_PREFIXES):
                variants.append("startapp/" + relative)
        elif source.get("name") == "sekai.best":
            variants.append(relative)
        elif str(source.get("name", "")).lower() == "local-updater":
            # Updater 的缩略图可能按 bundle 分目录：<asset>/<asset>.png。
            # 先兼容平铺布局，再尝试实际导出的 bundle 子目录。
            variants.append(relative)
            file = Path(relative)
            variants.append(str(file.parent / file.stem / file.name))
        variants.append(clean)
        for variant in variants:
            url = base + quote(variant, safe="/")
            if url not in seen:
                seen.add(url)
                yield url


def _decode_mysekai_remote_image(body: bytes) -> Image.Image:
    with Image.open(BytesIO(body)) as image:
        image.verify()
    with Image.open(BytesIO(body)) as image:
        return image.convert("RGBA")


async def _fetch_mysekai_remote_image(path: str, pjsk_type: int) -> Optional[Image.Image]:
    # 只访问配置中的候选源，不跟随重定向进入未配置的地址。
    async with httpx.AsyncClient(timeout=httpx.Timeout(4.0, connect=3.0),
                                 verify=True, trust_env=False, follow_redirects=False) as client:
        for url in _mysekai_remote_urls(path, pjsk_type):
            try:
                async with client.stream("GET", url) as response:
                    response.raise_for_status()
                    length = response.headers.get("content-length")
                    if length is not None and int(length) > _MYSEKAI_REMOTE_BODY_LIMIT:
                        raise ValueError("asset body too large")
                    body = bytearray()
                    async for chunk in response.aiter_bytes(chunk_size=64 * 1024):
                        if len(body) + len(chunk) > _MYSEKAI_REMOTE_BODY_LIMIT:
                            raise ValueError("asset body too large")
                        body.extend(chunk)
                image = await run_pjsk_thread(_decode_mysekai_remote_image, bytes(body))
                logger.debug(f"MySekai asset region={server_name(pjsk_type)} path={path} layer=configured-http")
                return image
            except (httpx.HTTPError, ValueError, OSError, Image.DecompressionBombError) as exc:
                logger.debug(f"MySekai asset path={path} layer=configured-http error={type(exc).__name__}")
    return None


async def _rip_img_uncached(path: str, pjsk_type: int = 0, size=None,
                            fallback=None, skip_local: bool = False,
                            skip_remote: bool = False, return_none_on_miss: bool = True,
                            *, remote_only: bool = False):
    """Internal loader returns real images or None; never caches caller fallback."""
    clean = normalize_mysekai_asset_path(path)
    if not skip_local and not remote_only:
        local = await probe_mysekai_asset(clean, pjsk_type, size)
        if local is not None:
            return local
    if skip_remote or _negative_cache_get(clean, pjsk_type, None, remote_only=remote_only):
        return None
    loop = asyncio.get_running_loop()
    limit = _MYSEKAI_REMOTE_LIMITS.setdefault(loop, asyncio.Semaphore(8))
    state = _MYSEKAI_ASSET_REQUEST.get()

    async def fetch_asset():
        if remote_only:
            return await _fetch_mysekai_remote_image(clean, pjsk_type)
        return await get_context().get_asset(str(Path(clean).parent), Path(clean).name, pjsk_type=pjsk_type)

    async def fetch():
        async with limit:
            if state is not None:
                async with state.semaphore:
                    return await fetch_asset()
            return await fetch_asset()

    try:
        img = await asyncio.wait_for(fetch(), timeout=_MYSEKAI_REMOTE_TIMEOUT)
        if img is not None:
            if img.mode != "RGBA":
                img = await run_pjsk_thread(img.convert, "RGBA")
            if size:
                img = await run_pjsk_thread(img.resize, size, Image.Resampling.LANCZOS)
            logger.debug(f"MySekai asset region={server_name(pjsk_type)} path={clean} layer=remote")
            with _MYSEKAI_IMAGE_CACHE_LOCK:
                negative_key = (clean, pjsk_type, None, "remote-only") if remote_only else (clean, pjsk_type, None)
                _MYSEKAI_IMAGE_NEGATIVE_CACHE.pop(negative_key, None)
            return _image_cache_put(clean, pjsk_type, size, img, remote_only=remote_only)
        reason = "remote-missing"
    except Exception as exc:
        reason = type(exc).__name__
    logger.debug(f"MySekai asset region={server_name(pjsk_type)} path={clean} layer=remote reason={reason}")
    if not skip_local and not remote_only:
        # Downloads and external updaters may have committed a file while this
        # attempt failed. Decode it before deciding that the asset is missing.
        late_local = await probe_mysekai_asset(clean, pjsk_type, size)
        if late_local is not None:
            return late_local
    _negative_cache_put(clean, pjsk_type, None, remote_only=remote_only)
    return None


async def load_mysekai_asset(path: str, pjsk_type: int = 0, size=None,
                            skip_local: bool = False, skip_remote: bool = False,
                            *, remote_only: bool = False) -> Optional[Image.Image]:
    """Load a real image; remote_only bypasses every local/disk/helper lookup."""
    clean = normalize_mysekai_asset_path(path)
    cached = _image_cache_get(clean, pjsk_type, size, remote_only=remote_only)
    if cached is None and remote_only:
        cached = _image_cache_get(clean, pjsk_type, None, remote_only=True)
        if cached is not None:
            if size is not None and cached.size != size:
                cached = await run_pjsk_thread(cached.resize, size, Image.Resampling.LANCZOS)
            return _image_cache_put(clean, pjsk_type, size, cached, remote_only=True)
    if cached is not None:
        return cached
    # Probe before remote negative cache: newly arrived local files win immediately.
    if not skip_local and not remote_only:
        local = await probe_mysekai_asset(clean, pjsk_type, size)
        if local is not None:
            return local
    if skip_remote or _negative_cache_get(clean, pjsk_type, None, remote_only=remote_only):
        return None
    state = _MYSEKAI_ASSET_REQUEST.get()
    remaining = state.deadline - time.monotonic() if state and is_mysekai_fast_render() else None
    if remaining is not None and remaining <= 0:
        return None
    loop = asyncio.get_running_loop()
    key = (loop, clean, pjsk_type, bool(skip_local), bool(skip_remote), bool(remote_only))
    task = _MYSEKAI_IMAGE_INFLIGHT.get(key)
    if task is None:
        task = asyncio.create_task(_rip_img_uncached(clean, pjsk_type, skip_local=skip_local,
                                                  skip_remote=skip_remote, remote_only=remote_only))
        _MYSEKAI_IMAGE_INFLIGHT[key] = task

        def cleanup(done, flight_key=key):
            if _MYSEKAI_IMAGE_INFLIGHT.get(flight_key) is done:
                _MYSEKAI_IMAGE_INFLIGHT.pop(flight_key, None)
            try:
                done.exception()
            except (asyncio.CancelledError, Exception):
                pass

        task.add_done_callback(cleanup)
    try:
        if remaining is None:
            raw = await asyncio.shield(task)
        else:
            raw = await asyncio.wait_for(asyncio.shield(task), timeout=max(0.0, remaining))
    except asyncio.TimeoutError:
        return None
    except asyncio.CancelledError:
        # A concurrent memory-cache clear may cancel the shared flight, but
        # must not cancel a live drawing request. Caller cancellation still wins.
        current = asyncio.current_task()
        if task.cancelled() and current is not None and not current.cancelling():
            return None
        raise
    if raw is None:
        return None
    if size is not None and raw.size != size:
        raw = await run_pjsk_thread(raw.resize, size, Image.Resampling.LANCZOS)
    return _image_cache_put(clean, pjsk_type, size, raw, remote_only=remote_only)


async def rip_img(path: str, pjsk_type: int = 0, size=None, fallback=None,
                  skip_local: bool = False, skip_remote: bool = False,
                  *, remote_only: bool = False) -> Image.Image:
    img = await load_mysekai_asset(path, pjsk_type, size, skip_local, skip_remote, remote_only=remote_only)
    if img is not None:
        return img
    state = _MYSEKAI_ASSET_REQUEST.get()
    reason = "budget-exhausted" if state and is_mysekai_fast_render() and time.monotonic() >= state.deadline else "missing"
    record_missing_asset(normalize_mysekai_asset_path(path), pjsk_type, reason)
    return fallback if fallback is not None else placeholder(size or (64, 64))


async def prefetch_mysekai_assets(paths: Iterable[str], pjsk_type: int = 0) -> None:
    """Prefetch paths concurrently under the active request's shared deadline.

    Call inside mysekai_asset_request, before rendering the panels. Probe misses
    are not render misses; final rendering alone determines completeness.
    """
    await asyncio.gather(*(load_mysekai_asset(path, pjsk_type) for path in dict.fromkeys(paths)))


# 角色 SD 头像

async def get_character_icon(cid: int, pjsk_type: int = 0, size=(56, 56)) -> Image.Image:
    """按 chara_id（含组合 cuid 1-26）返回 chibi sd 头像。"""
    if cid is None:
        return placeholder(size, "?")
    candidates = [
        f"chara/chr_sd_{str(cid).zfill(2)}_01/chr_sd_{str(cid).zfill(2)}_01.png",
        f"chara/chr_sd_{cid}_01/chr_sd_{cid}_01.png",
        f"chara/chr_ts_{cid}.png",
        f"chara/chr_sd_{str(cid).zfill(2)}_01.png",
    ]
    for rel in candidates:
        p = data_path / rel
        if p.exists():
            try:
                return await _load_image_rgba(p, size)
            except Exception:
                continue
    return placeholder(size, str(cid))


async def get_chara_icon_by_chara_unit_id(cuid: int, pjsk_type: int = 0, size=(56, 56)) -> Image.Image:
    """根据 game_character_units.id 找到底层 chara_id 再取头像。"""
    cu = get_by_id("gameCharacterUnits.json", cuid, pjsk_type)
    cid = cu.get("gameCharacterId") if cu else cuid
    return await get_character_icon(cid, pjsk_type, size)


def draw_watermark(pic: Image.Image, text: str = PJSK_WATERMARK_TEXT) -> None:
    draw = ImageDraw.Draw(pic)
    f = medium(13)
    draw.text((30, pic.height - 28), text, fill=TIP_COLOR, font=f)


# 通用时间格式化

def format_time(ts) -> str:
    if not ts:
        return "未知"
    try:
        # 后端时间戳可能是秒或毫秒
        ts = int(ts)
        if ts > 10_000_000_000:
            ts = ts / 1000
        dt = datetime.fromtimestamp(ts)
        diff = int(datetime.now().timestamp() - ts)
        if diff < 60:
            rel = "刚刚"
        elif diff < 3600:
            rel = f"{diff // 60}分钟前"
        elif diff < 86400:
            rel = f"{diff // 3600}小时前"
        else:
            rel = f"{diff // 86400}天前"
        return f"{dt.strftime('%m-%d %H:%M:%S')} ({rel})"
    except Exception:
        return "未知"
