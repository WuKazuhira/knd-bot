#!/usr/bin/env python3
"""已结束活动榜线 SQLite 无损 RLE 压缩归档工具，转换后按原序做 BLAKE2b 校验。"""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import os
import shutil
import sqlite3
import struct
import sys
import tempfile
import time
import zlib
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

_PACK_Q3 = struct.Struct("<qqq").pack


@dataclass
class ArchiveRecord:
    region: str
    event_id: int
    base_event_id: int
    event_name: str
    source_db: str
    source_bytes: int
    source_wal_bytes: int
    output_db: str
    output_bytes: int
    ratio: float
    total_rows: int
    identities: int
    batches: int
    signatures: int
    runs: int
    blake2b_128: str
    verified: bool
    elapsed_sec: float


def _load_events(masterdata_root: Path, region: str) -> Dict[int, dict]:
    ev_path = masterdata_root / region / "events.json"
    if not ev_path.exists():
        return {}
    with ev_path.open("r", encoding="utf-8") as fh:
        items = json.load(fh)
    return {int(item["id"]): item for item in items if isinstance(item, dict) and "id" in item}


def _discover_ended_dbs(
    db_root: Path,
    masterdata_root: Path,
    regions: Iterable[str],
    buffer_hours: float,
    only_events: Optional[set[int]] = None,
) -> List[Tuple[str, int, int, str, Path]]:
    now_ms = int(time.time() * 1000)
    buffer_ms = int(buffer_hours * 3600 * 1000)
    targets: List[Tuple[str, int, int, str, Path]] = []

    for region in regions:
        events = _load_events(masterdata_root, region)
        going_ids = {
            eid
            for eid, ev in events.items()
            if int(ev.get("startAt", 0)) < now_ms < int(ev.get("aggregateAt", 0))
        }
        rdir = db_root / f"sk_{region}"
        if not rdir.is_dir():
            continue
        for fname in sorted(os.listdir(rdir)):
            if not fname.endswith("_ranking.db"):
                continue
            stem = fname[: -len("_ranking.db")]
            if not stem.isdigit():
                continue
            event_id = int(stem)
            if only_events is not None and event_id not in only_events:
                continue
            base_id = event_id % 1000 if event_id >= 1000 else event_id
            if base_id in going_ids:
                continue
            ev = events.get(base_id)
            if not ev:
                continue
            agg_ms = int(ev.get("aggregateAt", 0))
            if agg_ms <= 0 or now_ms < agg_ms + buffer_ms:
                continue
            targets.append((region, event_id, base_id, str(ev.get("name", "")), rdir / fname))
    return targets


def _is_already_rle(db_path: Path) -> bool:
    try:
        con = sqlite3.connect(f"file:{db_path}?immutable=1", uri=True)
        try:
            row = con.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='runs'"
            ).fetchone()
            return row is not None
        finally:
            con.close()
    except sqlite3.Error:
        return False


def _prepare_readable_db(src_db: Path, tmp_dir: Path) -> Tuple[Path, bool, int, int]:
    """有非空 WAL 时先拷到临时目录合并后再扫。"""
    src_bytes = src_db.stat().st_size
    wal = Path(f"{src_db}-wal")
    shm = Path(f"{src_db}-shm")
    wal_bytes = wal.stat().st_size if wal.exists() else 0

    if wal_bytes == 0:
        return src_db, False, src_bytes, 0

    work_db = tmp_dir / src_db.name
    shutil.copyfile(src_db, work_db)
    shutil.copyfile(wal, Path(f"{work_db}-wal"))
    if shm.exists():
        shutil.copyfile(shm, Path(f"{work_db}-shm"))

    con = sqlite3.connect(str(work_db))
    try:
        con.execute("PRAGMA wal_checkpoint(TRUNCATE);")
        con.execute("PRAGMA journal_mode=DELETE;")
        con.commit()
    finally:
        con.close()
    for suffix in ("-wal", "-shm"):
        p = Path(f"{work_db}{suffix}")
        if p.exists():
            p.unlink()
    return work_db, True, src_bytes, wal_bytes


