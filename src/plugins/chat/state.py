from __future__ import annotations

from config.path_config import DATA_PATH
from plugins.llm.storage import get_file_db


file_db = get_file_db(DATA_PATH / "chat" / "db.json")


def _set_members(key: str) -> set[int]:
    return {int(x) for x in file_db.get(key, []) or []}


def _save_members(key: str, values: set[int]):
    file_db.set(key, sorted(int(x) for x in values))


def is_chat_enabled(group_id: int) -> bool:
    return int(group_id) in _set_members("chat_enabled_groups")


def set_chat_enabled(group_id: int, enabled: bool):
    groups = _set_members("chat_enabled_groups")
    groups.add(int(group_id)) if enabled else groups.discard(int(group_id))
    _save_members("chat_enabled_groups", groups)


def is_autochat_enabled(group_id: int) -> bool:
    return int(group_id) in _set_members("autochat_enabled_groups")


def set_autochat_enabled(group_id: int, enabled: bool):
    groups = _set_members("autochat_enabled_groups")
    groups.add(int(group_id)) if enabled else groups.discard(int(group_id))
    _save_members("autochat_enabled_groups", groups)


def enabled_autochat_groups() -> list[int]:
    return sorted(_set_members("chat_enabled_groups") & _set_members("autochat_enabled_groups"))


def get_model_key(is_group: bool, target_id: int) -> str:
    return f"{'group' if is_group else 'private'}_model:{int(target_id)}"


def get_model(target_id: int, is_group: bool, mode: str, default):
    data = file_db.get(get_model_key(is_group, target_id), {}) or {}
    if isinstance(default, dict):
        return data.get(mode) or default.get(mode)
    return data.get(mode) or default


def set_model(target_id: int, is_group: bool, mode: str, model_name: str):
    key = get_model_key(is_group, target_id)
    data = file_db.get(key, {}) or {}
    data[mode] = model_name
    file_db.set(key, data)


def clear_model(target_id: int, is_group: bool):
    file_db.delete(get_model_key(is_group, target_id))
