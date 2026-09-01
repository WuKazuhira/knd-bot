"""榜线预测模型的本机 CPU 推断端（纯 stdlib，零 numpy/torch 依赖）。

与训练端 scripts/train_model.py 共享完全一致的：
  - 特征构造（进度格点 interp、log 差曲线、观测掩码、静态 one-hot、序列拼接）
  - GRU 前向数学（PyTorch 双线性 GRU：门序 [r,z,n]，new 门对 hidden 部分乘 r）+ head MLP

权重来源：训练端从 model.pt 导出的纯文本 ``model_weights.json``（与 calib.json 同目录）。

本模块被 Bot 主环境（Python 3.14 free-threading，无 numpy/torch）调用，
因此刻意避免任何第三方 import，只用 math 等标准库。
"""

from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

# --------------------------------------------------------------------------- 常量


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, float(x)))


def _linspace(n: int) -> List[float]:
    """等距 [0,1] 网格（与 np.linspace(0.0,1.0,n) 一致，n>=2）。"""
    if n <= 1:
        return [0.0]
    step = 1.0 / (n - 1)
    return [_clamp01(i * step) for i in range(n)]


def _interp(xi: float, xp: List[float], fp: List[float]) -> float:
    """单点线性插值，语义对齐 numpy.interp(xi, xp, fp)（xp 已升序）。"""
    n = len(xp)
    if n == 0:
        return 0.0
    if xi <= xp[0]:
        return fp[0]
    if xi >= xp[-1]:
        return fp[-1]
    lo, hi = 0, n - 1
    while lo + 1 < hi:  # 二分找区间
        mid = (lo + hi) // 2
        if xp[mid] <= xi:
            lo = mid
        else:
            hi = mid
    if xp[hi] == xp[lo]:
        return fp[hi]
    t = (xi - xp[lo]) / (xp[hi] - xp[lo])
    return fp[lo] + (fp[hi] - fp[lo]) * t


# 标准服务区与活动类型 one-hot 顺序（必须与 _features.REGION_ORDER / TYPE_ORDER 一致）
REGION_ORDER: List[str] = ["cn", "tw", "jp"]
TYPE_ORDER: List[str] = ["marathon", "world_bloom"]
_BASE_STATIC: List[str] = ["log_rank", "progress"]

# WL 章节编码系数（与 _features / _forecast 一致）
_WL_FACTOR = 1000
# 参考点索引（进度网格第 1 个有分时刻）
_REF_INDEX = 0
# 最少格点数（信息不足放弃）
_MIN_GRID = 4


def _static_features(region_order: List[str], type_order: List[str]) -> List[str]:
    return list(_BASE_STATIC) + [f"region_{r}" for r in region_order] + [f"type_{t}" for t in type_order]


# --------------------------------------------------------------------------- 权重加载


class ModelLoadError(Exception):
    """模型包裹加载失败。"""


def _read_many(f, n: int) -> List[float]:
    return [float(f.readline().strip()) for _ in range(n)]


def load_weights(weights_json: str) -> Dict[str, object]:
    if not os.path.exists(weights_json):
        raise ModelLoadError(f"找不到权重文件 {weights_json}")
    try:
        with open(weights_json, "r", encoding="utf-8") as f:
            w = json.load(f)
    except Exception as e:
        raise ModelLoadError(f"解析权重 JSON 失败: {e}") from e
    if not isinstance(w, dict):
        raise ModelLoadError("权重文件顶层应为 dict")
    return w


