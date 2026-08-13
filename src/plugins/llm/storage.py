from __future__ import annotations

import json
from pathlib import Path
from threading import RLock
from typing import Any


class JsonFileDB:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        self._data: dict[str, Any] | None = None

    def _load(self) -> dict[str, Any]:
        with self._lock:
            if self._data is None:
                if self.path.exists():
                    try:
                        self._data = json.loads(self.path.read_text(encoding="utf-8"))
                    except Exception:
                        self._data = {}
                else:
                    self._data = {}
            return self._data

    def _save(self):
        with self._lock:
            self.path.write_text(json.dumps(self._load(), ensure_ascii=False, indent=2), encoding="utf-8")

    def get(self, key: str, default: Any = None) -> Any:
        return self._load().get(key, default)

    def set(self, key: str, value: Any) -> None:
        self._load()[key] = value
        self._save()

    def delete(self, key: str) -> None:
        self._load().pop(key, None)
        self._save()


def get_file_db(path: str | Path) -> JsonFileDB:
    return JsonFileDB(path)
