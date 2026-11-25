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
    data: Any
    wrapper_key: str | None = None
    unwrapped_single_container: bool = False
    filtered_metadata_count: int = 0


def normalize_master_data(data: Any) -> MasterDataUnwrapResult:
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
