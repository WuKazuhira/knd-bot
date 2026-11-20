"""基于 orjson 的高性能 JSON 编解码工具（未安装或遇到非标准 JSON 时回退标准库 json）。"""

from __future__ import annotations

import json
from typing import Any

try:
    import orjson as _orjson
except ImportError:  # pragma: no cover
    _orjson = None

HAS_ORJSON = _orjson is not None


def loads_bytes(data: bytes | bytearray | memoryview | str) -> Any:
    if _orjson is not None:
        try:
            return _orjson.loads(data)
        except ValueError:
            pass
    if isinstance(data, (bytes, bytearray)):
        data = data.decode("utf-8")
    elif isinstance(data, memoryview):
        data = bytes(data).decode("utf-8")
    return json.loads(data)


def load_file(path) -> Any:
    with open(path, "rb") as f:
        return loads_bytes(f.read())


def dumps_bytes(obj: Any, *, default: Any = None, sort_keys: bool = False) -> bytes:
    if _orjson is not None:
        option = _orjson.OPT_NON_STR_KEYS
        if sort_keys:
            option |= _orjson.OPT_SORT_KEYS
        try:
            return _orjson.dumps(obj, default=default, option=option)
        except TypeError:
            pass
    return json.dumps(
        obj,
        ensure_ascii=False,
        default=default,
        sort_keys=sort_keys,
        separators=(",", ":"),
    ).encode("utf-8")
