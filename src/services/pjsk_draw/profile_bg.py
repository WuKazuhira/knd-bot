"""个人信息图的自定义背景存储。

属于绘图服务的资源存储：背景图和每个用户的背景参数都放在
data/pjsk/static/profile_bg 下，出图时由 renderers/profile 读取。
「上传个人信息背景」等指令通过本模块写入。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional, Tuple

from PIL import Image

from utils.pjsk_paths import STATIC_PATH

PROFILE_BG_DIR = STATIC_PATH / 'profile_bg'
PROFILE_BG_SETTINGS_FILE = PROFILE_BG_DIR / 'settings.json'

_SETTINGS_CACHE_META: Optional[Tuple[int, int]] = None
_SETTINGS_CACHE: dict = {}


def _ensure_profile_bg_dir():
    """确保 profile_bg 目录存在"""
    PROFILE_BG_DIR.mkdir(parents=True, exist_ok=True)


def _load_settings() -> dict:
    """读取背景设置 JSON（带 mtime 缓存）"""
    global _SETTINGS_CACHE_META, _SETTINGS_CACHE
    _ensure_profile_bg_dir()
    if not PROFILE_BG_SETTINGS_FILE.exists():
        _SETTINGS_CACHE_META = None
        _SETTINGS_CACHE = {}
        return {}
    try:
        stat = PROFILE_BG_SETTINGS_FILE.stat()
        meta = (stat.st_mtime_ns, stat.st_size)
        if _SETTINGS_CACHE_META == meta:
            return dict(_SETTINGS_CACHE)
        with open(PROFILE_BG_SETTINGS_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
        _SETTINGS_CACHE_META = meta
        _SETTINGS_CACHE = data if isinstance(data, dict) else {}
        return dict(_SETTINGS_CACHE)
    except (json.JSONDecodeError, OSError):
        return {}


def _save_settings(settings: dict):
    """保存背景设置 JSON，并同步缓存"""
    global _SETTINGS_CACHE_META, _SETTINGS_CACHE
    _ensure_profile_bg_dir()
    with open(PROFILE_BG_SETTINGS_FILE, 'w', encoding='utf-8') as f:
        json.dump(settings, f, ensure_ascii=False, separators=(',', ':'))
    try:
        stat = PROFILE_BG_SETTINGS_FILE.stat()
        _SETTINGS_CACHE_META = (stat.st_mtime_ns, stat.st_size)
        _SETTINGS_CACHE = dict(settings)
    except OSError:
        _SETTINGS_CACHE_META = None
        _SETTINGS_CACHE = dict(settings)


def get_user_bg_settings(userid: str, server: str) -> dict:
    """获取用户背景设置"""
    settings = _load_settings()
    key = f'{server}:{userid}'
    return settings.get(key, {})


def set_user_bg_settings(userid: str, server: str, **kwargs):
    """设置用户背景参数（只更新非None的值）"""
    settings = _load_settings()
    key = f'{server}:{userid}'
    if key not in settings:
        settings[key] = {}
    for k, v in kwargs.items():
        if v is not None:
            settings[key][k] = v
    _save_settings(settings)


def get_user_bg_path(userid: str, server: str) -> Path:
    """获取用户自定义背景图路径"""
    return PROFILE_BG_DIR / server / f'{userid}.jpg'


def save_user_bg(userid: str, server: str, img: Image.Image):
    """保存用户自定义背景图，限制最大边 3000px，保存为 jpg quality=85"""
    bg_path = get_user_bg_path(userid, server)
    bg_path.parent.mkdir(parents=True, exist_ok=True)
    # 限制最大边
    max_side = 3000
    w, h = img.size
    if w > max_side or h > max_side:
        ratio = max_side / max(w, h)
        img = img.resize((int(w * ratio), int(h * ratio)), Image.LANCZOS)
    img = img.convert('RGB')
    img.save(bg_path, 'JPEG', quality=85)
    # 自动设置默认参数
    current = get_user_bg_settings(userid, server)
    if 'vertical' not in current:
        set_user_bg_settings(userid, server, vertical=False)
    if 'blur' not in current:
        set_user_bg_settings(userid, server, blur=1)
    if 'alpha' not in current:
        set_user_bg_settings(userid, server, alpha=180)


def remove_user_bg(userid: str, server: str):
    """删除用户自定义背景图"""
    bg_path = get_user_bg_path(userid, server)
    if bg_path.exists():
        bg_path.unlink()
