#!/usr/bin/env python3
"""将旧 data/pjsk 目录安全迁移为 static/ondemand 布局。"""

from __future__ import annotations

import argparse
import filecmp
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "data" / "pjsk"


def _same_file(a: Path, b: Path) -> bool:
    try:
        return filecmp.cmp(a, b, shallow=False)
    except OSError:
        return False


def _merge(source: Path, target: Path, apply: bool) -> tuple[int, int]:
    moved = conflicts = 0
    if not source.exists() and not source.is_symlink():
        return moved, conflicts
    if source.is_dir() and not source.is_symlink():
        if target.exists() and not target.is_dir():
            return 0, 1
        if apply:
            target.mkdir(parents=True, exist_ok=True)
        for child in list(source.iterdir()):
            child_moved, child_conflicts = _merge(child, target / child.name, apply)
            moved += child_moved
            conflicts += child_conflicts
        if apply and source.exists() and not any(source.iterdir()):
            source.rmdir()
        return moved, conflicts

    if target.exists() or target.is_symlink():
        if target.is_file() and source.is_file() and _same_file(source, target):
            if apply:
                source.unlink()
            return 1, 0
        return 0, 1
    if apply:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source), str(target))
    return 1, 0


def plan_moves() -> list[tuple[Path, Path]]:
    old_masterdata = ROOT / "masterdata"
    old_profile = ROOT / "profile"
    moves: list[tuple[Path, Path]] = []

    for name in ("bonds", "chara", "pics", "profile_bg"):
        moves.append((old_masterdata / name, ROOT / "static" / name))
    for name in ("jp", "cn", "tw", "realtime"):
        moves.append((old_masterdata / name, ROOT / "ondemand" / name))

    moves.extend(
        [
            (old_profile / "mysekai", ROOT / "ondemand" / "profile" / "mysekai"),
            (old_profile / "suite", ROOT / "ondemand" / "suite"),
            (ROOT / "suite", ROOT / "ondemand" / "suite"),
        ]
    )
    for name in ("deckrec", "forecast", "remote", "database", "temp"):
        moves.append((ROOT / name, ROOT / "ondemand" / name))
    for name in ("deck_backend_state.json", "sk_api_state.json"):
        moves.append((ROOT / name, ROOT / "ondemand" / name))
    return moves


def prune_empty_old_dirs() -> None:
    for path in (ROOT / "masterdata", ROOT / "profile", ROOT / "suite"):
        if path.is_dir() and not any(path.iterdir()):
            path.rmdir()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="实际移动文件；默认只预览")
    args = parser.parse_args()

    total_moved = total_conflicts = 0
    for source, target in plan_moves():
        if not source.exists() and not source.is_symlink():
            continue
        moved, conflicts = _merge(source, target, args.apply)
        total_moved += moved
        total_conflicts += conflicts
        action = "迁移" if args.apply else "将迁移"
        print(f"[{action}] {source} -> {target}: files={moved}, conflicts={conflicts}")

    if args.apply:
        prune_empty_old_dirs()
    print(f"完成：files={total_moved}, conflicts={total_conflicts}, mode={'apply' if args.apply else 'dry-run'}")
    return 2 if total_conflicts else 0


if __name__ == "__main__":
    raise SystemExit(main())
