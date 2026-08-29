"""新曲/虚拟Live 定时推送与图片绘制。"""
import asyncio
import json
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from nonebot import get_bot
from PIL import Image, ImageDraw

from config.path_config import FONT_PATH
from services import logger
from utils.imageutils import pic2b64
from utils.message_builder import image
from utils.utils import scheduler

from .._config import SERVER_MAP, data_path
from .._paths import DATABASE_PATH
from .._utils import (
    async_load_master_data,
    get_pjsk_asset_cached,
    get_pjsk_font,
    run_pjsk_thread,
    vertical_gradient,
)
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


# ---------- 绘图（同步，调用方放线程池） ----------

def _panel_bg(width: int, height: int) -> Image.Image:
    top, bottom = (255, 235, 244), (232, 240, 255)
    img = vertical_gradient(width, height, top, bottom)
    return img


def _draw_rows_image(title: str, rows: List[List[str]], footer: str = '') -> Image.Image:
    f_title = get_pjsk_font('SourceHanSansCN-Bold.otf', 24)
    f_body = get_pjsk_font('SourceHanSansCN-Medium.otf', 18)
    f_small = get_pjsk_font('SourceHanSansCN-Medium.otf', 13)

    pad, row_h, title_h = 24, 30, 56
    _tmp = ImageDraw.Draw(Image.new('RGB', (1, 1)))
    width = max(
        [int(_tmp.textlength(title, font=f_title)) + pad * 2 + 20]
        + [int(max((_tmp.textlength(line, font=f_body) for line in row), default=0)) + pad * 2 + 24 for row in rows]
        + [560]
    )
    total_lines = sum(len(r) for r in rows)
    height = title_h + pad * 2 + total_lines * row_h + len(rows) * 16 + (26 if footer else 0)

    img = _panel_bg(width, height)
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((pad - 8, pad - 8, width - pad + 8, pad + title_h - 12), radius=14, fill=(255, 255, 255, 230))
    d.text((pad + 4, pad + 8), title, font=f_title, fill=(60, 34, 60))
    y = title_h + pad + 8
    for row in rows:
        block_h = len(row) * row_h + 8
        d.rounded_rectangle((pad - 6, y - 6, width - pad + 6, y + block_h - 4), radius=12, fill=(255, 255, 255))
        for line in row:
            d.text((pad + 8, y), line, font=f_body, fill=(50, 40, 56))
            y += row_h
        y += 16
    if footer:
        d.text((pad, height - 24), footer, font=f_small, fill=(130, 110, 130))
    return img


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


def _vlive_state_text(v: dict) -> tuple:
    """返回 (状态文本, 剩余场次)。"""
    now_ts = datetime.now().timestamp()
    schedules = v.get('virtualLiveSchedules') or []
    rest = sum(1 for s in schedules if s.get('startAt', 0) / 1000 > now_ts)
    current = next((s for s in schedules if s.get('endAt', 0) / 1000 > now_ts), None)
    if current and current.get('startAt', 0) / 1000 <= now_ts:
        return '当前Live进行中!', rest
    if current:
        return f'下一场: {_fmt_ts(current["startAt"])}', rest
    return '已无剩余场次', rest


