"""Restore captured compact MySekai harvest rows using the project AVSC projection."""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

_SCHEMA_PATH = Path(__file__).with_name("mysekai_harvest_maps.avsc")


@lru_cache(maxsize=1)
def _schema() -> dict[str, Any]:
    return json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))


def _record(value: Any, spec: dict[str, Any]) -> dict[str, Any]:
    fields = spec.get("fields", [])
    if isinstance(value, list):
        indexed = [(field, field.get("msgpack_key")) for field in fields]
        indexed = [(field, key) for field, key in indexed if type(key) is int]
        width = max((key for _, key in indexed), default=-1) + 1
        if len(value) != width:
            raise ValueError(
                f"{spec.get('name', 'record')} compact width mismatch: "
                f"schema expects {width}, received {len(value)}"
            )
        out = {field["name"]: value[key] for field, key in indexed}
    elif isinstance(value, dict):
        out = dict(value)  # preserve unrelated fields from the API response
        for field in fields:
            name = field["name"]
            key = field.get("msgpack_key", name)
            if key in value:
                out[name] = value[key]
            elif isinstance(key, int) and str(key) in value:
                out[name] = value[str(key)]
    else:
        raise TypeError(f"{spec.get('name', 'record')} must be an object or compact array")

    # Some compact records omit fields that are present in the dict form.  Defaults
    # let callers retain the raw positional slots while using a safe normalized value.
    for field in fields:
        name = field["name"]
        if name not in out and "default" in field:
            out[name] = field["default"]
        if name in out:
            out[name] = _restore_value(out[name], field["type"])
    return out


def _restore_value(value: Any, spec: Any) -> Any:
    if isinstance(spec, list):  # Avro union, including nullable relation-group IDs
        if value is None:
            return None
        for branch in spec:
            if branch != "null":
                return _restore_value(value, branch)
        return value
    if not isinstance(spec, dict):
        return value
    kind = spec.get("type")
    if kind == "record":
        return _record(value, spec)
    if kind == "array":
        if value is None:
            return None
        if not isinstance(value, list):
            raise TypeError("AVSC array field must be a list")
        return [_restore_value(item, spec["items"]) for item in value]
    if isinstance(kind, (dict, list)):
        return _restore_value(value, kind)
    return value


def restore_compact_mysekai_data(payload: Any) -> Any:
    """Restore compact MySekai fields in either an info or Suite payload."""
    if not isinstance(payload, dict):
        return payload
    root = _schema()
    restored = dict(payload)
    for field in root.get("fields", []):
        name = field.get("name")
        if name in payload:
            restored[name] = _restore_value(payload[name], field["type"])
    return restored


def restore_compact_harvest_maps(mysekai_info: Any) -> Any:
    """Backward-compatible alias for the generic MySekai compact restorer."""
    return restore_compact_mysekai_data(mysekai_info)


def effective_drop_quantity(drop: Any) -> int:
    """Return display quantity without mistaking a compact timing slot for quantity."""
    if not isinstance(drop, dict):
        return 1
    value = drop.get("quantity")
    if value is None:
        return 1
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 1
