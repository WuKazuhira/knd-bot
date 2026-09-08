#!/usr/bin/env python3
"""校验 go-pjsk-bot 的命令别名映射与 Python 侧所有权归一化表一致。

命令所有权互斥依赖两侧一致：
- Go：internal/pjsk/*.go 里的 r.Register(name, []string{aliases...}) / RegisterRegex(name)
- Python：src/services/go_ownership.py 里的 _PJSK_ALIAS_TO_CANON

两者漂移会导致：
- Go 有、Python 缺  -> 该命令灰度时 Python 不退场 -> 双回复
- Python 多出       -> 可能误吞本应由 Python 处理的指令

本脚本从 Go 源码提取权威映射，与 Python 表逐项比对，不一致时以非零退出，
适合接入 CI 或提交前手动运行：

    python3 src/services/go/go-pjsk-bot/scripts/check_ownership_sync.py
"""

from __future__ import annotations

import importlib.util
import pathlib
import re
import sys


def _repo_root() -> pathlib.Path:
    # scripts/ -> go-pjsk-bot -> go -> services -> src -> repo root
    return pathlib.Path(__file__).resolve().parents[5]


def extract_go_map(go_dir: pathlib.Path) -> dict[str, str]:
    go_map: dict[str, str] = {}
    reg = re.compile(r'r\.Register\("([^"]+)"\s*,\s*(nil|\[\]string\{([^}]*)\})')
    for f in go_dir.glob("*.go"):
        if f.name.endswith("_test.go"):
            continue
        text = f.read_text(encoding="utf-8")
        for m in reg.finditer(text):
            name = m.group(1)
            go_map[name] = name
            if m.group(3):
                for a in re.findall(r'"([^"]+)"', m.group(3)):
                    go_map[a] = name
        for m in re.finditer(r'RegisterRegex\("([^"]+)"', text):
            go_map.setdefault(m.group(1), m.group(1))
    return go_map


def load_py_map(path: pathlib.Path) -> dict[str, str]:
    spec = importlib.util.spec_from_file_location("go_ownership", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return dict(mod._PJSK_ALIAS_TO_CANON)


def main() -> int:
    root = _repo_root()
    go_dir = root / "src/services/go/go-pjsk-bot/internal/pjsk"
    py_path = root / "src/services/go_ownership.py"

    go_map = extract_go_map(go_dir)
    py_map = load_py_map(py_path)

    go_t, py_t = set(go_map), set(py_map)
    missing = go_t - py_t
    extra = py_t - go_t
    mismatch = {t: (go_map[t], py_map[t]) for t in go_t & py_t if go_map[t] != py_map[t]}

    ok = True
    print(f"Go 触发词: {len(go_t)}  Python 触发词: {len(py_t)}")
    if missing:
        ok = False
        print("✗ Go 有但 Python 缺失（会导致双回复）:")
        for t in sorted(missing):
            print(f"    {t!r} -> {go_map[t]!r}")
    if extra:
        ok = False
        print("✗ Python 多出（可能误吞 Python 指令）:")
        for t in sorted(extra):
            print(f"    {t!r} -> {py_map[t]!r}")
    if mismatch:
        ok = False
        print("✗ 规范名不一致:")
        for t, (g, p) in sorted(mismatch.items()):
            print(f"    {t!r}: Go={g!r} Py={p!r}")

    if ok:
        print("✓ 两侧命令所有权映射完全一致")
        return 0
    print("\n请同步 src/services/go_ownership.py 的 _PJSK_ALIAS_TO_CANON 与 Go 的 Register 调用。")
    return 1


if __name__ == "__main__":
    sys.exit(main())
