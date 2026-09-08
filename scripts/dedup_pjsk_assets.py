#!/usr/bin/env python3
"""扫描或执行 CN/TW 与 JP PJSK 资源去重。"""

from __future__ import annotations

import argparse
import os

os.environ.setdefault("KNDBOT_SKIP_PJSK_PLUGIN_AUTOLOAD", "1")

from plugins.pjsk._asset_dedup import deduplicate  # noqa: E402


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
