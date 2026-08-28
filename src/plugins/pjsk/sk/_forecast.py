import asyncio
import json
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional

import httpx

from services.log import logger
from utils.http_utils import _get_shared_client

from .._config import SERVER_MAP
from .._paths import FORECAST_PATH
from .._utils import load_master_data
from ._forecast_config import FORECAST_SOURCES

FORECAST_DATA_DIR = str(FORECAST_PATH)


@dataclass
class ForecastRanking:
    """预测排名数据"""
    score: int
    ts: int


@dataclass
class RankForecastData:
    """单个排名的预测数据"""
    final_score: Optional[int] = None
    history_final_score: Optional[List[ForecastRanking]] = None
    future_rankings: Optional[List[ForecastRanking]] = None


@dataclass
class ForecastData:
    """预测数据容器"""
    source: str
    region: str
    event_id: int
    mtime: Optional[int] = None
    forecast_ts: Optional[int] = None
    rank_data: Dict[int, RankForecastData] = field(default_factory=dict)

    def get_save_path(self) -> str:
        """获取本地保存路径"""
        return f"{FORECAST_DATA_DIR}/{self.source}/{self.region}/forecast/{self.event_id}.json"

    def load_from_local(self, update_interval_minutes: Optional[int] = None) -> bool:
        """从本地加载预测数据"""
        path = self.get_save_path()
        if not os.path.exists(path):
            return False
        
        mtime = int(os.path.getmtime(path))
        if update_interval_minutes is not None and int(time.time()) - mtime > update_interval_minutes * 60:
            return False
        
        try:
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            self.mtime = mtime
            self.forecast_ts = data.get('forecast_ts')
            self.rank_data = {}
            
            for rank_str, rank_info in data.get('rank_data', {}).items():
                rank = int(rank_str)
                history = None
                if rank_info.get('history_final_score'):
                    history = [
                        ForecastRanking(score=int(h['score']), ts=int(h['ts']))
                        for h in rank_info['history_final_score']
                        if h.get('score') is not None and h.get('ts') is not None
                    ]

                future = None
                if rank_info.get('future_rankings'):
                    future = [
                        ForecastRanking(score=int(h['score']), ts=int(h['ts']))
                        for h in rank_info['future_rankings']
                        if h.get('score') is not None and h.get('ts') is not None
                    ]
                
                self.rank_data[rank] = RankForecastData(
                    final_score=rank_info.get('final_score'),
                    history_final_score=history,
                    future_rankings=future
                )
            return True
        except Exception as e:
            logger.warning(f"加载预测数据 {path} 失败: {e}")
            return False

    def save_to_local(self):
        """保存预测数据到本地，并累计每次预测的历史点用于曲线绘制"""
        path = self.get_save_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)

        old_history: Dict[int, List[ForecastRanking]] = {}
        old_rank_data: Dict[int, dict] = {}
        if os.path.exists(path):
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    old_data = json.load(f)
                old_forecast_ts = old_data.get('forecast_ts')
                for rank_str, rank_info in old_data.get('rank_data', {}).items():
                    rank = int(rank_str)
                    old_rank_data[rank] = rank_info
                    history = []
                    for h in rank_info.get('history_final_score') or []:
                        if h.get('score') is not None and h.get('ts') is not None:
                            history.append(ForecastRanking(score=int(h['score']), ts=int(h['ts'])))
                    # 兼容旧缓存：旧文件没有 history_final_score 时，把旧 final_score 补成历史预测点。
                    if not history and rank_info.get('final_score') is not None:
                        ts = int(old_forecast_ts or os.path.getmtime(path))
                        history.append(ForecastRanking(score=int(rank_info['final_score']), ts=ts))
                    if history:
                        old_history[rank] = history
            except Exception as e:
                logger.debug(f"读取旧预测缓存 {path} 失败，将重新保存: {e}")
        
        data = {
            'source': self.source,
            'region': self.region,
            'event_id': self.event_id,
            'forecast_ts': self.forecast_ts,
            'rank_data': {},
        }
        
        all_ranks = sorted(set(old_rank_data.keys()) | set(self.rank_data.keys()))
        for rank in all_ranks:
            rank_info = self.rank_data.get(rank)
            old_info = old_rank_data.get(rank, {})
            final_score = rank_info.final_score if rank_info else old_info.get('final_score')

            history_map = {h.ts: h for h in old_history.get(rank, [])}
            if rank_info and rank_info.history_final_score:
                for h in rank_info.history_final_score:
                    history_map[int(h.ts)] = ForecastRanking(score=int(h.score), ts=int(h.ts))
            if final_score is not None:
                ts = int(self.forecast_ts or time.time())
                history_map[ts] = ForecastRanking(score=int(final_score), ts=ts)

            history = [
                {'score': h.score, 'ts': h.ts}
                for h in sorted(history_map.values(), key=lambda item: item.ts)
            ] or None

            future_rankings = None
            if rank_info and rank_info.future_rankings:
                future_rankings = [
                    {'score': int(h.score), 'ts': int(h.ts)}
                    for h in sorted(rank_info.future_rankings, key=lambda item: item.ts)
                ]
            elif old_info.get('future_rankings'):
                future_rankings = old_info.get('future_rankings')
            
            data['rank_data'][str(rank)] = {
                'final_score': final_score,
                'history_final_score': history,
                'future_rankings': future_rankings,
            }
        
        try:
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            self.mtime = int(os.path.getmtime(path))
            logger.info(f"已保存 {self.source} {self.region}_{self.event_id} 预测数据")
        except Exception as e:
            logger.warning(f"保存预测数据 {path} 失败: {e}")


