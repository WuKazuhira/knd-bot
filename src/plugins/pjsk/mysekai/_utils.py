from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from datetime import datetime, timedelta
from pathlib import Path
from threading import RLock
from typing import Any, Iterable, Optional

from PIL import Image, ImageDraw, ImageFont

from config.path_config import FONT_PATH
from services.log import logger

from .._autoask import pjsk_update_manager
from .._common_utils import PJSK_WATERMARK_TEXT
from .._config import SERVER_MAP, data_path
from .._paths import PROFILE_PATH, STATIC_PATH
from .._utils import load_master_data, master_data_by_id, open_pjsk_image, run_pjsk_thread

# 路径常量

MYSEKAI_PICS_PATH = data_path / "pics" / "mysekai"
CACHE_PATH = PROFILE_PATH / "mysekai"
CN_MSR_GROUPS_FILE = STATIC_PATH / "cn_msr_allowed_groups.json"

# MySekai 图片缓存：同一进程内复用本地解码和远程资源结果。
_MYSEKAI_IMAGE_CACHE: OrderedDict[tuple[str, int, Optional[tuple[int, int]]], Image.Image] = OrderedDict()
_MYSEKAI_IMAGE_CACHE_LIMIT = 512
_MYSEKAI_IMAGE_CACHE_LOCK = RLock()
_MYSEKAI_IMAGE_NEGATIVE_CACHE: OrderedDict[tuple[str, int, Optional[tuple[int, int]]], float] = OrderedDict()
_MYSEKAI_IMAGE_NEGATIVE_TTL = 300.0
_MYSEKAI_IMAGE_INFLIGHT: dict[tuple[str, int, Optional[tuple[int, int]]], asyncio.Task] = {}


def _negative_cache_get(
    path: str,
    pjsk_type: int,
    size: Optional[tuple[int, int]],
) -> bool:
    key = (path, pjsk_type, size)
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
) -> None:
    key = (path, pjsk_type, size)
    with _MYSEKAI_IMAGE_CACHE_LOCK:
        _MYSEKAI_IMAGE_NEGATIVE_CACHE[key] = time.monotonic() + _MYSEKAI_IMAGE_NEGATIVE_TTL
        _MYSEKAI_IMAGE_NEGATIVE_CACHE.move_to_end(key)
        while len(_MYSEKAI_IMAGE_NEGATIVE_CACHE) > _MYSEKAI_IMAGE_CACHE_LIMIT:
            _MYSEKAI_IMAGE_NEGATIVE_CACHE.popitem(last=False)


def _image_cache_get(
    path: str,
    pjsk_type: int,
    size: Optional[tuple[int, int]],
) -> Optional[Image.Image]:
    key = (path, pjsk_type, size)
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
) -> Image.Image:
    key = (path, pjsk_type, size)
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
    return SERVER_MAP.get(pjsk_type, "jp")


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
        return master_data_by_id(filename, pjsk_type).get(item_id)
    except Exception as e:
        logger.warning(f"读取 {filename} 失败: {e}")
        return None