def _create_rle_schema(con: sqlite3.Connection) -> None:
    con.executescript(
        """
        PRAGMA journal_mode=OFF;
        PRAGMA synchronous=OFF;
        PRAGMA page_size=4096;
        CREATE TABLE archive_meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        CREATE TABLE identities (
            id INTEGER PRIMARY KEY,
            uid TEXT NOT NULL,
            name TEXT NOT NULL
        );
        CREATE TABLE batch_sigs (
            sig_id INTEGER PRIMARY KEY,
            ranks_blob BLOB NOT NULL
        );
        CREATE TABLE sig_ranks (
            sig_id INTEGER NOT NULL,
            rank INTEGER NOT NULL,
            ordinal INTEGER NOT NULL,
            PRIMARY KEY (rank, sig_id)
        ) WITHOUT ROWID;
        CREATE TABLE batches (
            batch_id INTEGER PRIMARY KEY,
            ts INTEGER NOT NULL,
            sig_id INTEGER NOT NULL
        );
        CREATE TABLE runs (
            rank INTEGER NOT NULL,
            start_batch INTEGER NOT NULL,
            end_batch INTEGER NOT NULL,
            identity_id INTEGER NOT NULL,
            score INTEGER NOT NULL,
            PRIMARY KEY (rank, start_batch)
        ) WITHOUT ROWID;
        """
    )


