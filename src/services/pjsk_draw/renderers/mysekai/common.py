from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Optional

from ...context import get_context

UNIT_GATEID_MAP = {
    "light_sound": 1,
    "idol": 2,
    "street": 3,
    "theme_park": 4,
    "school_refusal": 5,
}

UNIT_ALIASES = {
    "ln": "light_sound", "leo": "light_sound", "l/n": "light_sound", "leoneed": "light_sound", "leo/need": "light_sound", "星星": "light_sound",
    "mmj": "idol", "mm": "idol", "moremorejump": "idol", "偶像": "idol",
    "vbs": "street", "vividbadsquad": "street", "街头": "street",
    "ws": "theme_park", "wxs": "theme_park", "wonderlands": "theme_park", "游乐园": "theme_park",
    "25": "school_refusal", "n25": "school_refusal", "ニーゴ": "school_refusal", "25时": "school_refusal", "25時": "school_refusal", "nightcord": "school_refusal",
}


def server_name(pjsk_type: int) -> str:
    return get_context().server_name(pjsk_type)


def get_refresh_hours(pjsk_type: int) -> tuple[int, int]:
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


def _listify(data: Any) -> list:
    if data is None:
        return []
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        return list(data.values())
    return []


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
        for chara in _listify(get_context().load_master_data("gameCharacters.json", pjsk_type)):
            if not isinstance(chara, dict):
                continue
            names = [
                chara.get("firstName", ""), chara.get("givenName", ""),
                chara.get("firstNameRuby", ""), chara.get("givenNameRuby", ""),
                f"{chara.get('firstName', '')}{chara.get('givenName', '')}",
            ]
            if any(n and n.lower() == name for n in names):
                return int(chara["id"])
    except Exception:
        pass
    return None
