"""活动相关数据解析。

活动信息图与活动图鉴的绘制已迁至绘图服务
services/pjsk_draw/renderers/event.py；主数据解析部分放在
services/pjsk_draw/event_data.py，这里再导出给指令侧使用。
"""

import re
from typing import Dict, List, Optional, Tuple

from services.pjsk_draw.event_data import (
    analysisunitid,
    get_ban_events_id_set,
    get_event_banner_chara_id,
    get_event_card_ids,
    get_event_music_ids,
    normalize_master_list as _normalize_master_list,
)

from ._utils import get_chara_alias_map, load_master_data

__all__ = [
    "DEFAULT_CHARA_ALIAS_MAP",
    "analysisunitid",
    "extract_ban_event_arg",
    "get_ban_events_id_set",
    "get_chara_ban_events",
    "get_event_banner_chara_id",
    "get_event_card_ids",
    "get_event_music_ids",
    "resolve_chara_alias",
]

# 角色默认缩写（fallback，优先仍使用 character_nicknames.yaml）
DEFAULT_CHARA_ALIAS_MAP: Dict[str, int] = {
    'ick': 1, 'saki': 2, 'hnm': 3, 'shiho': 4,
    'mnr': 5, 'hrk': 6, 'airi': 7, 'szk': 8,
    'khn': 9, 'an': 10, 'akt': 11, 'toya': 12,
    'tks': 13, 'emu': 14, 'nene': 15, 'rui': 16,
    'knd': 17, 'mfy': 18, 'ena': 19, 'mzk': 20,
    'miku': 21, 'rin': 22, 'len': 23, 'luka': 24, 'meiko': 25, 'kaito': 26,
}


async def resolve_chara_alias(alias: str, group_id: Optional[int] = None) -> int:
    """解析角色别名为 characterId。"""
    alias = (alias or '').strip().lower()
    if not alias:
        return 0

    alias_map = get_chara_alias_map() or {}
    chara_id = alias_map.get(alias) or DEFAULT_CHARA_ALIAS_MAP.get(alias, 0)
    if chara_id:
        return int(chara_id)

    try:
        from plugins.image_management.pjsk_images.pjsk_db_source import PjskAlias
        official_alias = await PjskAlias.query_name(alias, group_id=group_id)
        if official_alias:
            return int(alias_map.get(official_alias) or DEFAULT_CHARA_ALIAS_MAP.get(official_alias, 0))
    except Exception:
        pass
    return 0


def get_chara_ban_events(chara_id: int, pjsk_type: int = 0) -> List[Dict]:
    """获取某角色所有箱活，按开始时间升序。"""
    if not chara_id:
        return []
    events = _normalize_master_list(load_master_data('events.json', pjsk_type))
    ban_ids = get_ban_events_id_set(pjsk_type)
    result = []
    for event in events:
        if not isinstance(event, dict) or event.get('id') not in ban_ids:
            continue
        if get_event_banner_chara_id(event['id'], pjsk_type) == chara_id:
            result.append(dict(event))
    result.sort(key=lambda x: x.get('startAt', 0))
    for index, event in enumerate(result, 1):
        event['ban_index'] = index
    return result


async def extract_ban_event_arg(
    text: str,
    pjsk_type: int = 0,
    group_id: Optional[int] = None,
) -> Tuple[Optional[Dict], str, Optional[str]]:
    """从文本中提取 ena7 这类箱活短写，返回 (活动, 剩余文本, 错误提示)。"""
    raw_text = text or ''
    numeric_arg = raw_text.strip()
    if numeric_arg.lstrip('-').isdigit():
        return None, raw_text, None
    for match in re.finditer(r'(?<!\w)([\w\u3040-\u30ff\u3400-\u9fff]+?)(\d+)(?!\w)', raw_text, flags=re.I):
        alias = match.group(1).strip().lower()
        seq = int(match.group(2))
        if seq <= 0:
            continue
        chara_id = await resolve_chara_alias(alias, group_id=group_id)
        if not chara_id:
            continue
        ban_events = get_chara_ban_events(chara_id, pjsk_type)
        if seq > len(ban_events):
            return None, raw_text, f"角色{alias}只有{len(ban_events)}次箱活"
        event = ban_events[seq - 1]
        rest = (raw_text[:match.start()] + raw_text[match.end():]).strip()
        rest = re.sub(r'\s+', ' ', rest)
        return event, rest, None
    return None, raw_text, None
