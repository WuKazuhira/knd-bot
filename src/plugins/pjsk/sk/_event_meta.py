"""事件/章节时间范围与类型的统一解析（两机共用，纯 stdlib）。

供「数据导出」(scripts/export_dataset.py) 与「推断端」(src/plugins/pjsk/sk/_model.py)
复用，从而保证两端拿到相同的 event 元信息（start/end/type）。

直接读取 masterdata 的 events.json / worldBlooms.json，不依赖 bot 运行时，
保证在仅拷 subset 给高性能机训练时也能独立工作。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Dict, Optional, Tuple

# WL 章节编码系数（与 _features / _forecast 一致）
_WL_FACTOR = 1000


@dataclass
class EventMeta:
    """单个事件/章节的时间范围与类型。"""

    start_ts: int  # unix 秒
    end_ts: int  # unix 秒（aggregateAt / 章节结束）
    event_type: str  # marathon / world_bloom


def _load_json(path: str) -> list:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict):
        for key in ("data", "items", "list", "entries"):
            if key in data and isinstance(data[key], list):
                data = data[key]
                break
    if not isinstance(data, list):
        raise ValueError(f"expected JSON list at {path}")
    return data


class UnreadableMasterdataError(PermissionError):
    """masterdata 文件存在但无读取权限（如 jp 为 root 600）。"""


def load_events(masterdata_root: str, region: str) -> list:
    path = _events_path(masterdata_root, region)
    try:
        return _load_json(path)
    except PermissionError as e:
        raise UnreadableMasterdataError(f"无法读取 {path}: {e}") from e


def load_world_blooms(masterdata_root: str, region: str) -> list:
    path = _world_blooms_path(masterdata_root, region)
    try:
        return _load_json(path)
    except PermissionError as e:
        raise UnreadableMasterdataError(f"无法读取 {path}: {e}") from e


def _region_dir(masterdata_root: str, region: str) -> str:
    return os.path.join(masterdata_root, region)


def _events_path(masterdata_root: str, region: str) -> str:
    return os.path.join(_region_dir(masterdata_root, region), "events.json")


def _world_blooms_path(masterdata_root: str, region: str) -> str:
    return os.path.join(_region_dir(masterdata_root, region), "worldBlooms.json")


def _event_index(events: list) -> Dict[int, dict]:
    return {int(e.get("id", -1)): e for e in events}


def resolve_meta(
    masterdata_root: str,
    region: str,
    event_id: int,
    fallback: Optional[Tuple[Optional[int], Optional[int]]] = None,
) -> Tuple[Optional[EventMeta], int]:
    """返回 (meta, 活动类型编码)。

    event_id >= _WL_FACTOR 视为 WL 章节编码 (chapterNo*1000+base_event_id)，
    从 worldBlooms.json 解析章节时间；否则从 events.json 解析整场活动时间。

    fallback=(start_ts, end_ts) 用于 masterdata 读不到时（如 jp 为 root 600）用
    数据库自身 MIN/MAX(ts) 推断时间范围。此时活动类型：WL 章节编码判定为
    world_bloom，其余判定为 marathon（可能不精确，仅作回退）。

    返回的 event_type 恒为 'marathon'/'world_bloom' 之一（未知归为 marathon）。
    """
    if event_id >= _WL_FACTOR:
        base_id = event_id % _WL_FACTOR
        chapter_no = event_id // _WL_FACTOR
        try:
            chapters = load_world_blooms(masterdata_root, region)
        except UnreadableMasterdataError:
            chapters = []
        for ch in chapters:
            if (
                isinstance(ch, dict)
                and int(ch.get("eventId", -1)) == base_id
                and int(ch.get("chapterNo", -1)) == chapter_no
            ):
                start_ts = int(ch["chapterStartAt"] / 1000)
                end_ts = int(ch["aggregateAt"] / 1000)
                if end_ts > start_ts:
                    return EventMeta(start_ts, end_ts, "world_bloom"), 1
        if fallback is not None and fallback[0] is not None and fallback[1] is not None:
            st, et = fallback
            if et > st:
                return EventMeta(int(st), int(et), "world_bloom"), 1
        return None, 1

    try:
        events = load_events(masterdata_root, region)
    except UnreadableMasterdataError:
        events = []
    idx = _event_index(events)
    ev = idx.get(int(event_id))
    if ev is not None:
        start_ts = int(ev["startAt"] / 1000)
        end_ts = int(ev["aggregateAt"] / 1000)
        if end_ts > start_ts:
            etype = str(ev.get("eventType", "marathon"))
            if etype == "world_bloom":
                return EventMeta(start_ts, end_ts, "world_bloom"), 1
            return EventMeta(start_ts, end_ts, "marathon"), 0
    if fallback is not None and fallback[0] is not None and fallback[1] is not None:
        st, et = fallback
        if et > st:
            return EventMeta(int(st), int(et), "marathon"), 0
    return None, 0