def convert_one_db(
    region: str,
    event_id: int,
    base_id: int,
    event_name: str,
    src_db: Path,
    out_db: Path,
    tmp_root: Path,
) -> ArchiveRecord:
    t0 = time.monotonic()
    with tempfile.TemporaryDirectory(prefix=f"rk_{region}_{event_id}_", dir=str(tmp_root)) as td:
        tdir = Path(td)
        scan_db, _copied, src_bytes, wal_bytes = _prepare_readable_db(src_db, tdir)

        out_db.parent.mkdir(parents=True, exist_ok=True)
        tmp_out = out_db.with_suffix(".db.tmp")
        if tmp_out.exists():
            tmp_out.unlink()

        if scan_db == src_db:
            scon = sqlite3.connect(f"file:{scan_db}?immutable=1", uri=True)
        else:
            scon = sqlite3.connect(str(scan_db))
        scon.execute("PRAGMA cache_size=-32768;")

        dcon = sqlite3.connect(str(tmp_out))
        try:
            _create_rle_schema(dcon)

            id_map: Dict[Tuple[str, str], int] = {}
            id_bytes: List[bytes] = [b""]  # 1-based 预编码字节，加速 blake2b
            sig_map: Dict[Tuple[int, ...], int] = {}
            active_runs: Dict[int, List[int]] = {}  # rank -> [start_batch, end_batch, iid, score]

            ident_buf: List[Tuple[int, str, str]] = []
            batch_buf: List[Tuple[int, int, int]] = []
            runs_buf: List[Tuple[int, int, int, int, int]] = []

            orig_hash = hashlib.blake2b(digest_size=16)
            orig_count = 0
            runs_count = 0
            batch_id = 0

            cur_ts: Optional[int] = None
            cur_ranks_order: List[int] = []
            cur_batch_rows: Dict[int, Tuple[int, int]] = {}

            def flush_buffers() -> None:
                if ident_buf:
                    dcon.executemany("INSERT INTO identities VALUES (?,?,?)", ident_buf)
                    ident_buf.clear()
                if batch_buf:
                    dcon.executemany("INSERT INTO batches VALUES (?,?,?)", batch_buf)
                    batch_buf.clear()
                if runs_buf:
                    dcon.executemany("INSERT INTO runs VALUES (?,?,?,?,?)", runs_buf)
                    runs_buf.clear()

            def flush_batch(ts_val: int) -> None:
                nonlocal batch_id, runs_count
                if not cur_batch_rows:
                    return
                batch_id += 1
                sig_key = tuple(cur_ranks_order)
                sig_id = sig_map.get(sig_key)
                if sig_id is None:
                    sig_id = len(sig_map) + 1
                    sig_map[sig_key] = sig_id
                    blob = zlib.compress(",".join(map(str, sig_key)).encode("ascii"), level=6)
                    dcon.execute("INSERT INTO batch_sigs VALUES (?,?)", (sig_id, blob))
                    dcon.executemany(
                        "INSERT INTO sig_ranks VALUES (?,?,?)",
                        [(sig_id, rk, idx) for idx, rk in enumerate(sig_key)],
                    )
                batch_buf.append((batch_id, ts_val, sig_id))

                for r, (iid, sc) in cur_batch_rows.items():
                    prev = active_runs.get(r)
                    if prev is not None and prev[2] == iid and prev[3] == sc:
                        prev[1] = batch_id
                    else:
                        if prev is not None:
                            runs_buf.append((r, prev[0], prev[1], prev[2], prev[3]))
                            runs_count += 1
                        active_runs[r] = [batch_id, batch_id, iid, sc]

                if len(batch_buf) >= 10000 or len(runs_buf) >= 20000:
                    flush_buffers()

            def _iter_source_rows(src_conn: sqlite3.Connection):
                has_runs = (
                    src_conn.execute(
                        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='runs' LIMIT 1"
                    ).fetchone()
                    is not None
                )
                if not has_runs:
                    yield from src_conn.execute(
                        "SELECT uid, name, score, rank, ts FROM ranking ORDER BY id ASC"
                    )
                    return

                src_id_map: Dict[int, Tuple[str, str]] = {
                    int(i): (str(u), str(n))
                    for i, u, n in src_conn.execute("SELECT id, uid, name FROM identities")
                }
                src_sigs: Dict[int, List[int]] = {}
                for sid, blob in src_conn.execute("SELECT sig_id, ranks_blob FROM batch_sigs"):
                    src_sigs[int(sid)] = [
                        int(x) for x in zlib.decompress(blob).decode("ascii").split(",")
                    ]
                src_max_bid_row = src_conn.execute(
                    "SELECT COALESCE(MAX(batch_id), 0) FROM batches"
                ).fetchone()
                src_max_bid = int(src_max_bid_row[0]) if src_max_bid_row else 0
                src_by_rank: Dict[int, List[Tuple[int, int, int, int]]] = {}
                for r, sb, eb, iid, sc in src_conn.execute(
                    "SELECT rank, start_batch, end_batch, identity_id, score "
                    "FROM runs ORDER BY rank, start_batch"
                ):
                    eff_eb = min(int(eb), src_max_bid)
                    src_by_rank.setdefault(int(r), []).append((int(sb), eff_eb, int(iid), int(sc)))
                src_pos = {r: 0 for r in src_by_rank}
                for bid, bts, sid in src_conn.execute(
                    "SELECT batch_id, ts, sig_id FROM batches ORDER BY batch_id"
                ):
                    bid_i = int(bid)
                    bts_i = int(bts)
                    for r in src_sigs[int(sid)]:
                        lst = src_by_rank[r]
                        idx = src_pos[r]
                        while lst[idx][1] < bid_i:
                            idx += 1
                        src_pos[r] = idx
                        sb, eb, iid, sc = lst[idx]
                        if not (sb <= bid_i <= eb):
                            raise RuntimeError(
                                f"Source RLE mismatch at batch={bid_i}, rank={r}: [{sb},{eb}]"
                            )
                        u, n = src_id_map[iid]
                        yield u, n, sc, r, bts_i

            for uid, name, score, rank, ts in _iter_source_rows(scon):
                orig_count += 1
                uid_s = str(uid)
                name_s = str(name)
                sc_i = int(score)
                rk_i = int(rank)
                ts_i = int(ts)

                key = (uid_s, name_s)
                iid = id_map.get(key)
                if iid is None:
                    iid = len(id_map) + 1
                    id_map[key] = iid
                    ident_buf.append((iid, uid_s, name_s))
                    ib = (
                        uid_s.encode("utf-8", "surrogatepass")
                        + b"\x00"
                        + name_s.encode("utf-8", "surrogatepass")
                        + b"\x00"
                    )
                    id_bytes.append(ib)
                else:
                    ib = id_bytes[iid]

                orig_hash.update(ib)
                orig_hash.update(_PACK_Q3(sc_i, rk_i, ts_i))

                if cur_ts is None:
                    cur_ts = ts_i
                elif ts_i != cur_ts or rk_i in cur_batch_rows:
                    flush_batch(cur_ts)
                    cur_ts = ts_i
                    cur_ranks_order.clear()
                    cur_batch_rows.clear()
                cur_ranks_order.append(rk_i)
                cur_batch_rows[rk_i] = (iid, sc_i)

            if cur_ts is not None:
                flush_batch(cur_ts)

            for r, prev in active_runs.items():
                runs_buf.append((r, prev[0], prev[1], prev[2], prev[3]))
                runs_count += 1
            active_runs.clear()
            flush_buffers()

            dcon.execute("CREATE INDEX idx_runs_ident ON runs (identity_id, start_batch)")
            dcon.execute("CREATE INDEX idx_batches_sig ON batches (sig_id, batch_id)")
            orig_hex = orig_hash.hexdigest()
            meta_rows = [
                ("format", "rle-v1"),
                ("region", region),
                ("event_id", str(event_id)),
                ("row_count", str(orig_count)),
                ("blake2b_128", orig_hex),
                ("created_at", datetime.now(timezone.utc).isoformat()),
            ]
            dcon.executemany("INSERT INTO archive_meta VALUES (?,?)", meta_rows)
            dcon.commit()
            dcon.execute("PRAGMA journal_mode=DELETE;")

            # 全量还原校验哈希
            sig_lookup: Dict[int, List[int]] = {}
            for sid, blob in dcon.execute("SELECT sig_id, ranks_blob FROM batch_sigs"):
                sig_lookup[sid] = [int(x) for x in zlib.decompress(blob).decode("ascii").split(",")]

            by_rank: Dict[int, List[Tuple[int, int, int, int]]] = {}
            for r, sb, eb, iid, sc in dcon.execute(
                "SELECT rank, start_batch, end_batch, identity_id, score FROM runs ORDER BY rank, start_batch"
            ):
                by_rank.setdefault(r, []).append((sb, eb, iid, sc))
            pos_by_rank = {r: 0 for r in by_rank}

            recon_hash = hashlib.blake2b(digest_size=16)
            recon_count = 0
            for bid, bts, sid in dcon.execute("SELECT batch_id, ts, sig_id FROM batches ORDER BY batch_id"):
                for r in sig_lookup[sid]:
                    lst = by_rank[r]
                    idx = pos_by_rank[r]
                    while lst[idx][1] < bid:
                        idx += 1
                    pos_by_rank[r] = idx
                    sb, eb, iid, sc = lst[idx]
                    if not (sb <= bid <= eb):
                        raise RuntimeError(f"RLE range mismatch at batch={bid}, rank={r}: [{sb},{eb}]")
                    recon_count += 1
                    recon_hash.update(id_bytes[iid])
                    recon_hash.update(_PACK_Q3(sc, r, bts))

            verified = recon_count == orig_count and recon_hash.hexdigest() == orig_hex
            if not verified:
                raise RuntimeError(
                    f"BLAKE2b verification failed for {region}/{event_id}: "
                    f"count {recon_count}!={orig_count}, hash {recon_hash.hexdigest()}!={orig_hex}"
                )
        finally:
            scon.close()
            dcon.close()

        os.replace(tmp_out, out_db)
        out_bytes = out_db.stat().st_size
        total_src = src_bytes + wal_bytes
        elapsed = time.monotonic() - t0
        return ArchiveRecord(
            region=region,
            event_id=event_id,
            base_event_id=base_id,
            event_name=event_name,
            source_db=str(src_db),
            source_bytes=src_bytes,
            source_wal_bytes=wal_bytes,
            output_db=str(out_db),
            output_bytes=out_bytes,
            ratio=(total_src / out_bytes) if out_bytes else 0.0,
            total_rows=orig_count,
            identities=len(id_map),
            batches=batch_id,
            signatures=len(sig_map),
            runs=runs_count,
            blake2b_128=orig_hex,
            verified=True,
            elapsed_sec=round(elapsed, 2),
        )