class GetForecastException(Exception):
    """预测获取异常"""
    pass


def _region_to_pjsk_type(region: str) -> int:
    return next((k for k, v in SERVER_MAP.items() if v == region), 0)


_WL_EVENT_ID_FACTOR = 1000


def _get_event_time_range(region: str, event_id: int) -> tuple[int, int]:
    pjsk_type = _region_to_pjsk_type(region)
    # WL 单榜 encoded id：chapter_no * 1000 + base_event_id
    if event_id >= _WL_EVENT_ID_FACTOR:
        base_id = event_id % _WL_EVENT_ID_FACTOR
        chapter_no = event_id // _WL_EVENT_ID_FACTOR
        try:
            chapters = load_master_data('worldBlooms.json', pjsk_type)
        except Exception:
            chapters = []
        for ch in chapters:
            if (
                isinstance(ch, dict)
                and int(ch.get('eventId', -1)) == base_id
                and int(ch.get('chapterNo', -1)) == chapter_no
            ):
                start_ts = int(ch['chapterStartAt'] / 1000)
                end_ts = int(ch['aggregateAt'] / 1000)
                if end_ts > start_ts:
                    return start_ts, end_ts
        raise GetForecastException(f"未找到 WL 章节时间信息 (event={base_id} chapter={chapter_no})")
    events = load_master_data('events.json', pjsk_type)
    for event in events:
        if isinstance(event, dict) and int(event.get('id', -1)) == int(event_id):
            start_ts = int(event['startAt'] / 1000)
            end_ts = int(event['aggregateAt'] / 1000)
            if end_ts > start_ts:
                return start_ts, end_ts
    raise GetForecastException("未找到活动时间信息")


def _calc_speed(points: List[tuple[int, int]], since_ts: int) -> Optional[float]:
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
    # 低次幂让早期预测变化更明显，活动后期自然趋于平稳。
    return x ** 0.72


def _make_local_future_rankings(
    points: List[tuple[int, int]],
    start_ts: int,
    end_ts: int,
    sample_points: int,
) -> tuple[int, List[ForecastRanking]]:
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
    future: List[ForecastRanking] = []
    first_known_ts, first_known_score = points[0]
    for i in range(sample_points):
        ts = int(start_ts + step * i)
        if i == sample_points - 1:
            ts = end_ts
        x = min(1.0, max(0.0, (ts - start_ts) / duration))
        if ts < first_known_ts:
            score = int(first_known_score * (ts - start_ts) / max(1, first_known_ts - start_ts))
        elif ts <= latest_ts:
            # 用真实历史插值补齐第 0 天到当前的预测轨迹，保留早期波动观感。
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
            # 不允许未来段低于当前分数。
            score = max(latest_score, score)
        future.append(ForecastRanking(score=max(0, score), ts=ts))

    if future:
        future[-1] = ForecastRanking(score=final_score, ts=end_ts)
    return final_score, future


