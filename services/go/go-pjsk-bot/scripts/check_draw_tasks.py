#!/usr/bin/env python3
"""校验 go-pjsk-bot 使用的 pjsk-draw 任务名都已在 Rust 渲染服务注册。

Go 业务侧不自建绘图，所有出图都 POST /render/{task} 委托 rust-pjsk-draw。
"""

from __future__ import annotations

import pathlib
import re
import sys


def _repo_root() -> pathlib.Path:
    return pathlib.Path(__file__).resolve().parents[4]


_GO_RE = re.compile(r'\.Render(?:Multi|WithMeta)?\(ctx,\s*"([\w_]+)"')
_GO_LITERAL_RE = re.compile(r'\{"([a-z][\w_]*)",\s*merge\(')
_RUST_NAME_FN_RE = re.compile(r'fn\s+name\s*\(\s*&self\s*\)\s*->\s*&\'static\s+str\s*\{([\s\S]*?)^\s*\}', re.M)
_QUOTED_TASK_RE = re.compile(r'"([a-z][a-z0-9_]*)"')


def go_tasks(go_dir: pathlib.Path) -> set[str]:
    out: set[str] = set()
    for f in go_dir.glob("*.go"):
        if f.name.endswith("_test.go"):
            continue
        text = f.read_text(encoding="utf-8")
        out.update(_GO_RE.findall(text))
        out.update(_GO_LITERAL_RE.findall(text))
    return out


def rust_tasks(renderers_dir: pathlib.Path) -> set[str]:
    out: set[str] = set()
    for f in renderers_dir.rglob("*.rs"):
        text = f.read_text(encoding="utf-8")
        for body in _RUST_NAME_FN_RE.findall(text):
            out.update(_QUOTED_TASK_RE.findall(body))
    return out


def main() -> int:
    root = _repo_root()
    go = go_tasks(root / "services/go/go-pjsk-bot/internal/pjsk")
    rust = rust_tasks(root / "services/rust-pjsk-draw/src/renderers")

    missing = sorted(go - rust)
    print(f"Go 使用 {len(go)} 个 draw 任务；Rust 渲染器共注册 {len(rust)} 个")
    if missing:
        print("✗ Go 使用了 Rust 侧不存在的 draw 任务（会导致出图失败）:")
        for t in missing:
            print(f"    {t}")
        return 1
    print("✓ Go 使用的所有 draw 任务名都存在于 Rust 渲染器")
    print("  仅 Rust 暴露或由其他调用方使用的任务:", ", ".join(sorted(rust - go)) or "(无)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
