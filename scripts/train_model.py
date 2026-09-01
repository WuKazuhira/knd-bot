#!/usr/bin/env python3
"""训练端脚本：在高性能机上用 PyTorch 炼制榜线预测模型。

读入「本机导出」的数据包裹（data/pjsk/forecast/models/dataset/），训练一个 GRU
时序回归模型（量级/形态分离），输出模型包裹 `model.pt + calib.json`，
拷回本机后由 src/plugins/pjsk/sk/_model.py 做 CPU 推理。

用法（高性能机）：
    pip install torch numpy       # 任选 CPU/GPU
    python scripts/train_model.py --data data/pjsk/forecast/models/dataset \\
        --out data/pjsk/forecast/models/model  [--epochs 60] [--gpu]

产出：
    model.pt   : torch.state_dict + 架构元信息（json 键 == 架构超参）
    calib.json : 输入归一化参数 + 预测区间标定（分位数误差）
    report.json: 训练指标与按活动留出评测
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

import numpy as np

# ---------------------------------------------------------------- 加载数据


def _load_json(path: str):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_data(data_dir: str):
    data = np.load(os.path.join(data_dir, "dataset.npz"))
    meta = _load_json(os.path.join(data_dir, "meta.json"))
    sample_keys = _load_json(os.path.join(data_dir, "sample_keys.json"))
    event_index = _load_json(os.path.join(data_dir, "event_index.json"))
    return {
        "X": data["X"],
        "Umask": data["Umask"],
        "Xz": data["Xz"],
        "y": data["y"],
        "meta": meta,
        "sample_keys": sample_keys,
        "event_index": event_index,
    }


# ---------------------------------------------------------------- 活动级划分
def activity_splits(sample_keys: list[str], event_index: dict, holdout_frac: float):
    """按活动留出划分训练/验证。

    返回 (train_idx, val_idx)。彻底避免同一活动被同时放进 train 与 val。
    """
    # 活动 -> 样本 idx
    acts: dict[str, list[int]] = {}
    for i, key in enumerate(sample_keys):
        acts.setdefault(key, []).append(i)
    keys = sorted(acts.keys())
    # 简单哈希划分（可复现，不需随机种子漂移）
    n_val = max(1, int(len(keys) * holdout_frac))
    val_keys = set(keys[:: max(1, len(keys) // n_val)][:n_val])
    train_idx, val_idx = [], []
    for a, idxs in acts.items():
        if a in val_keys:
            val_idx.extend(idxs)
        else:
            train_idx.extend(idxs)
    return np.array(sorted(train_idx)), np.array(sorted(val_idx))


# ---------------------------------------------------------------- 模型
def build_model(n_static: int, input_size: int, hidden: int, layers: int, dropout: float):
    import torch
    from torch import nn

    class GRUFinalModel(nn.Module):
        """输入 [B, T, input_size]，静态特征拼接进每时间步；输出 [B,1] = log(final/ref)。"""

        def __init__(self, n_static, input_size, hidden, layers, dropout):
            super().__init__()
            self.gru = nn.GRU(
                input_size=input_size,
                hidden_size=hidden,
                num_layers=layers,
                batch_first=True,
                dropout=dropout if layers > 1 else 0.0,
            )
            self.head = nn.Sequential(
                nn.Linear(hidden + n_static, hidden),
                nn.ReLU(),
                nn.Dropout(dropout),
                nn.Linear(hidden, 1),
            )

        def forward(self, seq, static):
            # seq: [B, T, input_size]；static: [B, n_static]
            out, _ = self.gru(seq)  # out: [B, T, hidden]
            last = out[:, -1, :]  # 最后一时间步隐状态
            x = torch.cat([last, static], dim=-1)
            return self.head(x).squeeze(-1)

    return GRUFinalModel(n_static, input_size, hidden, layers, dropout)


def export_numpy_json_weights(state_dict: dict, path: str) -> None:
    """把 PyTorch state_dict 里 GRU 与 head 的权重导出为纯 JSON，供本机纯 stdlib 推理。

    约定（与 src/plugins/pjsk/sk/_model.py 一致）：
      - gru: 每层 4 个键 (weight_ih/bias_ih/weight_hh/bias_hh)，作用在行 [r,z,n] 顺序上
      - head: 0=Linear(hidden+n_static,hidden), 3=Linear(hidden,1)，推理阶段 drop 关闭
    权重 matrix 以「行优先」列表存储，bias 为列表，float 保留全精度。
    """
    import json

    def to_float_list(x):
        return [float(v) for v in x.flatten().tolist()]

    weights: dict = {}
    for k, v in state_dict.items():
        if k.startswith("gru."):
            name = k[len("gru."):]
            weights[k] = (
                to_float_list(v)
                if v.ndim == 1  # bias_hh / bias_ih
                else [to_float_list(row) for row in v]
            )
        elif k.startswith("head."):
            weights[k] = (
                to_float_list(v)
                if v.ndim == 1  # bias
                else [to_float_list(row) for row in v]
            )
        # 其余键（非 GRU/head）不参与本机推理，忽略
    with open(path, "w", encoding="utf-8") as f:
        json.dump(weights, f, ensure_ascii=False, separators=(",", ":"))
    print(f"[train] 已导出纯文本权重 {path}")


def make_sequence_inputs(X, Umask, Xz, meta):
    """把 [B,T] 的 curve + mask + 静态特征拼成 GRU 输入 [B,T,input_size]。

    输入维度：curve(1) + mask(1) + static 广播(n_static)。
    额外每个时间步补充 progress 刻度（由网格位置推出 p，作为模型的时间感）。
    """
    n_static = int(meta["n_static"])
    T = X.shape[1]
    p_grid = np.linspace(0.0, 1.0, T, dtype=np.float64)
    # 每步输入: [curve, mask, progress, static...]
    seq = np.zeros((X.shape[0], T, 1 + 1 + 1 + n_static), dtype=np.float64)
    seq[:, :, 0] = X  # curve
    seq[:, :, 1] = Umask  # mask
    seq[:, :, 2] = p_grid[None, :]  # progress 刻度（逐格）
    # 静态特征广播到每个时间步
    static_reshaped = np.broadcast_to(Xz[:, None, :], (X.shape[0], T, n_static))
    seq[:, :, 3:] = static_reshaped
    return seq


# ---------------------------------------------------------------- 非对称损失
import torch as _torch  # 训练端依赖 torch；顶层 import 供自定义损失定义使用

class AsymmetricHuberLoss(_torch.nn.Module):
    """对 log_ratio 做非对称 Huber 回归。

    诊断发现 GRU 系统性低估最终榜线（活动末期冲榜未被学到，对称 MSE 把它当作
    噪声平均掉）。该损失对>低估侧赋予更高权重 `low_w`，使估计在 log 空间向其
    高分位偏移，从而修正系统性低估。smooth-l1 形状对离群(极端冲榜)鲁棒。
    """

    def __init__(self, low_w: float = 2.0, high_w: float = 1.0, delta: float = 1.0):
        super().__init__()
        self.low_w = low_w
        self.high_w = high_w
        self.delta = delta

    def forward(self, pred, target):
        diff = pred - target  # 目标为 log ratio；pred 低(diff<0)即低估
        abs_d = diff.abs()
        # 每样本权重：低估(实际值更高→我们没追上)用 low_w，高估用 high_w
        w = _torch.where(diff < 0, _torch.full_like(diff, self.low_w), _torch.full_like(diff, self.high_w))
        # smooth-l1
        smooth = _torch.where(
            abs_d < self.delta,
            0.5 * diff * diff / self.delta,
            abs_d - 0.5 * self.delta,
        )
        return (w * smooth).mean()


# ---------------------------------------------------------------- 评测
def eval_model(model, seq, xz, y_true, batch_size, device, mean, std):
    """返回 (pred_log_ratio numpy, mape_pct)。"""
    import torch

    model.eval()
    preds = []
    with torch.no_grad():
        for i in range(0, len(y_true), batch_size):
            s = torch.from_numpy(seq[i : i + batch_size]).float().to(device)
            st = torch.from_numpy(xz[i : i + batch_size]).float().to(device)
            pred = model(s, st).cpu().numpy()
            preds.append(pred)
    pred = np.concatenate(preds, axis=0)
    # 转回绝对误差（因为 y 是 log ratio，相对误差看 exp 差）
    y_abs = np.exp(np.concatenate(y_true, axis=0) if isinstance(y_true, list) else y_true)
    pred_abs = np.exp(pred)
    rel_err = np.abs(pred_abs - y_abs) / y_abs
    return pred, float(np.mean(rel_err)), rel_err


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data/pjsk/forecast/models/dataset")
    ap.add_argument("--out", default="data/pjsk/forecast/models/model")
    ap.add_argument("--epochs", type=int, default=60)
    ap.add_argument("--batch", type=int, default=256)
    ap.add_argument("--lr", type=float, default=1e-3)
    ap.add_argument("--hidden", type=int, default=64)
    ap.add_argument("--layers", type=int, default=2)
    ap.add_argument("--dropout", type=float, default=0.2)
    ap.add_argument("--val-frac", type=float, default=0.2)
    ap.add_argument("--gpu", action="store_true")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--loss", choices=["mse", "asym"], default="mse",
                    help="回归损失：mse=对称MSE；asym=非对称Huber(早期验证未改善低估，默认用mse)")
    ap.add_argument("--low-w", type=float, default=2.0,
                    help="asym 损失中低估侧权重（>high_w 以矫正系统性低估）")
    ap.add_argument("--late-w", type=float, default=0.0,
                    help="样本按进度加权：权重=1+late_w*progress。大于0时聚焦最终这段预测")
    args = ap.parse_args()

    import torch
    from torch import nn

    torch.manual_seed(args.seed)

    device = torch.device("cuda" if args.gpu and torch.cuda.is_available() else "cpu")
    print(f"[train] device={device}, torch={torch.__version__}")

    data = load_data(args.data)
    meta = data["meta"]
    X, Umask, Xz, y = data["X"], data["Umask"], data["Xz"], data["y"]
    n_static = int(meta["n_static"])
    n_curve = int(meta["n_curve"])

    train_idx, val_idx = activity_splits(data["sample_keys"], data["event_index"], args.val_frac)
    print(f"[train] 样本={len(X)}, train={len(train_idx)}, val={len(val_idx)} (按活动留出)")

    seq = make_sequence_inputs(X, Umask, Xz, meta)
    input_size = seq.shape[2]
    y = y.ravel()

    seq_tr, seq_va = seq[train_idx], seq[val_idx]
    xz_tr, xz_va = Xz[train_idx], Xz[val_idx]
    y_tr, y_va = y[train_idx], y[val_idx]

    model = build_model(n_static, input_size, args.hidden, args.layers, args.dropout).to(device)
    print(model)

    opt = torch.optim.Adam(model.parameters(), lr=args.lr)
    sched = torch.optim.lr_scheduler.CosineAnnealingLR(opt, T_max=args.epochs)
    if args.loss == "asym":
        loss_fn = AsymmetricHuberLoss(low_w=args.low_w, high_w=1.0, delta=1.0)
        print(f"[train] 损失=非对称Huber (低估权重={args.low_w}:高估权重=1, delta=1)")
    else:
        loss_fn = nn.MSELoss()  # 对 log ratio 做 MSE == 对称相对误差
        print("[train] 损失=对称MSE")

    best_val = float("inf")
    best_state = None
    for epoch in range(args.epochs):
        model.train()
        perm = torch.randperm(len(seq_tr))
        tot = 0.0
        n_batch = 0
        for i in range(0, len(perm), args.batch):
            idx = perm[i : i + args.batch]
            s = torch.from_numpy(seq_tr[idx]).float().to(device)
            st = torch.from_numpy(xz_tr[idx]).float().to(device)
            tgt = torch.from_numpy(y_tr[idx]).float().to(device)
            opt.zero_grad()
            out = model(s, st)
            # 按进度加权的平方误差：后期(progress高)样本更受重视，
            # 聚焦"最终最后一天"预测场景（static[1] 为 progress=progress_ceil）。
            # 这里统一用加权 MSE（不对齐 MSE 的数学性质，但对 log(target) 依旧合理）。
            if args.late_w > 0:
                prog = st[:, 1].clamp(0.0, 1.0)
                w = 1.0 + args.late_w * prog
                loss = ((out - tgt) ** 2 * w).mean()
            else:
                loss = loss_fn(out, tgt)
            loss.backward()
            opt.step()
            tot += loss.item() * len(idx)
            n_batch += len(idx)
        sched.step()
        train_loss = tot / max(1, n_batch)

        model.eval()
        with torch.no_grad():
            pred_va, rel_err, relerrs = eval_model(
                model, seq_va, xz_va, y_va, args.batch, device, None, None
            )
        metric = float(np.mean(relerrs)) * 100
        if metric < best_val:
            best_val = metric
            best_state = {k: v.detach().cpu().clone() for k, v in model.state_dict().items()}
        if epoch % 10 == 0 or epoch == args.epochs - 1:
            print(f"[train] epoch={epoch+1}/{args.epochs} loss={train_loss:.4f} val_mape={metric:.2f}%")

    # 载入最优权重做最终评测
    model.load_state_dict(best_state)
    pred_va, final_mape, relerrs = eval_model(model, seq_va, xz_va, y_va, args.batch, device, None, None)
    # 分位数标定（用于预测区间）
    abs_err = relerrs  # 相对误差序列
    calib = {
        "p10": float(np.quantile(abs_err, 0.10)),
        "p50": float(np.quantile(abs_err, 0.50)),
        "p90": float(np.quantile(abs_err, 0.90)),
    }

    os.makedirs(args.out, exist_ok=True)
    # 保存 state_dict + 架构
    arch = {
        "model": "gru_final",
        "input_size": input_size,
        "n_static": n_static,
        "n_curve": n_curve,
        "hidden": args.hidden,
        "layers": args.layers,
        "dropout": args.dropout,
    }
    # 标准 torch 产物（供 torch 复载/复核）
    torch.save(
        {"arch": arch, "state_dict": best_state},
        os.path.join(args.out, "model.pt"),
    )
    # 纯文本权重副本：本机 bot(Python 3.14t, 无 numpy/torch) 用纯 stdlib 反序列化做 GRU 推理。
    export_numpy_json_weights(best_state, os.path.join(args.out, "model_weights.json"))
    with open(os.path.join(args.out, "calib.json"), "w", encoding="utf-8") as f:
        json.dump(
            {
                "schema_version": 1,
                "model": "gru_final",
                "n_curve": n_curve,
                "n_static": n_static,
                "best_val_mape_pct": round(final_mape * 100, 3),
                "quantile": calib,
                "source_meta": {
                    "n_samples": int(meta["n_samples"]),
                    "created_meta": meta.get("created"),
                    "static_features": meta["config"]["static_features"],
                },
            },
            f,
            ensure_ascii=False,
            indent=2,
        )
    report = {
        "val_mape_pct": round(final_mape * 100, 3),
        "val_n": int(len(val_idx)),
        "quantile": calib,
        "arch": arch,
        "epochs": args.epochs,
    }
    with open(os.path.join(args.out, "report.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    print(f"[train] 完成。val_mape={final_mape*100:.2f}%, 输出目录={args.out}")
    print(f"[train] quantile(rel err): p10={calib['p10']*100:.1f}% p50={calib['p50']*100:.1f}% p90={calib['p90']*100:.1f}%")


if __name__ == "__main__":
    main()