async def get_local_forecast_data(region: str, event_id: int) -> Optional[ForecastData]:
    """基于本地 SQLite 榜线历史生成连续预测曲线。"""
    cfg = FORECAST_SOURCES.get('local')
    if not cfg or not cfg.get('enabled') or region not in cfg.get('regions', []):
        return None

    start_ts, end_ts = _get_event_time_range(region, event_id)
    now_ts = int(time.time())
    if now_ts - start_ts < float(cfg.get('start_after_hours', 1)) * 3600:
        raise GetForecastException("活动开始时间过短，本地预测暂不稳定")
    if end_ts - now_ts < float(cfg.get('end_before_hours', 0.25)) * 3600:
        raise GetForecastException("活动即将结束，停止本地预测")

    from .._sk_sql import query_ranking

    data = ForecastData(source='local', region=region, event_id=event_id, forecast_ts=now_ts)
    for rank in cfg.get('ranks', []):
        rows = await query_ranking(region, event_id, rank=int(rank), order_by='ts ASC')
        points = [(int(row.time.timestamp()), int(row.score)) for row in rows if row.score is not None]
        # 去掉同一时间戳重复点，保留最后一次记录。
        point_map = {ts: score for ts, score in points if start_ts <= ts <= end_ts}
        points = sorted(point_map.items())
        if len(points) < 2:
            continue
        final_score, future = _make_local_future_rankings(
            points=points,
            start_ts=start_ts,
            end_ts=end_ts,
            sample_points=cfg.get('sample_points', 80),
        )
        data.rank_data[int(rank)] = RankForecastData(
            final_score=final_score,
            future_rankings=future,
        )

    if not data.rank_data:
        raise GetForecastException("本地历史榜线不足，无法生成预测")
    return data


async def get_33kit_forecast_data(region: str, event_id: int) -> Optional[ForecastData]:
    """从 3-3.dev 获取预测数据"""
    if region != 'jp':
        return None
    
    cfg = FORECAST_SOURCES.get('33kit')
    if not cfg or not cfg.get('enabled'):
        return None
    
    data = ForecastData(
        source='33kit',
        region=region,
        event_id=event_id,
    )
    
    try:
        client = await _get_shared_client()
        resp = await client.get(cfg['url'], timeout=10)
        resp.raise_for_status()
        predict_data = resp.json()
        
        if predict_data.get("status") != "success":
            raise GetForecastException("API返回失败状态")
        
        if predict_data.get("event", {}).get("id") != event_id:
            raise GetForecastException("预测数据不是当前活动")
        
        data.forecast_ts = int(predict_data["data"]["ts"] / 1000)
        
        for rank_str, score in predict_data["data"].items():
            if rank_str != 'ts':
                try:
                    rank = int(rank_str)
                    data.rank_data[rank] = RankForecastData(final_score=score)
                except (ValueError, TypeError):
                    pass
        
        return data
    except Exception as e:
        logger.warning(f"获取 33kit {region} 预测失败: {e}")
        return None


