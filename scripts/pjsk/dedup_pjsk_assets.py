#!/usr/bin/env python3
"""扫描或执行 CN/TW 与 JP PJSK 资源去重。

用法：
    python3 scripts/pjsk/dedup_pjsk_assets.py [--region cn] [--apply]
"""

from __future__ import annotations

import argparse
import hashlib
import os
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
ONDEMAND_PATH = Path(os.getenv("PJSK_DATA_DIR") or ROOT_DIR / "data" / "pjsk") / "ondemand"

DEDUP_REGIONS = ("cn", "tw")
DEDUP_PREFIXES = (
    "ondemand/music/long/",
    "music/long/",
    "startapp/music/music_score/",
    "startapp/music/jacket/",
    "startapp/character/member/",
    "startapp/thumbnail/chara/",
    "charts/",
)


@dataclass
class DedupStats:
    scanned: int = 0
    candidates: int = 0
    duplicates: int = 0
    linked: int = 0
    symlinked: int = 0
    skipped: int = 0
    errors: int = 0
    saved_bytes: int = 0


def normalize_relative(path: str | Path) -> str:
    return str(path).replace("\\", "/").lstrip("/")


def is_dedup_candidate(relative_path: str | Path) -> bool:
    rel = normalize_relative(relative_path)
    return any(rel.startswith(prefix) for prefix in DEDUP_PREFIXES)


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        while chunk := file.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def same_file_content(path_a: Path, path_b: Path) -> bool:
    try:
        if path_a.stat().st_size != path_b.stat().st_size:
            return False
        return sha256_file(path_a) == sha256_file(path_b)
    except OSError:
        return False


def _replace_with_link(source: Path, target: Path) -> str:
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(f".{target.name}.dedup-{os.getpid()}")
    temp.unlink(missing_ok=True)
    try:
        try:
            os.link(source, temp)
            method = "hardlink"
        except OSError:
            temp.symlink_to(os.path.relpath(source, target.parent))
            method = "symlink"
        os.replace(temp, target)
        return method
    finally:
        temp.unlink(missing_ok=True)


def link_if_same(source: Path, target: Path) -> str | None:
    if not source.is_file() or not target.is_file():
        return None
    if source.resolve() == target.resolve():
        return "existing"
    if not same_file_content(source, target):
        return None
    return _replace_with_link(source, target)


def _iter_region_files(region_root: Path) -> Iterable[Path]:
    if not region_root.is_dir():
        return ()
    return (path for path in region_root.rglob("*") if path.is_file() and not path.is_symlink())


def deduplicate_region(
    region: str,
    root: Path = ONDEMAND_PATH,
    apply: bool = False,
) -> DedupStats:
    if region not in DEDUP_REGIONS:
        raise ValueError(f"不支持去重的服务器: {region}")
    stats = DedupStats()
    jp_root = root / "jp"
    region_root = root / region
    for target in _iter_region_files(region_root):
        stats.scanned += 1
        relative = target.relative_to(region_root)
        if not is_dedup_candidate(relative):
            continue
        stats.candidates += 1
        source = jp_root / relative
        if not source.is_file():
            stats.skipped += 1
            continue
        try:
            target_size = target.stat().st_size
            if not same_file_content(source, target):
                continue
            stats.duplicates += 1
            stats.saved_bytes += target_size
            if apply:
                method = link_if_same(source, target)
                if method == "hardlink":
                    stats.linked += 1
                elif method == "symlink":
                    stats.symlinked += 1
                elif method == "existing":
                    stats.skipped += 1
                else:
                    stats.errors += 1
        except OSError:
            stats.errors += 1
    return stats


def deduplicate(
    regions: Iterable[str] = DEDUP_REGIONS,
    root: Path = ONDEMAND_PATH,
    apply: bool = False,
) -> dict[str, DedupStats]:
    return {region: deduplicate_region(region, root=root, apply=apply) for region in regions}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--region", action="append", choices=("cn", "tw"))
    parser.add_argument("--apply", action="store_true", help="实际替换为 JP 硬链接/软链接")
    args = parser.parse_args()
    regions = args.region or ["cn", "tw"]
    stats = deduplicate(regions=regions, apply=args.apply)
    for region, item in stats.items():
        print(
            f"{region.upper()}: scanned={item.scanned} candidates={item.candidates} "
            f"duplicates={item.duplicates} saved={item.saved_bytes} "
            f"hardlinks={item.linked} symlinks={item.symlinked} "
            f"skipped={item.skipped} errors={item.errors}"
        )
    return 0 if not any(item.errors for item in stats.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
