#!/usr/bin/env python3
"""从各区服 ranking.db 导出榜线预测训练集（dataset.npz + meta.json）。"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sqlite3
import sys
from datetime import datetime

import numpy as np

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_SRC = os.path.normpath(os.path.join(_THIS_DIR, "..", "..", "src"))


def _load_mod(name: str, relpath: str):
    """直接按路径加载模块，避免触发 bot 包初始化。"""
    path = os.path.normpath(os.path.join(_SRC, relpath))
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


_features = _load_mod("pjsk_features", "plugins/pjsk/sk/_features.py")
_event_meta = _load_mod("pjsk_event_meta", "plugins/pjsk/sk/_event_meta.py")


def _iter_db_files(database_dir: str, regions: list[str]):
    """遍历各区服榜线库，同时兼容 archive 归档目录。"""
    archive_dir = os.path.normpath(os.path.join(database_dir, "..", "..", "archive", "ranking"))
    for region in regions:
        seen_eids: set[int] = set()
        candidates = [os.path.join(database_dir, f"sk_{region}")]
        if os.path.isdir(archive_dir):
            candidates.append(os.path.join(archive_dir, f"sk_{region}"))
        entries: list[tuple[int, str]] = []
        for region_dir in candidates:
            if not os.path.isdir(region_dir):
                continue
            for fname in sorted(os.listdir(region_dir)):
                if not fname.endswith("_ranking.db"):
                    continue
                stem = fname[: -len("_ranking.db")]
                if not stem.isdigit():
                    continue
                event_id = int(stem)
                if event_id in seen_eids:
                    continue
                seen_eids.add(event_id)
                entries.append((event_id, os.path.join(region_dir, fname)))
        for event_id, db_path in sorted(entries):
            yield region, event_id, db_path


def _open_ro_sqlite(db_path: str) -> sqlite3.Connection:
    """只读打开 SQLite，目录无写权限时回退 immutable 模式。"""
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        conn.execute("SELECT 1 FROM sqlite_master LIMIT 1").fetchone()
        return conn
    except sqlite3.OperationalError:
        return sqlite3.connect(f"file:{db_path}?immutable=1", uri=True)


def _is_rle_db(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='runs' LIMIT 1"
    ).fetchone()
    return row is not None


def _query_rank_series(db_path: str, ranks: list[int] | None) -> dict[int, list[tuple[int, float]]]:
    """读取单个活动榜线序列，兼容原始库和 RLE 归档库。"""
    conn = _open_ro_sqlite(db_path)
    try:
        cur = conn.cursor()
        is_rle = _is_rle_db(conn)
        if is_rle:
            if ranks:
                placeholders = ",".join("?" for _ in ranks)
                cur.execute(
                    f"SELECT r.rank, r.score, b.ts "
                    f"FROM runs AS r "
                    f"JOIN sig_ranks AS sr ON sr.rank = r.rank "
                    f"JOIN batches AS b ON b.sig_id = sr.sig_id "
                    f"  AND b.batch_id BETWEEN r.start_batch AND r.end_batch "
                    f"WHERE r.rank IN ({placeholders}) AND r.score IS NOT NULL AND r.score > 0 "
                    f"ORDER BY r.rank ASC, b.batch_id ASC",
                    ranks,
                )
            else:
                cur.execute(
                    "SELECT r.rank, r.score, b.ts "
                    "FROM runs AS r "
                    "JOIN sig_ranks AS sr ON sr.rank = r.rank "
                    "JOIN batches AS b ON b.sig_id = sr.sig_id "
                    "  AND b.batch_id BETWEEN r.start_batch AND r.end_batch "
                    "WHERE r.score IS NOT NULL AND r.score > 0 "
                    "ORDER BY r.rank ASC, b.batch_id ASC"
                )
        elif ranks:
            placeholders = ",".join("?" for _ in ranks)
            cur.execute(
                f"SELECT rank, score, ts FROM ranking "
                f"WHERE rank IN ({placeholders}) AND score IS NOT NULL AND score > 0",
                ranks,
            )
        else:
            cur.execute(
                "SELECT rank, score, ts FROM ranking WHERE score IS NOT NULL AND score > 0"
            )
        rows = cur.fetchall()
    finally:
        conn.close()
    series: dict[int, list[tuple[int, float]]] = {}
    for rank, score, ts in rows:
        series.setdefault(int(rank), []).append((int(ts), float(score)))
    return series


def _db_time_range(db_path: str) -> tuple[int, int] | None:
    """无主数据时用库内时间极值兜底。"""
    try:
        conn = _open_ro_sqlite(db_path)
        try:
            cur = conn.cursor()
            table = "batches" if _is_rle_db(conn) else "ranking"
            cur.execute(f"SELECT MIN(ts), MAX(ts) FROM {table}")
            row = cur.fetchone()
            return (int(row[0]), int(row[1])) if row and row[0] is not None else None
        finally:
            conn.close()
    except Exception:
        return None


def _build_activity_samples(
    region: str,
    event_id: int,
    db_path: str,
    masterdata_root: str,
    ranks: list[int] | None,
    max_ts: int,
) -> list[dict]:
    fallback = _db_time_range(db_path)
    meta, _ = _event_meta.resolve_meta(masterdata_root, region, event_id, fallback=fallback)
    if meta is None:
        return []
    if meta.end_ts >= max_ts:
        return []  # 未结束活动跳过
    series = _query_rank_series(db_path, ranks)
    samples: list[dict] = []
    for rank, points in series.items():
        if ranks is not None and rank not in ranks:
            continue
        # 剔除活动时间范围外的脏点
        points = [(ts, s) for ts, s in points if meta.start_ts <= ts <= meta.end_ts]
        if len(points) < _features.DEFAULTS.min_grid_points:
            continue
        points = _downsample_points(points, max_pts=120)
        if len(points) < _features.DEFAULTS.min_grid_points:
            continue
        final_score = points[-1][1]
        if final_score <= 0:
            continue
        samples.append({
            "region": region,
            "event_id": event_id,
            "rank": rank,
            "type": meta.event_type,
            "start_ts": meta.start_ts,
            "end_ts": meta.end_ts,
            "points": points,
            "final_score": final_score,
        })
    return samples


def _downsample_points(points: list, max_pts: int = 120) -> list:
    """均匀降采样到最多 max_pts 个点。"""
    if len(points) <= max_pts:
        return points
    idx = sorted(set(round(i * (len(points) - 1) / (max_pts - 1)) for i in range(max_pts)))
    return [points[i] for i in idx]


def _augment_prefix(
    sample: dict, config, rng: np.random.Generator, max_aug: int
) -> list[dict]:
    """按活动进度截断生成前缀增广样本（含完整样本）。"""
    start_ts = sample["start_ts"]
    end_ts = sample["end_ts"]
    duration = max(1, end_ts - start_ts)
    out = [dict(sample, progress_ceil=1.0)]
    if max_aug > 0:
        fracs = np.linspace(0.15, 0.92, max_aug)
        for frac in fracs:
            cut_ts = int(start_ts + duration * frac)
            prefix = [(t, s) for t, s in sample["points"] if t <= cut_ts]
            if len(prefix) < config.min_grid_points:
                continue
            out.append(dict(sample, progress_ceil=float(frac), points=prefix))
    return out


def prepare_samples(
    database_dir: str,
    masterdata_root: str,
    regions: list[str],
    ranks: list[int] | None,
    aug: int,
    config,
) -> tuple[list[dict], list[np.ndarray], list[np.ndarray], list[np.ndarray], list[np.ndarray], list[dict]]:
    now = int(datetime.now().timestamp())
    rng = np.random.default_rng(42)
    records: list[dict] = []
    X_list: list[np.ndarray] = []
    Umask_list: list[np.ndarray] = []
    Xz_list: list[np.ndarray] = []
    y_list: list[np.ndarray] = []
    event_index: dict[str, dict] = {}

    total_db = 0
    for region, event_id, db_path in _iter_db_files(database_dir, regions):
        act_samples = _build_activity_samples(
            region, event_id, db_path, masterdata_root, ranks, max_ts=now
        )
        if not act_samples:
            continue
        total_db += 1
        event_index[f"{region}:{event_id}"] = {
            "region": region,
            "event_id": event_id,
            "type": act_samples[0]["type"],
            "start_ts": act_samples[0]["start_ts"],
            "end_ts": act_samples[0]["end_ts"],
        }
        for s in act_samples:
            for var in _augment_prefix(s, config, rng, aug):
                feat = _features.extract_features(
                    timeline=var["points"],
                    start_ts=var["start_ts"],
                    end_ts=var["end_ts"],
                    final_score=var["final_score"],
                    region=var["region"],
                    event_type=var["type"],
                    rank=var["rank"],
                    progress_ceil=var["progress_ceil"],
                    config=config,
                )
                if feat is None:
                    continue
                curve, umask, static, target = feat
                X_list.append(curve)
                Umask_list.append(umask)
                Xz_list.append(static)
                y_list.append(target)
                records.append(var)
    return records, X_list, Umask_list, Xz_list, y_list, event_index


def fit_standardizer(Xz_list: list[np.ndarray]) -> dict:
    """计算静态特征 z-score 均值与标准差。"""
    Xz = np.stack(Xz_list, axis=0) if Xz_list else np.zeros((0, _features.DEFAULTS.n_static))
    mean = Xz.mean(axis=0)
    std = Xz.std(axis=0)
    std[std < 1e-9] = 1.0
    return {"static_mean": mean.tolist(), "static_std": std.tolist()}


def main():
    ap = argparse.ArgumentParser(description="生成绩线预测训练数据包裹")
    ap.add_argument("--database-dir", default="data/pjsk/ondemand/database")
    ap.add_argument("--masterdata-dir", default="data/pjsk/ondemand")
    ap.add_argument("--out", default="data/pjsk/ondemand/forecast/models/dataset")
    ap.add_argument("--regions", default="cn,tw,jp")
    ap.add_argument("--ranks", default=None, help="逗号分隔档位；缺省用默认档位集合")
    ap.add_argument("--aug", type=int, default=6, help="前缀截断增广数量")
    args = ap.parse_args()

    regions = [r.strip() for r in args.regions.split(",") if r.strip()]
    ranks = None
    if args.ranks:
        ranks = [int(x) for x in args.ranks.split(",") if x.strip()]

    config = _features.DEFAULTS
    print(f"[export] 扫描数据库目录 {args.database_dir}, 区域={regions}")
    records, X_list, Umask_list, Xz_list, y_list, event_index = prepare_samples(
        args.database_dir, args.masterdata_dir, regions, ranks, args.aug, config
    )
    if not X_list:
        raise SystemExit("没有生成任何样本，请检查数据库目录/排名/时间范围。")

    X = np.stack(X_list, axis=0)
    Umask = np.stack(Umask_list, axis=0)
    Xz = np.stack(Xz_list, axis=0)
    y = np.stack(y_list, axis=0)

    # 静态特征 z-score 归一化
    norm = fit_standardizer(Xz_list)
    Xz = (Xz - np.array(norm["static_mean"])) / np.array(norm["static_std"])

    os.makedirs(args.out, exist_ok=True)
    npz_path = os.path.join(args.out, "dataset.npz")
    np.savez_compressed(npz_path, X=X, Umask=Umask, Xz=Xz, y=y)

    meta = {
        "schema_version": 1,
        "created": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "regions": regions,
        "n_samples": int(len(records)),
        "n_curve": int(X.shape[1]),
        "n_static": int(Xz.shape[1]),
        "has_mask": True,
        "config": {
            "n_grid": config.n_grid,
            "ref_index": config.ref_index,
            "min_grid_points": config.min_grid_points,
            "region_order": config.region_order,
            "type_order": config.type_order,
            "static_features": config.static_features,
            "wl_base_factor": config.wl_base_factor,
        },
        "normalize": norm,
    }
    meta_path = os.path.join(args.out, "meta.json")
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False)

    idx_path = os.path.join(args.out, "event_index.json")
    with open(idx_path, "w", encoding="utf-8") as f:
        json.dump(event_index, f, ensure_ascii=False, indent=2)

    sample_keys = [f"{r['region']}:{r['event_id']}" for r in records]
    label_path = os.path.join(args.out, "sample_keys.json")
    with open(label_path, "w", encoding="utf-8") as f:
        json.dump(sample_keys, f)

    # 流式写样本记录，供 eval_forecast 评测复用
    rec_path = os.path.join(args.out, "records.jsonl.gz")
    try:
        import gzip

        with gzip.open(rec_path, "wt", encoding="utf-8") as f:
            for r in records:
                f.write(json.dumps(r, ensure_ascii=False))
                f.write("\n")
        print(f"[export] 已保存可重建样本记录 {rec_path} ({len(records)} 条)")
    except Exception as e:
        print(f"[export] 警告：保存 records 失败: {e}")

    print(f"[export] 完成：样本数={len(records)}, X={X.shape}, Umask={Umask.shape}, Xz={Xz.shape}, y={y.shape}")
    print(f"[export] 活动数={len(event_index)}, 输出目录={args.out}")
    print("[export] 请将该目录拷贝到高性能机，运行 scripts/pjsk/train_model.py 炼制模型。")


if __name__ == "__main__":
    main()