async def get_moe_forecast_data(region: str, event_id: int) -> Optional[ForecastData]:
    """从 Moesekai 获取预测数据"""
    cfg = FORECAST_SOURCES.get('moe')
    if not cfg or not cfg.get('enabled') or region not in cfg.get('regions', []):
        return None
    
    data = ForecastData(
        source='moe',
        region=region,
        event_id=event_id,
    )
    
    try:
        client = await _get_shared_client()
        # 检查活动是否存在
        events_resp = await client.get(cfg['events_url'].format(region=region), timeout=10)
        events_resp.raise_for_status()
        events_data = events_resp.json()
            
        if not any(int(e.get('event_id')) == event_id for e in events_data):
            raise GetForecastException("活动不在预测列表中")
            
        # 获取预测数据
        resp = await client.get(cfg['latest_url'].format(region=region, event_id=event_id), timeout=10)
        resp.raise_for_status()
        pred_data = resp.json()
        
        if int(pred_data.get('event_id')) != event_id:
            raise GetForecastException("预测数据不匹配")
        
        updated_at = datetime.fromisoformat(pred_data['updated_at'].replace('Z', '+00:00'))
        data.forecast_ts = int(updated_at.timestamp())
        
        for item in pred_data.get('items', []):
            rank = int(item['rank'])
            if rank not in cfg.get('ranks', []):
                continue
            
            # 活动进行中用 prediction，活动结束后用最终 score
            prediction = item.get('prediction')
            if prediction is None:
                prediction = item.get('score')
            if prediction is not None:
                data.rank_data[rank] = RankForecastData(final_score=int(prediction))
        
        if not data.rank_data:
            raise GetForecastException("未获取到预测数据")
        
        return data
    except Exception as e:
        logger.warning(f"获取 moe {region} 预测失败: {e}")
        return None


async def get_sekarun_forecast_data(region: str, event_id: int) -> Optional[ForecastData]:
    """从 SekaRun 获取预测数据"""
    cfg = FORECAST_SOURCES.get('sekarun')
    if not cfg or not cfg.get('enabled') or region not in cfg.get('regions', []):
        return None
    
    data = ForecastData(
        source='sekarun',
        region=region,
        event_id=event_id,
    )
    
    try:
        url = cfg['url'].format(region=region + '/' if region != 'jp' else '')
        client = await _get_shared_client()
        resp = await client.get(url, timeout=10)
        resp.raise_for_status()
        text = resp.text
        
        # 解析 JavaScript 数据
        start = text.find("[[") + 2
        end = text.rfind("]]")
        if start < 2 or end < 0:
            raise GetForecastException("数据格式错误")
        
        cur = start
        while cur < end:
            stop = text.find("], [", cur)
            if stop == -1:
                stop = end
            
            row_text = text[cur:stop]
            if row_text.startswith(f'"{event_id}'):
                values = row_text.replace("[", "").replace("]", "").split(", ")
                values = [v.strip().strip("'\"") for v in values]
                
                if len(values) > 9:
                    row_type = values[1]
                    rank = int(values[5])
                    
                    if row_type == 'p' and rank in cfg.get('ranks', []):
                        ts = int(values[6])
                        predict_lower = float(values[8])
                        predict_upper = float(values[9])
                        predict = int((predict_lower + predict_upper) / 2)
                        
                        if not data.forecast_ts:
                            data.forecast_ts = ts
                        
                        if rank not in data.rank_data:
                            data.rank_data[rank] = RankForecastData(final_score=0)
                        
                        data.rank_data[rank].final_score = max(
                            data.rank_data[rank].final_score or 0,
                            predict
                        )
            
            cur = stop + 4
        
        if not data.forecast_ts:
            raise GetForecastException("未获取到预测数据")
        
        return data
    except Exception as e:
        logger.warning(f"获取 sekarun {region} 预测失败: {e}")
        return None


# 预测获取函数映射
FORECAST_GET_FUNCS = {
    'local': get_local_forecast_data,
    '33kit': get_33kit_forecast_data,
    'moe': get_moe_forecast_data,
    'sekarun': get_sekarun_forecast_data,
}

# 预测获取锁和错误时间记录
_forecast_locks: Dict[str, asyncio.Lock] = {}
_forecast_last_error_time: Dict[str, datetime] = {}


