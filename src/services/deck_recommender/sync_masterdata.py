#!/usr/bin/env python3
"""Sync allium 组卡所需的 masterdata files via bot's PJSK updater."""
from __future__ import annotations

import argparse
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import requests
import yaml

ROOT_DIR = Path(__file__).resolve().parents[3]
SUPPORTED_REGIONS = {"jp", "tw", "cn"}

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

REGION_TO_PJSK_TYPE = {region: region for region in sorted(SUPPORTED_REGIONS)}
DATA_PATH = Path(os.getenv("PJSK_DATA_DIR", str(ROOT_DIR / "data" / "pjsk" / "ondemand")))
DECKREC_PATH = DATA_PATH / "deckrec"


def _music_metas_url() -> str:
    configured = (os.getenv("MUSIC_METAS_URL") or "").strip()
    if configured:
        return configured
    config_dir = Path(os.getenv("KNDBOT_CONFIG_DIR", ROOT_DIR / "config"))
    settings_path = config_dir / "pjsk" / "settings.yaml"
    if settings_path.exists():
        settings = yaml.safe_load(settings_path.read_text(encoding="utf-8")) or {}
        base_url = str(settings.get("endpoints", {}).get("music_metas_base_url") or "").rstrip("/")
        if base_url:
            return f"{base_url}/music_metas.json"
    raise RuntimeError(
        "music metas URL 未配置；请设置 MUSIC_METAS_URL 或 "
        "config/pjsk/settings.yaml:endpoints.music_metas_base_url"
    )


def _augment_omakase_music_metas(music_metas: list[dict]) -> list[dict]:
    """为 KND 的 music_id=10000 默认歌曲补充每个难度的最佳歌曲行。"""
    by_difficulty: dict[str, dict] = {}
    for item in music_metas:
        if not isinstance(item, dict):
            continue
        difficulty = item.get("difficulty")
        if not difficulty:
            continue
        current = by_difficulty.get(difficulty)
        item_score = item.get("pt_per_hour_multi") or item.get("multi_pt_max") or item.get("multi_score") or 0
        current_score = 0
        if current:
            current_score = current.get("pt_per_hour_multi") or current.get("multi_pt_max") or current.get("multi_score") or 0
        if current is None or item_score > current_score:
            by_difficulty[difficulty] = item

    existing = {
        (item.get("music_id"), item.get("difficulty"))
        for item in music_metas
        if isinstance(item, dict)
    }
    augmented = list(music_metas)
    for difficulty, item in by_difficulty.items():
        if (10000, difficulty) in existing:
            continue
        copied = dict(item)
        copied["music_id"] = 10000
        augmented.append(copied)
    return augmented


def _write_music_metas(regions: list[str]) -> None:
    paths = [DECKREC_PATH / f"musicmetas_{region}.json" for region in regions]
    try:
        url = _music_metas_url()
    except RuntimeError:
        missing = [path for path in paths if not path.is_file()]
        if not missing:
            print("[deck-masterdata-sync] music metas URL 未配置，复用现有 music metas")
            return
        raise

    response = requests.get(url, timeout=60)
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, list):
        raise ValueError("music_metas.json 必须是数组")
    payload = json.dumps(
        _augment_omakase_music_metas(data),
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    DECKREC_PATH.mkdir(parents=True, exist_ok=True)
    for path in paths:
        temp_path = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        temp_path.write_bytes(payload)
        temp_path.replace(path)
    print(f"[deck-masterdata-sync] music metas 已写入 {len(paths)} 个区服")


def _make_server_data_readable(regions: list[str]) -> None:
    """仅放开上游 server 需要读取的公开 JSON 表，不改变目录属主。"""
    paths = [
        path
        for region in regions
        for path in (DATA_PATH / region).glob("*.json")
    ]
    paths.extend(DECKREC_PATH / f"musicmetas_{region}.json" for region in regions)
    for path in paths:
        if path.is_file():
            path.chmod(path.stat().st_mode | 0o444)


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
    parser = argparse.ArgumentParser(
        description="准备 allium-deck-server 的 masterdata/music metas"
    )
    parser.add_argument(
        "--region",
        action="append",
        choices=sorted(REGION_TO_PJSK_TYPE),
        help="region to sync; can be repeated",
    )
    parser.add_argument("--include-optional", action="store_true", help="also sync optional masterdata files")
    parser.add_argument("--force", action="store_true", help="check/download all configured files instead of only missing files")
    parser.add_argument("--refresh-critical", action="store_true", help="also refresh cards.json and cardEpisodes.json even if they already exist")
    args = parser.parse_args()

    regions = args.region or ["jp", "cn", "tw"]
    DATA_PATH.mkdir(parents=True, exist_ok=True)
    for region in regions:
        sync_region(
            region,
            include_optional=args.include_optional,
            force=args.force,
            refresh_critical=args.refresh_critical,
        )
    _write_music_metas(regions)
    _make_server_data_readable(regions)


if __name__ == "__main__":
    main()
