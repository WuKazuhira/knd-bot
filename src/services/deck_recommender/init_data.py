#!/usr/bin/env python3
"""Initialize deck recommender backend with local masterdata and music metas."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from pathlib import Path
from typing import Iterable

import requests
import yaml
import zstandard

ROOT_DIR = Path(__file__).resolve().parents[3]
MASTERDATA_DIR = ROOT_DIR / "data" / "pjsk" / "masterdata"
DEFAULT_SERVER_URL = "http://127.0.0.1:45557"

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
        "music metas URL is not configured; set MUSIC_METAS_URL or "
        "config/pjsk/settings.yaml:endpoints.music_metas_base_url"
    )


def _add_payload_segment(payloads: list[bytes], data: bytes) -> None:
    payloads.append(len(data).to_bytes(4, "big"))
    payloads.append(data)


def _build_payload(segments: Iterable[bytes]) -> bytes:
    payloads: list[bytes] = []
    for segment in segments:
        _add_payload_segment(payloads, segment)
    return zstandard.ZstdCompressor().compress(b"".join(payloads))


def _wait_backend(server_url: str, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            resp = requests.get(f"{server_url.rstrip('/')}/docs", timeout=3)
            if resp.status_code < 500:
                return
        except Exception as exc:  # noqa: BLE001 - only used for retry diagnostics
            last_error = exc
        time.sleep(1)
    raise TimeoutError(f"deck backend is not ready: {last_error}")


def _load_masterdata(region: str) -> dict[str, bytes]:
    """Load root common masterdata plus region-specific overrides."""
    files = {path.name: path.read_bytes() for path in MASTERDATA_DIR.glob("*.json")}
    region_dir = MASTERDATA_DIR / region
    if region_dir.exists():
        files.update({path.name: path.read_bytes() for path in region_dir.glob("*.json")})
    if not files:
        raise FileNotFoundError(f"no masterdata json files found for region={region}")
    return files


def _augment_omakase_music_metas(music_metas: list[dict]) -> list[dict]:
    """Add synthetic music_id=10000 entries used by bot's default deck command.

    The recommender backend requires a concrete music meta row. The bot uses 10000
    as an omakase/default-song placeholder, so we map it to the best available
    chart per difficulty by event point per hour.
    """
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


def _download_music_metas() -> bytes:
    resp = requests.get(_music_metas_url(), timeout=60)
    resp.raise_for_status()
    data = resp.json()
    if not isinstance(data, list):
        raise ValueError("music_metas.json must be a list")
    return json.dumps(_augment_omakase_music_metas(data), ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def update_region(server_url: str, region: str) -> None:
    masterdata = _load_masterdata(region)
    musicmetas = _download_music_metas()
    version_src = b"".join(name.encode("utf-8") + masterdata[name] for name in sorted(masterdata))
    masterdata_version = hashlib.md5(version_src).hexdigest()[:12]
    header = {
        "region": region,
        "masterdata_version": masterdata_version,
        "musicmetas_update_ts": int(time.time()),
    }

    segments: list[bytes] = [json.dumps(header, ensure_ascii=False).encode("utf-8")]
    for name in sorted(masterdata):
        segments.append(name.encode("utf-8"))
        segments.append(masterdata[name])
    segments.append(b"musicmetas")
    segments.append(musicmetas)

    resp = requests.post(f"{server_url.rstrip('/')}/update_data", data=_build_payload(segments), timeout=120)
    resp.raise_for_status()
    print(f"[deck-init] {region} initialized: masterdata={len(masterdata)} musicmetas={len(musicmetas)}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Initialize deck recommender backend data")
    parser.add_argument("--server-url", default=DEFAULT_SERVER_URL)
    parser.add_argument("--region", action="append", default=None, help="region to initialize, can be repeated")
    parser.add_argument("--wait-timeout", type=float, default=60)
    args = parser.parse_args()

    regions = args.region or ["jp", "tw", "cn"]
    _wait_backend(args.server_url, args.wait_timeout)
    for region in regions:
        update_region(args.server_url, region)


if __name__ == "__main__":
    main()