def main() -> int:
    repo_root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database-dir",
        type=Path,
        default=repo_root / "data/pjsk/ondemand/database",
        help="源数据库根目录（含 sk_cn/sk_jp/sk_tw）",
    )
    parser.add_argument(
        "--masterdata-dir",
        type=Path,
        default=repo_root / "data/pjsk/ondemand",
        help="主数据根目录（读取 events.json 判定是否已结束）",
    )
    parser.add_argument(
        "--archive-dir",
        type=Path,
        default=repo_root / "data/pjsk/archive/ranking",
        help="无损归档输出根目录",
    )
    parser.add_argument("--regions", default="cn,jp,tw", help="要扫描的区域（逗号分隔）")
    parser.add_argument(
        "--events",
        default="",
        help="仅处理指定 event_id（逗号分隔），留空处理全部已结束活动",
    )
    parser.add_argument("--buffer-hours", type=float, default=12.0, help="活动结束后缓冲小时数")
    parser.add_argument("--tmp-dir", type=Path, default=Path("/tmp"), help="WAL 合并临时目录")
    parser.add_argument("--workers", type=int, default=1, help="并发处理数（注意每路会在 tmp-dir 占用至多单库大小临时空间）")
    parser.add_argument("--skip-existing", action="store_true", help="跳过已存在的同名归档")
    parser.add_argument("--dry-run", action="store_true", help="仅列出候选活动，不执行转换")
    args = parser.parse_args()

    regions = [r.strip() for r in args.regions.split(",") if r.strip()]
    only_events = (
        {int(x.strip()) for x in args.events.split(",") if x.strip()} if args.events else None
    )
    targets = _discover_ended_dbs(
        args.database_dir,
        args.masterdata_dir,
        regions,
        args.buffer_hours,
        only_events=only_events,
    )
    if not targets:
        print("未找到符合条件的已结束活动榜线库。")
        return 0

    total_src = 0
    pending: List[Tuple[str, int, int, str, Path, Path]] = []
    for region, eid, base_id, name, path in targets:
        if _is_already_rle(path):
            print(f"[SKIP already-RLE] {region}/{eid}_ranking.db")
            continue
        out_db = args.archive_dir / f"sk_{region}" / f"{eid}_ranking.db"
        wal = Path(f"{path}-wal")
        sz = path.stat().st_size
        wsz = wal.stat().st_size if wal.exists() else 0
        total_src += sz + wsz
        print(f"[CANDIDATE] {region}/{eid}_ranking.db db={sz/1e6:.1f}MB wal={wsz/1e6:.1f}MB ({name})")
        if args.skip_existing and out_db.exists() and _is_valid_archive(out_db):
            continue
        pending.append((region, eid, base_id, name, path, out_db))
    print(f"Total source size (db+wal): {total_src/1e9:.2f} GB ({len(targets)} files, pending={len(pending)})")

    if args.dry_run or not pending:
        return 0

    manifest_path = args.archive_dir / "manifest.json"
    if manifest_path.exists():
        try:
            old = json.loads(manifest_path.read_text(encoding="utf-8"))
            existing_map = {
                (r["region"], r["event_id"]): ArchiveRecord(**r)
                for r in old.get("archives", [])
            }
        except Exception:
            existing_map = {}
    else:
        existing_map = {}

    def record_done(idx: int, rec: ArchiveRecord) -> None:
        existing_map[(rec.region, rec.event_id)] = rec
        print(
            f"[{idx}/{len(pending)}] OK {rec.region}/{rec.event_id}: "
            f"{(rec.source_bytes+rec.source_wal_bytes)/1e6:.1f}MB -> {rec.output_bytes/1e6:.2f}MB "
            f"({rec.ratio:.1f}x) | rows={rec.total_rows} runs={rec.runs} blake2b={rec.blake2b_128} "
            f"verified={rec.verified} ({rec.elapsed_sec}s)",
            flush=True,
        )
        _save_manifest(manifest_path, list(existing_map.values()))

    workers = max(1, int(args.workers))
    if workers == 1:
        for idx, (region, eid, base_id, name, path, out_db) in enumerate(pending, 1):
            print(f"[{idx}/{len(pending)}] Archiving {region}/{eid} ({name}) ...", flush=True)
            rec = convert_one_db(region, eid, base_id, name, path, out_db, args.tmp_dir)
            record_done(idx, rec)
    else:
        # 大小交错排序，避免多进程同时塞爆 /tmp
        ordered = sorted(pending, key=lambda it: it[4].stat().st_size, reverse=True)
        interleaved: List[Tuple[str, int, int, str, Path, Path]] = []
        lo, hi = 0, len(ordered) - 1
        while lo <= hi:
            interleaved.append(ordered[lo])
            lo += 1
            if lo <= hi:
                interleaved.append(ordered[hi])
                hi -= 1
        with concurrent.futures.ProcessPoolExecutor(max_workers=workers) as pool:
            futs = {}
            for item in interleaved:
                region, eid, base_id, name, path, out_db = item
                print(f"[QUEUED] {region}/{eid} ({name})", flush=True)
                fut = pool.submit(convert_one_db, region, eid, base_id, name, path, out_db, args.tmp_dir)
                futs[fut] = (region, eid)
            done_cnt = 0
            for fut in concurrent.futures.as_completed(futs):
                done_cnt += 1
                rec = fut.result()
                record_done(done_cnt, rec)

    return 0


def _is_valid_archive(db_path: Path) -> bool:
    try:
        con = sqlite3.connect(f"file:{db_path}?immutable=1", uri=True)
        try:
            row = con.execute(
                "SELECT value FROM archive_meta WHERE key='format'"
            ).fetchone()
            return row is not None and row[0] == "rle-v1"
        finally:
            con.close()
    except sqlite3.Error:
        return False


def _save_manifest(manifest_path: Path, records: List[ArchiveRecord]) -> None:
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    records_sorted = sorted(records, key=lambda r: (r.region, r.event_id))
    total_src = sum(r.source_bytes + r.source_wal_bytes for r in records_sorted)
    total_out = sum(r.output_bytes for r in records_sorted)
    payload = {
        "format": "rle-v1",
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "count": len(records_sorted),
        "total_source_bytes": total_src,
        "total_output_bytes": total_out,
        "overall_ratio": round(total_src / total_out, 2) if total_out else 0.0,
        "archives": [asdict(r) for r in records_sorted],
    }
    tmp = manifest_path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, manifest_path)


if __name__ == "__main__":
    raise SystemExit(main())
