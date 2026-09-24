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

    for field in fields:
        name = field["name"]
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


def restore_compact_harvest_maps(mysekai_info: Any) -> Any:
    """Return an info copy with compact harvest map/fixture/drop rows expanded by AVSC."""
    if not isinstance(mysekai_info, dict):
        return mysekai_info
    root = _schema()
    updated_field = next(
        (field for field in root.get("fields", []) if field.get("name") == "updatedResources"),
        None,
    )
    if updated_field is None:
        raise ValueError("AVSC projection has no updatedResources field")
    updated = mysekai_info.get("updatedResources")
    if not isinstance(updated, dict):
        return mysekai_info
    restored = dict(mysekai_info)
    restored["updatedResources"] = _record(updated, updated_field["type"])
    return restored