def load_calib(calib_json: str) -> Dict:
    if not os.path.exists(calib_json):
        return {}
    try:
        with open(calib_json, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


# --------------------------------------------------------------------------- 特征构造（纯 Python，对齐 _features + make_sequence_inputs）


def _progress_position(ts: float, start_ts: float, end_ts: float) -> float:
    duration = max(1.0, end_ts - start_ts)
    return _clamp01((ts - start_ts) / duration)


def _timeline_to_grid(
    timeline: Sequence[Tuple[float, float]],
    start_ts: float,
    end_ts: float,
    progress_ceil: float,
    n_grid: int,
) -> Optional[Tuple[List[float], List[float]]]:
    """(ts, score) -> (score_grid, mask_grid)，语义对齐 _features.timeline_to_grid。

    返回 None 表示信息不足。
    """
    # 去重（同 ts 保留最后一条），升序
    uniq: Dict[float, float] = {}
    for t, s in timeline:
        uniq[float(t)] = float(s)
    items = sorted(uniq.items())
    if len(items) < 2:
        return None
    ts_items = [t for t, _ in items]
    score_items = [s for _, s in items]

    p_grid = _linspace(n_grid)
    # ceil 内观测点
    obs = [(ts_items[i], score_items[i]) for i in range(len(items))
           if _progress_position(ts_items[i], start_ts, end_ts) <= progress_ceil + 1e-9]
    if len(obs) < 2:
        return None
    obs_p = [_progress_position(t, start_ts, end_ts) for t, _ in obs]
    obs_s = [s for _, s in obs]

    score_grid: List[float] = []
    mask_grid: List[float] = []
    valid = obs_p[-1] + 1e-9
    for p in p_grid:
        val = _interp(p, obs_p, obs_s)
        score_grid.append(val)
        inside = (p >= obs_p[0] - 1e-9) and (p <= valid)
        within = p <= progress_ceil + 1e-9
        mask_grid.append(1.0 if (inside and within) else 0.0)
    return score_grid, mask_grid


def _latest_progress(timeline: Sequence[Tuple[float, float]], start_ts: float, end_ts: float) -> float:
    if not timeline:
        return 0.0
    last_ts = timeline[-1][0]
    return max(1e-9, _progress_position(float(last_ts), start_ts, end_ts))


def _build_inputs(
    timeline: Sequence[Tuple[float, float]],
    start_ts: float,
    end_ts: float,
    region: str,
    event_type: str,
    rank: float,
    n_grid: int,
    region_order: List[str],
    type_order: List[str],
    progress_ceil: Optional[float] = None,
) -> Optional[Tuple[List[float], List[float], List[float], float]]:
    """构造 GRU 输入序列所必需的最核心三组件。

    返回 (curve, mask, static_vec, ref_score)：
      curve    = log(score_grid/ref)，未观测置 0
      mask     = 观测掩码 {0,1}
      static   = [log_rank, progress, region one-hot, type one-hot]
      ref_score = 量级基准分（参考点分值）
    不满足最小观测格点要求时返回 None。

    progress_ceil：预测时刻的进度上界。默认取"已有观测的最新进度"；
    调用方可显式传入（与训练端样本的 progress_ceil 一致，保证静态特征对齐）。
    未观测的尾部区间会被 mask 掉，与训练端前缀截断样本语义一致（防止未来数据泄露）。
    """
    if progress_ceil is None:
        progress_ceil = _latest_progress(timeline, start_ts, end_ts)
    progress_ceil = max(1e-9, _clamp01(progress_ceil))
    grid = _timeline_to_grid(timeline, start_ts, end_ts, progress_ceil, n_grid)
    if grid is None:
        return None
    score_grid, mask_grid = grid
    # 参考点 = 最后一个观测格点的分数（与训练端 _features.extract_features 对齐）。
    # target=log(final/last_obs) 即末期剩余增幅；推断解码 final=last_obs*exp(lr) 协议不变。
    ref = None
    for i in range(len(mask_grid) - 1, -1, -1):
        if mask_grid[i] > 0 and score_grid[i] > 0:
            ref = float(score_grid[i])
            break
    if ref is None or ref <= 0:
        return None
    obs_cnt = int(round(sum(mask_grid)))
    if obs_cnt < _MIN_GRID:
        return None

    curve: List[float] = []
    for i in range(n_grid):
        if mask_grid[i] > 0 and score_grid[i] > 0:
            curve.append(math.log(score_grid[i]) - math.log(ref))
        else:
            curve.append(0.0)

    # 静态特征
    static = [math.log(max(1.0, float(rank))), _clamp01(progress_ceil)]
    r_idx = region_order.index(region) if region in region_order else 0
    t_idx = type_order.index(event_type) if event_type in type_order else 0
    static = static + [1.0 if i == r_idx else 0.0 for i in range(len(region_order))]
    static = static + [1.0 if i == t_idx else 0.0 for i in range(len(type_order))]
    return curve, mask_grid, static, ref


def _make_sequence(curve, mask, static, n_grid: int) -> List[List[float]]:
    """拼接 [curve, mask, progress刻度, static...] 每时间步，对齐训练端 make_sequence_inputs。"""
    p_grid = _linspace(n_grid)
    seq: List[List[float]] = []
    for t in range(n_grid):
        seq.append([curve[t], mask[t], p_grid[t]] + list(static))
    return seq


# --------------------------------------------------------------------------- GRU 前向（纯 Python，对齐 PyTorch GRU）


def _sigmoid(x: float) -> float:
    if x <= -700:
        return 0.0
    if x >= 700:
        return 1.0
    return 1.0 / (1.0 + math.exp(-x))


def _tanh(x: float) -> float:
    # 大数兜底，避免 math 溢出
    if x > 30:
        return 1.0
    if x < -30:
        return -1.0
    return math.tanh(x)


def _matmul_vec(w_rows: List[List[float]], v: List[float]) -> List[float]:
    out = [0.0] * len(w_rows)
    for r, row in enumerate(w_rows):
        acc = 0.0
        for c, xc in enumerate(v):
            acc += row[c] * xc
        out[r] = acc
    return out


def _gru_layer(x_t: List[float], h_prev: List[float],
               Wih: List[List[float]], bih: List[float],
               Whh: List[List[float]], bhh: List[float],
               hidden: int) -> List[float]:
    """单层 GRU 时间步前向。门序 [r,z,n]，new 门 = tanh(gi_n + r*gh_n)。"""
    gi = _matmul_vec(Wih, x_t)  # 3H 维
    gh = _matmul_vec(Whh, h_prev)  # 3H 维
    for c in range(3 * hidden):
        gi[c] += bih[c]
        gh[c] += bhh[c]
    h = [0.0] * hidden
    for i in range(hidden):
        r = _sigmoid(gi[i] + gh[i])
        z = _sigmoid(gi[i + hidden] + gh[i + hidden])
        n = _tanh(gi[i + 2 * hidden] + r * gh[i + 2 * hidden])
        h[i] = (1.0 - z) * n + z * h_prev[i]
    return h


@dataclass
class GruWeights:
    """解析好的 GRU + head 权重。"""

    hidden: int
    layers: int
    input_size: int
    n_static: int
    wih: List[Tuple[List[List[float]], List[float]]]  # (Wih, bih) per layer
    whh: List[Tuple[List[List[float]], List[float]]]  # (Whh, bhh) per layer
    head0_w: List[List[float]]
    head0_b: List[float]
    head3_w: List[List[float]]
    head3_b: List[float]


def parse_weights(weights: Dict[str, object], arch: Optional[dict] = None) -> GruWeights:
    """从 JSON 权重 dict 解析出 GruWeights。"""
    layers = weights.get("gru.num_layers")
    hidden = None
    # 由 weight_hh 形状推断层数/隐藏维（权重按行 r/z/n 展开）
    layer_keys = sorted(
        (k for k in weights if k.startswith("gru.weight_hh_l")),
        key=lambda k: int(k.split("l")[-1]),
    )
    if not layer_keys:
        raise ModelLoadError("权重缺少 GRU 层")
    layers = max(int(k.split("l")[-1]) for k in layer_keys) + 1
    first_hh = weights[layer_keys[0]]
    rows = len(first_hh)
    hidden = rows // 3

    # input_size 从 weight_ih_l0 的列数（每行长度）
    wih0 = weights.get("gru.weight_ih_l0")
    input_size = len(wih0[0]) if wih0 else 2 + 3 + 2  # fallback

    wih: list = []
    whh: list = []
    for l in range(layers):
        Wih = weights[f"gru.weight_ih_l{l}"]
        bih = weights.get(f"gru.bias_ih_l{l}", [0.0] * (3 * hidden))
        Whh = weights[f"gru.weight_hh_l{l}"]
        bhh = weights.get(f"gru.bias_hh_l{l}", [0.0] * (3 * hidden))
        wih.append((Wih, bih))
        whh.append((Whh, bhh))

    n_static = None
    head0_w = weights.get("head.0.weight")
    if head0_w and head0_w:
        n_static = len(head0_w[0]) - hidden  # in_features - hidden
    n_static = n_static if n_static is not None else 7

    return GruWeights(
        hidden=hidden,
        layers=layers,
        input_size=input_size,
        n_static=n_static,
        wih=wih,
        whh=whh,
        head0_w=weights.get("head.0.weight", []),
        head0_b=weights.get("head.0.bias", [0.0] * hidden),
        head3_w=weights.get("head.3.weight", [[]]),
        head3_b=weights.get("head.3.bias", [0.0]),
    )


def predict_log_ratio(gw: GruWeights, seq: List[List[float]]) -> float:
    """对单样本序列 [T, input_size] 前向，返回 log(final/ref)。"""
    T = len(seq)
    x = seq  # x[t] = [T, input_size]
    # 逐层逐时间步（层间串联）
    layer_input = x
    for l in range(gw.layers):
        Wih, bih = gw.wih[l]
        Whh, bhh = gw.whh[l]
        h = [0.0] * gw.hidden  # h_0 = 0
        out = []
        for t in range(T):
            h = _gru_layer(layer_input[t], h, Wih, bih, Whh, bhh, gw.hidden)
            out.append(h)
        layer_input = out  # 下一层输入 = 本层各时间步隐状态
    h_last = layer_input[-1]  # [hidden]

    # head: concat(last_hidden, static)；static 在 sequence 的每个时间步第 3 位起
    static = seq[-1][3:]  # 与训练端一致（static 在各时间步相同，取任一）
    z = list(h_last) + list(static)
    a = _matmul_vec(gw.head0_w, z)
    for i in range(len(a)):
        a[i] += gw.head0_b[i]
        a[i] = a[i] if a[i] > 0 else 0.0  # ReLU
    out = _matmul_vec(gw.head3_w, a)
    out = out[0] + gw.head3_b[0] if gw.head3_w else 0.0
    return float(out)


def decode_final(log_ratio: float, score_ref: float) -> float:
    return score_ref * math.exp(log_ratio)


# --------------------------------------------------------------------------- 高层接口


@dataclass
class ForecastPoint:
    score: int
    ts: int


class GruPredictor:
    """模型包裹的轻量 CPU 预测器。"""

    def __init__(self, weights_json: str, calib_json: Optional[str] = None):
        self.weights = load_weights(weights_json)
        self.gw = parse_weights(self.weights)
        self.n_grid = 48
        self.calib = load_calib(calib_json or os.path.join(os.path.dirname(weights_json), "calib.json"))
        self.region_order = list(REGION_ORDER)
        self.type_order = list(TYPE_ORDER)

    def predict_final(
        self,
        timeline: Sequence[Tuple[float, float]],
        start_ts: float,
        end_ts: float,
        region: str,
        event_type: str,
        rank: float,
        progress_ceil: Optional[float] = None,
    ) -> Optional[Tuple[float, float]]:
        """返回 (final_score, score_ref)。特征不足返回 None。

        progress_ceil：预测时刻进度上界，缺省用最新观测进度。
        """
        built = _build_inputs(
            timeline, start_ts, end_ts, region, event_type, rank,
            self.n_grid, self.region_order, self.type_order, progress_ceil,
        )
        if built is None:
            return None
        curve, mask, static, ref = built
        if ref is None or ref <= 0:
            return None
        seq = _make_sequence(curve, mask, static, self.n_grid)
        lr = predict_log_ratio(self.gw, seq)
        final = decode_final(lr, ref)
        return float(final), float(ref)

    def predict_future_rankings(
        self,
        timeline: Sequence[Tuple[float, float]],
        start_ts: float,
        end_ts: float,
        region: str,
        event_type: str,
        rank: float,
        sample_points: int = 80,
        progress_ceil: Optional[float] = None,
    ) -> Optional[Tuple[float, List[ForecastPoint]]]:
        """预测最终分，并生成至活动结束的连续曲线点。返回 (final, [ForecastPoint])。
        progress_ceil 同 predict_final。
        """
        pred = self.predict_final(
            timeline, start_ts, end_ts, region, event_type, rank, progress_ceil
        )
        if pred is None:
            return None
        final, _ref = pred
        return final, _make_future_curve(final, timeline, start_ts, end_ts, sample_points)


def _make_future_curve(
    final_score: float,
    points: Sequence[Tuple[float, float]],
    start_ts: float,
    end_ts: float,
    sample_points: int,
) -> List[ForecastPoint]:
    """用与局部预测一致的曲线形状外推到 end_ts（幂曲线 x^0.72）。"""
    points = sorted((float(t), float(s)) for t, s in points)
    latest_ts, latest_score = points[-1]
    duration = max(1.0, end_ts - start_ts)
    sample_points = max(12, min(240, int(sample_points or 80)))
    step = duration / (sample_points - 1)

    def curve(x: float) -> float:
        x = max(0.0, min(1.0, x))
        return x ** 0.72

    first_ts, first_score = points[0]
    out: List[ForecastPoint] = []
    for i in range(sample_points):
        ts = int(start_ts + step * i)
        if i == sample_points - 1:
            ts = int(end_ts)
        x = max(0.0, min(1.0, (ts - start_ts) / duration))
        if ts < first_ts:
            score = int(first_score * (ts - start_ts) / max(1, first_ts - start_ts))
        elif ts <= latest_ts:
            # 历史区间用真实插值
            left, right = points[0], points[-1]
            for j in range(len(points) - 1):
                if points[j][0] <= ts <= points[j + 1][0]:
                    left, right = points[j], points[j + 1]
                    break
            if right[0] == left[0]:
                score = right[1]
            else:
                ratio = (ts - left[0]) / (right[0] - left[0])
                score = int(left[1] + (right[1] - left[1]) * ratio)
        else:
            score = int(final_score * curve(x))
            score = max(int(latest_score), score)
        out.append(ForecastPoint(score=max(0, score), ts=ts))
    if out:
        out[-1] = ForecastPoint(score=int(final_score), ts=int(end_ts))
    return out