def draw_vlive_cards(title: str, vlives: List[dict], banners: dict, footer: str = '') -> Image.Image:
    """带 banner 缩略图的虚拟Live卡片列表。

    banners: {vlive_id: PIL.Image | None}
    """
    f_title = get_pjsk_font('SourceHanSansCN-Bold.otf', 26)
    f_name = get_pjsk_font('SourceHanSansCN-Bold.otf', 20)
    f_body = get_pjsk_font('SourceHanSansCN-Medium.otf', 17)
    f_small = get_pjsk_font('SourceHanSansCN-Medium.otf', 13)

    pad = 24
    card_h = 132
    card_gap = 14
    title_h = 62
    banner_w, banner_h = 200, 108
    width = 760
    height = pad * 2 + title_h + len(vlives) * (card_h + card_gap) + (26 if footer else 0)

    img = _panel_bg(width, height)
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((pad - 8, pad - 6, width - pad + 8, pad + title_h - 16), radius=16, fill=(255, 255, 255, 235))
    d.rounded_rectangle((pad + 4, pad + 8, pad + 10, pad + 34), radius=3, fill=(255, 128, 178))
    d.text((pad + 22, pad + 8), title, font=f_title, fill=(60, 34, 60))

    y = pad + title_h
    for v in vlives:
        d.rounded_rectangle((pad - 6, y, width - pad + 6, y + card_h), radius=16, fill=(255, 255, 255))
        banner = banners.get(v.get('id'))
        text_x = pad + 8
        if banner is not None:
            bx, by = pad + 4, y + (card_h - banner_h) // 2
            mask = Image.new('L', (banner_w, banner_h), 0)
            ImageDraw.Draw(mask).rounded_rectangle((0, 0, banner_w - 1, banner_h - 1), radius=12, fill=255)
            img.paste(banner, (bx, by), mask)
            text_x = bx + banner_w + 16

        name = v.get('name', '')
        max_w = width - pad - text_x - 12
        while name and d.textlength(f'【{v["id"]}】{name}', font=f_name) > max_w:
            name = name[:-1]
        d.text((text_x, y + 16), f'【{v["id"]}】{name}', font=f_name, fill=(48, 32, 56))

        state, rest = _vlive_state_text(v)
        d.text((text_x, y + 48), f'开始 {_fmt_ts(v.get("startAt", 0))}  结束 {_fmt_ts(v.get("endAt", 0))}',
               font=f_body, fill=(96, 82, 104))
        d.text((text_x, y + 76), state, font=f_body, fill=(196, 72, 128))
        rest_text = f'剩余 {rest} 场'
        rw = int(d.textlength(rest_text, font=f_small)) + 22
        d.rounded_rectangle((width - pad - rw - 4, y + 74, width - pad - 4, y + 100), radius=13,
                            fill=(255, 246, 251), outline=(245, 218, 232))
        d.text((width - pad - rw // 2 - 4, y + 87), rest_text, font=f_small, fill=(150, 96, 126), anchor='mm')
        y += card_h + card_gap

    if footer:
        d.text((pad, height - 24), footer, font=f_small, fill=(130, 110, 130))
    return img


async def fetch_vlive_banners(vlives: List[dict], pjsk_type: int = 0) -> dict:
    """并发拉取各 vlive 的 banner 缩略图，失败的记为 None。"""
    async def _one(v: dict):
        asset = v.get('assetbundleName')
        if not asset:
            return v.get('id'), None
        try:
            img = await get_pjsk_asset_cached(
                f'virtual_live/select/banner/{asset}',
                f'{asset}.png',
                pjsk_type=pjsk_type,
                mode='RGBA',
                size=(200, 108),
            )
        except Exception as e:
            logger.debug(f'[pjsk订阅] 拉取 vlive banner 失败 {asset}: {e}')
            img = None
        return v.get('id'), img

    results = await asyncio.gather(*[_one(v) for v in vlives], return_exceptions=True)
    return {r[0]: r[1] for r in results if not isinstance(r, Exception)}


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
        img = await run_pjsk_thread(
            _draw_rows_image, f'{name}新曲上线 - {len(pending)}首', build_music_rows(pending), 'KNDBOT · 新曲通知'
        )
        await _push_to_groups(KIND_MUSIC, server, image(b64=await run_pjsk_thread(pic2b64, img)))
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
            banners = await fetch_vlive_banners(start_pending, pjsk_type)
            img = await run_pjsk_thread(
                draw_vlive_cards, f'虚拟Live开始提醒（{name}）', start_pending, banners, 'KNDBOT · 虚拟Live通知'
            )
            await _push_to_groups(KIND_VLIVE, server, image(b64=await run_pjsk_thread(pic2b64, img)))
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
            banners = await fetch_vlive_banners(end_pending, pjsk_type)
            img = await run_pjsk_thread(
                draw_vlive_cards, f'虚拟Live末场提醒（{name}）', end_pending, banners, 'KNDBOT · 虚拟Live通知'
            )
            await _push_to_groups(KIND_VLIVE, server, image(b64=await run_pjsk_thread(pic2b64, img)))
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
        updated = await _check_new_music(state)
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
