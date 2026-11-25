from __future__ import annotations

from typing import List, Optional, Tuple


def _calc_speed(points: List[Tuple[int, int]], since_ts: int) -> Optional[float]:
    recent = [(ts, score) for ts, score in points if ts >= since_ts]
    if len(recent) < 2:
        return None
    first_ts, first_score = recent[0]
    last_ts, last_score = recent[-1]
    if last_ts <= first_ts:
        return None
    return max(0.0, (last_score - first_score) / (last_ts - first_ts))


def _progress_curve(x: float) -> float:
    x = min(1.0, max(0.0, x))
    return x ** 0.72


def predict_future_rankings(
    points: List[Tuple[int, int]],
    start_ts: int,
    end_ts: int,
    sample_points: int = 80,
) -> Tuple[int, List[Tuple[int, int]]]:
    points = sorted(points)
    latest_ts, latest_score = points[-1]
    duration = max(1, end_ts - start_ts)
    elapsed = max(1, latest_ts - start_ts)
    remain = max(0, end_ts - latest_ts)
    progress = min(0.995, max(0.001, elapsed / duration))

    avg_speed = max(0.0, latest_score / elapsed)
    speed_1h = _calc_speed(points, latest_ts - 3600)
    speed_3h = _calc_speed(points, latest_ts - 10800)
    speed_all = _calc_speed(points, start_ts)

    early_weight = max(0.0, 1.0 - progress)
    speed_now = (
        (speed_1h if speed_1h is not None else avg_speed) * (0.45 + 0.25 * early_weight)
        + (speed_3h if speed_3h is not None else avg_speed) * 0.25
        + (speed_all if speed_all is not None else avg_speed) * 0.15
        + avg_speed * (0.15 - 0.05 * early_weight)
    )
    speed_now = max(avg_speed * 0.35, min(speed_now, avg_speed * (2.8 if progress < 0.25 else 1.9)))

    curve_now = _progress_curve(progress)
    curve_final_by_progress = latest_score / max(curve_now, 0.001)
    curve_final_by_speed = latest_score + speed_now * remain
    blend_progress = min(0.85, max(0.25, progress))
    final_score = int(curve_final_by_progress * (1 - blend_progress) + curve_final_by_speed * blend_progress)
    final_score = max(final_score, latest_score)

    sample_points = max(12, min(240, int(sample_points or 80)))
    step = duration / (sample_points - 1)
    future: List[Tuple[int, int]] = []
    first_known_ts, first_known_score = points[0]
    for i in range(sample_points):
        ts = int(start_ts + step * i)
        if i == sample_points - 1:
            ts = end_ts
        x = min(1.0, max(0.0, (ts - start_ts) / duration))
        if ts < first_known_ts:
            score = int(first_known_score * (ts - start_ts) / max(1, first_known_ts - start_ts))
        elif ts <= latest_ts:
            left = points[0]
            right = points[-1]
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
            score = int(final_score * _progress_curve(x))
            score = max(latest_score, score)
        future.append((ts, max(0, score)))

    if future:
        future[-1] = (end_ts, final_score)
    return final_score, future
