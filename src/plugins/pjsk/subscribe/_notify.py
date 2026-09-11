"""新曲/虚拟Live 定时推送与图片绘制。"""
import json
from datetime import datetime, timedelta
from typing import Any, Dict, List

from nonebot import get_bot

from services import logger
from services.go_ownership import go_owns
from services.pjsk_draw import render
from utils.message_builder import image
from utils.utils import scheduler

from .._config import SERVER_MAP
from .._paths import DATABASE_PATH
from .._utils import async_load_master_data
from ._sub_sql import KIND_MUSIC, KIND_VLIVE, get_group_subs, get_user_subs

STATE_FILE = DATABASE_PATH / 'notify_state.json'

# 提醒窗口：都以「场次开演时间」为准，而不是活动的 startAt/endAt
# （活动 startAt 往往比首场早一天以上，endAt 也比末场晚十几小时，
#  按活动时间提醒会明显偏离实际开演）
VLIVE_START_NOTIFY_BEFORE = timedelta(minutes=3)
VLIVE_END_NOTIFY_BEFORE = timedelta(minutes=3)
MUSIC_NOTIFY_LOOKBACK = timedelta(hours=6)
MUSIC_NOTIFY_AHEAD = timedelta(minutes=1)

SERVER_NAME_CN = {'jp': '日服', 'cn': '国服', 'tw': '台服'}


# ---------- 已通知状态 ----------

def _load_state() -> Dict[str, Any]:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding='utf-8'))
        except Exception as e:
            logger.warning(f'[pjsk订阅] 读取通知状态失败: {e}')
    return {}


def _save_state(state: Dict[str, Any]) -> None:
    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(json.dumps(state), encoding='utf-8')
    except Exception as e:
        logger.warning(f'[pjsk订阅] 保存通知状态失败: {e}')



def _fmt_ts(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000).strftime('%m-%d %H:%M')


def _schedule_times(v: dict) -> tuple:
    """返回 (首场开演时间, 末场开演时间)；没有排期时回退到活动时间。"""
    schedules = v.get('virtualLiveSchedules') or []
    starts = sorted(
        s.get('startAt', 0) for s in schedules
        if isinstance(s, dict) and s.get('startAt')
    )
    if not starts:
        return v.get('startAt', 0), v.get('startAt', 0)
    return starts[0], starts[-1]


def build_vlive_rows(vlives: List[dict]) -> List[List[str]]:
    now = datetime.now()
    rows = []
    for v in vlives:
        schedules = v.get('virtualLiveSchedules') or []
        rest = sum(1 for s in schedules if s.get('startAt', 0) / 1000 > now.timestamp())
        current = next(
            (s for s in schedules if s.get('endAt', 0) / 1000 > now.timestamp()),
            None,
        )
        if current and current.get('startAt', 0) / 1000 <= now.timestamp():
            state = '当前Live进行中!'
        elif current:
            state = f'下一场: {_fmt_ts(current["startAt"])}'
        else:
            state = '已无剩余场次'
        rows.append([
            f'【{v["id"]}】{v.get("name", "")}',
            f'开始于 {_fmt_ts(v.get("startAt", 0))}  结束于 {_fmt_ts(v.get("endAt", 0))}',
            f'{state} | 剩余场次: {rest}',
        ])
    return rows


def build_music_rows(musics: List[dict]) -> List[List[str]]:
    rows = []
    for m in musics:
        rows.append([
            f'【{m["id"]}】{m.get("title", "")}',
            f'作曲: {m.get("composer", "-")}  作词: {m.get("lyricist", "-")}',
            f'上线时间: {_fmt_ts(m.get("publishedAt", 0))}',
        ])
    return rows


# ---------- vlive 数据获取 ----------

def _is_notifiable_vlive(v: dict, now: datetime) -> bool:
    if v.get('virtualLiveType') == 'beginner':
        return False
    start = datetime.fromtimestamp(v.get('startAt', 0) / 1000)
    end = datetime.fromtimestamp(v.get('endAt', 0) / 1000)
    # 排除常驻 live（持续超过 30 天）
    return end > now and end - start < timedelta(days=30)


async def get_recent_vlives(pjsk_type: int, within_days: int = 7) -> List[dict]:
    now = datetime.now()
    try:
        vlives = await async_load_master_data('virtualLives.json', pjsk_type)
    except FileNotFoundError:
        return []
    result = []
    for v in vlives:
        if not isinstance(v, dict) or not _is_notifiable_vlive(v, now):
            continue
        start = datetime.fromtimestamp(v.get('startAt', 0) / 1000)
        if start - now < timedelta(days=within_days):
            result.append(v)
    return result


# ---------- 推送 ----------

