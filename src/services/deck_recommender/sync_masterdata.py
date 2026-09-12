#!/usr/bin/env python3
"""Sync allium 组卡所需的 masterdata files via bot's PJSK updater."""
from __future__ import annotations

import argparse
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[3]
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from services.pjsk_shared.config import SERVER_MAP, data_path

REQUIRED_MASTERDATA_FILES = [
    "areaItemLevels.json",
    "areaItems.json",
    "areas.json",
    "cardEpisodes.json",
    "cards.json",
    "cardRarities.json",
    "characterRanks.json",
    "eventCards.json",
    "eventDeckBonuses.json",
    "eventExchangeSummaries.json",
    "events.json",
    "eventItems.json",
    "eventRarityBonusRates.json",
    "gameCharacters.json",
    "gameCharacterUnits.json",
    "honors.json",
    "masterLessons.json",
    "musicDifficulties.json",
    "musics.json",
    "musicVocals.json",
    "shopItems.json",
    "skills.json",
    "worldBloomDifferentAttributeBonuses.json",
    "worldBlooms.json",
    "worldBloomSupportDeckBonuses.json",
]

OPTIONAL_MASTERDATA_FILES = [
    "worldBloomSupportDeckUnitEventLimitedBonuses.json",
    "cardMysekaiCanvasBonuses.json",
    "mysekaiFixtureGameCharacterGroups.json",
    "mysekaiFixtureGameCharacterGroupPerformanceBonuses.json",
    "mysekaiGates.json",
    "mysekaiGateLevels.json",
]

# allium 会同时使用卡牌基础表和剧情表计算综合力。
# 之前启动脚本每次都会刷新 cards.json / cardEpisodes.json，导致一键启动被网络下载卡住。
# 现在默认只补齐缺失文件；如确实需要刷新关键表，可手动传 --refresh-critical。
CRITICAL_MASTERDATA_FILES = [
    "cards.json",
    "cardEpisodes.json",
]

REGION_TO_PJSK_TYPE = {region: pjsk_type for pjsk_type, region in SERVER_MAP.items()}
DATA_PATH = Path(os.getenv("PJSK_DATA_DIR", str(data_path)))


def _helper_url() -> str:
    return os.getenv("PJSK_HELPER_URL", "http://127.0.0.1:45558").rstrip("/")


def _refresh_file(region: str, file_name: str) -> None:
    query = urllib.parse.urlencode({"region": region, "file": file_name})
    request = urllib.request.Request(
        f"{_helper_url()}/masterdata/refresh?{query}",
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=160) as response:
            if response.status >= 300:
                raise RuntimeError(f"helper returned HTTP {response.status}")
    except urllib.error.URLError as exc:
        raise RuntimeError(f"无法连接 go-pjsk-helper ({_helper_url()}): {exc}") from exc


def sync_region(region: str, include_optional: bool, force: bool, refresh_critical: bool) -> None:
    if region not in REGION_TO_PJSK_TYPE:
        raise ValueError(f"unsupported region: {region}")

    pjsk_type = REGION_TO_PJSK_TYPE[region]
    files = REQUIRED_MASTERDATA_FILES + (OPTIONAL_MASTERDATA_FILES if include_optional else [])
    missing_before = [name for name in REQUIRED_MASTERDATA_FILES if not (DATA_PATH / region / name).is_file()]
    optional_missing = [
        name for name in OPTIONAL_MASTERDATA_FILES
        if include_optional and not (DATA_PATH / region / name).is_file()
    ]
    if force:
        targets = files
    else:
        critical_targets = CRITICAL_MASTERDATA_FILES if refresh_critical else []
        targets = list(dict.fromkeys(missing_before + optional_missing + critical_targets))

    if not targets:
        print(f"[deck-masterdata-sync] {region}: required masterdata exists, skip remote sync")
    else:
        reason = "force" if force else "missing files" + (" / critical refresh" if refresh_critical else "")
        print(f"[deck-masterdata-sync] sync {region}: {len(targets)} files ({reason})")
        for name in targets:
            _refresh_file(region, name)

    missing_after = [name for name in REQUIRED_MASTERDATA_FILES if not (DATA_PATH / region / name).is_file()]
    if missing_after:
        raise FileNotFoundError(f"{region} missing required masterdata files: {', '.join(missing_after)}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync allium 组卡 masterdata via bot updater")
    parser.add_argument("--region", action="append", choices=sorted(REGION_TO_PJSK_TYPE), help="region to sync; can be repeated")
    parser.add_argument("--include-optional", action="store_true", help="also sync optional masterdata files")
    parser.add_argument("--force", action="store_true", help="check/download all configured files instead of only missing files")
    parser.add_argument("--refresh-critical", action="store_true", help="also refresh cards.json and cardEpisodes.json even if they already exist")
    args = parser.parse_args()

    regions = args.region or ["jp", "cn", "tw"]
    DATA_PATH.mkdir(parents=True, exist_ok=True)
    for region in regions:
        sync_region(region, include_optional=args.include_optional, force=args.force, refresh_critical=args.refresh_critical)


if __name__ == "__main__":
    main()
