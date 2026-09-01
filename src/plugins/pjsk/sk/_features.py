"""榜线预测的统一定量/形态分离特征定义。

本模块被「训练端」(scripts/train_model.py，在高性能机跑) 与「推断端」
(src/plugins/pjsk/sk/_model.py，本机 bot) 共用，从而保证两端特征顺序和归一化完全一致。

设计原则（量级/形态分离 + 部分观测掩码）：
  直接回归原始分在跨活动量级差异大（同档位可差 10 倍）时训不动，因此一律在
  对数域做差、相对进度对齐：
    - 曲线特征 : y[p] = log(score[p]) - log(score_ref)，仅对已观测格点有效
    - 观测掩码 : m[p] ∈ {0,1}，1 表示该进度格点有真实观测
    - 预测目标 : z   = log(final / score_ref)
    - 供推断    : final = score_ref * exp(z)

  「部分进度预测」通过截断样本 + mask 表达：截断点之后 curve=0、mask=0，
  模型学会只在"已有观测"基础上外推，避免信息泄露。

本模块除 numpy 外零依赖（不 import torch、不 import bot 模块），保证两机可移植。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

import numpy as np

# 进度网格点数（时间轴对齐，与时长无关）
N_GRID = 48
# 预测目标网格上的参考点索引（取相对进度第 1 个有分时刻作为量级基准）
DEFAULT_REF_INDEX = 0

# 服务区 one-hot 顺序（同时写入 meta，推断端据此对齐）
REGION_ORDER = ["cn", "tw", "jp"]
# 活动类型 one-hot 顺序
TYPE_ORDER = ["marathon", "world_bloom"]

# 静态特征命名顺序（用于 meta 记录与对齐；model 会用动态 one-hot 长度）
BASE_STATIC = ["log_rank", "progress"]

# 允许的档位集合（默认展示档位）
RANK_LEVELS = [
    1, 2, 3, 4, 5, 10, 20, 30, 40, 50, 100, 200, 300, 400, 500,
    1000, 2000, 3000, 4000, 5000, 10000, 20000, 30000, 40000, 50000, 100000,
]


@dataclass
class FeatureConfig:
    """特征工程的超参（打包进 meta.json 供推断端复现）。"""

    n_grid: int = N_GRID
    ref_index: int = DEFAULT_REF_INDEX
    region_order: List[str] = field(default_factory=lambda: list(REGION_ORDER))
    type_order: List[str] = field(default_factory=lambda: list(TYPE_ORDER))
    wl_base_factor: int = 1000  # WL 章节编码系数（与 _forecast 一致）
    min_grid_points: int = 4  # 少于该点数的样本丢弃（曲线信息不足）

    @property
    def n_static(self) -> int:
        # base(log_rank, progress) + region one-hot + type one-hot
        return 2 + len(self.region_order) + len(self.type_order)

    @property
    def static_features(self) -> List[str]:
        return (
            list(BASE_STATIC)
            + [f"region_{r}" for r in self.region_order]
            + [f"type_{t}" for t in self.type_order]
        )


# 全局默认配置，两端共用同一份
DEFAULTS = FeatureConfig()


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


def progress_position(ts: float, start_ts: float, end_ts: float) -> float:
    """把绝对时间戳换算为相对进度 p∈[0,1]。"""
    duration = max(1.0, end_ts - start_ts)
    return _clamp01((ts - start_ts) / duration)


def is_wl_encoded(event_id: int, factor: int = DEFAULTS.wl_base_factor) -> bool:
    """按 _forecast 的约定判断是否为 WL 章节编码 ID (>=1000)。"""
    return event_id >= factor


def event_type_id(event_type: str, config: FeatureConfig = DEFAULTS) -> int:
    try:
        return config.type_order.index(event_type)
    except ValueError:
        return 0


def region_id(region: str, config: FeatureConfig = DEFAULTS) -> int:
    try:
        return config.region_order.index(region)
    except ValueError:
        return 0


def static_vector(
    region: str,
    event_type: str,
    rank: float,
    progress: float,
    config: FeatureConfig = DEFAULTS,
) -> np.ndarray:
    """构造静态特征向量（顺序 = static_features）。"""
    n_region = len(config.region_order)
    n_type = len(config.type_order)
    vec = np.zeros(2 + n_region + n_type, dtype=np.float64)
    vec[0] = np.log(max(1.0, float(rank)))
    vec[1] = _clamp01(progress)
    vec[2 + region_id(region, config)] = 1.0
    vec[2 + n_region + event_type_id(event_type, config)] = 1.0
    return vec


def timeline_to_grid(
    timeline: Sequence[Tuple[float, float]],
    start_ts: float,
    end_ts: float,
    progress_ceil: float,
    config: FeatureConfig = DEFAULTS,
) -> Tuple[np.ndarray, np.ndarray]:
    """把 (ts, score) 映射到全局进度网格。

    返回 (score_grid, mask_grid)，各条长度为 config.n_grid：
      - 对 p <= progress_ceil 的格点：由真实观测插值得到 score，mask=1
      - 对 p >  progress_ceil 的格点：score=nan（曲线特征后续置 0），mask=0

    当观测点不足 2 个或时间范围不合理时抛 ValueError。
    """
    n = config.n_grid
    timeline = sorted(timeline)
    if not timeline:
        raise ValueError("timeline 为空")

    ts_items, score_items = [], []
    # 去重（同 ts 保留最后一条）
    uniq: dict = {}
    for t, s in timeline:
        uniq[t] = s
    for t in sorted(uniq.keys()):
        ts_items.append(t)
        score_items.append(uniq[t])
    if len(ts_items) < 2:
        raise ValueError("时间点过少")

    # 全局进度格点
    p_grid = np.linspace(0.0, 1.0, n)
    # 观测点是否落在 ceil 内
    obs_p = [progress_position(t, start_ts, end_ts) for t in ts_items]
    val = obs_p[0]
    obs_only = [(pp, s) for pp, s in zip(obs_p, score_items) if pp <= progress_ceil + 1e-9]
    if len(obs_only) < 2:
        raise ValueError("progress_ceil 内观测点不足")

    # 先构造完整网格上的插值（仅用 ceil 内观测点）
    obs_p_arr = np.array([pp for pp, _ in obs_only])
    obs_s_arr = np.array([s for _, s in obs_only])
    score_grid = np.interp(
        p_grid,
        obs_p_arr,
        obs_s_arr,
        left=obs_s_arr[0],
        right=obs_s_arr[-1],
    )
    mask_grid = (p_grid <= progress_ceil + 1e-9).astype(np.float64)
    all_mask = (p_grid <= obs_p_arr[-1] + 1e-9) & (p_grid >= obs_p_arr[0] - 1e-9) & mask_grid.astype(bool)
    # 用严格 mask：仅在观测覆盖区间内视为已观测
    mask_grid = all_mask.astype(np.float64)
    return score_grid, mask_grid


def extract_features(
    timeline: Sequence[Tuple[float, float]],
    start_ts: float,
    end_ts: float,
    final_score: float,
    region: str,
    event_type: str,
    rank: float,
    progress_ceil: float = 1.0,
    config: FeatureConfig = DEFAULTS,
) -> Optional[Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]]:
    """从一个 (活动/章节, 档位) 的观测曲线提取训练样本。

    返回 (curve_feat, mask_feat, static_feat, target)：
      curve_feat : [n_grid] 对数差曲线；未观测格点为 0
      mask_feat  : [n_grid] 观测掩码 {0,1}
      static_feat: [n_static]
      target     : [1] log(final/score_ref)

    信息不足时返回 None。
    """
    final_score_f = float(final_score)
    if final_score_f <= 0:
        return None
    try:
        score_grid, mask_grid = timeline_to_grid(
            timeline, start_ts, end_ts, progress_ceil, config
        )
    except ValueError:
        return None

    obs = score_grid[mask_grid.astype(bool)]
    if len(obs) < config.min_grid_points:
        return None
    # 参考点 = 最后一个观测格点的分数（而非固定格 index）。
    # 由此 target = log(final / last_obs) 正是"末期剩余增幅"，模型需外推出
    # 未观测末段的必涨幅度（PJSK 末期冲榜），且推断解码 final=last_obs*exp(lr) 不改协议。
    seen = list(zip(score_grid, mask_grid.astype(bool)))
    last_obs = None
    for s, m in reversed(seen):
        if m and s > 0:
            last_obs = s
            break
    if last_obs is None or last_obs <= 0:
        return None
    ref = float(last_obs)

    # log 差曲线；未观测处置 0，掩码 0
    curve = np.zeros(config.n_grid, dtype=np.float64)
    valid = (score_grid > 0)
    mask_v = mask_grid.astype(bool) & valid
    curve[mask_v] = np.log(score_grid[mask_v]) - np.log(ref)
    # 补一个"参考点位置置 1"提示：参考点虽有观测但 log(ref/ref)=0，无妨

    target = np.log(final_score_f) - np.log(ref)
    static = static_vector(region, event_type, rank, progress_ceil, config)
    return curve, mask_grid.astype(np.float64), static, np.array([target], dtype=np.float64)


def decode_final(log_ratio: float, score_ref: float) -> float:
    """由预测的 log(final/ref) 反解真实最终分。"""
    return score_ref * float(np.exp(log_ratio))


def encode_final(final_score: float, score_ref: float) -> float:
    return float(np.log(final_score)) - float(np.log(score_ref))