async def _push_to_groups(kind: str, server: str, msg) -> None:
    try:
        bot = get_bot()
    except Exception:
        return
    for sub in await get_group_subs(kind, server):
        try:
            at_msg = msg
            user_subs = await get_user_subs(kind, server, sub.group_id)
            for u in user_subs:
                from utils.message_builder import at

                at_msg = at_msg + at(int(u.qq_id))
            await bot.send_group_msg(group_id=int(sub.group_id), message=at_msg)
        except Exception as e:
            logger.warning(f'[pjsk订阅] 推送 {kind}/{server} 到群 {sub.group_id} 失败: {e}')


async def _check_new_music(state: Dict[str, Any]) -> bool:
    updated = False
    now = datetime.now()
    for pjsk_type, server in SERVER_MAP.items():
        if not await get_group_subs(KIND_MUSIC, server):
            continue
        try:
            musics = await async_load_master_data('musics.json', pjsk_type)
        except FileNotFoundError:
            continue
        notified = set(state.setdefault('music', {}).setdefault(server, []))
        pending = []
        for m in musics:
            if not isinstance(m, dict) or m.get('id') in notified:
                continue
            publish = datetime.fromtimestamp(m.get('publishedAt', 0) / 1000)
            if now - publish > MUSIC_NOTIFY_LOOKBACK or publish - now > MUSIC_NOTIFY_AHEAD:
                continue
            pending.append(m)
        if not pending:
            continue
        name = SERVER_NAME_CN.get(server, server)
        logger.info(f'[pjsk订阅] {server} 新曲上线: {[m["id"] for m in pending]}')
        pic = await render('notify_rows', {
            'title': f'{name}新曲上线 - {len(pending)}首',
            'rows': build_music_rows(pending),
            'footer': 'KNDBOT · 新曲通知',
        })
        await _push_to_groups(KIND_MUSIC, server, image(pic))
        state['music'][server].extend(m['id'] for m in pending)
        updated = True
    return updated


async def _check_vlive(state: Dict[str, Any]) -> bool:
    updated = False
    now = datetime.now()
    for pjsk_type, server in SERVER_MAP.items():
        if not await get_group_subs(KIND_VLIVE, server):
            continue
        vlives = await get_recent_vlives(pjsk_type, within_days=30)
        name = SERVER_NAME_CN.get(server, server)

        start_state = set(state.setdefault('vlive_start', {}).setdefault(server, []))
        start_pending = []
        for v in vlives:
            if v['id'] in start_state:
                continue
            first_start = datetime.fromtimestamp(_schedule_times(v)[0] / 1000)
            if now < first_start and first_start - now <= VLIVE_START_NOTIFY_BEFORE:
                start_pending.append(v)
        if start_pending:
            logger.info(f'[pjsk订阅] {server} vlive开始提醒: {[v["id"] for v in start_pending]}')
            pic = await render('vlive_cards', {
                'title': f'虚拟Live开始提醒（{name}）',
                'vlives': start_pending,
                'footer': 'KNDBOT · 虚拟Live通知',
                'pjsk_type': pjsk_type,
            })
            await _push_to_groups(KIND_VLIVE, server, image(pic))
            state['vlive_start'][server].extend(v['id'] for v in start_pending)
            updated = True

        end_state = set(state.setdefault('vlive_end', {}).setdefault(server, []))
        end_pending = []
        for v in vlives:
            if v['id'] in end_state:
                continue
            first_start, last_start = _schedule_times(v)
            last_dt = datetime.fromtimestamp(last_start / 1000)
            # 只有一场时首场即末场，开始提醒已经覆盖，不再重复推末场
            if last_start == first_start:
                continue
            if now < last_dt and last_dt - now <= VLIVE_END_NOTIFY_BEFORE:
                end_pending.append(v)
        if end_pending:
            logger.info(f'[pjsk订阅] {server} vlive末场提醒: {[v["id"] for v in end_pending]}')
            pic = await render('vlive_cards', {
                'title': f'虚拟Live末场提醒（{name}）',
                'vlives': end_pending,
                'footer': 'KNDBOT · 虚拟Live通知',
                'pjsk_type': pjsk_type,
            })
            await _push_to_groups(KIND_VLIVE, server, image(pic))
            state['vlive_end'][server].extend(v['id'] for v in end_pending)
            updated = True
    return updated


@scheduler.scheduled_job('interval', seconds=60, max_instances=1, coalesce=True, misfire_grace_time=10)
async def _pjsk_notify_job():
    try:
        get_bot()
    except Exception:
        return
    state = _load_state()
    try:
        updated = False
        if not go_owns("pjsk开启新曲通知"):
            updated = await _check_new_music(state)
        if not go_owns("pjsk开启live通知"):
            updated = await _check_vlive(state) or updated
        if updated:
            # 只保留最近 500 条，避免状态文件无限增长
            for key in ('music', 'vlive_start', 'vlive_end'):
                for server, ids in (state.get(key) or {}).items():
                    if len(ids) > 500:
                        state[key][server] = ids[-500:]
            _save_state(state)
    except Exception as e:
        logger.error(f'[pjsk订阅] 定时检查失败: {e}')
