#!/usr/bin/env python3
"""Sync deck-service required masterdata files via bot's PJSk updater."""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

os.environ.setdefault("KNDBOT_SKIP_PJSK_PLUGIN_AUTOLOAD", "1")

ROOT_DIR = Path(__file__).resolve().parents[3]
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from plugins.pjsk._autoask import pjsk_update_manager
from plugins.pjsk._config import SERVER_MAP, data_path

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

# deck-service 会同时使用卡牌基础表和剧情表计算综合力。
# 之前启动脚本每次都会刷新 cards.json / cardEpisodes.json，导致一键启动被网络下载卡住。
# 现在默认只补齐缺失文件；如确实需要刷新关键表，可手动传 --refresh-critical。
CRITICAL_MASTERDATA_FILES = [
    "cards.json",
    "cardEpisodes.json",
]

REGION_TO_PJSK_TYPE = {region: pjsk_type for pjsk_type, region in SERVER_MAP.items()}


def sync_region(region: str, include_optional: bool, force: bool, refresh_critical: bool) -> None:
    if region not in REGION_TO_PJSK_TYPE:
        raise ValueError(f"unsupported region: {region}")

    pjsk_type = REGION_TO_PJSK_TYPE[region]
    files = REQUIRED_MASTERDATA_FILES + (OPTIONAL_MASTERDATA_FILES if include_optional else [])
    missing_before = [name for name in REQUIRED_MASTERDATA_FILES if not (data_path / region / name).is_file()]
    optional_missing = [
        name for name in OPTIONAL_MASTERDATA_FILES
        if include_optional and not (data_path / region / name).is_file()
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
            pjsk_update_manager.sync_update_music_data(name, pjsk_type=pjsk_type)

    missing_after = [name for name in REQUIRED_MASTERDATA_FILES if not (data_path / region / name).is_file()]
    if missing_after:
        raise FileNotFoundError(f"{region} missing required masterdata files: {', '.join(missing_after)}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync deck-service masterdata via bot updater")
    parser.add_argument("--region", action="append", choices=sorted(REGION_TO_PJSK_TYPE), help="region to sync; can be repeated")
    parser.add_argument("--include-optional", action="store_true", help="also sync optional masterdata files")
    parser.add_argument("--force", action="store_true", help="check/download all configured files instead of only missing files")
    parser.add_argument("--refresh-critical", action="store_true", help="also refresh cards.json and cardEpisodes.json even if they already exist")
    args = parser.parse_args()

    regions = args.region or ["jp", "cn", "tw"]
    Path(data_path).mkdir(parents=True, exist_ok=True)
    for region in regions:
        sync_region(region, include_optional=args.include_optional, force=args.force, refresh_critical=args.refresh_critical)


if __name__ == "__main__":
    main()