async def get_forecast_data(region: str, event_id: int) -> List[ForecastData]:
    """获取指定活动的所有预测数据"""
    logger.info(f"[预测] 开始获取 {region} 服务器活动 {event_id} 的预测数据")
    results = []
    
    for source, func in FORECAST_GET_FUNCS.items():
        cfg = FORECAST_SOURCES.get(source)
        if not cfg or not cfg.get('enabled') or region not in cfg.get('regions', []):
            logger.debug(f"[预测] 跳过 {source} (不支持 {region} 或已禁用)")
            continue
        
        logger.info(f"[预测] 尝试获取 {source} {region} 预测数据...")
        
        # 初始化锁
        lock_key = f"{source}_{region}"
        if lock_key not in _forecast_locks:
            _forecast_locks[lock_key] = asyncio.Lock()
        
        async with _forecast_locks[lock_key]:
            try:
                # 尝试从本地加载
                data = ForecastData(source=source, region=region, event_id=event_id)
                if data.load_from_local(cfg.get('update_interval_minutes')):
                    logger.info(f"[预测] 从本地缓存加载 {source} {region} 预测数据")
                    results.append(data)
                    continue
                
                # 检查错误重试时间
                last_error = _forecast_last_error_time.get(lock_key, datetime.min)
                if datetime.now() - last_error < timedelta(minutes=cfg.get('error_retry_minutes', 10)):
                    logger.debug(f"[预测] {source} {region} 在错误重试冷却期内")
                    if data.load_from_local():
                        results.append(data)
                    continue
                
                # 从数据源获取
                logger.info(f"[预测] 从网络获取 {source} {region} 预测数据...")
                data = await func(region, event_id)
                if data:
                    logger.info(f"[预测] 成功获取 {source} {region} 预测数据，包含 {len(data.rank_data)} 个排名")
                    data.save_to_local()
                    results.append(data)
                else:
                    logger.warning(f"[预测] {source} {region} 返回空数据")
                    _forecast_last_error_time[lock_key] = datetime.now()
            
            except Exception as e:
                logger.error(f"[预测] 获取 {source} {region} 预测异常: {e}", exc_info=True)
                _forecast_last_error_time[lock_key] = datetime.now()
                
                # 尝试使用本地缓存
                data = ForecastData(source=source, region=region, event_id=event_id)
                if data.load_from_local():
                    logger.info(f"[预测] 使用本地缓存作为备份: {source} {region}")
                    results.append(data)
    
    logger.info(f"[预测] 完成获取，共获得 {len(results)} 个预测源的数据")
    return results


async def get_forecast_data_cached(region: str, event_id: int) -> List[ForecastData]:
    """获取预测数据（优先使用缓存，用于用户查询）
    
    与 get_forecast_data 的区别：
    - 优先使用本地缓存，即使过期也使用（只要文件存在）
    - 不会主动从网络获取，避免用户查询时等待过久
    - 适合用户查询场景，定时任务会负责更新缓存
    """
    logger.info(f"[预测缓存] 开始获取 {region} 服务器活动 {event_id} 的预测数据（优先缓存）")
    results = []
    
    for source in FORECAST_GET_FUNCS.keys():
        cfg = FORECAST_SOURCES.get(source)
        if not cfg or not cfg.get('enabled') or region not in cfg.get('regions', []):
            continue
        
        try:
            # 尝试从本地加载（不检查过期时间）
            data = ForecastData(source=source, region=region, event_id=event_id)
            if data.load_from_local(update_interval_minutes=None):  # None 表示不检查过期
                logger.info(f"[预测缓存] 从本地缓存加载 {source} {region} 预测数据")
                results.append(data)
            elif source == 'local':
                # local 不依赖外部网络；用户查询时缓存缺失也可以即时生成，避免必须等定时任务。
                try:
                    generated = await get_local_forecast_data(region, event_id)
                    if generated:
                        generated.save_to_local()
                        results.append(generated)
                        logger.info(f"[预测缓存] 即时生成 local {region} 预测数据")
                except Exception as gen_e:
                    logger.debug(f"[预测缓存] 即时生成 local {region} 预测失败: {gen_e}")
            else:
                logger.debug(f"[预测缓存] {source} {region} 没有本地缓存")
        except Exception as e:
            logger.warning(f"[预测缓存] 加载 {source} {region} 缓存失败: {e}")
    
    logger.info(f"[预测缓存] 完成获取，共获得 {len(results)} 个预测源的数据")
    return results

