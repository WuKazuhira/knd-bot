"""PJSK 主数据的纯变换函数。

文件定位、缓存、回退下载和日志由调用侧管理；本模块只统一容器解包与 ID 索引语义。
"""

from dataclasses import dataclass
from typing import Any, Literal

_WRAPPER_KEYS = (
    'data',
    'items',
    'list',
    'entries',
    'rankings',
    'masterData',
    'cards',
    'musics',
    'events',
    'skills',
)

DuplicatePolicy = Literal['first', 'last']


@dataclass(frozen=True)
class MasterDataUnwrapResult:
    """解包结果及供调用侧保留日志的变换摘要。"""

    data: Any
    wrapper_key: str | None = None
    unwrapped_single_container: bool = False
    filtered_metadata_count: int = 0


def normalize_master_data(data: Any) -> MasterDataUnwrapResult:
    """按兼容顺序解包主数据，不执行文件访问或日志记录。"""
    if not isinstance(data, dict):
        return MasterDataUnwrapResult(data)

    wrapper_key = None
    for key in _WRAPPER_KEYS:
        value = data.get(key)
        if isinstance(value, (list, dict)):
            data = value
            wrapper_key = key
            break

    if not isinstance(data, dict):
        return MasterDataUnwrapResult(data, wrapper_key=wrapper_key)

    keys = list(data.keys())
    if all(str(key).isdigit() for key in keys):
        return MasterDataUnwrapResult(list(data.values()), wrapper_key=wrapper_key)

    filtered_data = [value for value in data.values() if isinstance(value, (dict, list))]
    if len(filtered_data) == 1 and len(data) > 1:
        return MasterDataUnwrapResult(
            filtered_data[0],
            wrapper_key=wrapper_key,
            unwrapped_single_container=True,
        )

    return MasterDataUnwrapResult(
        filtered_data,
        wrapper_key=wrapper_key,
        filtered_metadata_count=len(data) - len(filtered_data),
    )


def index_by_id(
    items: Any,
    key: str = 'id',
    duplicate_policy: DuplicatePolicy = 'last',
) -> dict[Any, Any]:
    """按字段建立索引；重复键可明确选择保留首条或末条。"""
    if duplicate_policy not in ('first', 'last'):
        raise ValueError(f'不支持的重复键策略: {duplicate_policy}')
    if not isinstance(items, list):
        return {}
    if duplicate_policy == 'last':
        return {item[key]: item for item in items if isinstance(item, dict) and key in item}

    indexed = {}
    for item in items:
        if isinstance(item, dict) and key in item:
            indexed.setdefault(item[key], item)
    return indexed
