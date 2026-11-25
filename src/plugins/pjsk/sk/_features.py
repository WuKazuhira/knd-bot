# 训练端特征定义，顺序需与 _model.py 一致
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

import numpy as np

N_GRID = 48
DEFAULT_REF_INDEX = 0

REGION_ORDER = ["cn", "tw", "jp"]
TYPE_ORDER = ["marathon", "world_bloom"]

BASE_STATIC = ["log_rank", "progress"]

RANK_LEVELS = [
    1, 2, 3, 4, 5, 10, 20, 30, 40, 50, 100, 200, 300, 400, 500,
    1000, 2000, 3000, 4000, 5000, 10000, 20000, 30000, 40000, 50000, 100000,
]


@dataclass
class FeatureConfig:
    n_grid: int = N_GRID
    ref_index: int = DEFAULT_REF_INDEX
    region_order: List[str] = field(default_factory=lambda: list(REGION_ORDER))
    type_order: List[str] = field(default_factory=lambda: list(TYPE_ORDER))
    wl_base_factor: int = 1000
    min_grid_points: int = 4

    @property
    def n_static(self) -> int:
        return 2 + len(self.region_order) + len(self.type_order)

    @property
    def static_features(self) -> List[str]:
        return (
            list(BASE_STATIC)
            + [f"region_{r}" for r in self.region_order]
            + [f"type_{t}" for t in self.type_order]
        )


DEFAULTS = FeatureConfig()


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


def progress_position(ts: float, start_ts: float, end_ts: float) -> float:
    duration = max(1.0, end_ts - start_ts)
    return _clamp01((ts - start_ts) / duration)


def is_wl_encoded(event_id: int, factor: int = DEFAULTS.wl_base_factor) -> bool:
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
    n = config.n_grid
    timeline = sorted(timeline)
    if not timeline:
        raise ValueError("timeline 为空")

    ts_items, score_items = [], []
    uniq: dict = {}
    for t, s in timeline:
        uniq[t] = s
    for t in sorted(uniq.keys()):
        ts_items.append(t)
        score_items.append(uniq[t])
    if len(ts_items) < 2:
        raise ValueError("时间点过少")

    p_grid = np.linspace(0.0, 1.0, n)
    obs_p = [progress_position(t, start_ts, end_ts) for t in ts_items]
    val = obs_p[0]
    obs_only = [(pp, s) for pp, s in zip(obs_p, score_items) if pp <= progress_ceil + 1e-9]
    if len(obs_only) < 2:
        raise ValueError("progress_ceil 内观测点不足")

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
    # 参考点取最后一个观测格点，预测末期剩余增幅
    seen = list(zip(score_grid, mask_grid.astype(bool)))
    last_obs = None
    for s, m in reversed(seen):
        if m and s > 0:
            last_obs = s
            break
    if last_obs is None or last_obs <= 0:
        return None
    ref = float(last_obs)

    curve = np.zeros(config.n_grid, dtype=np.float64)
    valid = (score_grid > 0)
    mask_v = mask_grid.astype(bool) & valid
    curve[mask_v] = np.log(score_grid[mask_v]) - np.log(ref)

    target = np.log(final_score_f) - np.log(ref)
    static = static_vector(region, event_type, rank, progress_ceil, config)
    return curve, mask_grid.astype(np.float64), static, np.array([target], dtype=np.float64)


def decode_final(log_ratio: float, score_ref: float) -> float:
    return score_ref * float(np.exp(log_ratio))


def encode_final(final_score: float, score_ref: float) -> float:
    return float(np.log(final_score)) - float(np.log(score_ref))