def collect_by(filename: str, key: str, value: Any, pjsk_type: int = 0) -> list[dict]:
    try:
        return [
            i for i in listify(load_master_data(filename, pjsk_type))
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
        for chara in listify(load_master_data("gameCharacters.json", pjsk_type)):
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

async def _rip_img_uncached(
    path: str,
    pjsk_type: int = 0,
    size: Optional[tuple[int, int]] = None,
    fallback: Optional[Image.Image] = None,
    skip_local: bool = False,
    skip_remote: bool = False,
) -> Image.Image:
    """加载游戏解包资源（``mysekai/...`` 或 ``thumbnail/...``）。

    查找顺序（命中即返回，避免触发 ``pjsk_update_manager`` 的远程下载 WARNING 日志）：

    1. ``skip_local=False`` 时，逐个尝试本地候选位置，包括：
       - ``MYSEKAI_PICS_PATH/<去掉 mysekai 前缀和子目录后的扁平名>``（kndbot 老布局，
         材料/道具图标都直接放在 ``pics/mysekai`` 根目录）
       - ``MYSEKAI_PICS_PATH/<去掉 mysekai/ 前缀的相对路径>``（保留子目录的新布局）
       - ``data/<region>/<原始路径>``、``data/<region>/startapp/<原始路径>``
         （pjsk_update_manager 已下载到的服务器缓存）
       - ``data/jp/...`` 与 ``data/cn/...`` 兜底（不区分服）
    2. ``skip_remote=False`` 时，调用 ``pjsk_update_manager.get_asset`` 走远程下载。
       该函数若本地缺失会输出 WARNING；返回 None 也表示找不到。
    3. 仍失败则返回 ``fallback`` 或占位图。
    """
    img: Optional[Image.Image] = None
    clean_path = path.replace("startapp/", "")
    cached = _image_cache_get(clean_path, pjsk_type, size)
    if cached is not None:
        return cached

    if not skip_local:
        candidates: list[Path] = []
        name = Path(clean_path).name

        if clean_path.startswith("mysekai/"):
            inner = clean_path[len("mysekai/"):]
            # 1. pics/mysekai 老布局：材料/道具图标都在根目录
            #    例：mysekai/thumbnail/material/item_junk_6.png → pics/mysekai/item_junk_6.png
            if inner.startswith("thumbnail/material/") or inner.startswith("thumbnail/item/"):
                candidates.append(MYSEKAI_PICS_PATH / name)
            # 2. pics/mysekai 子目录布局
            candidates.append(MYSEKAI_PICS_PATH / inner)
            # 3. pics/mysekai 直接平铺
            candidates.append(MYSEKAI_PICS_PATH / name)

        # 4. server cache: data/<region>/mysekai/...
        for region in (server_name(pjsk_type), "jp", "cn"):
            if region:
                candidates.append(data_path / region / clean_path)
                candidates.append(data_path / region / "startapp" / clean_path)
        # 5. 仓库自带的部分资源走 data_path/<原路径>
        candidates.append(data_path / clean_path)
        candidates.append(data_path / "startapp" / clean_path)

        seen = set()
        for cand in candidates:
            if cand in seen:
                continue
            seen.add(cand)
            if cand.exists() and cand.is_file():
                try:
                    img = await _load_image_rgba(cand)
                    break
                except Exception as e:
                    logger.debug(f"本地资源 {cand} 加载失败: {e}")

    if img is None and not skip_remote:
        parent = str(Path(clean_path).parent)
        name = Path(clean_path).name
        try:
            img = await pjsk_update_manager.get_asset(parent, name, pjsk_type=pjsk_type)
        except Exception as e:
            logger.debug(f"远程资源 {path} 加载失败: {e}")

    if img is None:
        _negative_cache_put(clean_path, pjsk_type, size)
        return fallback or placeholder(size or (64, 64))
    if img.mode != "RGBA":
        img = await run_pjsk_thread(img.convert, "RGBA")
    if size:
        img = await run_pjsk_thread(img.resize, size, Image.Resampling.LANCZOS)
    return _image_cache_put(clean_path, pjsk_type, size, img)


async def rip_img(
    path: str,
    pjsk_type: int = 0,
    size: Optional[tuple[int, int]] = None,
    fallback: Optional[Image.Image] = None,
    skip_local: bool = False,
    skip_remote: bool = False,
) -> Image.Image:
    """按原图路径缓存，按请求尺寸缩放，合并并发下载。"""
    clean_path = path.replace("startapp/", "")
    cached = _image_cache_get(clean_path, pjsk_type, size)
    if cached is not None:
        return cached
    if _negative_cache_get(clean_path, pjsk_type, None):
        return fallback or placeholder(size or (64, 64))

    key = (clean_path, pjsk_type, None)
    task = _MYSEKAI_IMAGE_INFLIGHT.get(key)
    if task is None:
        task = asyncio.create_task(
            _rip_img_uncached(
                path,
                pjsk_type=pjsk_type,
                size=None,
                fallback=fallback,
                skip_local=skip_local,
                skip_remote=skip_remote,
            )
        )
        _MYSEKAI_IMAGE_INFLIGHT[key] = task
    try:
        raw = await asyncio.shield(task)
        if raw is None:
            return fallback or placeholder(size or (64, 64))
        if size is not None and raw.size != size:
            raw = await run_pjsk_thread(raw.resize, size, Image.Resampling.LANCZOS)
            return _image_cache_put(clean_path, pjsk_type, size, raw)
        return raw.copy()
    finally:
        if task.done() and _MYSEKAI_IMAGE_INFLIGHT.get(key) is task:
            _MYSEKAI_IMAGE_INFLIGHT.pop(key, None)


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
