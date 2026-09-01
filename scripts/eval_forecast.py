#!/usr/bin/env python3
"""历史回头看评测：在新旧榜线预测模型上量化误差。

复用 export_dataset 的样本准备（records 含 points / start/end / final_score / progress_ceil），
对每个样本同时跑：
  - 深度学习 GRU 模型（src/plugins/pjsk/sk/_model.py，纯 stdlib）
  - 旧经验式模型（src/plugins/pjsk/sk/_legacy.py，纯 stdlib）
与真实最终分对比，报告分位数相对误差，并按预测进度分桶统计。

本脚本不依赖 torch / numpy（纯 stdlib），可在本机或高机运行。

用法：
    python scripts/eval_forecast.py \
        [--database-dir data/pjsk/database] [--masterdata-dir data/pjsk/masterdata] \
        [--model-dir data/pjsk/forecast/models/model] [--ranks ...] [--aug 6]
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
import os
import sys

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_SRC = os.path.normpath(os.path.join(_THIS_DIR, "..", "src"))


def _load_mod(name: str, relpath: str):
    path = os.path.normpath(os.path.join(_SRC, relpath))
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


_model = _load_mod("pjsk_model", "plugins/pjsk/sk/_model.py")
_legacy = _load_mod("pjsk_legacy", "plugins/pjsk/sk/_legacy.py")
_features = _load_mod("pjsk_features", "plugins/pjsk/sk/_features.py")
_event_meta = _load_mod("pjsk_event_meta", "plugins/pjsk/sk/_event_meta.py")

_export = {"mod": None}  # 惰性加载 scripts/export_dataset.py


def _load_export():
    if _export["mod"] is None:
        spec = importlib.util.spec_from_file_location(
            "pjsk_export", os.path.join(_THIS_DIR, "export_dataset.py")
        )
        mod = importlib.util.module_from_spec(spec)
        sys.modules["pjsk_export"] = mod
        spec.loader.exec_module(mod)
        _export["mod"] = mod
    return _export["mod"]


def _load_records(database_dir, masterdata_dir, regions, ranks, aug, gz_path):
    """优先读 records.jsonl.gz（免重扫数据库）；缺失则现场组装。"""
    jsonl = gz_path and (gz_path.endswith(".jsonl.gz") or gz_path.endswith(".jsonl"))
    if jsonl and os.path.exists(gz_path):
        import gzip

        recs = []
        opener = gzip.open(gz_path, "rt", encoding="utf-8") if gz_path.endswith(".gz") else open(gz_path, encoding="utf-8")
        with opener as f:
            for line in f:
                line = line.strip()
                if line:
                    recs.append(json.loads(line))
        return recs
    export = _load_export()
    records, _, _, _, _, _ = export.prepare_samples(
        database_dir, masterdata_dir, regions, ranks, aug, _features.DEFAULTS
    )
    return records


def _summarize(errors: list[float], label: str) -> None:
    if not errors:
        print(f"  {label}: 无样本")
        return
    es = sorted(errors)
    n = len(es)
    p10 = es[int(0.10 * (n - 1))]
    p50 = es[int(0.50 * (n - 1))]
    p90 = es[int(0.90 * (n - 1))]
    mean = sum(es) / n
    print(f"  {label}: n={n}  mape(mean)={mean*100:.2f}%  p10={p10*100:.2f}%  p50={p50*100:.2f}%  p90={p90*100:.2f}%")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--database-dir", default="data/pjsk/database")
    ap.add_argument("--masterdata-dir", default="data/pjsk/masterdata")
    ap.add_argument("--model-dir", default="data/pjsk/forecast/models/model")
    ap.add_argument("--ranks", default=None, help="逗号分隔档位；缺省用 _features.RANK_LEVELS")
    ap.add_argument("--aug", type=int, default=6)
    ap.add_argument("--max-samples", type=int, default=None,
                    help="最多评测样本数；缺省全部。设小值（如3000）可快速抽样")
    ap.add_argument("--records", default=None,
                    help="records.jsonl.gz 路径；缺省 <dataset_dir>/records.jsonl.gz 或现场组装")
    args = ap.parse_args()

    weights_json = os.path.join(args.model_dir, "model_weights.json")
    calib_json = os.path.join(args.model_dir, "calib.json")
    if not os.path.exists(weights_json):
        raise SystemExit(f"找不到模型权重: {weights_json}")
    predictor = _model.GruPredictor(weights_json, calib_json)

    if args.ranks:
        ranks = [int(x) for x in args.ranks.split(",") if x.strip()]
    else:
        ranks = list(_features.RANK_LEVELS)
    regions = ["cn", "tw", "jp"]

    gz_path = args.records or os.path.join(
        os.path.dirname(weights_json), "..", "dataset", "records.jsonl.gz"
    )
    records = _load_records(
        args.database_dir, args.masterdata_dir, regions, ranks, args.aug, gz_path
    )
    if args.ranks and records and args.ranks:
        records = [r for r in records if int(r["rank"]) in ranks]
    if args.max_samples and len(records) > args.max_samples:
        # 按活动分组，均匀抽样以保证覆盖各服务/类型/进度
        by_act: dict[str, list] = {}
        for r in records:
            by_act.setdefault(f"{r['region']}:{r['event_id']}", []).append(r)
        acts = sorted(by_act.keys())
        target_per_act = max(1, args.max_samples // len(acts))
        sampled: list = []
        for a in acts:
            grp = by_act[a]
            step = max(1, len(grp) / target_per_act)
            for i in range(0, len(grp), int(step)):
                sampled.append(grp[i])
        # 若仍超，进一步截断（不破坏均匀性）
        records = sampled[: args.max_samples]
    print(f"[eval] 载入样本 {len(records)}，模型 {weights_json}")

    err_ml: list[float] = []
    err_legacy: list[float] = []
    # 按进度桶（两模型）
    buck_ml: dict[int, list[float]] = {}
    buck_legacy: dict[int, list[float]] = {}
    # 仅"进行中"预测（progress<1.0，模拟活动未结束的实时预测）
    inprogress_ml: list[float] = []
    inprogress_legacy: list[float] = []

    for r in records:
        true = float(r["final_score"])
        if true <= 0:
            continue
        points = [(int(t), int(s)) for t, s in r["points"]]
        start_ts, end_ts = int(r["start_ts"]), int(r["end_ts"])
        event_type = r["type"]
        progress_ceil = float(r.get("progress_ceil", 1.0))

        # --- GRU 预测 ---
        pred_ml = predictor.predict_final(
            timeline=[(float(t), float(s)) for t, s in points],
            start_ts=float(start_ts),
            end_ts=float(end_ts),
            region=r["region"],
            event_type=event_type,
            rank=float(r["rank"]),
            progress_ceil=progress_ceil,
        )
        if pred_ml is None:
            # 特征不足，跳过该样本的两端对比（不扭曲统计）
            continue

        # --- 旧经验式预测 ---
        final_legacy, _ = _legacy.predict_future_rankings(points, start_ts, end_ts, sample_points=80)

        err_ml.append(abs(pred_ml[0] - true) / true)
        err_legacy.append(abs(final_legacy - true) / true)
        bucket = int(max(0.0, min(0.99, progress_ceil)) // 0.25)
        buck_ml.setdefault(bucket, []).append(abs(pred_ml[0] - true) / true)
        buck_legacy.setdefault(bucket, []).append(abs(final_legacy - true) / true)
        if progress_ceil < 0.99:
            inprogress_ml.append(abs(pred_ml[0] - true) / true)
            inprogress_legacy.append(abs(final_legacy - true) / true)

    print("\n=== 整体对比（相对误差）===")
    _summarize(err_ml, "GRU 深度学习")
    _summarize(err_legacy, "旧经验式")

    print("\n=== 进行中预测（progress<1.0，模拟活动未结束时）===")
    _summarize(inprogress_ml, "GRU 深度学习")
    _summarize(inprogress_legacy, "旧经验式")

    print("\n=== 按预测进度分桶（中位数相对误差）===")
    for b in sorted(set(list(buck_ml.keys()) + list(buck_legacy.keys()))):
        lo, hi = b * 0.25, (b + 1) * 0.25
        ml = sorted(buck_ml.get(b, []))
        lg = sorted(buck_legacy.get(b, []))
        p50_ml = ml[len(ml) // 2] * 100 if ml else 0.0
        p50_lg = lg[len(lg) // 2] * 100 if lg else 0.0
        print(f"  progress {lo:.2f}-{min(1.0, hi):.2f}: GRU p50={p50_ml:6.1f}% (n={len(ml):4d})   "
              f"经验式 p50={p50_lg:6.1f}% (n={len(lg):4d})")


if __name__ == "__main__":
    main()
