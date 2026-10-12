"""新曲/虚拟Live 定时推送与图片绘制。"""
import json
from datetime import datetime, timedelta
from typing import Any, Dict, List

from nonebot import get_bot

from services import logger
from services.go_ownership import go_owns
from services.pjsk_draw import render
from utils.message_builder import image, at, custom_forward_msg
from utils.utils import scheduler

from .._config import SERVER_MAP
from .._paths import DATABASE_PATH
from .._utils import async_load_master_data
from ._sub_sql import (
    KIND_MUSIC, KIND_VLIVE, get_group_subs, get_user_subs,
    get_notify_sent_ids, mark_notify_sent_batch,
)

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


VLIVE_FORWARD_NODE_LIMIT = 50  # 工程分片保护，不代表平台统一上限


def _vlive_delivery_id(server: str, reminder: str, live_id: int, group_id: str) -> str:
    return f'vlive/{server}/{reminder}/{live_id}/{group_id}'


async def _push_vlive_batch(server, pjsk_type, reminder, members, groups, notified, sent):
    """原始成员确定首项和策略，只有投递内容按逐群状态过滤。"""
    bot = get_bot()
    name = SERVER_NAME_CN.get(server, server)
    label = '开始' if reminder == 'start' else '末场'
    title = f'虚拟Live{label}提醒（{name}）'
    pictures = {}

    async def picture(items):
        key = tuple(v['id'] for v in items)
        if key not in pictures:
            pic = await render('vlive_cards', {
                'title': title, 'vlives': items,
                'footer': 'KNDBOT · 虚拟Live通知', 'pjsk_type': pjsk_type,
            })
            pictures[key] = image(pic)
        return pictures[key]

    for sub in groups:
        gid = sub.group_id

        def delivery(v):
            return _vlive_delivery_id(server, reminder, v['id'], gid)

        def pending(v):
            return v['id'] not in notified and delivery(v) not in sent

        async def mark(items):
            ids = [delivery(v) for v in items]
            await mark_notify_sent_batch(KIND_VLIVE, server, ids)
            sent.update(ids)

        async def ordinary(items):
            msg = await picture(items)
            for user in await get_user_subs(KIND_VLIVE, server, gid):
                msg = msg + at(int(user.qq_id))
            await bot.send_group_msg(group_id=int(gid), message=msg)
            await mark(items)

        if len(members) <= 3:
            items = [v for v in members if pending(v)]
            if items:
                try:
                    await ordinary(items)
                except Exception as e:
                    logger.warning(f'[pjsk订阅] 推送 vlive/{server}/{reminder} 到群 {gid} 失败: {e}')
            continue

        first = members[0]
        if pending(first):
            try:
                await ordinary([first])
            except Exception as e:
                logger.warning(f'[pjsk订阅] 推送 vlive/{server}/{reminder} 首项到群 {gid} 失败: {e}')
        rest = [v for v in members[1:] if pending(v)]
        for offset in range(0, len(rest), VLIVE_FORWARD_NODE_LIMIT):
            items = rest[offset:offset + VLIVE_FORWARD_NODE_LIMIT]
            try:
                nodes = custom_forward_msg(
                    [await picture([v]) for v in items], bot.self_id, name='KNDBOT · 虚拟Live通知',
                )
                await bot.send_group_forward_msg(group_id=int(gid), messages=nodes)
                await mark(items)
            except Exception as e:
                # 不伪装成普通多图；失败包保持未发送，下包仍独立投递。
                logger.warning(f'[pjsk订阅] 合并转发 vlive/{server}/{reminder} 到群 {gid} 失败: {e}')


async def _check_vlive(state: Dict[str, Any]) -> bool:
    updated = False
    now = datetime.now()
    for pjsk_type, server in SERVER_MAP.items():
        groups = await get_group_subs(KIND_VLIVE, server)
        if not groups:
            continue
        vlives = await get_recent_vlives(pjsk_type, within_days=30)
        sent = await get_notify_sent_ids(KIND_VLIVE, server)
        # 未知/重复 ID 不参与排序或转发；不要在分批前过滤 JSON/SQL 已发送成员。
        unique = {}
        for v in vlives:
            if isinstance(v.get('id'), int) and not isinstance(v['id'], bool) and v['id'] > 0:
                unique.setdefault(v['id'], v)
        for reminder, index, window in (
            ('start', 0, VLIVE_START_NOTIFY_BEFORE),
            ('end', 1, VLIVE_END_NOTIFY_BEFORE),
        ):
            history = state.setdefault(f'vlive_{reminder}', {}).setdefault(server, [])
            notified = set(history)
            batches = {}
            for v in sorted(unique.values(), key=lambda v: v['id']):
                first, last = _schedule_times(v)
                if reminder == 'end' and first == last:
                    continue
                notify_at = (first, last)[index]
                notify_dt = datetime.fromtimestamp(notify_at / 1000)
                if now < notify_dt and notify_dt - now <= window:
                    batches.setdefault(notify_at, []).append(v)
            for members in batches.values():
                if all(v['id'] in notified for v in members):
                    continue
                await _push_vlive_batch(server, pjsk_type, reminder, members, groups, notified, sent)
                # 只有该 Live 的所有订阅群成功才推进旧全局状态。
                for v in members:
                    if v['id'] not in notified and all(
                        _vlive_delivery_id(server, reminder, v['id'], sub.group_id) in sent
                        for sub in groups
                    ):
                        history.append(v['id'])
                        notified.add(v['id'])
                        updated = True
                if updated:
                    # 后续批次异常也不丢掉此前完成的 JSON；部分成功已独立 SQL commit。
                    _save_state(state)
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
