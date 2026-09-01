#!/usr/bin/env python3
"""数据导出脚本：从 sk_{cn,jp,tw} 的 ranking.db 生成训练数据集包裹。

在本机（低算力）运行，一次性离线把数据库整理成高性能机训练用的 dataset.npz + meta.json。

用法：
    python scripts/export_dataset.py [--database-dir data/pjsk/database] \\
        [--masterdata-dir data/pjsk/masterdata] [--out data/pjsk/forecast/models/dataset] \\
        [--regions cn,tw,jp] [--ranks '10,50,100,1000,10000'] [--aug 1]

产出（默认写到 data/pjsk/forecast/models/）：
    dataset.npz     : X (曲线特征), Xz(静态特征), y(目标), event_meta 索引
    meta.json       : 特征工程配置与归一化信息（训练端/推断端共用）
    train_split.json: 训练/验证的活动划分（供 train_model.py 使用）

仅依赖标准库 + numpy + 本仓库 src 下的 _features / _event_meta（纯 stdlib/numpy）。
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sqlite3
import sys
from datetime import datetime

import numpy as np

# ---------------------------------------------------------------- 路径辅助
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_SRC = os.path.normpath(os.path.join(_THIS_DIR, "..", "src"))


def _load_mod(name: str, relpath: str):
    """从 src 下直接加载纯 stdlib/numpy 模块（不触发 bot 包初始化）。"""
    path = os.path.normpath(os.path.join(_SRC, relpath))
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


_features = _load_mod("pjsk_features", "plugins/pjsk/sk/_features.py")
_event_meta = _load_mod("pjsk_event_meta", "plugins/pjsk/sk/_event_meta.py")

# ---------------------------------------------------------------- 数据加载


def _iter_db_files(database_dir: str, regions: list[str]):
    """yield (region, event_id, db_path)。"""
    for region in regions:
        region_dir = os.path.join(database_dir, f"sk_{region}")
        if not os.path.isdir(region_dir):
            continue
        for fname in sorted(os.listdir(region_dir)):
            if not fname.endswith("_ranking.db"):
                continue
            stem = fname[: -len("_ranking.db")]
            if not stem.isdigit():
                continue
            event_id = int(stem)
            yield region, event_id, os.path.join(region_dir, fname)


def _query_rank_series(db_path: str, ranks: list[int] | None) -> dict[int, list[tuple[int, float]]]:
    """查询一个 activity db，返回 {rank: [(ts, score), ...]}，仅取感兴趣的档位。"""
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.cursor()
        if ranks:
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
    """返回数据库内 MIN(ts), MAX(ts) 作为时间范围回退（masterdata 不可读时）。"""
    try:
        conn = sqlite3.connect(db_path)
        try:
            cur = conn.cursor()
            cur.execute("SELECT MIN(ts), MAX(ts) FROM ranking")
            row = cur.fetchone()
            return (int(row[0]), int(row[1])) if row and row[0] is not None else None
        finally:
            conn.close()
    except Exception:
        return None


# ---------------------------------------------------------------- 样本构造


def _build_activity_samples(
    region: str,
    event_id: int,
    db_path: str,
    masterdata_root: str,
    ranks: list[int] | None,
    max_ts: int,
) -> list[dict]:
    """构造单个活动的样本；活动未结束则返回空。"""
    fallback = _db_time_range(db_path)
    meta, _ = _event_meta.resolve_meta(masterdata_root, region, event_id, fallback=fallback)
    if meta is None:
        return []
    if meta.end_ts >= max_ts:
        return []  # 活动尚未结束（或在结算缓冲内），无法确定最终分
    series = _query_rank_series(db_path, ranks)  # SQL 已按档位过滤
    samples: list[dict] = []
    for rank, points in series.items():
        if ranks is not None and rank not in ranks:
            continue
        # 过滤掉时间范围外的点（数据库可能残留跨活动数据）
        points = [(ts, s) for ts, s in points if meta.start_ts <= ts <= meta.end_ts]
        if len(points) < _features.DEFAULTS.min_grid_points:
            continue
        points = _downsample_points(points, max_pts=120)  # 控制内存/体积
        if len(points) < _features.DEFAULTS.min_grid_points:
            continue
        # 最终分 = 结束时间最后一条的分数
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
    """把时序点均匀降采样到至多 max_pts 个，降低内存占用（保留首尾与时速轮廓）。"""
    if len(points) <= max_pts:
        return points
    idx = sorted(set(round(i * (len(points) - 1) / (max_pts - 1)) for i in range(max_pts)))
    return [points[i] for i in idx]


def _augment_prefix(
    sample: dict, config, rng: np.random.Generator, max_aug: int
) -> list[dict]:
    """按相对进度做前缀截断增广。

    返回若干个 sample 副本，每个带 progress_ceil（该副本用于预测的进度上界）。
    保留完整样本（ceil=1.0）及 max_aug 个截断前缀样本。
    """
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
    """组装所有样本。

    返回 (records, X_list, Umask_list, Xz_list, y_list, event_index)。
    """
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


# ---------------------------------------------------------------- 归一化


def fit_standardizer(Xz_list: list[np.ndarray]) -> dict:
    """对静态特征做 z-score 归一化（推断端需保存 mean/std）。"""
    Xz = np.stack(Xz_list, axis=0) if Xz_list else np.zeros((0, _features.DEFAULTS.n_static))
    mean = Xz.mean(axis=0)
    std = Xz.std(axis=0)
    std[std < 1e-9] = 1.0
    return {"static_mean": mean.tolist(), "static_std": std.tolist()}


def main():
    ap = argparse.ArgumentParser(description="生成绩线预测训练数据包裹")
    ap.add_argument("--database-dir", default="data/pjsk/database")
    ap.add_argument("--masterdata-dir", default="data/pjsk/masterdata")
    ap.add_argument("--out", default="data/pjsk/forecast/models/dataset")
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

    X = np.stack(X_list, axis=0)  # [N, n_grid] 对数差曲线
    Umask = np.stack(Umask_list, axis=0)  # [N, n_grid] 观测掩码
    Xz = np.stack(Xz_list, axis=0)  # [N, n_static]
    y = np.stack(y_list, axis=0)  # [N, 1]

    # 归一化静态特征（曲线特征已在 extract_features 中对数差归一化；mask 恒 0/1 无需）
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

    # 活动级索引（用于按活动留出法划分训练/验证）
    idx_path = os.path.join(args.out, "event_index.json")
    with open(idx_path, "w", encoding="utf-8") as f:
        json.dump(event_index, f, ensure_ascii=False, indent=2)

    # 每个样本隶属的活动标签（长度 = n_samples），供按活动切分
    sample_keys = [f"{r['region']}:{r['event_id']}" for r in records]
    label_path = os.path.join(args.out, "sample_keys.json")
    with open(label_path, "w", encoding="utf-8") as f:
        json.dump(sample_keys, f)

    # 记录可重建信息（供 eval_forecast 做新旧对比，无需重扫数据库）
    # 逐行流式写为 JSONL，避免一次性 json.dump 大列表造成内存峰值。
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
    print("[export] 请将该目录拷贝到高性能机，运行 scripts/train_model.py 炼制模型。")


if __name__ == "__main__":
    main()
