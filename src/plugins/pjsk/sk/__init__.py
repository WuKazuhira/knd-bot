import asyncio
import json
import random
import re
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple, Union

import yaml
from nonebot import get_driver, on_command, on_regex
from nonebot.adapters.onebot.v11 import ActionFailed, Message, MessageEvent
from nonebot.exception import FinishedException, PausedException, RejectedException, StopPropagation
from nonebot.internal.matcher import Matcher
from nonebot.params import Command, CommandArg
from nonebot.permission import SUPERUSER
from PIL import Image, ImageDraw, ImageFont

from config.path_config import FONT_PATH
from services import logger
from utils.imageutils import pic2b64, text2image
from utils.message_builder import image
from utils.utils import get_message_at, is_number, scheduler

from .._autoask import pjsk_update_manager
from .._config import *
from .._errors import apiCallError, maintenanceIn, userIdBan
from .._models import PjskBind
from .._sk_sql import Ranking
from .._utils import (
    callapi,
    currentevent,
    get_chara_alias_map,
    get_pjsk_font,
    get_pjsk_type,
    getEventId,
    getUserData,
    load_master_data,
    near_rank,
    run_pjsk_thread,
)
from ._api_state import load_api_mode, save_api_mode
from ._forecast import ForecastData, get_forecast_data, get_forecast_data_cached
from ._forecast_config import FORECAST_EXPIRE_HOURS, FORECAST_SOURCES, LIVE_RANKS
from ._ranking_api import (
    HarukiRankingSnapshot,
    fetch_haruki_ranking_snapshot,
    fetch_main_rankings,
    merge_rankings,
    rankings_from_items,
)


def _blocking_get_json(url, headers=None):
    from .._common_utils import _blocking_get_json as _impl
    return _impl(url, headers=headers, timeout=10)
driver = get_driver()


WL_EVENT_ID_FACTOR = 1000
_WL_CHAPTER_CACHE: Dict[Tuple[str, int], List[dict]] = {}


def _base_event_id(event_id: int) -> int:
    return event_id % WL_EVENT_ID_FACTOR if event_id >= WL_EVENT_ID_FACTOR else event_id


def _wl_encoded_event_id(base_event_id: int, chapter_no: int) -> int:
    return chapter_no * WL_EVENT_ID_FACTOR + base_event_id


def _get_wl_chapters(event_id: int, pjsk_type: int = 0) -> List[dict]:
    try:
        chapters = load_master_data('worldBlooms.json', pjsk_type)
    except Exception:
        return []
    base_id = _base_event_id(event_id)
    ret = [c for c in chapters if isinstance(c, dict) and c.get('eventId') == base_id]
    ret.sort(key=lambda x: x.get('chapterNo', 0))
    return ret


def _current_wl_chapter(chapters: List[dict]) -> Optional[dict]:
    if not chapters:
        return None
    now_ms = int(time.time() * 1000)
    started = [c for c in chapters if c.get('chapterStartAt', 0) <= now_ms]
    if started:
        started.sort(key=lambda x: x.get('chapterStartAt', 0), reverse=True)
        return started[0]
    return chapters[0]


def _format_wl_event_label(region: str, event_id: int, chapter: Optional[dict] = None) -> str:
    if chapter:
        return f"【{region.upper()}-{_base_event_id(event_id)}-第{chapter.get('chapterNo')}章单榜】"
    return f"【{region.upper()}】"


def _is_world_bloom_event(event_id: int, pjsk_type: int = 0) -> bool:
    try:
        events = load_master_data('events.json', pjsk_type)
    except Exception:
        return False
    base_id = _base_event_id(event_id)
    for event in events:
        if isinstance(event, dict) and event.get('id') == base_id:
            return event.get('eventType') == 'world_bloom'
    return False


def _resolve_wl_query_event_id_from_chapters(
    args: str,
    base_event_id: int,
    chapters: List[dict],
) -> Tuple[int, str, Optional[dict]]:
    """根据给定 WL 章节解析查询参数。"""
    if not chapters:
        return base_event_id, args.strip(), None

    raw_args = args.strip()
    tokens = raw_args.split()
    alias_map = get_chara_alias_map()

    def by_cid(cid: Optional[int]) -> Optional[dict]:
        if cid is None:
            return None
        return next((c for c in chapters if c.get('gameCharacterId') == cid), None)

    def finish(chapter: Optional[dict], new_tokens: List[str]) -> Tuple[int, str, Optional[dict]]:
        if not chapter:
            return base_event_id, raw_args, None
        return _wl_encoded_event_id(base_event_id, int(chapter.get('chapterNo', 0))), ' '.join(new_tokens).strip(), chapter

    # wl2 / wl第2章 / wl 2
    for i, token in enumerate(tokens):
        tl = token.lower()
        m = re.fullmatch(r'wl(?:第)?(\d+)(?:章)?', tl)
        if m:
            chapter = next((c for c in chapters if c.get('chapterNo') == int(m.group(1))), None)
            return finish(chapter, tokens[:i] + tokens[i + 1:])
        if tl == 'wl' and i + 1 < len(tokens) and re.fullmatch(r'(?:第)?\d+(?:章)?', tokens[i + 1]):
            seq = int(re.sub(r'\D', '', tokens[i + 1]))
            chapter = next((c for c in chapters if c.get('chapterNo') == seq), None)
            return finish(chapter, tokens[:i] + tokens[i + 2:])

    # -c mfy
    for i, token in enumerate(tokens[:-1]):
        if token.lower() in ('-c', 'c'):
            chapter = by_cid(alias_map.get(tokens[i + 1].lower()))
            if chapter:
                return finish(chapter, tokens[:i] + tokens[i + 2:])

    sorted_aliases = sorted(alias_map.keys(), key=len, reverse=True)
    for nick in sorted_aliases:
        chapter = by_cid(alias_map[nick])
        if not chapter:
            continue
        compact = f'wl{nick}'
        for i, token in enumerate(tokens):
            tl = token.lower()
            if tl == compact:
                return finish(chapter, tokens[:i] + tokens[i + 1:])
            if tl == 'wl' and i + 1 < len(tokens) and tokens[i + 1].lower() == nick:
                return finish(chapter, tokens[:i] + tokens[i + 2:])

    for i, token in enumerate(tokens):
        if token.lower() == 'wl':
            chapter = _current_wl_chapter(chapters)
            return finish(chapter, tokens[:i] + tokens[i + 1:])

    # sk 查询不把裸角色名视为 WL，避免误把玩家 ID/其它参数吃掉；请用 wlmfy 或 -c mfy。
    return base_event_id, raw_args, None


def _resolve_wl_query_event_id(args: str, base_event_id: int, pjsk_type: int = 0) -> Tuple[int, str, Optional[dict]]:
    """解析 sk 查询里的 WL 参数。

    默认返回总榜 event_id；带 wl/wl2/wl角色/-c 角色时返回 chapterNo*1000+eventId。
    """
    return _resolve_wl_query_event_id_from_chapters(args, base_event_id, _get_wl_chapters(base_event_id, pjsk_type))


def _wl_payload(data: Any) -> dict:
    """兼容 WL 接口直接返回数据或使用 data 包装的两种格式。"""
    if not isinstance(data, dict):
        return {}
    nested = data.get('data')
    if isinstance(nested, dict) and any(
        key in nested
        for key in (
            'groups',
            'userWorldBloomChapterRankings',
            'worldBloomChapterRankings',
            'userWorldBloomChapterRankingBorders',
            'worldBloomChapterRankingBorders',
        )
    ):
        return nested
    return data


def _wl_group_sources(data: Any) -> List[dict]:
    payload = _wl_payload(data)
    groups = []
    for key in (
        'groups',
        'userWorldBloomChapterRankings',
        'worldBloomChapterRankings',
        'userWorldBloomChapterRankingBorders',
        'worldBloomChapterRankingBorders',
    ):
        value = payload.get(key)
        if isinstance(value, list):
            groups.extend(group for group in value if isinstance(group, dict))
    return groups


def _wl_response_event_id(data: Any) -> Optional[int]:
    """提取接口活动号，兼容 event_id 与 eventId。字段缺失时由调用方按当前活动判断。"""
    payload = _wl_payload(data)
    value = payload.get('event_id') or payload.get('eventId')
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _cache_wl_chapters(region: str, event_id: int, chapters: List[dict]) -> None:
    if not chapters:
        return
    key = (region, _base_event_id(event_id))
    cached = _WL_CHAPTER_CACHE.get(key, [])
    # 当前章节接口可能逐章开放，只接受章节数不减少的新结果。
    if len(chapters) >= len(cached):
        _WL_CHAPTER_CACHE[key] = [dict(chapter) for chapter in chapters]


async def _refresh_wl_chapters(event_id: int, pjsk_type: int) -> List[dict]:
    """缺少 WL 配置时更新 MasterData；绝不从榜线接口或数据库猜章节号。"""
    base_id = _base_event_id(event_id)
    chapters = _get_wl_chapters(base_id, pjsk_type)
    if chapters:
        return chapters
    try:
        await pjsk_update_manager.update_server_game_data(
            'worldBlooms.json',
            pjsk_type,
            block=False,
        )
    except Exception as e:
        logger.warning(f"[WL] 更新 worldBlooms.json 失败: {e}")
    return _get_wl_chapters(base_id, pjsk_type)


async def _get_wl_chapters_for_query(region: str, event_id: int, pjsk_type: int = 0) -> List[dict]:
    """仅从权威 MasterData 或由其产生的进程缓存获取 WL 章节。"""
    base_id = _base_event_id(event_id)
    chapters = _get_wl_chapters(base_id, pjsk_type)
    if chapters:
        _cache_wl_chapters(region, base_id, chapters)
        return chapters

    cached = _WL_CHAPTER_CACHE.get((region, base_id))
    if cached:
        return [dict(chapter) for chapter in cached]

    event_data = currentevent(pjsk_type)
    if event_data.get('id') != base_id or not _is_world_bloom_event(base_id, pjsk_type):
        return []

    chapters = await _refresh_wl_chapters(base_id, pjsk_type)
    if not chapters:
        logger.warning(
            f"[WL] 缺少 {region}-{base_id} 的权威章节配置，跳过分榜查询"
        )
        return []
    _cache_wl_chapters(region, base_id, chapters)
    return chapters



def _sk_font(path_or_name, size: int):

    path_text = str(path_or_name)
    name = getattr(path_or_name, 'name', None) or path_text.split('/')[-1]
    return get_pjsk_font(str(name), size)


def _sk_gradient_bg(width: int, height: int) -> Image.Image:
    """SK 出图用柔和粉紫渐变背景。"""
    top = (255, 246, 250)
    bottom = (236, 244, 255)
    img = Image.new("RGB", (width, height), top)
    d = ImageDraw.Draw(img)
    for y in range(height):
        t = y / max(1, height - 1)
        color = tuple(int(top[i] * (1 - t) + bottom[i] * t) for i in range(3))
        d.line((0, y, width, y), fill=color)
    glow = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    gd.ellipse((-width // 5, -height // 4, width // 2, height // 3), fill=(255, 190, 220, 62))
    gd.ellipse((width // 2, height // 4, width + width // 4, height + height // 5), fill=(170, 210, 255, 54))
    img.paste(glow, (0, 0), glow.split()[3])
    return img


def _sk_panel(base: Image.Image, xy, radius: int = 18, fill=(255, 255, 255, 218), outline=(255, 255, 255, 220)):
    """在 RGB 画布上绘制半透明圆角面板。"""
    overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    od.rounded_rectangle(xy, radius=radius, fill=fill, outline=outline, width=1 if outline else 0)
    base.paste(overlay, (0, 0), overlay.split()[3])


def _sk_fit_text(draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> str:
    if draw.textlength(text, font=font) <= max_width:
        return text
    ellipsis = "…"
    while text and draw.textlength(text + ellipsis, font=font) > max_width:
        text = text[:-1]
    return text + ellipsis if text else ellipsis


def _sk_text_width(draw: ImageDraw.ImageDraw, text: str, font) -> int:
    return int(draw.textlength(str(text), font=font))


def _sk_chip(draw: ImageDraw.ImageDraw, xy, text: str, font, fill=(255, 255, 255), outline=(245, 218, 232), text_fill=(120, 80, 100), radius: int = 14, anchor: str = "mm"):
    x1, y1, x2, y2 = xy
    draw.rounded_rectangle(xy, radius=radius, fill=fill, outline=outline, width=1 if outline else 0)
    if anchor == "lm":
        pos = (x1 + 12, (y1 + y2) // 2)
    elif anchor == "rm":
        pos = (x2 - 12, (y1 + y2) // 2)
    else:
        pos = ((x1 + x2) // 2, (y1 + y2) // 2)
    draw.text(pos, str(text), font=font, fill=text_fill, anchor=anchor)


def _sk_title_panel(img: Image.Image, title: str, font, subtitle: str = "", subtitle_font=None, pad: int = 18, height: int = 58):
    d = ImageDraw.Draw(img)
    x1, y1, x2, y2 = pad, pad, img.width - pad, pad + height
    _sk_panel(img, (x1, y1, x2, y2), radius=22, fill=(255, 255, 255, 222), outline=(255, 255, 255, 230))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((x1 + 16, y1 + 15, x1 + 78, y1 + 21), radius=3, fill=(255, 128, 178))
    max_title_w = x2 - x1 - 44
    if subtitle and subtitle_font:
        max_title_w -= 150
    d.text((x1 + 18, y1 + height // 2 + 8), _sk_fit_text(d, title, font, max_title_w), font=font, fill=(50, 30, 50), anchor="lm")
    if subtitle and subtitle_font:
        _sk_chip(d, (x2 - 142, y1 + 16, x2 - 14, y1 + 44), subtitle, subtitle_font, fill=(255, 246, 251), text_fill=(120, 80, 100))


def _sk_draw_row(draw: ImageDraw.ImageDraw, xy, cells, col_widths, fonts, fills, bg=(255, 255, 255), outline=(255, 255, 255), radius: int = 14, pad_x: int = 14, align_right_from: int = -1):
    x1, y1, x2, y2 = xy
    draw.rounded_rectangle(xy, radius=radius, fill=bg, outline=outline, width=1 if outline else 0)
    x = x1
    for i, (cell, cw, font, fill) in enumerate(zip(cells, col_widths, fonts, fills)):
        if align_right_from >= 0 and i >= align_right_from:
            draw.text((x + cw - pad_x, (y1 + y2) // 2), str(cell), font=font, fill=fill, anchor="rm")
        else:
            draw.text((x + cw // 2, (y1 + y2) // 2), str(cell), font=font, fill=fill, anchor="mm")
        if i < len(col_widths) - 1:
            draw.rounded_rectangle((x + cw - 1, y1 + 8, x + cw + 1, y2 - 8), radius=1, fill=(235, 210, 226))
        x += cw


pred_score_json = {"data": {}, "time": 0, "id": 0}
pred_url = "https://api.3-3.dev/sekai/jp/event/predict"
cheer_pred_url_bak = "https://api.3-3.dev/sekai/jp/event/cheer/predict"


__plugin_name__ = "活动查分/sk"
__plugin_type__ = "烧烤相关&uni移植"
__plugin_version__ = 0.2
__plugin_usage__ = f"""
usage：
    pjsk活动查分，支持日服/国服/台服
    若群内已有unibot请勿开启此bot该功能
    私聊可用，一分钟内每人最多查询3次
    因为sbga的原因，今后只能查前百的分数
    指令：
        sk/cf [排名]             查询此排名玩家的活动查房信息，仅限前百
        sk/cf *[多个排名]          查询给出的排名玩家的活动查房信息，排名用空格隔开，仅限前百
        sk/cf [范围]              查询此范围内玩家的活动查房信息，如 1-10，仅限前百
        sk/cf [id]               查询此id玩家的活动查房信息，仅限前百
        sk/cf @qq                查看艾特用户的活动查房信息(对方必须已绑定烧烤账户且排名前百)
        sk/cf                    查询自己的活动查房信息，仅限前百
        sk预测/活动预测/ycx        查看日服活动预测线（多源预测）
        cnsk预测/cn活动预测/cnycx  查看国服活动预测线
        twsk预测/tw活动预测/twycx  查看台服活动预测线
        sks/时速/sk时速          查看近1小时各档线时速；WL可加 wl/wl2/wl角色
        日速/半日速               查看近24小时/12小时各档线速度；WL可加 wl/wl2/wl角色
        skl/排名线/sk线           查看当前活动排名线；WL活动默认总榜，加 wl/wl2/wl角色 查单榜
        cf/查房 [排名/id/范围]     同 sk，查看玩家最近1小时的活动情况；WL单榜同样支持 wl/wl2/wl角色
        csb/查水表 [排名/id]      查看玩家整个活动期间每小时的游玩次数和停车时间段
        wlsk [排名/id/范围]       WL当前分榜查房；可用 wl2/wl角色 指定分榜
        wlsks/wl时速             WL当前分榜时速；wl日速/wl半日速 查看分榜日速/半日速
        wlskl/wlsk线             WL当前分榜排名线；可用 wl2/wl角色 指定分榜
        wlcsb [排名/id]          WL当前分榜查水表；可用 wl2/wl角色 指定分榜
        5v5人数                  查看当前5v5活动的两队人数
    数据来源：
        pjsekai.moe
        unipjsk.com
        3-3.dev / Moesekai / SekaRun
""".strip()
__plugin_superuser_usage__ = f"""
superuser_usage：
    pjsk活动号更新
    指令：
        pjsk活动更新        手动更新当前活动号
        SKAPI切换 [新|旧]  查看或切换榜线 API（重启后保持）
"""
__plugin_settings__ = {
    "default_status": False,
    "cmd": ['sk', 'wlsk', "活动查分", "烧烤相关"],
}
__plugin_cd_limit__ = {"cd": 60, "count_limit": 3, "rst": "别急，你才刚查完呢", "limit_type": "user"}
__plugin_block_limit__ = {"rst": "别急，还在查！"}


# pjsk查分
pjsk_sk = on_command('sk', priority=5, block=True)
cn_sk = on_command('cnsk', priority=5, block=True)
tw_sk = on_command('twsk', priority=5, block=True)

# WL 当前/指定分榜查分快捷入口
pjsk_wlsk = on_command('wlsk', aliases={"wl查房"}, priority=5, block=True)
cn_wlsk = on_command('cnwlsk', aliases={"cnwl查房"}, priority=5, block=True)
tw_wlsk = on_command('twwlsk', aliases={"twwl查房"}, priority=5, block=True)

# pjsk活动号更新
pjsk_event_update = on_command('pjsk活动更新', permission=SUPERUSER, priority=1, block=True)
cn_event_update = on_command('cnpjsk活动更新', permission=SUPERUSER, priority=1, block=True)
tw_event_update = on_command('twpjsk活动更新', permission=SUPERUSER, priority=1, block=True)
sk_api_switch = on_command(
    'SKAPI切换',
    aliases={
        'skapi切换', 'SK API切换', 'sk api切换',
        'SKAPI切换新', 'SKAPI切换旧', 'skapi切换新', 'skapi切换旧',
    },
    permission=SUPERUSER,
    priority=1,
    block=True,
)

# pjsk榜线查询
pjsk_pred_query = on_command('sk预测', aliases={"活动预测", 'ycx', 'skp', 'ycxall', 'lsycx'}, priority=4, block=True)
cn_pred_query = on_command('cnsk预测', aliases={"cn活动预测", 'cnycx', 'cnskp', 'cnycxall', 'cnlsycx'}, priority=4, block=True)
tw_pred_query = on_command('twsk预测', aliases={"tw活动预测", 'twycx', 'twskp', 'twycxall', 'twlsycx'}, priority=4, block=True)

# remote 自动打歌账号的个人排名曲线（仅超级用户，其他人发送无任何响应）
pjsk_me_curve = on_command('skme', aliases={"sk我的曲线"}, permission=SUPERUSER, priority=1, block=True)
cn_me_curve = on_command('cnskme', aliases={"cnsk我的曲线"}, permission=SUPERUSER, priority=1, block=True)
tw_me_curve = on_command('twskme', aliases={"twsk我的曲线"}, permission=SUPERUSER, priority=1, block=True)

# pjsk榜线曲线查询
pjsk_pred_curve_query = on_command('ycx曲线', aliases={"sk预测曲线", "活动预测曲线"}, priority=4, block=True)
cn_pred_curve_query = on_command('cnycx曲线', aliases={"cnsk预测曲线", "cn活动预测曲线"}, priority=4, block=True)
tw_pred_curve_query = on_command('twycx曲线', aliases={"twsk预测曲线", "tw活动预测曲线"}, priority=4, block=True)

# pjsk 5v5人数查询
pjsk_5v5_query = on_command('5v5人数', priority=5, block=True)
cn_5v5_query = on_command('cn5v5人数', priority=5, block=True)
tw_5v5_query = on_command('tw5v5人数', priority=5, block=True)

# pjsk时速/日速/半日速
pjsk_sks = on_command('sks', aliases={"时速", "sk时速", "日速", "sk日速", "半日速", "sk半日速"}, priority=5, block=True)
cn_sks = on_command('cnsks', aliases={"cn时速", "cnsk时速", "cn日速", "cnsk日速", "cn半日速", "cnsk半日速"}, priority=5, block=True)
tw_sks = on_command('twsks', aliases={"tw时速", "twsk时速", "tw日速", "twsk日速", "tw半日速", "twsk半日速"}, priority=5, block=True)

# WL 当前/指定分榜时速/日速/半日速
pjsk_wlsks = on_command('wlsks', aliases={"wl时速", "wlsk时速", "wl日速", "wlsk日速", "wl半日速", "wlsk半日速"}, priority=5, block=True)
cn_wlsks = on_command('cnwlsks', aliases={"cnwl时速", "cnwlsk时速", "cnwl日速", "cnwlsk日速", "cnwl半日速", "cnwlsk半日速"}, priority=5, block=True)
tw_wlsks = on_command('twwlsks', aliases={"twwl时速", "twwlsk时速", "twwl日速", "twwlsk日速", "twwl半日速", "twwlsk半日速"}, priority=5, block=True)

# pjsk排名线
pjsk_skl = on_command('skl', aliases={"排名线", "sk排名线", "sk线"}, priority=5, block=True)
cn_skl = on_command('cnskl', aliases={"cn排名线", "cnsk排名线", "cnsk线"}, priority=5, block=True)
tw_skl = on_command('twskl', aliases={"tw排名线", "twsk排名线", "twsk线"}, priority=5, block=True)

# WL 当前/指定分榜排名线
pjsk_wlskl = on_command('wlskl', aliases={"wl排名线", "wlsk排名线", "wlsk线"}, priority=5, block=True)
cn_wlskl = on_command('cnwlskl', aliases={"cnwl排名线", "cnwlsk排名线", "cnwlsk线"}, priority=5, block=True)
tw_wlskl = on_command('twwlskl', aliases={"twwl排名线", "twwlsk排名线", "twwlsk线"}, priority=5, block=True)

# pjsk查房
pjsk_cf = on_command('cf', aliases={"查房"}, priority=5, block=True)
cn_cf = on_command('cncf', aliases={"cn查房"}, priority=5, block=True)
tw_cf = on_command('twcf', aliases={"tw查房"}, priority=5, block=True)

# pjsk订阅（优先级更高，避免被 sk 指令匹配）
pjsk_subscribe = on_command('订阅sk', aliases={"sk订阅"}, priority=3, block=True)
cn_subscribe = on_command('cn订阅sk', aliases={"cnsk订阅", "订阅cnsk"}, priority=3, block=True)
tw_subscribe = on_command('tw订阅sk', aliases={"twsk订阅", "订阅twsk"}, priority=3, block=True)

# pjsk取消订阅
pjsk_unsubscribe = on_command('退订sk', aliases={"取消订阅sk", "sk取消订阅", "sk退订"}, priority=3, block=True)
cn_unsubscribe = on_command('cn退订sk', aliases={"cn取消订阅sk", "cnsk取消订阅", "取消订阅cnsk", "cnsk退订"}, priority=3, block=True)
tw_unsubscribe = on_command('tw退订sk', aliases={"tw取消订阅sk", "twsk取消订阅", "取消订阅twsk", "twsk退订"}, priority=3, block=True)

# pjsk清空订阅（管理员）
clear_subscriptions = on_command('清空sk订阅', permission=SUPERUSER, priority=1, block=True)


# pjsk查水表
pjsk_csb = on_command('csb', aliases={"查水表"}, priority=5, block=True)
cn_csb = on_command('cncsb', aliases={"cn查水表"}, priority=5, block=True)
tw_csb = on_command('twcsb', aliases={"tw查水表"}, priority=5, block=True)

# WL 当前/指定分榜查水表
pjsk_wlcsb = on_command('wlcsb', aliases={"wl查水表"}, priority=5, block=True)
cn_wlcsb = on_command('cnwlcsb', aliases={"cnwl查水表"}, priority=5, block=True)
tw_wlcsb = on_command('twwlcsb', aliases={"twwl查水表"}, priority=5, block=True)

# 兼容无空格写法，如 cnwlsk100 / wlsk1-10。
wlsk_compact = on_regex(r'^[!！/／]?(?P<cmd>(?:cn|tw)?wlsk)\s*(?P<arg>\d+(?:-\d+)?(?:\s+\d+)*)$', priority=4, block=True)


def _is_wl_shortcut_command(cmd_name: str) -> bool:
    cmd_name = (cmd_name or '').lower()
    return cmd_name.startswith(('wl', 'cnwl', 'twwl'))


def _should_render_wl_rank_table(
    cmd_name: str,
    wl_chapter: Optional[dict],
) -> bool:
    """WL 快捷指令固定使用总榜+章节专用表；普通时速仅在未指定单章时使用。"""
    return _is_wl_shortcut_command(cmd_name) or wl_chapter is None


def _has_wl_selector(arg: str) -> bool:
    tokens = arg.split()
    return any(
        token.lower() == 'wl'
        or token.lower().startswith('wl')
        or token.lower() in ('-c', 'c')
        for token in tokens
    )


def _with_default_wl_arg(msg: Message) -> Message:
    arg = msg.extract_plain_text().strip()
    if _has_wl_selector(arg):
        return msg
    return Message(f"wl {arg}".strip())


def _with_current_wl_chapter_arg(msg: Message, current_id: int, pjsk_type: int) -> Message:
    """WL 快捷查房默认查当前章节，避免 `wl 100` 被误解析成第 100 章。"""
    arg = msg.extract_plain_text().strip()
    if _has_wl_selector(arg):
        return msg
    chapter = _current_wl_chapter(_get_wl_chapters(_base_event_id(current_id), pjsk_type))
    if not chapter:
        return msg
    return Message(f"wl{chapter.get('chapterNo')} {arg}".strip())


def _parse_rank_args(arg: str, default_ranks: List[int], limit: int = 20) -> List[int]:
    """解析单个、多个或范围排名；无参数时返回默认档位。"""
    raw = (arg or '').strip()
    if not raw:
        return list(default_ranks)
    range_match = re.fullmatch(r'(\d+)-(\d+)', raw)
    if range_match:
        start, end = map(int, range_match.groups())
        if start <= 0 or end < start or end - start + 1 > limit:
            return []
        return list(range(start, end + 1))
    if re.fullmatch(r'\d+(?:\s+\d+)*', raw):
        ranks = list(dict.fromkeys(int(value) for value in raw.split()))
        if len(ranks) <= limit and all(rank > 0 for rank in ranks):
            return ranks
    return []


@pjsk_sk.handle()
@cn_sk.handle()
@tw_sk.handle()
async def _(matcher: Matcher, event: MessageEvent, msg: Message = CommandArg(), cmd: Tuple[str, ...] = Command()):
    """sk 指令并入 cf：保留原 sk/cnsk/twsk 入口，输出查房信息。"""
    await _handle_cf_query(matcher, event, msg, cmd)


@pjsk_wlsk.handle()
@cn_wlsk.handle()
@tw_wlsk.handle()
async def _(matcher: Matcher, event: MessageEvent, msg: Message = CommandArg(), cmd: Tuple[str, ...] = Command()):
    """WL 当前/指定分榜查房快捷入口。"""
    await _handle_cf_query(matcher, event, msg, cmd)


@wlsk_compact.handle()
async def _(matcher: Matcher, event: MessageEvent):
    """兼容 cnwlsk100 / wlsk1-10 这类无空格写法。"""
    raw = event.get_plaintext().strip()
    match = re.fullmatch(r'[!！/／]?(?P<cmd>(?:cn|tw)?wlsk)\s*(?P<arg>\d+(?:-\d+)?(?:\s+\d+)*)', raw)
    if not match:
        return
    await _handle_cf_query(
        matcher,
        event,
        Message(match.group('arg').strip()),
        (match.group('cmd'),),
    )


async def send_msg(
    matcher: Matcher,
    param: Dict[str, Union[str, List[str]]],
    isprivate: bool,
    event_data: Optional[Dict] = None,
    pjsk_type: int = 0
):
    global event_id
    server_name = SERVER_MAP.get(pjsk_type, 'jp')
    if event_data is None:
        event_data = currentevent(pjsk_type)
    
    current_server_event_id = max(event_data["id"], event_id) # 这里的 event_id 可能是全局同步的，或者是 JP 用的
    
    # 单排名图片
    is_simple = any(isinstance(i, List) for i in param.values())
    if not is_simple:
        # 获取自己排名信息
        try:
            userdata = await getUserData(current_server_event_id, param, pjsk_type=pjsk_type)
            myid = userdata['id']
            myname = userdata['name']
            myscore = userdata['score']
            myrank = userdata['rank']
            myteaminfo = userdata['teaminfo']
            assetbundleName = userdata['assetbundleName']
        except IndexError:
            await matcher.finish('查不到数据捏，可能这期活动没打', at_sender=True)
            return
        except (maintenanceIn, apiCallError, userIdBan) as e:
            await matcher.finish(str(e), at_sender=True)
            return
        except Exception as e:
            await matcher.finish(BUG_ERROR, at_sender=True)
            logger.warning(f"pjsk查分失败。Error：{e}")
            return
        # 制作排名图片
        # 获取附近排名信息
        mynear_rank = near_rank(myrank)
        near_ranks_data = []
        
        try:
            for eachrank in mynear_rank:
                try:
                    query_param = {'targetRank': eachrank['rank']}
                    user_data = await getUserData(current_server_event_id, query_param, pjsk_type=pjsk_type)
                    score = user_data['score']
                    deviation = abs(score - myscore) / 10000
                    
                    # 获取预测数据
                    pred = None
                    if event_data['status'] == 'going' and pjsk_type == 0:
                        if pred_score_json['id'] == current_server_event_id:
                            pred = pred_score_json['data'].get(str(eachrank['rank']))
                    
                    near_ranks_data.append({
                        'rank': eachrank['rank'],
                        'score': score,
                        'tag': eachrank['tag'],
                        'deviation': deviation,
                        'pred': pred
                    })
                except:
                    pass
        except Exception as e:
            logger.warning(f'获取附近排名玩家信息错误，Error:{e}')
            pass
        
        # 准备队伍信息
        team_info = None
        team_image = None
        if myteaminfo:
            team_info = (myteaminfo[0], myteaminfo[1] if len(myteaminfo) > 1 else '')
            try:
                team_image = await pjsk_update_manager.get_asset(
                    f'ondemand/event/{event_data["assetbundleName"]}/team_image', f'{assetbundleName}.png',
                    pjsk_type=pjsk_type,
                    block=True
                )
            except:
                pass
        
        # 准备预测数据
        pred_data = None
        if event_data['status'] == 'going' and pjsk_type == 0:
            if pred_score_json['id'] == current_server_event_id:
                pred_data = pred_score_json['data']
        
        # 活动剩余时间
        remain_time = None
        if event_data['status'] == 'going' and event_data["id"] == current_server_event_id:
            remain_time = event_data['remain']
        
        # 生成图片
        img = await run_pjsk_thread(compose_sk_image, 
            name=myname,
            uid=myid if not isprivate else myid,
            score=myscore,
            rank=myrank,
            near_ranks=near_ranks_data,
            pred_data=pred_data,
            remain_time=remain_time,
            update_time=userdata["updateTime"],
            team_info=team_info,
            team_image=team_image
        )
        
        # 发送排名图片
        await matcher.finish(image(b64=pic2b64(img)))
    # 多排名图片
    else:
        players_data = []
        updateTime = ''
        
        for q in param.keys():
            for userid in param[q]:
                try:
                    userdata = await getUserData(current_server_event_id, {q: userid}, pjsk_type=pjsk_type)
                    userId = userdata['id']
                    name = userdata['name']
                    score = userdata['score']
                    rank = userdata['rank']
                    updateTime = updateTime if updateTime else userdata["updateTime"]
                    
                    players_data.append({
                        'name': name,
                        'uid': userId,
                        'score': score,
                        'rank': rank
                    })
                except Exception as e:
                    logger.warning(f"获取玩家 {userid} 数据失败: {e}")
                    continue
        
        if players_data:
            # 生成图片
            img = await run_pjsk_thread(compose_sk_multi_image, players_data, updateTime)
            await matcher.finish(image(b64=pic2b64(img)))
        else:
            await matcher.finish(BUG_ERROR + '\n查分仅支持前百！')


@pjsk_event_update.handle()
@cn_event_update.handle()
@tw_event_update.handle()
async def _(matcher: Matcher, event: MessageEvent, msg: Message = CommandArg(), cmd: Tuple[str, ...] = Command()):
    pjsk_type = get_pjsk_type(cmd[0])
    server_name = SERVER_MAP.get(pjsk_type, 'jp')

    global pred_score_json
    global event_id
    arg = msg.extract_plain_text().strip()
    if is_number(arg):
        # 这种手动更新通常是针对特定服务器的，但代码里 event_id 似乎是全局变量
        # 建议改为局部或根据 pjsk_type 区分
        await matcher.finish(f"{server_name} pjsk暂不支持手动指定活动号", at_sender=True)
    else:
        try:
            event_data = currentevent(pjsk_type)
            current_id = event_data['id']
            await matcher.send(f"{server_name} pjsk活动号当前为 {current_id}", at_sender=True)
        except:
            await matcher.send(f"{server_name} pjsk更新活动号失败", at_sender=True)
    
    if pjsk_type == 0:
        try:
            tmp_json = await asyncio.to_thread(_blocking_get_json, pred_url, headers)
            if tmp_json["status"] == "success":
                pred_score_json = {
                    "data": tmp_json["data"],
                    "time": tmp_json["data"]["ts"]/1000,
                    "id": tmp_json["data"]["eventId"]
                }
            logger.info("pjsk更新预测线成功！")
        except Exception as e:
            logger.warning(f"pjsk更新预测线失败！Error:{e}")


@sk_api_switch.handle()
async def _(
    matcher: Matcher,
    msg: Message = CommandArg(),
    cmd: Tuple[str, ...] = Command(),
):
    raw = msg.extract_plain_text().strip().lower().replace(" ", "")
    command_name = "".join(cmd).lower().replace(" ", "")
    if not raw:
        if command_name.endswith("新"):
            raw = "新"
        elif command_name.endswith("旧"):
            raw = "旧"
    aliases = {
        "新": "new",
        "新版": "new",
        "新api": "new",
        "new": "new",
        "旧": "old",
        "旧版": "old",
        "旧api": "old",
        "old": "old",
    }
    current = load_api_mode()
    if not raw:
        label = "新 API（30 秒）" if current == "new" else "旧 API（180 秒）"
        await matcher.finish(f"当前 SK 榜线来源：{label}\n用法：SKAPI切换 新 / SKAPI切换 旧")

    mode = aliases.get(raw)
    if mode is None:
        await matcher.finish("参数无效。用法：SKAPI切换 新 / SKAPI切换 旧")
    if mode == current:
        label = "新 API（30 秒）" if current == "new" else "旧 API（180 秒）"
        await matcher.finish(f"SK 榜线来源已经是：{label}")

    saved = save_api_mode(mode)
    label = "新 API（30 秒）" if saved == "new" else "旧 API（180 秒，注意调用限制）"
    logger.warning(f"[SK API] 榜线来源已由 {current} 切换为 {saved}")
    await matcher.finish(f"已切换 SK 榜线来源：{label}\n下一轮定时抓取立即生效。")


def _fmt_time_delta(ts: int) -> tuple:
    """返回 (时间文字, 是否过期)"""
    delta = datetime.now() - datetime.fromtimestamp(ts)
    secs = delta.total_seconds()
    if secs < 60:
        s = "刚刚"
    elif secs < 3600:
        s = f"{int(secs / 60)}分钟前"
    elif secs < 86400:
        s = f"{int(secs / 3600)}小时前"
    else:
        s = f"{int(secs / 86400)}天前"
    expired = secs > FORECAST_EXPIRE_HOURS * 3600
    return s, expired


def _calculate_rank_speed(
    latest: Ranking,
    older: Optional[Ranking],
    period_seconds: int = 3600,
) -> Optional[float]:
    """按实际采样间隔折算速度；分数回退或时间异常时拒绝该样本。"""
    if older is None or latest.score < older.score:
        return None
    elapsed_seconds = (latest.time - older.time).total_seconds()
    if elapsed_seconds <= 0:
        return None
    return (
        (latest.score - older.score)
        * period_seconds
        / elapsed_seconds
        / 10000
    )


def _build_rank_table_data(
    latest_rankings: List[Ranking],
    older_rankings: List[Ranking],
    period_seconds: int = 3600,
) -> List[dict]:
    """合并最新榜线和历史榜线，生成绘图所需的分数及时速数据。"""
    older_by_rank = {ranking.rank: ranking for ranking in older_rankings}
    return [
        {
            'rank': latest.rank,
            'score': latest.score,
            'speed': _calculate_rank_speed(
                latest,
                older_by_rank.get(latest.rank),
                period_seconds,
            ),
        }
        for latest in latest_rankings
    ]


def compose_rank_table_image(title: str, ranks_data: List[dict], update_minutes_ago: int = 0, speed_header: str = "时速", speed_unit: str = "万/h") -> Image.Image:
    """用 PIL 绘制排名表格图片（用于时速、排名线等）"""
    font_path      = str(FONT_PATH / "SourceHanSansCN-Medium.otf")
    font_bold_path = str(FONT_PATH / "SourceHanSansCN-Bold.otf")
    f_title  = _sk_font(font_bold_path, 22)
    f_header = _sk_font(font_bold_path, 18)
    f_body   = _sk_font(font_path, 18)
    f_small  = _sk_font(font_path, 12)

    PAD_X    = 20
    ROW_H    = 40
    HEADER_H = 44
    TITLE_H  = 58
    FOOTER_H = 34
    OUT_PAD  = 18
    GAP      = 8

    _tmp = Image.new("RGB", (1, 1))
    _d   = ImageDraw.Draw(_tmp)

    def tw(text: str, font) -> int:
        return int(_d.textlength(text, font=font))

    col0_cands = [("排名", f_header)] + [(f"T{d['rank']}", f_body) for d in ranks_data]
    col0_w = max(tw(t, f) for t, f in col0_cands) + PAD_X * 2
    col1_cands = [("实时分数", f_header)] + [(f"{d['score'] / 10000:.2f}万" if d['score'] else "-", f_body) for d in ranks_data]
    col1_w = max(tw(t, f) for t, f in col1_cands) + PAD_X * 2
    col2_cands = [(speed_header, f_header)] + [(f"{d['speed']:.1f}{speed_unit}" if d['speed'] is not None else "-", f_body) for d in ranks_data]
    col2_w = max(tw(t, f) for t, f in col2_cands) + PAD_X * 2

    all_col_ws = [col0_w, col1_w, col2_w]
    table_w = sum(all_col_ws)
    content_w = max(table_w, tw(title, f_title) + PAD_X * 2, 520)
    total_w = content_w + OUT_PAD * 2
    total_h = OUT_PAD * 2 + TITLE_H + GAP + HEADER_H + GAP + ROW_H * len(ranks_data) + FOOTER_H

    C_HEAD_BG  = (230, 140, 170, 224)
    C_HEAD_FG  = (255, 255, 255)
    C_ROW_ODD  = (255, 255, 255, 224)
    C_ROW_EVEN = (255, 240, 248, 216)
    C_TEXT     = (50, 30, 50)
    C_MUTED    = (120, 80, 100)

    img = _sk_gradient_bg(total_w, total_h)
    d = ImageDraw.Draw(img)

    x0 = OUT_PAD
    x1 = total_w - OUT_PAD
    y = OUT_PAD

    _sk_panel(img, (x0, y, x1, y + TITLE_H), radius=22, fill=(255, 255, 255, 222))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((x0 + 18, y + 16, x0 + 80, y + 22), radius=3, fill=(255, 128, 178))
    d.text((x0 + 20, y + TITLE_H // 2 + 8), _sk_fit_text(d, title, f_title, content_w - 190), font=f_title, fill=C_TEXT, anchor="lm")
    update_text = f"{update_minutes_ago} 分钟前" if update_minutes_ago > 0 else "刚刚更新"
    d.rounded_rectangle((x1 - 132, y + 16, x1 - 14, y + 44), radius=14, fill=(255, 246, 251), outline=(245, 218, 232))
    d.text((x1 - 73, y + 30), update_text, font=f_small, fill=C_MUTED, anchor="mm")
    y += TITLE_H + GAP

    _sk_panel(img, (x0, y - 4, x1, total_h - OUT_PAD), radius=20, fill=(255, 255, 255, 132), outline=(255, 255, 255, 210))
    d = ImageDraw.Draw(img)

    def draw_row(row_y, cells, bg, fonts, fg_list, h=ROW_H):
        inset = 10
        d.rounded_rectangle((x0 + inset, row_y, x1 - inset, row_y + h - 2), radius=15, fill=bg, outline=(255, 255, 255, 180))
        widths = all_col_ws[:]
        widths[-1] += max(0, content_w - table_w - inset * 2)
        x = x0 + inset
        for i, (cell, font, fg) in enumerate(zip(cells, fonts, fg_list)):
            cw = widths[i]
            d.text((x + cw // 2, row_y + h // 2), cell, font=font, fill=fg, anchor="mm")
            if i < len(widths) - 1:
                d.rounded_rectangle((x + cw - 1, row_y + 8, x + cw + 1, row_y + h - 10), radius=1, fill=(235, 210, 226))
            x += cw

    draw_row(y, ["排名", "实时分数", speed_header], C_HEAD_BG, [f_header] * 3, [C_HEAD_FG] * 3, h=HEADER_H)
    y += HEADER_H + GAP

    for i, data in enumerate(ranks_data):
        bg = C_ROW_ODD if i % 2 == 0 else C_ROW_EVEN
        cells = [
            f"T{data['rank']}",
            f"{data['score'] / 10000:.2f}万" if data['score'] else "-",
            f"{data['speed']:.1f}{speed_unit}" if data['speed'] is not None else "-"
        ]
        draw_row(y, cells, bg, [f_body] * 3, [C_TEXT] * 3)
        y += ROW_H

    footer_text = f"数据更新于 {update_minutes_ago} 分钟前" if update_minutes_ago > 0 else "数据刚刚更新"
    d.text((x1 - 16, total_h - OUT_PAD - FOOTER_H // 2 + 2), footer_text, font=f_small, fill=C_MUTED, anchor="rm")
    return img


def _load_wl_chara_icon(cid: int, size: int = 36) -> Optional[Image.Image]:
    """加载 WL 表头角色头像。"""
    candidates = [
        data_path / 'chara' / f'chr_ts_90_{cid}.png',
        data_path / 'chara' / f'chr_ts_90_{cid}_2.png',
    ]
    for path in candidates:
        if path.exists():
            try:
                return Image.open(path).convert('RGBA').resize((size, size), Image.Resampling.LANCZOS)
            except Exception:
                pass
    return None


async def _get_wl_rank_table_rows(
    region: str,
    event_id: int,
    chapters: List[dict],
    ranks: List[int],
    period_hours: Optional[int] = None,
    period_seconds: int = 3600,
) -> Tuple[List[dict], int]:
    """读取 WL 总榜 + 各角色单榜排名线/速度数据。"""
    import datetime

    from .._sk_sql import query_first_ranking_after, query_latest_ranking

    base_id = _base_event_id(event_id)
    ids = [('total', base_id, None)] + [
        (f"chapter_{c.get('chapterNo')}", _wl_encoded_event_id(base_id, int(c.get('chapterNo', 0))), c)
        for c in chapters
    ]

    now = datetime.datetime.now()
    old_time = now - datetime.timedelta(hours=period_hours) if period_hours else None
    data: Dict[str, Dict[int, dict]] = {}
    update_minutes: Optional[int] = None

    for key, query_event_id, chapter in ids:
        latest = await query_latest_ranking(region, query_event_id, ranks)
        older = await query_first_ranking_after(region, query_event_id, old_time, ranks) if old_time else []
        latest_map = {row.rank: row for row in latest}
        older_map = {row.rank: row for row in older}
        data[key] = {}
        for rank, row in latest_map.items():
            speed = (
                _calculate_rank_speed(row, older_map.get(rank), period_seconds)
                if period_hours
                else None
            )
            data[key][rank] = {'score': row.score, 'speed': speed, 'time': row.time}
            row_update_minutes = int((now - row.time).total_seconds() / 60)
            update_minutes = row_update_minutes if update_minutes is None else min(update_minutes, row_update_minutes)

    rows = []
    for rank in ranks:
        row = {
            'rank': rank,
            'total': data.get('total', {}).get(rank),
            'chapters': {},
        }
        for chapter in chapters:
            key = f"chapter_{chapter.get('chapterNo')}"
            row['chapters'][int(chapter.get('chapterNo', 0))] = data.get(key, {}).get(rank)
        if row['total'] or any(row['chapters'].values()):
            rows.append(row)
    return rows, update_minutes or 0


def compose_wl_rank_table_image(
    title: str,
    chapters: List[dict],
    rows: List[dict],
    update_minutes_ago: int = 0,
    value_mode: str = 'score',
    value_header: str = '分数',
    value_unit: str = '',
) -> Image.Image:
    """绘制 WL 总榜 + 各角色单榜横向表格。"""
    font_path      = str(FONT_PATH / "SourceHanSansCN-Medium.otf")
    font_bold_path = str(FONT_PATH / "SourceHanSansCN-Bold.otf")
    f_title  = _sk_font(font_bold_path, 22)
    f_header = _sk_font(font_bold_path, 16)
    f_body   = _sk_font(font_path, 16)
    f_small  = _sk_font(font_path, 12)

    OUT_PAD = 18
    TITLE_H = 58
    HEADER_H = 52
    ROW_H = 38
    FOOTER_H = 34
    GAP = 8
    RANK_W = 82
    TOTAL_W = 126
    CHAPTER_W = 104
    table_w = RANK_W + TOTAL_W + CHAPTER_W * len(chapters)
    content_w = max(table_w, 620)
    total_w = content_w + OUT_PAD * 2
    total_h = OUT_PAD * 2 + TITLE_H + GAP + HEADER_H + GAP + ROW_H * len(rows) + FOOTER_H

    C_HEAD_BG  = (230, 140, 170, 224)
    C_HEAD_FG  = (255, 255, 255)
    C_ROW_ODD  = (255, 255, 255, 224)
    C_ROW_EVEN = (255, 240, 248, 216)
    C_TEXT     = (50, 30, 50)
    C_MUTED    = (120, 80, 100)

    def fmt_value(item: Optional[dict]) -> str:
        if not item:
            return '-'
        if value_mode == 'speed':
            speed = item.get('speed')
            return f"{speed:.1f}{value_unit}" if speed is not None else '-'
        score = item.get('score')
        return f"{score / 10000:.2f}万" if score else '-'

    img = _sk_gradient_bg(total_w, total_h)
    _sk_title_panel(img, title, f_title, "WL总榜+单榜", f_small, pad=OUT_PAD, height=TITLE_H)
    d = ImageDraw.Draw(img)
    x0 = OUT_PAD
    x1 = total_w - OUT_PAD
    y = OUT_PAD + TITLE_H + GAP
    _sk_panel(img, (x0, y - 4, x1, total_h - OUT_PAD), radius=20, fill=(255, 255, 255, 140), outline=(255, 255, 255, 210))
    d = ImageDraw.Draw(img)

    col_widths = [RANK_W, TOTAL_W] + [CHAPTER_W] * len(chapters)
    table_x = x0 + max(0, (content_w - table_w) // 2)

    def draw_cell_text(text, x, yy, w, h, font, fill, anchor='mm'):
        d.text((x + w // 2, yy + h // 2), str(text), font=font, fill=fill, anchor=anchor)

    # header
    d.rounded_rectangle((table_x, y, table_x + table_w, y + HEADER_H - 2), radius=15, fill=C_HEAD_BG, outline=(255, 255, 255))
    x = table_x
    draw_cell_text('排名', x, y, RANK_W, HEADER_H, f_header, C_HEAD_FG); x += RANK_W
    draw_cell_text(f'总榜{value_header}', x, y, TOTAL_W, HEADER_H, f_header, C_HEAD_FG); x += TOTAL_W
    for chapter in chapters:
        icon = _load_wl_chara_icon(int(chapter.get('gameCharacterId', 0)), size=34)
        if icon:
            img.paste(icon, (x + (CHAPTER_W - icon.width) // 2, y + 6), icon.split()[3])
            d.text((x + CHAPTER_W // 2, y + 43), f"第{chapter.get('chapterNo')}章", font=f_small, fill=C_HEAD_FG, anchor='mm')
        else:
            draw_cell_text(f"第{chapter.get('chapterNo')}章", x, y, CHAPTER_W, HEADER_H, f_header, C_HEAD_FG)
        x += CHAPTER_W
    y += HEADER_H + GAP

    for idx, row in enumerate(rows):
        bg = C_ROW_ODD if idx % 2 == 0 else C_ROW_EVEN
        d.rounded_rectangle((table_x, y, table_x + table_w, y + ROW_H - 2), radius=14, fill=bg, outline=(255, 255, 255))
        x = table_x
        values = [f"T{row['rank']}", fmt_value(row.get('total'))]
        for chapter in chapters:
            values.append(fmt_value(row.get('chapters', {}).get(int(chapter.get('chapterNo', 0)))))
        for value, w in zip(values, col_widths):
            draw_cell_text(value, x, y, w, ROW_H, f_body, C_TEXT)
            x += w
        y += ROW_H

    footer_text = f"数据更新于 {update_minutes_ago} 分钟前" if update_minutes_ago > 0 else "数据刚刚更新"
    d.text((x1 - 16, total_h - OUT_PAD - FOOTER_H // 2 + 2), footer_text, font=f_small, fill=C_MUTED, anchor="rm")
    return img


async def _get_wl_chapter_forecast_data(
    region: str,
    base_event_id: int,
    chapters: List[dict],
) -> Dict[int, Optional[ForecastData]]:
    """为每个 WL 章节生成本地预测数据，返回 chapter_no -> ForecastData | None。
    单榜预测只走本地来源，外部预测源（moe/33kit/sekarun）不提供分榜数据。
    """
    from ._forecast import get_local_forecast_data

    result: Dict[int, Optional[ForecastData]] = {}
    for chapter in chapters:
        chapter_no = int(chapter.get('chapterNo', 0))
        if not chapter_no:
            continue
        encoded_id = _wl_encoded_event_id(base_event_id, chapter_no)
        try:
            fc = await get_local_forecast_data(region, encoded_id)
            logger.info(f"[WL预测] 章节 {chapter_no} (encoded={encoded_id}) 预测: {'成功' if fc else '无数据'}")
        except Exception as e:
            logger.warning(f"[WL预测] 章节 {chapter_no} 预测生成失败: {e}")
            fc = None
        result[chapter_no] = fc
    return result


async def compose_wl_forecast_image(
    region: str,
    base_event_id: int,
    event_name: str,
    chapters: List[dict],
    total_forecasts: List['ForecastData'],
    chapter_forecasts: Dict[int, Optional['ForecastData']],
    live_scores: Dict[int, int],
    live_speeds: Dict[int, float],
    display_ranks: List[int],
) -> Image.Image:
    """绘制 WL 总榜预测 + 各章节单榜预测的横向表格图。"""
    font_path      = str(FONT_PATH / "SourceHanSansCN-Medium.otf")
    font_bold_path = str(FONT_PATH / "SourceHanSansCN-Bold.otf")
    f_title  = _sk_font(font_bold_path, 22)
    f_header = _sk_font(font_bold_path, 17)
    f_body   = _sk_font(font_path, 17)
    f_small  = _sk_font(font_path, 13)

    PAD_X    = 18
    ROW_H    = 40
    HEADER_H = 50
    TITLE_H  = 60
    FOOTER_H = 36
    OUT_PAD  = 18
    GAP      = 8
    ICON_SIZE = 32

    C_HEAD_BG   = (230, 140, 170)
    C_HEAD_FG   = (255, 255, 255)
    C_ROW_ODD   = (255, 255, 255)
    C_ROW_EVEN  = (255, 240, 248)
    C_TEXT      = (50, 30, 50)
    C_WARN      = (200, 60, 60)
    C_MUTED     = (120, 80, 100)
    C_WL_BG     = (200, 140, 200)  # 单榜列表头背景

    _tmp = Image.new("RGB", (1, 1))
    _d   = ImageDraw.Draw(_tmp)

    def tw(text: str, font) -> int:
        return int(_d.textlength(text, font=font))

    # --- 列宽计算 ---
    # col0: 排名
    col0_cands = [("排名", f_header)] + [(f"T{r}", f_body) for r in display_ranks]
    col0_w = max(tw(t, f) for t, f in col0_cands) + PAD_X * 2

    # col1/col2: 总榜当前分 / 时速
    score_strs = {r: (f"{s/10000:.2f}万" if s else "-") for r, s in live_scores.items()}
    speed_strs = {r: (f"{v:.1f}万/h" if v is not None else "-") for r, v in live_speeds.items()}
    col1_cands = [("当前分数", f_header)] + [(score_strs.get(r, "-"), f_body) for r in display_ranks]
    col1_w = max(tw(t, f) for t, f in col1_cands) + PAD_X * 2
    col2_cands = [("时速", f_header)] + [(speed_strs.get(r, "-"), f_body) for r in display_ranks]
    col2_w = max(tw(t, f) for t, f in col2_cands) + PAD_X * 2

    # 总榜预测列（可能多源）
    total_source_names = [FORECAST_SOURCES.get(fc.source, {}).get('name', fc.source) for fc in total_forecasts]
    total_pred_col_ws: List[int] = []
    for idx, fc in enumerate(total_forecasts):
        cands = [(total_source_names[idx], f_header)]
        for rank in display_ranks:
            rd = fc.rank_data.get(rank)
            cands.append((f"{rd.final_score/10000:.2f}万" if (rd and rd.final_score) else "-", f_body))
        total_pred_col_ws.append(max(tw(t, f) for t, f in cands) + PAD_X * 2)

    # WL 章节预测列（每章一列，只有 local 来源）
    chapter_col_ws: List[int] = []
    chapter_list = sorted(chapters, key=lambda c: c.get('chapterNo', 0))
    for chapter in chapter_list:
        chapter_no = int(chapter.get('chapterNo', 0))
        fc = chapter_forecasts.get(chapter_no)
        cid = chapter.get('gameCharacterId', 0)
        col_header = f"第{chapter_no}章"
        cands = [(col_header, f_header)]
        for rank in display_ranks:
            rd = fc.rank_data.get(rank) if fc else None
            cands.append((f"{rd.final_score/10000:.2f}万" if (rd and rd.final_score) else "-", f_body))
        chapter_col_ws.append(max(tw(t, f) for t, f in cands) + PAD_X * 2)

    all_col_ws = [col0_w, col1_w, col2_w] + total_pred_col_ws + chapter_col_ws
    table_w = sum(all_col_ws)

    title_text = f"【{region.upper()}-{base_event_id}】{event_name}  WL榜线预测"
    content_w = max(table_w, tw(title_text, f_title) + PAD_X * 2, 700)
    extra_w = max(0, content_w - table_w)
    draw_col_ws = all_col_ws[:]
    draw_col_ws[-1] += extra_w

    total_w = content_w + OUT_PAD * 2
    # 行数：表头 + 数据行 + 时间行
    total_h = OUT_PAD * 2 + TITLE_H + GAP + HEADER_H + ROW_H * len(display_ranks) + HEADER_H + FOOTER_H + GAP * 2

    img = _sk_gradient_bg(total_w, total_h)
    _sk_title_panel(img, title_text, f_title, "WL榜线预测", f_small, pad=OUT_PAD, height=TITLE_H)
    d = ImageDraw.Draw(img)

    x0 = OUT_PAD
    x1 = total_w - OUT_PAD
    y  = OUT_PAD + TITLE_H + GAP
    _sk_panel(img, (x0, y - 4, x1, total_h - OUT_PAD), radius=20,
              fill=(255, 255, 255, 140), outline=(255, 255, 255, 210))
    d = ImageDraw.Draw(img)

    def draw_row(row_y, cells, bg, fonts, fg_list, h=ROW_H, right_from=1):
        _sk_draw_row(
            d,
            (x0 + 10, row_y, x1 - 10, row_y + h - 2),
            cells,
            draw_col_ws,
            fonts,
            fg_list,
            bg=bg,
            outline=(255, 255, 255),
            radius=14,
            pad_x=PAD_X,
            align_right_from=right_from,
        )

    # --- 表头行（含角色图标） ---
    # 先绘制纯色背景表头
    total_pred_count = len(total_forecasts)
    chapter_count = len(chapter_list)
    head_cells = (
        ["排名", "当前分数", "时速"]
        + total_source_names
        + [f"第{int(c.get('chapterNo',0))}章" for c in chapter_list]
    )
    # 总榜列用粉色表头，章节列用紫色背景区分
    head_bg_list = (
        [C_HEAD_BG] * (3 + total_pred_count)
        + [C_WL_BG] * chapter_count
    )
    # 由于 _sk_draw_row 只支持单一 bg，先画一整行粉色再局部覆盖章节列
    draw_row(y, head_cells, C_HEAD_BG,
             [f_header] * len(head_cells), [C_HEAD_FG] * len(head_cells),
             h=HEADER_H, right_from=-1)

    # 章节列背景覆盖（紫色）
    chapter_col_start_x = x0 + 10 + sum(draw_col_ws[:3 + total_pred_count])
    chapter_col_end_x   = x1 - 10
    if chapter_count:
        d.rounded_rectangle(
            (chapter_col_start_x, y, chapter_col_end_x, y + HEADER_H - 2),
            radius=14, fill=C_WL_BG,
        )
        d = ImageDraw.Draw(img)
        # 重新绘章节列文字及角色图标
        cx = chapter_col_start_x
        for idx, chapter in enumerate(chapter_list):
            chapter_no = int(chapter.get('chapterNo', 0))
            cid        = int(chapter.get('gameCharacterId', 0))
            cw         = draw_col_ws[3 + total_pred_count + idx]
            icon = _load_wl_chara_icon(cid, ICON_SIZE) if cid else None
            if icon:
                icon_x = cx + (cw - ICON_SIZE) // 2
                icon_y = y + (HEADER_H - ICON_SIZE) // 2 - 6
                img.paste(icon, (icon_x, icon_y), icon)
                d = ImageDraw.Draw(img)
                d.text((cx + cw // 2, y + HEADER_H - 10),
                       f"第{chapter_no}章", font=f_small,
                       fill=C_HEAD_FG, anchor="mm")
            else:
                d.text((cx + cw // 2, y + HEADER_H // 2),
                       f"第{chapter_no}章", font=f_header,
                       fill=C_HEAD_FG, anchor="mm")
            cx += cw
    y += HEADER_H

    # --- 数据行 ---
    for i, rank in enumerate(display_ranks):
        bg = C_ROW_ODD if i % 2 == 0 else C_ROW_EVEN
        cells = [f"T{rank}", score_strs.get(rank, "-"), speed_strs.get(rank, "-")]
        fgs   = [C_TEXT, C_TEXT, C_TEXT]
        for fc in total_forecasts:
            rd = fc.rank_data.get(rank)
            cells.append(f"{rd.final_score/10000:.2f}万" if (rd and rd.final_score) else "-")
            fgs.append(C_TEXT)
        for chapter in chapter_list:
            chapter_no = int(chapter.get('chapterNo', 0))
            fc = chapter_forecasts.get(chapter_no)
            rd = fc.rank_data.get(rank) if fc else None
            cells.append(f"{rd.final_score/10000:.2f}万" if (rd and rd.final_score) else "-")
            fgs.append(C_TEXT)
        draw_row(y, cells, bg, [f_body] * len(cells), fgs)
        y += ROW_H

    # --- 预测时间行 ---
    time_cells = ["预测时间", "-", "-"]
    time_fgs   = [C_HEAD_FG, C_HEAD_FG, C_HEAD_FG]
    for fc in total_forecasts:
        if fc and fc.forecast_ts:
            t_str, expired = _fmt_time_delta(fc.forecast_ts)
            time_cells.append(t_str + (" ⚠" if expired else ""))
            time_fgs.append(C_WARN if expired else C_HEAD_FG)
        else:
            time_cells.append("-")
            time_fgs.append(C_HEAD_FG)
    for chapter in chapter_list:
        chapter_no = int(chapter.get('chapterNo', 0))
        fc = chapter_forecasts.get(chapter_no)
        if fc and fc.forecast_ts:
            t_str, expired = _fmt_time_delta(fc.forecast_ts)
            time_cells.append(t_str + (" ⚠" if expired else ""))
            time_fgs.append(C_WARN if expired else C_HEAD_FG)
        else:
            time_cells.append("-")
            time_fgs.append(C_HEAD_FG)
    draw_row(y, time_cells, C_HEAD_BG,
             [f_header, f_body, f_body] + [f_body] * (total_pred_count + chapter_count),
             time_fgs, h=HEADER_H)
    y += HEADER_H + GAP

    # --- Footer ---
    source_names_str = " / ".join(dict.fromkeys(total_source_names)) if total_source_names else "无"
    footer = f"总榜预测源：{source_names_str}；单榜仅本地预测；实线来自本地排名记录，请谨慎参考"
    _sk_chip(d, (x0 + 10, y + 2, x1 - 10, y + FOOTER_H - 2),
             _sk_fit_text(d, footer, f_small, content_w - 60), f_small,
             fill=(255, 255, 255), outline=(255, 255, 255),
             text_fill=C_MUTED, radius=14, anchor="lm")

    # 总榜与章节列之间的分割线
    if total_pred_count and chapter_count:
        sep_x = x0 + 10 + sum(draw_col_ws[:3 + total_pred_count])
        d.rounded_rectangle(
            (sep_x - 1, OUT_PAD + TITLE_H + GAP + 8, sep_x + 2, y - 8),
            radius=1, fill=(180, 100, 160),
        )
    return img


async def compose_forecast_image(
    region: str,
    event_id: int,
    event_name: str,
    forecasts: List[ForecastData],
    live_scores: Dict[int, int],    # rank -> 最新分数
    live_speeds: Dict[int, float],  # rank -> 时速（万/h），None 表示无数据
    display_ranks: Optional[List[int]] = None,
) -> Image.Image:
    """用 PIL 绘制带条纹的预测表格图片。"""
    font_path      = str(FONT_PATH / "SourceHanSansCN-Medium.otf")
    font_bold_path = str(FONT_PATH / "SourceHanSansCN-Bold.otf")
    f_title  = _sk_font(font_bold_path, 22)
    f_header = _sk_font(font_bold_path, 18)
    f_body   = _sk_font(font_path, 18)
    f_small  = _sk_font(font_path, 14)

    if display_ranks is None:
        forecast_ranks: set = set()
        for fc in forecasts:
            if fc.rank_data:
                forecast_ranks.update(fc.rank_data.keys())
        display_ranks = sorted(set(LIVE_RANKS) | (forecast_ranks & set(LIVE_RANKS)))
    else:
        display_ranks = sorted({int(r) for r in display_ranks if int(r) > 0})

    source_names = [FORECAST_SOURCES.get(fc.source, {}).get('name', fc.source) for fc in forecasts]

    PAD_X    = 20
    ROW_H    = 40
    HEADER_H = 44
    TITLE_H  = 58
    FOOTER_H = 36
    OUT_PAD  = 18
    GAP      = 8

    _tmp = Image.new("RGB", (1, 1))
    _d   = ImageDraw.Draw(_tmp)

    def tw(text: str, font) -> int:
        return int(_d.textlength(text, font=font))

    col0_cands = [("排名", f_header), ("预测时间", f_header)] + [(f"T{r}", f_body) for r in display_ranks]
    col0_w = max(tw(t, f) for t, f in col0_cands) + PAD_X * 2

    score_strs = {r: (f"{s / 10000:.2f}万" if s else "-") for r, s in live_scores.items()}
    col1_cands = [("当前分数", f_header), ("-", f_body)] + [(score_strs.get(r, "-"), f_body) for r in display_ranks]
    col1_w = max(tw(t, f) for t, f in col1_cands) + PAD_X * 2

    speed_strs = {}
    for r, spd in live_speeds.items():
        speed_strs[r] = f"{spd:.1f}万/h" if spd is not None else "-"
    col2_cands = [("时速", f_header), ("-", f_body)] + [(speed_strs.get(r, "-"), f_body) for r in display_ranks]
    col2_w = max(tw(t, f) for t, f in col2_cands) + PAD_X * 2

    pred_col_ws = []
    for idx, fc in enumerate(forecasts):
        cands = [(source_names[idx], f_header)]
        for rank in display_ranks:
            rd = fc.rank_data.get(rank)
            cands.append((f"{rd.final_score / 10000:.2f}万" if (rd and rd.final_score) else "-", f_body))
        if fc.forecast_ts:
            t_str, expired = _fmt_time_delta(fc.forecast_ts)
            cands.append((t_str + (" ⚠" if expired else ""), f_body))
        else:
            cands.append(("-", f_body))
        pred_col_ws.append(max(tw(t, f) for t, f in cands) + PAD_X * 2)

    all_col_ws = [col0_w, col1_w, col2_w] + pred_col_ws
    table_w = sum(all_col_ws)

    title_text = f"【{region.upper()}-{event_id}】{event_name}  榜线预测"
    content_w = max(table_w, tw(title_text, f_title) + PAD_X * 2, 620)
    extra_w = max(0, content_w - table_w)
    draw_col_ws = all_col_ws[:]
    draw_col_ws[-1] += extra_w

    total_w = content_w + OUT_PAD * 2
    total_h = OUT_PAD * 2 + TITLE_H + GAP + HEADER_H + ROW_H * len(display_ranks) + HEADER_H + FOOTER_H + GAP * 2

    C_HEAD_BG  = (230, 140, 170)
    C_HEAD_FG  = (255, 255, 255)
    C_ROW_ODD  = (255, 255, 255)
    C_ROW_EVEN = (255, 240, 248)
    C_TEXT     = (50, 30, 50)
    C_WARN     = (200, 60, 60)
    C_MUTED    = (120, 80, 100)

    img = _sk_gradient_bg(total_w, total_h)
    _sk_title_panel(img, title_text, f_title, "榜线预测", f_small, pad=OUT_PAD, height=TITLE_H)
    d = ImageDraw.Draw(img)

    x0 = OUT_PAD
    x1 = total_w - OUT_PAD
    y = OUT_PAD + TITLE_H + GAP
    _sk_panel(img, (x0, y - 4, x1, total_h - OUT_PAD), radius=20, fill=(255, 255, 255, 140), outline=(255, 255, 255, 210))
    d = ImageDraw.Draw(img)

    def draw_row(row_y, cells, bg, fonts, fg_list, h=ROW_H, right_from=1):
        _sk_draw_row(
            d,
            (x0 + 10, row_y, x1 - 10, row_y + h - 2),
            cells,
            draw_col_ws,
            fonts,
            fg_list,
            bg=bg,
            outline=(255, 255, 255),
            radius=14,
            pad_x=PAD_X,
            align_right_from=right_from,
        )

    head_cells = ["排名", "当前分数", "时速"] + source_names
    draw_row(y, head_cells, C_HEAD_BG, [f_header] * len(head_cells), [C_HEAD_FG] * len(head_cells), h=HEADER_H, right_from=-1)
    y += HEADER_H

    for i, rank in enumerate(display_ranks):
        bg = C_ROW_ODD if i % 2 == 0 else C_ROW_EVEN
        cells = [f"T{rank}", score_strs.get(rank, "-"), speed_strs.get(rank, "-")]
        fgs = [C_TEXT, C_TEXT, C_TEXT]
        for fc in forecasts:
            rd = fc.rank_data.get(rank)
            cells.append(f"{rd.final_score / 10000:.2f}万" if (rd and rd.final_score) else "-")
            fgs.append(C_TEXT)
        draw_row(y, cells, bg, [f_body] * len(cells), fgs)
        y += ROW_H

    time_cells = ["预测时间", "-", "-"]
    time_fgs   = [C_HEAD_FG, C_HEAD_FG, C_HEAD_FG]
    for fc in forecasts:
        if fc.forecast_ts:
            t_str, expired = _fmt_time_delta(fc.forecast_ts)
            time_cells.append(t_str + (" ⚠" if expired else ""))
            time_fgs.append(C_WARN if expired else C_HEAD_FG)
        else:
            time_cells.append("-")
            time_fgs.append(C_HEAD_FG)
    draw_row(y, time_cells, C_HEAD_BG, [f_header, f_body, f_body] + [f_body] * len(forecasts), time_fgs, h=HEADER_H)
    y += HEADER_H + GAP

    footer_text = "预测来自本地缓存/多源接口，实时分数来自本地排名记录，请谨慎参考"
    _sk_chip(d, (x0 + 10, y + 2, x1 - 10, y + FOOTER_H - 2), _sk_fit_text(d, footer_text, f_small, content_w - 60), f_small,
             fill=(255, 255, 255), outline=(255, 255, 255), text_fill=C_MUTED, radius=14, anchor="lm")

    sep_x = x0 + 10 + sum(draw_col_ws[:3])
    d.rounded_rectangle((sep_x - 1, OUT_PAD + TITLE_H + GAP + 8, sep_x + 2, y - 8), radius=1, fill=(205, 135, 165))
    return img


def _get_event_master(event_id: int, pjsk_type: int = 0) -> Optional[dict]:
    try:
        events = load_master_data('events.json', pjsk_type)
        base_id = _base_event_id(event_id)
        for event in events:
            if isinstance(event, dict) and event.get('id') == base_id:
                return event
    except Exception as e:
        logger.debug(f"[预测] 获取活动信息失败: {e}")
    return None


async def _get_event_name(event_id: int, pjsk_type: int = 0) -> str:
    event = _get_event_master(event_id, pjsk_type)
    if event:
        return event.get('name') or f'Event {event_id}'
    return f'Event {event_id}'


def _resolve_forecast_display_ranks(forecasts: List[ForecastData]) -> List[int]:
    """合并配置档位与预测缓存档位，生成 ycx 总览展示行。"""
    ranks = set(rank_levels)
    ranks.update(LIVE_RANKS)
    for fc in forecasts:
        ranks.update(fc.rank_data.keys())
    return sorted(r for r in ranks if isinstance(r, int) and r > 0)


async def _get_live_rank_data(region: str, event_id: int, ranks: List[int]) -> tuple[Dict[int, int], Dict[int, float]]:
    import datetime

    from .._sk_sql import query_first_ranking_after, query_latest_ranking

    if not ranks:
        return {}, {}

    now = datetime.datetime.now()
    one_hour_ago = now - datetime.timedelta(hours=1)
    latest = await query_latest_ranking(region, event_id, ranks)
    if not latest:
        return {}, {}

    older = await query_first_ranking_after(region, event_id, one_hour_ago, ranks)

    live_scores = {row.rank: row.score for row in latest}
    live_speeds = {}
    for row in latest:
        old = next((item for item in older if item.rank == row.rank), None)
        speed = None
        if old:
            dtime = (row.time - old.time).total_seconds()
            if dtime > 0:
                speed = (row.score - old.score) * 3600 / dtime / 10000
        live_speeds[row.rank] = speed
    return live_scores, live_speeds


def _default_forecast_curve_ranks() -> List[int]:
    return [100, 500, 1000, 5000, 10000]


def _parse_forecast_curve_args(arg: str, current_event_id: int) -> tuple[int, List[int]]:
    """解析 ycx 曲线参数：支持 [活动ID] [排名...] 或仅 [排名...]。"""
    nums = [int(x) for x in re.findall(r'\d+', arg or '')]
    if not nums:
        return current_event_id, _default_forecast_curve_ranks()

    event_id = current_event_id
    ranks = nums
    known_ranks = set(rank_levels) | set(LIVE_RANKS)
    # 单参数优先按常见档位解释；不属于档位的数字（如 203）按活动 ID 解释。
    # 多参数时，首个数字若不是常见档位，则作为活动 ID，其余作为档位。
    if len(nums) >= 2 and nums[0] not in known_ranks:
        event_id = nums[0]
        ranks = nums[1:]
    elif len(nums) == 1:
        if nums[0] not in known_ranks:
            event_id = nums[0]
            ranks = _default_forecast_curve_ranks()
        else:
            ranks = nums

    ranks = [r for r in ranks if r > 0]
    return event_id, ranks[:8] or [100]


def _get_event_time_range(event_id: int, pjsk_type: int, history: Dict[int, List[tuple]]) -> tuple[int, int]:
    """返回曲线横轴起止秒时间戳。"""
    event = _get_event_master(event_id, pjsk_type)
    if event and event.get('startAt') and event.get('aggregateAt'):
        return int(event['startAt'] / 1000), int(event['aggregateAt'] / 1000)

    timestamps = [ts for points in history.values() for ts, _ in points]
    if timestamps:
        return min(timestamps), max(timestamps)
    now = int(time.time())
    return now - 3600, now


def _format_event_remaining(event_id: int, pjsk_type: int) -> str:
    event = _get_event_master(event_id, pjsk_type)
    if not event or not event.get('aggregateAt'):
        return "未知"
    remain = int(event['aggregateAt'] / 1000 - time.time())
    if remain <= 0:
        return "已结束"
    days, rem = divmod(remain, 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    if days > 0:
        return f"{days}天{hours}小时"
    return f"{hours}小时{minutes}分钟"


async def _get_rank_history_data(region: str, event_id: int, ranks: List[int]) -> Dict[int, List[tuple]]:
    """从本地排名数据库读取真实历史曲线点，返回 rank -> [(ts, score)]。"""
    from .._sk_sql import query_ranking

    result: Dict[int, List[tuple]] = {}
    for rank in ranks:
        rows = await query_ranking(region, event_id, rank=rank, order_by='ts ASC')
        if rows:
            result[rank] = [(int(row.time.timestamp()), row.score) for row in rows]
    return result


def compose_forecast_curve_image(
    region: str,
    event_id: int,
    event_name: str,
    ranks: List[int],
    history: Dict[int, List[tuple]],
    forecasts: List[ForecastData],
    pjsk_type: int = 0,
    remain_text: Optional[str] = None,
) -> Image.Image:
    """绘制 tsugu 风格的真实历史曲线 + 预测曲线。"""
    font_path = str(FONT_PATH / "SourceHanSansCN-Medium.otf")
    font_bold_path = str(FONT_PATH / "SourceHanSansCN-Bold.otf")
    f_title = _sk_font(font_bold_path, 24)
    f_label = _sk_font(font_path, 16)
    f_small = _sk_font(font_path, 13)

    W, H = 1180, 700
    M_L, M_R, M_T, M_B = 90, 330, 80, 105
    plot_w = W - M_L - M_R
    plot_h = H - M_T - M_B

    start_ts, end_ts = _get_event_time_range(event_id, pjsk_type, history)
    if end_ts <= start_ts:
        end_ts = start_ts + 3600
    remain_text = remain_text or _format_event_remaining(event_id, pjsk_type)

    current_scores: Dict[int, int] = {}
    current_speeds: Dict[int, Optional[float]] = {}
    for rank, points in history.items():
        if not points:
            continue
        current_scores[rank] = points[-1][1]
        latest_ts, latest_score = points[-1]
        older = next(((ts, score) for ts, score in points if ts >= latest_ts - 3600), None)
        current_speeds[rank] = None
        if older and latest_ts > older[0]:
            current_speeds[rank] = (latest_score - older[1]) * 3600 / (latest_ts - older[0]) / 10000

    latest_history_ts = max((points[-1][0] for points in history.values() if points), default=start_ts)
    now_ts = min(max(latest_history_ts, start_ts), end_ts)

    source_forecast_points: Dict[tuple, List[tuple]] = {}
    latest_forecasts: Dict[int, List[tuple]] = {rank: [] for rank in ranks}
    source_names = []
    for fc in forecasts:
        source_name = FORECAST_SOURCES.get(fc.source, {}).get('name', fc.source)
        source_names.append(source_name)
        for rank in ranks:
            rd = fc.rank_data.get(rank)
            if not rd:
                continue
            points: List[tuple] = []
            if getattr(rd, 'future_rankings', None):
                points.extend((item.ts, item.score) for item in rd.future_rankings)
            elif rd.history_final_score:
                points.extend((item.ts, item.score) for item in rd.history_final_score)
            if points:
                min_pred_ts = now_ts if fc.source == 'local' else start_ts
                source_forecast_points[(rank, fc.source)] = sorted({
                    (int(ts), int(score)) for ts, score in points if min_pred_ts <= int(ts) <= end_ts
                })
            if rd.final_score:
                latest_forecasts.setdefault(rank, []).append((
                    fc.source,
                    source_name,
                    int(fc.forecast_ts or end_ts),
                    int(rd.final_score),
                ))

    all_scores = []
    for points in history.values():
        all_scores.extend(score for _, score in points)
    for points in source_forecast_points.values():
        all_scores.extend(score for _, score in points)
    for items in latest_forecasts.values():
        all_scores.extend(score for _, _, _, score in items)
    if not all_scores:
        all_scores = [0, 10000]
    min_score = max(0, min(all_scores) * 0.95)
    max_score = max(all_scores) * 1.05
    if max_score <= min_score:
        max_score = min_score + 10000

    def sx(ts: int) -> int:
        return int(M_L + (ts - start_ts) / (end_ts - start_ts) * plot_w)

    def sy(score: int) -> int:
        return int(M_T + (max_score - score) / (max_score - min_score) * plot_h)

    def fmt_score(score: float) -> str:
        return f"{score / 10000:.0f}万"

    def fit_text(text: str, font, max_width: int) -> str:
        """截断文字，避免右侧状态栏/底部说明超出图片边界。"""
        if d.textlength(text, font=font) <= max_width:
            return text
        ellipsis = "…"
        while text and d.textlength(text + ellipsis, font=font) > max_width:
            text = text[:-1]
        return text + ellipsis if text else ellipsis

    real_colors = [
        (210, 45, 95), (35, 115, 210), (45, 150, 85), (220, 125, 35),
        (120, 75, 200), (30, 160, 170), (200, 70, 175), (95, 95, 95),
    ]
    forecast_colors = [
        (130, 35, 210), (0, 180, 210), (245, 70, 45), (95, 190, 35),
        (255, 80, 170), (155, 95, 25), (40, 200, 145), (35, 35, 35),
    ]

    img = _sk_gradient_bg(W, H)
    d = ImageDraw.Draw(img)

    title = f"【{region.upper()}-{event_id}】{event_name}  ycx曲线"
    _sk_title_panel(img, title, f_title, f"剩余 {remain_text}", f_small, pad=18, height=54)
    d = ImageDraw.Draw(img)

    _sk_panel(img, (M_L - 12, M_T - 12, M_L + plot_w + 12, M_T + plot_h + 12), radius=24, fill=(255, 255, 255, 222), outline=(255, 255, 255, 230))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([(M_L, M_T), (M_L + plot_w, M_T + plot_h)], radius=18, fill=(255, 255, 255), outline=(235, 210, 226), width=1)

    # 网格与 Y 轴标签
    for i in range(6):
        y = M_T + int(plot_h * i / 5)
        score = max_score - (max_score - min_score) * i / 5
        d.line([(M_L, y), (M_L + plot_w, y)], fill=(240, 220, 230), width=1)
        d.text((M_L - 10, y), fmt_score(score), font=f_small, fill=(100, 80, 95), anchor="rm")

    duration = max(1, end_ts - start_ts)
    whole_days = int(duration // 86400)
    for day in range(whole_days + 1):
        ts = start_ts + day * 86400
        if ts > end_ts:
            break
        x = sx(ts)
        d.line([(x, M_T), (x, M_T + plot_h)], fill=(245, 230, 238), width=1)
        label = "0天" if day == 0 else f"第{day}天"
        d.text((x, M_T + plot_h + 18), label, font=f_small, fill=(100, 80, 95), anchor="mm")
    if end_ts > start_ts + whole_days * 86400:
        x = sx(end_ts)
        d.line([(x, M_T), (x, M_T + plot_h)], fill=(245, 230, 238), width=1)
        d.text((x, M_T + plot_h + 18), "结束", font=f_small, fill=(100, 80, 95), anchor="mm")

    now_x = sx(now_ts)
    d.line([(now_x, M_T), (now_x, M_T + plot_h)], fill=(170, 110, 140), width=2)
    d.text((now_x + 4, M_T + 8), "当前", font=f_small, fill=(150, 80, 115), anchor="la")

    d.text((M_L + plot_w // 2, H - 45), f"活动经过时间（剩余 {remain_text}）", font=f_label, fill=(80, 60, 75), anchor="mm")
    d.text((28, M_T + plot_h // 2), "活动分数", font=f_label, fill=(80, 60, 75), anchor="mm")

    def draw_polyline(points: List[tuple], color: tuple, width: int = 3):
        if len(points) < 2:
            return
        pts = [(sx(ts), sy(score)) for ts, score in points if start_ts <= ts <= end_ts]
        if len(pts) >= 2:
            d.line(pts, fill=color, width=width, joint="curve")

    def draw_dash_segment(x1: int, y1: int, x2: int, y2: int, color: tuple, width: int = 3, dash: int = 8, gap: int = 8):
        dist = max(1, ((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5)
        pattern = max(2, dash + gap)
        steps = max(1, int(dist // pattern * 2))
        for i in range(steps):
            a = i / steps
            b = min(1.0, a + dash / max(dist, 1))
            d.line([(x1 + (x2 - x1) * a, y1 + (y2 - y1) * a),
                    (x1 + (x2 - x1) * b, y1 + (y2 - y1) * b)], fill=color, width=width)

    def draw_dashed(points: List[tuple], color: tuple, width: int = 3, dash: int = 8, gap: int = 8):
        if len(points) < 2:
            return
        pts = [(sx(ts), sy(score)) for ts, score in points if start_ts <= ts <= end_ts]
        if len(pts) < 2:
            return
        for p1, p2 in zip(pts, pts[1:]):
            x1, y1 = p1
            x2, y2 = p2
            draw_dash_segment(x1, y1, x2, y2, color, width=width, dash=dash, gap=gap)

    def draw_value_label(x: int, y: int, text: str, color: tuple):
        plot_left = M_L + 2
        plot_right = M_L + plot_w - 4
        label = text
        tw = int(d.textlength(label, font=f_small))
        max_label_w = plot_right - plot_left - 10
        if tw > max_label_w:
            label = fit_text(text, f_small, max_label_w)
            tw = int(d.textlength(label, font=f_small))

        # 优先放在线尾右侧；右侧放不下时将框整体移到线尾左侧，而不是截成省略号。
        tx = x + 6
        if tx + tw + 5 > plot_right:
            tx = x - tw - 10
        tx = min(max(tx, plot_left), plot_right - tw - 5)
        ty = min(max(y - 11, M_T + 2), M_T + plot_h - 18)
        d.rounded_rectangle([(tx - 3, ty - 1), (tx + tw + 5, ty + 16)], radius=4, fill=(255, 255, 255), outline=color, width=1)
        d.text((tx, ty + 7), label, font=f_small, fill=color, anchor="lm")

    source_styles = {
        'local': (3, 9, 3),
        '33kit': (4, 8, 3),
        'moe': (6, 10, 3),
        'sekarun': (2, 8, 3),
    }

    for idx, rank in enumerate(ranks):
        real_color = real_colors[idx % len(real_colors)]
        forecast_color = forecast_colors[idx % len(forecast_colors)]
        real_points = history.get(rank, [])
        draw_polyline(real_points, real_color)
        if real_points:
            x, y = sx(real_points[-1][0]), sy(real_points[-1][1])
            d.ellipse([(x - 4, y - 4), (x + 4, y + 4)], fill=real_color)
            draw_value_label(x, y, f"T{rank} {real_points[-1][1] / 10000:.1f}万", real_color)

        for source_idx, fc in enumerate(forecasts):
            pred_hist = source_forecast_points.get((rank, fc.source), [])
            dash, gap, width = source_styles.get(fc.source, (3 + source_idx, 9, 3))
            if len(pred_hist) >= 2:
                draw_dashed(pred_hist, forecast_color, width=width, dash=dash, gap=gap)
                px, py = sx(pred_hist[-1][0]), sy(pred_hist[-1][1])
                draw_value_label(px, py, f"T{rank}预 {pred_hist[-1][1] / 10000:.1f}万", forecast_color)
            elif len(pred_hist) == 1:
                px, py = sx(pred_hist[0][0]), sy(pred_hist[0][1])
                d.rectangle([(px - 4, py - 4), (px + 4, py + 4)], fill=forecast_color)
                draw_value_label(px, py, f"T{rank}预 {pred_hist[0][1] / 10000:.1f}万", forecast_color)

        for source, _, pred_ts, pred_score in latest_forecasts.get(rank, []):
            if (rank, source) in source_forecast_points:
                continue
            pred_ts = min(max(pred_ts, start_ts), end_ts)
            px, py = sx(pred_ts), sy(pred_score)
            d.rectangle([(px - 4, py - 4), (px + 4, py + 4)], outline=forecast_color, width=2)
            draw_value_label(px, py, f"T{rank}预 {pred_score / 10000:.1f}万", forecast_color)

    # 图例 / 状态栏
    panel_x = M_L + plot_w + 24
    panel_right = W - 24
    panel_w = panel_right - panel_x
    _sk_panel(img, (panel_x - 12, M_T - 12, panel_right, M_T + plot_h + 12), radius=22, fill=(255, 255, 255, 205), outline=(255, 255, 255, 230))
    d = ImageDraw.Draw(img)
    lx, ly = panel_x, M_T
    _sk_chip(d, (lx, ly - 3, lx + 76, ly + 23), "图例", f_label, fill=(255, 246, 251), text_fill=(60, 45, 60))
    ly += 30
    for idx, rank in enumerate(ranks):
        real_color = real_colors[idx % len(real_colors)]
        forecast_color = forecast_colors[idx % len(forecast_colors)]
        d.line([(lx, ly + 5), (lx + 42, ly + 5)], fill=real_color, width=4)
        draw_dash_segment(lx, ly + 13, lx + 42, ly + 13, forecast_color, width=3, dash=3, gap=9)
        text = fit_text(f"T{rank} 上实线=真实 / 下虚线=预测", f_small, panel_w - 52)
        d.text((lx + 52, ly + 9), text, font=f_small, fill=(60, 45, 60), anchor="lm")
        ly += 24

    ly += 8
    d.text((lx, ly), "预测源线型", font=f_label, fill=(60, 45, 60), anchor="la")
    ly += 24
    for source_idx, source in enumerate(dict.fromkeys(fc.source for fc in forecasts)):
        name = FORECAST_SOURCES.get(source, {}).get('name', source)
        dash, gap, width = source_styles.get(source, (8 + source_idx * 2, 6, 2))
        draw_dash_segment(lx, ly + 8, lx + 42, ly + 8, (80, 80, 80), width=width, dash=dash, gap=gap)
        d.text((lx + 52, ly + 8), fit_text(name, f_small, panel_w - 52), font=f_small, fill=(60, 45, 60), anchor="lm")
        ly += 18
        if ly > M_T + plot_h - 230:
            d.text((lx, ly + 8), "…", font=f_small, fill=(100, 75, 90), anchor="la")
            ly += 18
            break

    ly += 12
    d.text((lx, ly), fit_text(f"剩余 {remain_text}", f_label, panel_w), font=f_label, fill=(120, 70, 95), anchor="la")
    ly += 28
    d.text((lx, ly), "当前状态", font=f_label, fill=(60, 45, 60), anchor="la")
    ly += 24
    for rank in ranks[:8]:
        if ly > H - 90:
            break
        score = current_scores.get(rank)
        speed = current_speeds.get(rank)
        preds = latest_forecasts.get(rank, [])
        score_text = f"{score / 10000:.1f}万" if score else "-"
        speed_text = f"{speed:.1f}万/h" if speed is not None else "-"
        header = fit_text(f"T{rank} {score_text}  时速{speed_text}", f_small, panel_w)
        d.text((lx, ly), header, font=f_small, fill=(60, 45, 60), anchor="la")
        ly += 18
        if preds:
            for _, name, _, pred_score in preds[:4]:
                if ly > H - 64:
                    break
                pred_line = fit_text(f"- {name}: {pred_score / 10000:.1f}万", f_small, panel_w - 12)
                d.text((lx + 12, ly), pred_line, font=f_small, fill=(100, 75, 90), anchor="la")
                ly += 16
            if len(preds) > 4 and ly <= H - 64:
                d.text((lx + 12, ly), f"…另 {len(preds) - 4} 个来源", font=f_small, fill=(100, 75, 90), anchor="la")
                ly += 16
        else:
            d.text((lx + 12, ly), "- 预测: -", font=f_small, fill=(100, 75, 90), anchor="la")
            ly += 16
        ly += 6

    footer = "预测源：" + (" / ".join(dict.fromkeys(source_names)) if source_names else "无")
    footer += "；实线为真实历史，高对比虚线为预测，本地预测仅显示当前之后"
    _sk_chip(d, (24, H - 34, W - 24, H - 8), fit_text(footer, f_small, W - 72), f_small,
             fill=(255, 255, 255), outline=(255, 255, 255), text_fill=(120, 80, 100), anchor="lm")
    return img


@pjsk_pred_query.handle()
@cn_pred_query.handle()
@tw_pred_query.handle()
async def _(matcher: Matcher, event: MessageEvent, msg: Message = CommandArg(), cmd: Tuple[str, ...] = Command()):
    pjsk_type = get_pjsk_type(cmd[0])
    region = SERVER_MAP.get(pjsk_type, 'jp')
    arg = msg.extract_plain_text().strip()

    event_data = currentevent(pjsk_type)
    event_id = event_data.get('id', 0)
    cmd_name = cmd[0].lower()
    curve_keywords = ('ycx', 'lsycx', '曲线')
    should_draw_curve = any(keyword in cmd_name for keyword in curve_keywords)
    # ycx 100 / ycx 1000 表示当前活动的 T100/T1000 曲线，不是活动 100/1000。
    # 只有表格型 sk预测/skp 的纯数字参数才继续作为活动 ID。
    if is_number(arg) and not should_draw_curve:
        event_id = int(arg)
    if not event_id:
        await matcher.finish('未找到可查询的活动')

    base_event_id = _base_event_id(event_id)
    event_name = await _get_event_name(base_event_id, pjsk_type)

    try:
        forecasts = await get_forecast_data_cached(region, base_event_id)
        if not forecasts:
            await matcher.send('本地暂无预测缓存，正在尝试实时获取预测数据，请稍等...')
            forecasts = await get_forecast_data(region, base_event_id)
        if not forecasts:
            await matcher.finish(f'{region.upper()} 活动 {base_event_id} 暂无可用预测数据')

        display_ranks = _resolve_forecast_display_ranks(forecasts)

        if should_draw_curve:
            nums = [int(x) for x in re.findall(r'\d+', arg or '')]
            if 'all' in cmd_name or 'lsycx' in cmd_name:
                curve_ranks = _default_forecast_curve_ranks()
            elif nums:
                known_ranks = set(rank_levels) | set(LIVE_RANKS) | set(display_ranks)
                if len(nums) >= 2 and nums[0] not in known_ranks:
                    curve_ranks = nums[1:]
                elif len(nums) == 1 and nums[0] not in known_ranks:
                    curve_ranks = _default_forecast_curve_ranks()
                else:
                    curve_ranks = nums
            else:
                curve_ranks = _default_forecast_curve_ranks()
            curve_ranks = [rank for rank in curve_ranks if rank > 0][:8] or _default_forecast_curve_ranks()
            history = await _get_rank_history_data(region, base_event_id, curve_ranks)
            remain_text = _format_event_remaining(base_event_id, pjsk_type)
            pic = await run_pjsk_thread(compose_forecast_curve_image, region, base_event_id, event_name, curve_ranks, history, forecasts, pjsk_type, remain_text)
            await matcher.finish(image(b64=pic2b64(pic)))
            return

        # 非曲线模式：检查是否为 WL 活动，若是则自动展示总榜 + 各章节单榜预测
        wl_chapters = await _get_wl_chapters_for_query(region, base_event_id, pjsk_type)
        if wl_chapters:
            chapter_forecasts = await _get_wl_chapter_forecast_data(region, base_event_id, wl_chapters)
            live_scores, live_speeds = await _get_live_rank_data(region, base_event_id, display_ranks)
            # 合并总榜预测档位与各章节已有档位，扩充展示行
            all_chapter_ranks: set = set()
            for fc in chapter_forecasts.values():
                if fc:
                    all_chapter_ranks.update(fc.rank_data.keys())
            wl_display_ranks = sorted(
                set(display_ranks) | (all_chapter_ranks & set(LIVE_RANKS))
            )
            if not wl_display_ranks:
                wl_display_ranks = display_ranks
            pic = await compose_wl_forecast_image(
                region=region,
                base_event_id=base_event_id,
                event_name=event_name,
                chapters=wl_chapters,
                total_forecasts=forecasts,
                chapter_forecasts=chapter_forecasts,
                live_scores=live_scores,
                live_speeds=live_speeds,
                display_ranks=wl_display_ranks,
            )
        else:
            live_scores, live_speeds = await _get_live_rank_data(region, base_event_id, display_ranks)
            pic = await compose_forecast_image(region, base_event_id, event_name, forecasts, live_scores, live_speeds, display_ranks)

        await matcher.finish(image(b64=pic2b64(pic)))
    except (FinishedException, PausedException, RejectedException, StopPropagation):
        raise
    except Exception as e:
        logger.error(f"[预测] 查询 {region} 活动 {base_event_id} 预测失败: {e}", exc_info=True)
        await matcher.finish(f'预测查询失败：{e}')


@pjsk_pred_curve_query.handle()
@cn_pred_curve_query.handle()
@tw_pred_curve_query.handle()
async def _(matcher: Matcher, event: MessageEvent, msg: Message = CommandArg(), cmd: Tuple[str, ...] = Command()):
    pjsk_type = get_pjsk_type(cmd[0])
    region = SERVER_MAP.get(pjsk_type, 'jp')
    current_id = currentevent(pjsk_type).get('id', 0)
    event_id, ranks = _parse_forecast_curve_args(msg.extract_plain_text().strip(), current_id)
    if not event_id:
        await matcher.finish('未找到可查询的活动')

    event_name = await _get_event_name(event_id, pjsk_type)
    try:
        forecasts = await get_forecast_data_cached(region, event_id)
        if not forecasts:
            await matcher.send('本地暂无预测缓存，正在尝试实时获取预测数据，请稍等...')
            forecasts = await get_forecast_data(region, event_id)

        available_ranks = _resolve_forecast_display_ranks(forecasts) if forecasts else rank_levels
        ranks = [rank for rank in ranks if rank in available_ranks or rank <= 100000]
        if not ranks:
            await matcher.finish('没有可绘制的排名档位')

        history = await _get_rank_history_data(region, event_id, ranks)
        if not history and not forecasts:
            await matcher.finish(f'{region.upper()} 活动 {event_id} 暂无历史榜线或预测数据')

        remain_text = _format_event_remaining(event_id, pjsk_type)
        pic = await run_pjsk_thread(compose_forecast_curve_image, region, event_id, event_name, ranks, history, forecasts, pjsk_type, remain_text)
        await matcher.finish(image(b64=pic2b64(pic)))
    except (FinishedException, PausedException, RejectedException, StopPropagation):
        raise
    except Exception as e:
        logger.error(f"[预测曲线] 查询 {region} 活动 {event_id} 曲线失败: {e}", exc_info=True)
        await matcher.finish(f'预测曲线查询失败：{e}')


async def pjsk_pred_update(pjsk_type: int = 0):
    global pred_score_json
    global event_id
    resp_text = ""
    try:
        # 因为AsyncHttpx封装的异步httpx处理不了跳转url，所以此处只能用阻塞式网络请求orz
        tmp_json = await asyncio.to_thread(_blocking_get_json, pred_url, headers)

        if tmp_json["status"] == "success":
            pred_score_json = {
                "data": tmp_json["data"],
                "time": tmp_json["data"]["ts"]/1000,
                "id": tmp_json["data"]["eventId"]
            }
            logger.info("[定时任务]:pjsk更新预测线成功！")
        else:
            logger.warning(f"[定时任务]:pjsk更新预测线失败！")
    except Exception as e:
        logger.warning(f"[定时任务]:pjsk更新预测线失败！Error:{e}")


# 自动更新多源预测数据
@scheduler.scheduled_job(
    "interval",
    minutes=20
)
async def _():
    """定时更新多个服务器的多源预测数据"""
    try:
        for pjsk_type in SERVER_MAP.keys():
            server_name = SERVER_MAP[pjsk_type]
            try:
                event_data = currentevent(pjsk_type)
                event_id = event_data['id']
                
                # 获取多源预测数据
                forecasts = await get_forecast_data(server_name, event_id)
                
                if forecasts:
                    logger.info(f"[定时任务]:成功更新 {server_name} 服务器活动 {event_id} 的多源预测数据，共 {len(forecasts)} 个来源")
                else:
                    logger.warning(f"[定时任务]:{server_name} 服务器活动 {event_id} 未获取到预测数据")
            except Exception as e:
                logger.warning(f"[定时任务]:更新 {server_name} 预测数据失败: {e}")
    except Exception as e:
        logger.warning(f"[定时任务]:多源预测更新任务异常: {e}")



# 自动更新活动号
@scheduler.scheduled_job(
    "cron",
    hour=14,
    minute=3
)
async def _():
    for pjsk_type in SERVER_MAP.keys():
        try:
            event_data = currentevent(pjsk_type)
            logger.info(f"[定时任务]:{SERVER_MAP[pjsk_type]} pjsk更新活动号成功，当前活动号: {event_data['id']}！")
        except Exception as e:
            logger.warning(f"[定时任务]:{SERVER_MAP[pjsk_type]} pjsk更新活动号失败！Error:{e}")


def _rankings_from_items(items: Any) -> List['Ranking']:
    """兼容既有调用名，统一使用可容错的榜线解析器。"""
    return rankings_from_items(items)


async def _encode_wl_chapter_rankings(
    rankings_by_character: Dict[int, List['Ranking']],
    event_id: int,
    pjsk_type: int,
) -> Dict[int, List['Ranking']]:
    """把按角色归组的 WL 榜线映射为 encoded_event_id -> rankings。"""
    ret: Dict[int, List['Ranking']] = {}
    if not rankings_by_character:
        return ret

    region = SERVER_MAP.get(pjsk_type, 'jp')
    base_id = _base_event_id(event_id)
    chapters = _get_wl_chapters(base_id, pjsk_type)
    if not chapters:
        chapters = _WL_CHAPTER_CACHE.get((region, base_id), [])
    if not chapters:
        chapters = await _refresh_wl_chapters(base_id, pjsk_type)
    if not chapters:
        logger.warning(
            f"[WL] 缺少 {region}-{base_id} 的权威章节配置，跳过本轮分榜写入"
        )
        return ret

    _cache_wl_chapters(region, base_id, chapters)
    chapter_by_cid = {
        int(chapter.get('gameCharacterId', 0)): chapter
        for chapter in chapters
        if chapter.get('gameCharacterId')
    }

    for cid, rankings in rankings_by_character.items():
        chapter = chapter_by_cid.get(cid)
        if chapter is None:
            logger.warning(
                f"[WL] {region}-{base_id} 的权威章节配置中不存在角色 {cid}，跳过本轮写入"
            )
            continue
        chapter_no = int(chapter.get('chapterNo', 0))
        if not chapter_no:
            continue
        encoded_id = _wl_encoded_event_id(base_id, chapter_no)
        ret[encoded_id] = merge_rankings(ret.get(encoded_id, []), rankings)
    return ret


async def _extract_wl_chapter_rankings(
    data: dict,
    event_id: int,
    pjsk_type: int,
) -> Dict[int, List['Ranking']]:
    """从兼容接口响应提取 WL 分榜，并映射到章节数据库 ID。"""
    if not isinstance(data, dict):
        return {}

    rankings_by_character: Dict[int, List['Ranking']] = {}
    for group in _wl_group_sources(data):
        try:
            cid = int(group.get('gameCharacterId') or group.get('game_character_id') or 0)
        except (TypeError, ValueError):
            continue
        if not cid:
            continue
        items = (
            group.get('rankings')
            or group.get('borderRankings')
            or group.get('ranking')
            or []
        )
        rankings = _rankings_from_items(items)
        if rankings:
            rankings_by_character[cid] = merge_rankings(
                rankings_by_character.get(cid, []),
                rankings,
            )

    return await _encode_wl_chapter_rankings(
        rankings_by_character,
        event_id,
        pjsk_type,
    )


_SK_HARUKI_INTERVAL_SECONDS = 180
_SK_HARUKI_LAST_RUN = 0.0


async def _record_main_rankings(
    server_name: str,
    event_id: int,
    rankings: List['Ranking'],
    source: str,
) -> int:
    from .._sk_sql import record_rankings

    if not rankings:
        logger.warning(
            f"[SK API] {server_name} event_id={event_id} 未获取到总榜数据（{source}）"
        )
        return 0
    await record_rankings(server_name, event_id, rankings)
    logger.info(
        f"[SK API] {server_name} event_id={event_id} 写入 {len(rankings)} 条总榜（{source}）"
    )
    return len(rankings)


async def _refresh_main_rankings(
    pjsk_type: int,
    server_name: str,
    event_id: int,
    mode: str,
) -> int:
    rankings = await fetch_main_rankings(pjsk_type, event_id, mode)
    return await _record_main_rankings(server_name, event_id, rankings, mode)


async def _write_worldlink_rankings(
    pjsk_type: int,
    server_name: str,
    event_id: int,
    rankings_by_character: Dict[int, List['Ranking']],
    source: str,
) -> int:
    from .._sk_sql import record_rankings

    total = 0
    wl_rankings = await _encode_wl_chapter_rankings(
        rankings_by_character,
        event_id,
        pjsk_type,
    )
    for wl_event_id, chapter_rankings in wl_rankings.items():
        await record_rankings(server_name, wl_event_id, chapter_rankings)
        total += len(chapter_rankings)
        logger.info(
            f"[SK API] {server_name} 更新 WL 单榜成功（{source}）："
            f"event_id={wl_event_id}, count={len(chapter_rankings)}"
        )
    return total


def _worldlink_latest_api_url(region: str) -> str:
    if not WORLDLINK_LATEST_API_URL:
        return ""
    host = "rks-n-cn.exmeaning.com" if region == "cn" else "rks-n.exmeaning.com"
    template = WORLDLINK_LATEST_API_URL.strip()
    if "rks.exmeaning.com/api/public" in template and "worldlink-latest" in template:
        return f"https://{host}/api/public/v2/{region}/worldlink-latest"
    try:
        return template.format(region=region, host=host)
    except KeyError:
        return template.format(region=region)


async def _refresh_worldlink_rankings(
    pjsk_type: int,
    server_name: str,
    event_id: int,
    haruki_snapshot: Optional[HarukiRankingSnapshot] = None,
) -> int:
    from .._gameapi import request_gameapi
    from .._sk_sql import record_rankings

    if not (_is_world_bloom_event(event_id, pjsk_type) or _get_wl_chapters(event_id, pjsk_type)):
        return 0

    if haruki_snapshot and haruki_snapshot.world_bloom_rankings:
        return await _write_worldlink_rankings(
            pjsk_type,
            server_name,
            event_id,
            haruki_snapshot.world_bloom_rankings,
            'Haruki',
        )

    wl_url = _worldlink_latest_api_url(server_name)
    if not wl_url:
        logger.warning(f"[SK API] {server_name} Haruki 未返回 WL 分榜，且未配置回退接口")
        return 0

    try:
        wl_data = await request_gameapi(
            wl_url,
            'GET',
            'json',
        )
    except Exception as exc:
        logger.warning(f"[SK API] {server_name} WL 回退接口请求失败：{exc}")
        return 0

    if not isinstance(wl_data, dict):
        return 0
    total = 0
    wl_rankings = await _extract_wl_chapter_rankings(wl_data, event_id, pjsk_type)
    for wl_event_id, chapter_rankings in wl_rankings.items():
        await record_rankings(server_name, wl_event_id, chapter_rankings)
        total += len(chapter_rankings)
        logger.info(
            f"[SK API] {server_name} 更新 WL 单榜成功（回退接口）："
            f"event_id={wl_event_id}, count={len(chapter_rankings)}"
        )
    return total


async def update_sk_rankings(force: bool = False) -> Dict[str, Any]:
    """新 API 高频更新总榜；Haruki 低频同时补总榜和 WL 分榜。"""
    global _SK_HARUKI_LAST_RUN

    mode = load_api_mode()
    now = time.monotonic()
    fetch_haruki = (
        force
        or _SK_HARUKI_LAST_RUN == 0
        or now - _SK_HARUKI_LAST_RUN >= _SK_HARUKI_INTERVAL_SECONDS
    )
    if fetch_haruki:
        _SK_HARUKI_LAST_RUN = now

    result: Dict[str, Any] = {'mode': mode, 'servers': {}}
    for pjsk_type in [0, 1, 2]:
        server_name = SERVER_MAP.get(pjsk_type, 'jp')
        server_result = {'main': 0, 'worldlink': 0}
        result['servers'][server_name] = server_result
        try:
            event_data = currentevent(pjsk_type)
            current_id = int(event_data['id'])
        except Exception as exc:
            logger.warning(f"[SK API] {server_name} 获取当前活动失败：{exc}")
            continue

        if event_data.get('status') != 'going':
            logger.debug(f"[SK API] {server_name} 当前无进行中活动，跳过榜线请求")
            continue

        is_world_bloom = (
            _is_world_bloom_event(current_id, pjsk_type)
            or bool(_get_wl_chapters(current_id, pjsk_type))
        )
        haruki_snapshot: Optional[HarukiRankingSnapshot] = None
        if fetch_haruki and (mode == 'old' or is_world_bloom):
            try:
                haruki_snapshot = await fetch_haruki_ranking_snapshot(
                    pjsk_type,
                    current_id,
                )
            except Exception as exc:
                logger.warning(f"[SK API] {server_name} Haruki 榜线快照请求失败：{exc}")

        if mode == 'new':
            try:
                server_result['main'] = await _refresh_main_rankings(
                    pjsk_type,
                    server_name,
                    current_id,
                    mode,
                )
            except Exception as exc:
                logger.warning(
                    f"[SK API] {server_name} 主榜线更新失败（{mode}）：{exc}"
                )
        elif fetch_haruki and haruki_snapshot is not None:
            try:
                server_result['main'] = await _record_main_rankings(
                    server_name,
                    current_id,
                    haruki_snapshot.main_rankings,
                    'Haruki',
                )
            except Exception as exc:
                logger.warning(f"[SK API] {server_name} 主榜线写入失败（Haruki）：{exc}")

        if fetch_haruki and is_world_bloom:
            try:
                server_result['worldlink'] = await _refresh_worldlink_rankings(
                    pjsk_type,
                    server_name,
                    current_id,
                    haruki_snapshot,
                )
            except Exception as exc:
                logger.warning(f"[SK API] {server_name} WL 单榜更新失败：{exc}")
    return result


@scheduler.scheduled_job("interval", seconds=30)
async def _():
    await update_sk_rankings()


# 自动更新档线分数（旧 API / Haruki - 调用过多会被 ban，保持注释）
# 如需临时补历史档线，请手动解除注释并控制调用频率。
# @scheduler.scheduled_job(
#     "interval",
#     minutes=3
# )
# async def _():
#     from .._gameapi import request_gameapi, GameApiConfig
#     from .._sk_sql import Ranking, record_rankings
#
#     for pjsk_type in [0, 1, 2]:  # 0=jp, 1=tw, 2=cn
#         server_name = SERVER_MAP.get(pjsk_type, 'jp')
#         try:
#             event_data = currentevent(pjsk_type)
#             current_id = event_data['id']
#
#             config = GameApiConfig(pjsk_type)
#             border_url = config.ranking_border_api_url
#
#             if not border_url:
#                 continue
#
#             border_url = border_url.format(event_id=current_id)
#             border_data = await request_gameapi(border_url, 'GET', 'json')
#
#             rankings = []
#
#             # 只提取 borderRankings 字段，避免与新 API 前百数据重复写入 T100。
#             for item in border_data.get('borderRankings', []):
#                 if item.get('rank') != 100:
#                     rankings.append(Ranking.from_sk(item))
#
#             if rankings:
#                 await record_rankings(server_name, current_id, rankings)
#                 logger.info(f"[定时任务]:pjsk {server_name} 更新档线数据成功（旧API）！")
#         except Exception as e:
#             logger.warning(f"[定时任务]:pjsk {server_name} 更新档线数据失败（旧API）！Error:{e}")


# 自动更新前百分数及档线（旧 API / Haruki - 调用过多会被 ban，保持注释）
# @scheduler.scheduled_job(
#     "interval",
#     seconds=180
# )
# async def _():
#     from .._gameapi import request_gameapi, GameApiConfig
#     from .._sk_sql import Ranking, record_rankings
#
#     for pjsk_type in [0, 1, 2]:
#         server_name = SERVER_MAP.get(pjsk_type, 'jp')
#         try:
#             event_data = currentevent(pjsk_type)
#             current_id = event_data['id']
#
#             config = GameApiConfig(pjsk_type)
#             new_api_url = config.ranking_top100_new_api_url
#             # 有新 API 的服务器由 30 秒任务负责前百，这里只做无新 API 的兜底，避免重复写库。
#             if new_api_url:
#                 continue
#
#             top100_url = config.ranking_top100_api_url
#             border_url = config.ranking_border_api_url
#
#             if not top100_url and not border_url:
#                 continue
#
#             rankings = []
#             top100_data = None
#             if top100_url:
#                 top100_url = top100_url.format(event_id=current_id)
#                 top100_data = await request_gameapi(top100_url, 'GET', 'json')
#                 for item in top100_data.get('rankings', []):
#                     rankings.append(Ranking.from_sk(item))
#
#             if border_url:
#                 border_url = border_url.format(event_id=current_id)
#                 border_data = await request_gameapi(border_url, 'GET', 'json')
#                 for item in border_data.get('borderRankings', []):
#                     if item.get('rank') != 100:
#                         rankings.append(Ranking.from_sk(item))
#
#             if rankings:
#                 await record_rankings(server_name, current_id, rankings)
#
#             if top100_data:
#                 server_data_path = data_path / server_name
#                 if not server_data_path.exists():
#                     server_data_path.mkdir(parents=True, exist_ok=True)
#
#                 with open(server_data_path / 'sktop100.json', 'w', encoding='utf-8') as f:
#                     json.dump(top100_data, f, sort_keys=True, indent=4)
#                 logger.info(f"[定时任务]:pjsk {server_name} 旧API兜底更新前百数据成功！")
#         except Exception as e:
#             logger.warning(f"[定时任务]:pjsk {server_name} 旧API兜底更新失败！Error:{e}")



# 自动检查订阅用户的分数变动（每分钟检查一次）
@scheduler.scheduled_job(
    "interval",
    minutes=1
)
async def _():
    """定时检查订阅用户的分数变动"""
    import datetime

    from nonebot import get_bot

    from plugins.pjsk._sk_subscription import (
        get_all_subscriptions,
        remove_subscription,
        remove_subscriptions_by_event,
        update_subscription_statuses,
    )

    from .._sk_sql import query_latest_rankings_by_uids, query_ranking
    
    try:
        for pjsk_type in SERVER_MAP.keys():
            server_name = SERVER_MAP[pjsk_type]
            try:
                event_data = currentevent(pjsk_type)
                event_id = event_data['id']
                
                # 如果活动已结束，删除所有订阅
                if event_data.get('status') == 'closed':
                    deleted = await remove_subscriptions_by_event(server_name, event_id)
                    if deleted > 0:
                        logger.info(f"[订阅检查]:活动 {server_name}-{event_id} 已结束，删除 {deleted} 个订阅")
                    continue
                
                # 获取所有订阅
                subscriptions = await get_all_subscriptions(server_name, event_id)
                
                if not subscriptions:
                    continue
                
                logger.debug(f"[订阅检查]:检查 {server_name}-{event_id} 的 {len(subscriptions)} 个订阅")
                
                bot = get_bot()
                valid_uids = [sub.uid for sub in subscriptions if sub.uid and sub.uid != '0']
                latest_by_uid = await query_latest_rankings_by_uids(
                    server_name, event_id, valid_uids
                )
                status_updates = []

                for sub in subscriptions:
                    try:
                        # 检查 uid 是否有效
                        if not sub.uid or sub.uid == '0':
                            await remove_subscription(sub.qq_id, server_name, event_id)
                            logger.warning(f"[订阅检查]:订阅 {sub.id} 的 uid 无效（{sub.uid}），已自动删除")
                            continue
                        
                        # 每轮先批量查询所有 UID 的最新记录；仅分数变化时再读取完整历史。
                        latest = latest_by_uid.get(str(sub.uid))
                        if latest is None:
                            # 无法查询到数据，可能玩家不在榜上，取消订阅
                            await remove_subscription(sub.qq_id, server_name, event_id)
                            logger.info(f"[订阅检查]:无法查询到玩家 {sub.uid} 的数据，已自动取消订阅")
                            
                            # 在群里通知取消订阅
                            if sub.group_id:
                                try:
                                    from nonebot.adapters.onebot.v11 import Message, MessageSegment
                                    cancel_msg = f"由于无法查询到你的排名数据（可能未进入记录范围），已自动取消{server_name.upper()}服活动{event_id}的订阅"
                                    msg = Message([
                                        MessageSegment.at(int(sub.qq_id)),
                                        MessageSegment.text(f"\n{cancel_msg}"),
                                    ])
                                    await bot.send_group_msg(group_id=int(sub.group_id), message=msg)
                                except:
                                    pass
                            continue
                        
                        # 仅分数变化时查询历史、生成图片并推送。
                        if latest.score != sub.last_score:
                            history = await query_ranking(
                                server_name, event_id, uid=sub.uid, order_by='ts ASC'
                            )
                            logger.info(f"[订阅检查]:玩家 {sub.uid} 分数变化: {sub.last_score} -> {latest.score}")
                            
                            # 计算查房数据
                            stats = _build_activity_stats(history, latest)
                            hourly_speed = stats['hourly_speed']
                            twenty_min_speed = stats['twenty_min_speed']
                            play_count = stats['play_count']
                            avg_pt = stats['avg_pt']
                            last_pt = stats['last_pt']
                            is_playing = stats['is_playing']
                            stop_duration = stats['stop_duration']
                            
                            # 生成图片
                            img = await run_pjsk_thread(compose_cf_image, 
                                latest.name,
                                latest.uid,
                                latest.score,
                                latest.rank,
                                hourly_speed,
                                twenty_min_speed,
                                play_count,
                                avg_pt,
                                last_pt,
                                is_playing,
                                stop_duration
                            )
                            
                            # 发送通知（仅群消息）
                            if not sub.group_id:
                                logger.warning(f"[订阅检查]:订阅 {sub.id} 没有群号，跳过推送")
                            else:
                                try:
                                    from nonebot.adapters.onebot.v11 import Message, MessageSegment
                                    
                                    msg = Message([
                                        MessageSegment.at(int(sub.qq_id)),
                                        image(b64=pic2b64(img)),
                                    ])
                                    
                                    await bot.send_group_msg(group_id=int(sub.group_id), message=msg)
                                    logger.info(f"[订阅检查]:已向群 {sub.group_id} 发送 {sub.qq_id} 的分数变动通知")
                                except Exception as e:
                                    logger.warning(f"[订阅检查]:向群 {sub.group_id} 发送通知失败: {e}")

                        if latest.score != sub.last_score or latest.rank != sub.last_rank:
                            status_updates.append((sub.id, latest.score, latest.rank))

                    except Exception as e:
                        logger.warning(f"[订阅检查]:检查订阅 {sub.id} 失败: {e}")
                        continue

                if status_updates:
                    await update_subscription_statuses(status_updates)

            except Exception as e:
                logger.warning(f"[订阅检查]:检查 {server_name} 订阅失败: {e}")
                continue
    except Exception as e:
        logger.error(f"[订阅检查]:订阅检查任务异常: {e}", exc_info=True)


@pjsk_sks.handle()
@cn_sks.handle()
@tw_sks.handle()
@pjsk_wlsks.handle()
@cn_wlsks.handle()
@tw_wlsks.handle()
async def _(matcher: Matcher, event: MessageEvent, msg: Message = CommandArg(), cmd: Tuple[str, ...] = Command()):
    import datetime

    from .._sk_sql import query_first_ranking_after, query_latest_ranking
    pjsk_type = get_pjsk_type(cmd[0])
    server_name = SERVER_MAP.get(pjsk_type, 'jp')
    
    event_data = currentevent(pjsk_type)
    current_id = event_data['id']
    cmd_name = (cmd[0] or '').lower()
    if _is_wl_shortcut_command(cmd_name):
        msg = _with_default_wl_arg(msg)
    arg = msg.extract_plain_text().strip()
    current_id, arg, wl_chapter = _resolve_wl_query_event_id(arg, current_id, pjsk_type)
    target_ranks = _parse_rank_args(arg, rank_levels)
    if not target_ranks:
        await matcher.finish('请输入有效的排名（如1000、100 1000或100-110）')
    
    now = datetime.datetime.now()
    event_label = _format_wl_event_label(server_name, current_id, wl_chapter)
    if '半日速' in cmd_name:
        period_hours = 12
        period_seconds = 12 * 3600
        speed_header = "半日速"
        speed_unit = "万/半日"
        title = f"{event_label}近12小时半日速"
        missing_msg = "缺少足够的历史数据计算半日速！"
    elif '日速' in cmd_name:
        period_hours = 24
        period_seconds = 24 * 3600
        speed_header = "日速"
        speed_unit = "万/日"
        title = f"{event_label}近24小时日速"
        missing_msg = "缺少足够的历史数据计算日速！"
    else:
        period_hours = 1
        period_seconds = 3600
        speed_header = "时速"
        speed_unit = "万/h"
        title = f"{event_label}近1小时时速"
        missing_msg = "缺少足够的历史数据计算时速！"

    base_event_id = _base_event_id(current_id)
    wl_chapters = await _get_wl_chapters_for_query(server_name, base_event_id, pjsk_type)
    if wl_chapters and wl_chapter is None:
        resolved_id, resolved_arg, resolved_chapter = _resolve_wl_query_event_id_from_chapters(arg, base_event_id, wl_chapters)
        if resolved_chapter:
            current_id, arg, wl_chapter = resolved_id, resolved_arg, resolved_chapter
    if wl_chapters and _should_render_wl_rank_table(cmd_name, wl_chapter):
        rows, update_minutes_ago = await _get_wl_rank_table_rows(
            server_name,
            base_event_id,
            wl_chapters,
            target_ranks,
            period_hours=period_hours,
            period_seconds=period_seconds,
        )
        if not rows:
            await matcher.finish(missing_msg)
        title = f"【{server_name.upper()}-{base_event_id}】WL近{period_hours if period_hours != 1 else 1}{'小时' if period_hours != 24 else '小时'}{speed_header}"
        img = await run_pjsk_thread(compose_wl_rank_table_image, 
            title,
            wl_chapters,
            rows,
            update_minutes_ago,
            value_mode='speed',
            value_header=speed_header,
            value_unit=speed_unit,
        )
        await matcher.finish(image(b64=pic2b64(img)))

    old_time = now - datetime.timedelta(hours=period_hours)
    old_ranks = await query_first_ranking_after(server_name, current_id, old_time, target_ranks)
    new_ranks = await query_latest_ranking(server_name, current_id, target_ranks)
    
    if not old_ranks or not new_ranks:
        await matcher.finish(missing_msg)
    
    ranks_data = _build_rank_table_data(new_ranks, old_ranks, period_seconds)
    
    # 计算数据更新时间
    update_minutes_ago = int((now - new_ranks[0].time).total_seconds() / 60)
    
    # 生成图片
    img = await run_pjsk_thread(compose_rank_table_image, title, ranks_data, update_minutes_ago, speed_header=speed_header, speed_unit=speed_unit)
    
    await matcher.finish(image(b64=pic2b64(img)))


@pjsk_skl.handle()
@cn_skl.handle()
@tw_skl.handle()
@pjsk_wlskl.handle()
@cn_wlskl.handle()
@tw_wlskl.handle()
async def _(matcher: Matcher, event: MessageEvent, msg: Message = CommandArg(), cmd: Tuple[str, ...] = Command()):
    import datetime

    from .._sk_sql import query_first_ranking_after, query_latest_ranking
    pjsk_type = get_pjsk_type(cmd[0])
    server_name = SERVER_MAP.get(pjsk_type, 'jp')
    
    event_data = currentevent(pjsk_type)
    current_id = event_data['id']
    cmd_name = (cmd[0] or '').lower()
    if _is_wl_shortcut_command(cmd_name):
        msg = _with_default_wl_arg(msg)
    arg = msg.extract_plain_text().strip()
    current_id, arg, wl_chapter = _resolve_wl_query_event_id(arg, current_id, pjsk_type)
    # 默认查询 T50 以后；指定数字时查询用户要求的排名。
    target_ranks = _parse_rank_args(arg, [r for r in rank_levels if r >= 50])
    if not target_ranks:
        await matcher.finish('请输入有效的排名（如1000、100 1000或100-110）')
    base_event_id = _base_event_id(current_id)
    wl_chapters = await _get_wl_chapters_for_query(server_name, base_event_id, pjsk_type)
    if wl_chapters and wl_chapter is None:
        resolved_id, resolved_arg, resolved_chapter = _resolve_wl_query_event_id_from_chapters(arg, base_event_id, wl_chapters)
        if resolved_chapter:
            current_id, arg, wl_chapter = resolved_id, resolved_arg, resolved_chapter
    if wl_chapters and not (_is_wl_shortcut_command(cmd_name) and wl_chapter):
        rows, update_minutes_ago = await _get_wl_rank_table_rows(
            server_name,
            base_event_id,
            wl_chapters,
            target_ranks,
            period_hours=None,
        )
        if not rows:
            await matcher.finish("缺少排名线数据！")
        title = f"【{server_name.upper()}-{base_event_id}】WL排名线"
        img = await run_pjsk_thread(compose_wl_rank_table_image, 
            title,
            wl_chapters,
            rows,
            update_minutes_ago,
            value_mode='score',
            value_header='分数',
        )
        await matcher.finish(image(b64=pic2b64(img)))
    
    now = datetime.datetime.now()
    one_hour_ago = now - datetime.timedelta(hours=1)
    old_ranks, new_ranks = await asyncio.gather(
        query_first_ranking_after(server_name, current_id, one_hour_ago, target_ranks),
        query_latest_ranking(server_name, current_id, target_ranks),
    )

    if not new_ranks:
        await matcher.finish("缺少排名线数据！")

    # 排名线与“时速”指令使用相同口径：以一小时前后的首条记录折算万分/小时。
    ranks_data = _build_rank_table_data(new_ranks, old_ranks)

    # 计算数据更新时间
    update_minutes_ago = int((now - new_ranks[0].time).total_seconds() / 60)
    
    # 生成标题
    title = f"{_format_wl_event_label(server_name, current_id, wl_chapter)}排名线"
    
    # 生成图片
    img = await run_pjsk_thread(compose_rank_table_image, title, ranks_data, update_minutes_ago)
    
    await matcher.finish(image(b64=pic2b64(img)))


def _build_activity_stats(history: list, latest):
    """根据玩家历史记录计算查房/订阅共用的活动统计。"""
    from datetime import timedelta

    cf_start_time = latest.time - timedelta(hours=1)
    recent_history = [r for r in history if r.time >= cf_start_time]

    hourly_speed = 0.0
    if len(recent_history) >= 2:
        first = recent_history[0]
        last = recent_history[-1]
        score_diff = last.score - first.score
        time_diff = (last.time - first.time).total_seconds()
        if time_diff > 0:
            hourly_speed = score_diff / time_diff * 3600 / 10000

    twenty_min_speed = 0.0
    twenty_min_ago = latest.time - timedelta(minutes=20)
    twenty_min_history = [r for r in history if r.time >= twenty_min_ago]
    if len(twenty_min_history) >= 2:
        first = twenty_min_history[0]
        last = twenty_min_history[-1]
        score_diff = last.score - first.score
        time_diff = (last.time - first.time).total_seconds()
        if time_diff > 0:
            twenty_min_speed = score_diff / time_diff * 3600 / 10000

    pts = []
    for i in range(len(recent_history) - 1):
        if recent_history[i + 1].score > recent_history[i].score:
            pts.append(recent_history[i + 1].score - recent_history[i].score)
    play_count = len(pts)
    avg_pt = sum(pts[-min(10, len(pts)):]) / min(10, len(pts)) if pts else 0
    last_pt = pts[-1] if pts else 0

    five_min_ago = latest.time - timedelta(minutes=5)
    recent_five_min = [r for r in history if r.time >= five_min_ago]

    is_playing = False
    stop_duration = None

    if len(recent_five_min) >= 2:
        has_score_change = any(
            recent_five_min[i + 1].score != recent_five_min[i].score
            for i in range(len(recent_five_min) - 1)
        )
        is_playing = has_score_change

    if not is_playing:
        last_score_change_time = latest.time
        for i in range(len(history) - 1, 0, -1):
            if history[i].score != history[i - 1].score:
                last_score_change_time = history[i].time
                break
        stop_duration = latest.time - last_score_change_time

    return {
        'hourly_speed': hourly_speed,
        'twenty_min_speed': twenty_min_speed,
        'play_count': play_count,
        'avg_pt': avg_pt,
        'last_pt': last_pt,
        'is_playing': is_playing,
        'stop_duration': stop_duration,
    }


async def _handle_cf_query(matcher: Matcher, event: MessageEvent, msg: Message, cmd: Tuple[str, ...]):
    import datetime

    from .._sk_sql import query_latest_ranking, query_ranking
    pjsk_type = get_pjsk_type(cmd[0])
    server_name = SERVER_MAP.get(pjsk_type, 'jp')
    
    event_data = currentevent(pjsk_type)
    current_id = event_data['id']
    if _is_wl_shortcut_command(cmd[0]):
        msg = _with_current_wl_chapter_arg(msg, current_id, pjsk_type)
    
    arg = msg.extract_plain_text().strip()
    
    # 如果没有参数,使用绑定的账号
    if not arg:
        qq_ls = get_message_at(event.raw_message)
        qid = qq_ls[0] if qq_ls and qq_ls[0] != event.self_id else event.user_id
        arg, isprivate = await PjskBind.get_user_bind(qid, pjsk_type)
        if not arg:
            await matcher.finish("请提供排名或由绑定的账号查询！")
        if isprivate and qid != event.user_id:
            await matcher.finish("查不到捏，可能是不给看", at_sender=True)
    
    # 确保 arg 是字符串类型，并解析 WL 单角色榜参数
    arg = str(arg)
    current_id, arg, wl_chapter = _resolve_wl_query_event_id(arg, current_id, pjsk_type)
    if not arg:
        qq_ls = get_message_at(event.raw_message)
        qid = qq_ls[0] if qq_ls and qq_ls[0] != event.self_id else event.user_id
        arg, isprivate = await PjskBind.get_user_bind(qid, pjsk_type)
        if not arg:
            await matcher.finish("请提供排名或由绑定的账号查询！")
        if isprivate and qid != event.user_id:
            await matcher.finish("查不到捏，可能是不给看", at_sender=True)
        arg = str(arg)
    
    # 检查是否是范围查询（如 1-10）或多个排名（如 1 2 3）
    range_match = re.match(r'^(\d+)-(\d+)$', arg)
    ranks = None
    range_desc = None
    if range_match:
        start = int(range_match.group(1))
        end = int(range_match.group(2))
        ranks = list(range(start, end + 1))
        range_desc = f"{start}-{end}"
    elif re.fullmatch(r'\d+(?:\s+\d+)+', arg):
        ranks = [int(x) for x in arg.split()]
        range_desc = "、".join(str(x) for x in ranks)

    if ranks:
        if len(ranks) > 20:
            await matcher.finish("最多查20个人哦", at_sender=True)

        # 查询多个排名的查房数据
        cf_data_list = []
        for rank in ranks:
            try:
                latest_rankings = await query_latest_ranking(server_name, current_id, [rank])
                if not latest_rankings:
                    continue
                latest = latest_rankings[0]
                uid = latest.uid
                
                # 查询该玩家的历史数据
                history = await query_ranking(server_name, current_id, uid=str(uid))
                if not history:
                    continue
                
                history.sort(key=lambda x: x.time)
                latest_record = history[-1]
                
                stats = _build_activity_stats(history, latest_record)
                hourly_speed = stats['hourly_speed']
                play_count = stats['play_count']
                is_playing = stats['is_playing']
                stop_duration = stats['stop_duration']
                
                cf_data_list.append({
                    'rank': rank,
                    'name': latest.name,
                    'uid': latest.uid,
                    'score': latest_record.score or 0,
                    'hourly_speed': hourly_speed,
                    'play_count': play_count,
                    'is_playing': is_playing,
                    'stop_duration': stop_duration,
                })
            except:
                continue
        
        if cf_data_list:
            img = await run_pjsk_thread(compose_cf_range_image, cf_data_list)
            await matcher.finish(image(b64=pic2b64(img)))
        else:
            await matcher.finish(f"没有排名{range_desc}的数据")
        return
    
    # 判断输入是排名还是ID
    # 排名通常是1-100的数字，ID通常是8位以上的数字
    if arg.isdigit():
        rank_or_id = int(arg)
        if rank_or_id <= 100000 and _is_wl_shortcut_command(cmd[0]):
            # WL 分榜支持查询 T100 以后的排名
            latest_rankings = await query_latest_ranking(server_name, current_id, [rank_or_id])
            if not latest_rankings:
                await matcher.finish(f"没有排名{rank_or_id}的数据")
            latest = latest_rankings[0]
            uid = latest.uid
        elif rank_or_id <= 100:
            # 普通总榜沿用 T100 以内排名查询
            latest_rankings = await query_latest_ranking(server_name, current_id, [rank_or_id])
            if not latest_rankings:
                await matcher.finish(f"没有排名{rank_or_id}的数据")
            latest = latest_rankings[0]
            uid = latest.uid
        else:
            # 按ID查询
            uid = arg
    else:
        await matcher.finish("请输入有效的排名（1-100）、多个排名（如1 2 3）、范围（如1-10）或玩家ID")
        return
    
    history = await query_ranking(server_name, current_id, uid=str(uid))
    if not history:
        await matcher.finish("没有该玩家的榜线历史记录（可能未进入记录的档线范围内）")
        
    history.sort(key=lambda x: x.time)
    latest = history[-1]
    
    stats = _build_activity_stats(history, latest)
    hourly_speed = stats['hourly_speed']
    twenty_min_speed = stats['twenty_min_speed']
    play_count = stats['play_count']
    avg_pt = stats['avg_pt']
    last_pt = stats['last_pt']
    is_playing = stats['is_playing']
    stop_duration = stats['stop_duration']

    wl_chapter_stats = []
    base_event_id = _base_event_id(current_id)
    for chapter in _get_wl_chapters(base_event_id, pjsk_type):
        encoded_id = _wl_encoded_event_id(base_event_id, int(chapter.get('chapterNo', 0)))
        chapter_history = await query_ranking(server_name, encoded_id, uid=str(uid))
        if not chapter_history:
            wl_chapter_stats.append({
                'chapter_no': chapter.get('chapterNo'),
                'cid': chapter.get('gameCharacterId'),
            })
            continue
        chapter_history.sort(key=lambda x: x.time)
        chapter_latest = chapter_history[-1]
        chapter_stats = _build_activity_stats(chapter_history, chapter_latest)
        wl_chapter_stats.append({
            'chapter_no': chapter.get('chapterNo'),
            'cid': chapter.get('gameCharacterId'),
            'rank': chapter_latest.rank,
            'score': chapter_latest.score,
            'hourly_speed': chapter_stats['hourly_speed'],
        })
    
    # 生成图片
    img = await run_pjsk_thread(compose_cf_image, 
        latest.name,
        latest.uid,
        latest.score,
        latest.rank,
        hourly_speed,
        twenty_min_speed,
        play_count,
        avg_pt,
        last_pt,
        is_playing,
        stop_duration,
        wl_chapter_stats=wl_chapter_stats or None,
    )
    
    await matcher.finish(image(b64=pic2b64(img)))


@pjsk_cf.handle()
@cn_cf.handle()
@tw_cf.handle()
async def _(matcher: Matcher, event: MessageEvent, msg: Message = CommandArg(), cmd: Tuple[str, ...] = Command()):
    await _handle_cf_query(matcher, event, msg, cmd)


@pjsk_subscribe.handle()
@cn_subscribe.handle()
@tw_subscribe.handle()
async def _(matcher: Matcher, event: MessageEvent, cmd: Tuple[str, ...] = Command()):
    """订阅分数变动通知"""
    from nonebot.adapters.onebot.v11 import GroupMessageEvent

    from plugins.pjsk._models import PjskBind
    from plugins.pjsk._sk_subscription import add_subscription, get_subscription
    
    pjsk_type = get_pjsk_type(cmd[0])
    server_name = SERVER_MAP.get(pjsk_type, 'jp')
    
    # 必须在群里订阅
    if not isinstance(event, GroupMessageEvent):
        await matcher.finish("订阅功能仅支持在群聊中使用", at_sender=True)
        return
    
    # 获取当前活动
    event_data = currentevent(pjsk_type)
    current_id = event_data['id']
    
    # 获取用户绑定的账号
    qq_id = str(event.user_id)
    uid, isprivate = await PjskBind.get_user_bind(event.user_id, pjsk_type)
    
    if not uid:
        server_display = "日服" if pjsk_type == 0 else ("台服" if pjsk_type == 1 else "国服")
        await matcher.finish(f"你还没有绑定{server_display}账号哦，请先绑定账号", at_sender=True)
        return
    
    # 获取群号
    group_id = str(event.group_id)
    
    # 检查是否已经订阅
    existing = await get_subscription(qq_id, server_name, current_id)
    if existing:
        await matcher.finish(f"你已经订阅了{server_name.upper()}服活动{current_id}的分数变动通知", at_sender=True)
        return
    
    # 添加订阅
    success = await add_subscription(qq_id, group_id, server_name, current_id, uid)
    
    if success:
        await matcher.finish(
            f"订阅成功！\n"
            f"服务器：{server_name.upper()}\n"
            f"活动号：{current_id}\n"
            f"当你的分数发生变化时，将在本群自动推送查房信息",
            at_sender=True
        )
    else:
        await matcher.finish("订阅失败，请稍后重试", at_sender=True)


@pjsk_unsubscribe.handle()
@cn_unsubscribe.handle()
@tw_unsubscribe.handle()
async def _(matcher: Matcher, event: MessageEvent, cmd: Tuple[str, ...] = Command()):
    """取消订阅分数变动通知"""
    from plugins.pjsk._sk_subscription import remove_subscription
    
    pjsk_type = get_pjsk_type(cmd[0])
    server_name = SERVER_MAP.get(pjsk_type, 'jp')
    
    # 获取当前活动
    event_data = currentevent(pjsk_type)
    current_id = event_data['id']
    
    qq_id = str(event.user_id)
    
    # 取消订阅
    success = await remove_subscription(qq_id, server_name, current_id)
    
    if success:
        await matcher.finish(f"已取消订阅{server_name.upper()}服活动{current_id}的分数变动通知", at_sender=True)
    else:
        await matcher.finish("你还没有订阅该活动", at_sender=True)


@clear_subscriptions.handle()
async def _(matcher: Matcher):
    """清空所有订阅（管理员命令）"""
    from plugins.pjsk._sk_subscription import clear_all_subscriptions
    
    deleted = await clear_all_subscriptions()
    
    if deleted > 0:
        await matcher.finish(f"已清空所有订阅，共删除 {deleted} 条记录")
    else:
        await matcher.finish("订阅表已经是空的")


def compose_sk_image(name: str, uid: str, score: int, rank: int,
                     near_ranks: list, pred_data: dict = None,
                     remain_time: str = None, update_time: str = None,
                     team_info: tuple = None, team_image: Image.Image = None) -> Image.Image:
    """用 PIL 绘制 sk 查分图片（粉白色系）。"""
    font_path      = str(FONT_PATH / "SourceHanSansCN-Medium.otf")
    font_bold_path = str(FONT_PATH / "SourceHanSansCN-Bold.otf")
    f_title  = _sk_font(font_bold_path, 24)
    f_score  = _sk_font(font_bold_path, 30)
    f_label  = _sk_font(font_bold_path, 18)
    f_value  = _sk_font(font_path, 18)
    f_small  = _sk_font(font_path, 14)

    PAD_X    = 22
    PAD_Y    = 18
    LINE_H   = 38
    TITLE_H  = 58
    TEAM_H   = 50 if team_info else 0
    MIN_WIDTH = 620

    _tmp = Image.new("RGB", (1, 1))
    _d   = ImageDraw.Draw(_tmp)

    uid_display = f"****{str(uid)[-4:]}" if len(str(uid)) > 4 else str(uid)
    title_text = f"{name} - {uid_display}"
    score_text = f"分数 {score / 10000:.1f}W，排名 {rank}"

    num_near_ranks = len(near_ranks)
    num_pred_lines = sum(1 for r in near_ranks if r.get('pred')) if pred_data else 0

    total_h = PAD_Y * 2 + TITLE_H + 8 + TEAM_H + 52
    total_h += LINE_H * num_near_ranks
    if num_pred_lines > 0:
        total_h += LINE_H * num_pred_lines + 42
    if remain_time:
        total_h += LINE_H + 8
    if update_time:
        total_h += LINE_H

    C_TEXT  = (50, 30, 50)
    C_LABEL = (208, 95, 142)
    C_PRED  = (180, 80, 135)
    C_MUTED = (120, 80, 100)

    img = _sk_gradient_bg(MIN_WIDTH, total_h)
    _sk_title_panel(img, title_text, f_title, "SK", f_small, pad=PAD_X, height=TITLE_H)
    d = ImageDraw.Draw(img)

    y = PAD_Y + TITLE_H + 8

    if team_info:
        team_name, team_tag = team_info
        team_text = team_name + (f'({team_tag})' if team_tag else '')
        _sk_panel(img, (PAD_X, y, MIN_WIDTH - PAD_X, y + TEAM_H - 4), radius=18, fill=(255, 255, 255, 210))
        d = ImageDraw.Draw(img)
        if team_image:
            team_image = team_image.resize((40, 40))
            try:
                r, g, b, mask = team_image.split()
                img.paste(team_image, (PAD_X + 10, y + 3), mask)
            except Exception:
                img.paste(team_image, (PAD_X + 10, y + 3))
            d.text((PAD_X + 62, y + TEAM_H // 2), team_text, font=f_value, fill=C_TEXT, anchor="lm")
        else:
            d.text((PAD_X + 14, y + TEAM_H // 2), team_text, font=f_value, fill=C_TEXT, anchor="lm")
        y += TEAM_H

    _sk_panel(img, (PAD_X, y, MIN_WIDTH - PAD_X, y + 50), radius=18, fill=(230, 140, 170, 226), outline=(255, 255, 255, 210))
    d = ImageDraw.Draw(img)
    d.text((PAD_X + 18, y + 25), score_text, font=f_score, fill=(255, 255, 255), anchor="lm")
    y += 58

    for idx, rank_info in enumerate(near_ranks):
        rank_num = rank_info['rank']
        rank_score = rank_info['score']
        tag = rank_info['tag']
        deviation = rank_info['deviation']
        rank_text = f"T{rank_num}  {rank_score / 10000:.1f}W  {tag}{deviation:.1f}W"
        bg = (255, 255, 255) if idx % 2 == 0 else (255, 240, 248)
        d.rounded_rectangle((PAD_X, y, MIN_WIDTH - PAD_X, y + LINE_H - 4), radius=15, fill=bg, outline=(255, 255, 255))
        d.text((PAD_X + 16, y + LINE_H // 2 - 2), rank_text, font=f_value, fill=C_TEXT, anchor="lm")
        y += LINE_H

    if num_pred_lines > 0:
        y += 8
        _sk_chip(d, (PAD_X, y, PAD_X + 96, y + 28), "预测线", f_label, fill=(255, 246, 251), text_fill=C_LABEL)
        y += 36
        for rank_info in near_ranks:
            pred = rank_info.get('pred')
            if pred:
                pred_text = f"T{rank_info['rank']}  预测 {pred / 10000:.1f}W"
                d.rounded_rectangle((PAD_X, y, MIN_WIDTH - PAD_X, y + LINE_H - 4), radius=15, fill=(255, 246, 251), outline=(245, 218, 232))
                d.text((PAD_X + 16, y + LINE_H // 2 - 2), pred_text, font=f_value, fill=C_PRED, anchor="lm")
                y += LINE_H
        d.text((MIN_WIDTH - PAD_X, y - 4), "预测线来自33（3-3.dev）", font=f_small, fill=C_MUTED, anchor="rm")

    if remain_time:
        y += 8
        _sk_chip(d, (PAD_X, y, MIN_WIDTH - PAD_X, y + 30), f"活动还剩 {remain_time}", f_value, fill=(255, 255, 255), text_fill=C_TEXT, anchor="lm")
        y += LINE_H

    if update_time:
        d.text((PAD_X, y + LINE_H // 2), f"数据生成于 {update_time}", font=f_small, fill=C_MUTED, anchor="lm")
    return img


def compose_sk_multi_image(players_data: list, update_time: str = None) -> Image.Image:
    """用 PIL 绘制多人 sk 查分图片（粉白色系）。"""
    font_path      = str(FONT_PATH / "SourceHanSansCN-Medium.otf")
    font_bold_path = str(FONT_PATH / "SourceHanSansCN-Bold.otf")
    f_title  = _sk_font(font_bold_path, 20)
    f_header = _sk_font(font_bold_path, 16)
    f_body   = _sk_font(font_path, 15)
    f_small  = _sk_font(font_path, 12)

    PAD_X    = 15
    ROW_H    = 42
    HEADER_H = 42
    TITLE_H  = 56
    FOOTER_H = 34
    OUT_PAD  = 18
    MIN_WIDTH = 600
    GAP = 8

    _tmp = Image.new("RGB", (1, 1))
    _d   = ImageDraw.Draw(_tmp)

    def tw(text: str, font) -> int:
        return int(_d.textlength(text, font=font))

    name_w = tw("玩家名", f_header) + PAD_X * 2
    for p in players_data:
        uid_display = f"****{str(p['uid'])[-4:]}" if len(str(p['uid'])) > 4 else str(p['uid'])
        name_text = f"{p['name']}({uid_display})"
        name_w = max(name_w, tw(name_text, f_body) + PAD_X * 2)
    score_w = max(tw("分数", f_header), tw("9999.9W", f_body)) + PAD_X * 2
    rank_w = max(tw("排名", f_header), tw("T99999", f_body)) + PAD_X * 2

    col_widths = {'name': name_w, 'score': score_w, 'rank': rank_w}
    table_w = sum(col_widths.values())
    content_w = max(MIN_WIDTH, table_w)
    extra_w = content_w - table_w
    draw_col_ws = [name_w + extra_w, score_w, rank_w]

    total_w = content_w + OUT_PAD * 2
    total_h = OUT_PAD * 2 + TITLE_H + GAP + HEADER_H + ROW_H * len(players_data) + FOOTER_H

    C_HEAD_BG  = (230, 140, 170)
    C_HEAD_FG  = (255, 255, 255)
    C_ROW_ODD  = (255, 255, 255)
    C_ROW_EVEN = (255, 240, 248)
    C_TEXT     = (50, 30, 50)
    C_MUTED    = (120, 80, 100)

    img = _sk_gradient_bg(total_w, total_h)
    _sk_title_panel(img, f"查询结果（共 {len(players_data)} 人）", f_title, "MULTI", f_small, pad=OUT_PAD, height=TITLE_H)
    d = ImageDraw.Draw(img)

    x0 = OUT_PAD
    x1 = total_w - OUT_PAD
    y = OUT_PAD + TITLE_H + GAP
    _sk_panel(img, (x0, y - 4, x1, total_h - OUT_PAD), radius=20, fill=(255, 255, 255, 140), outline=(255, 255, 255, 210))
    d = ImageDraw.Draw(img)

    headers = ["玩家名", "分数", "排名"]
    _sk_draw_row(d, (x0 + 10, y, x1 - 10, y + HEADER_H - 2), headers, draw_col_ws, [f_header] * 3, [C_HEAD_FG] * 3, bg=C_HEAD_BG, radius=14)
    y += HEADER_H

    for i, player in enumerate(players_data):
        bg = C_ROW_ODD if i % 2 == 0 else C_ROW_EVEN
        uid_display = f"****{str(player['uid'])[-4:]}" if len(str(player['uid'])) > 4 else str(player['uid'])
        name_text = f"{player['name']}({uid_display})"
        score_text = f"{player['score'] / 10000:.1f}W"
        rank_text = f"T{player['rank']}"
        _sk_draw_row(
            d,
            (x0 + 10, y, x1 - 10, y + ROW_H - 2),
            [name_text, score_text, rank_text],
            draw_col_ws,
            [f_body] * 3,
            [C_TEXT] * 3,
            bg=bg,
            radius=14,
        )
        y += ROW_H

    if update_time:
        _sk_chip(d, (x0 + 10, y + 4, x1 - 10, y + FOOTER_H - 2), f"数据生成于 {update_time}", f_small,
                 fill=(255, 255, 255), outline=(255, 255, 255), text_fill=C_MUTED, anchor="lm")
    return img


def compose_cf_range_image(cf_data_list: list) -> Image.Image:
    """用 PIL 绘制多个玩家的查房图片。"""
    font_path      = str(FONT_PATH / "SourceHanSansCN-Medium.otf")
    font_bold_path = str(FONT_PATH / "SourceHanSansCN-Bold.otf")
    f_header = _sk_font(font_bold_path, 16)
    f_body   = _sk_font(font_path, 14)

    PAD_X    = 15
    ROW_H    = 34
    HEADER_H = 38
    OUT_PAD  = 14
    MIN_WIDTH = 620
    MAX_WIDTH = 1200

    _tmp = Image.new("RGB", (1, 1))
    _d   = ImageDraw.Draw(_tmp)

    def tw(text: str, font) -> int:
        return int(_d.textlength(text, font=font))

    rank_w = max(tw("排名", f_header), tw("T100", f_body)) + PAD_X * 2
    name_w = tw("玩家名", f_header) + PAD_X * 2
    for dct in cf_data_list:
        uid_display = f"****{str(dct['uid'])[-4:]}" if len(str(dct['uid'])) > 4 else str(dct['uid'])
        name_w = max(name_w, tw(f"{dct['name']}({uid_display})", f_body) + PAD_X * 2)
    score_w = max(tw("当前分", f_header), tw("9999.9W", f_body)) + PAD_X * 2
    speed_w = max(tw("时速", f_header), tw("999.9W/h", f_body)) + PAD_X * 2
    play_w = max(tw("周回", f_header), tw("999", f_body)) + PAD_X * 2
    status_w = max(tw("状态", f_header), max(tw("周回中" if d['is_playing'] else "停车中", f_body) for d in cf_data_list) if cf_data_list else 0) + PAD_X * 2

    base_widths = [rank_w, name_w, score_w, speed_w, play_w, status_w]
    table_w = sum(base_widths)
    content_w = max(MIN_WIDTH, min(table_w, MAX_WIDTH))
    draw_col_ws = base_widths[:]
    if content_w > table_w:
        draw_col_ws[1] += content_w - table_w

    total_w = content_w + OUT_PAD * 2
    total_h = OUT_PAD * 2 + HEADER_H + ROW_H * len(cf_data_list)

    C_HEAD_BG  = (230, 140, 170)
    C_HEAD_FG  = (255, 255, 255)
    C_ROW_ODD  = (255, 255, 255)
    C_ROW_EVEN = (255, 240, 248)
    C_TEXT     = (50, 30, 50)

    img = _sk_gradient_bg(total_w, total_h)
    d = ImageDraw.Draw(img)
    x0 = OUT_PAD
    x1 = total_w - OUT_PAD
    y = OUT_PAD
    _sk_panel(img, (x0, y - 4, x1, total_h - OUT_PAD + 4), radius=20, fill=(255, 255, 255, 150), outline=(255, 255, 255, 220))
    d = ImageDraw.Draw(img)

    headers = ["排名", "玩家名", "当前分", "时速", "周回", "状态"]
    _sk_draw_row(d, (x0 + 8, y, x1 - 8, y + HEADER_H - 2), headers, draw_col_ws, [f_header] * 6, [C_HEAD_FG] * 6, bg=C_HEAD_BG, radius=14)
    y += HEADER_H

    status_positions = []
    for i, cf_data in enumerate(cf_data_list):
        bg = C_ROW_ODD if i % 2 == 0 else C_ROW_EVEN
        uid_display = f"****{str(cf_data['uid'])[-4:]}" if len(str(cf_data['uid'])) > 4 else str(cf_data['uid'])
        name_text = f"{cf_data['name']}({uid_display})"
        status_text = "周回中" if cf_data['is_playing'] else "停车中"
        score_val = cf_data.get('score', 0) or 0
        score_text = f"{score_val / 10000:.1f}W" if score_val >= 10000 else str(score_val)
        cells = [f"T{cf_data['rank']}", name_text, score_text, f"{cf_data['hourly_speed']:.1f}W/h", str(cf_data['play_count']), status_text]
        _sk_draw_row(d, (x0 + 8, y, x1 - 8, y + ROW_H - 2), cells, draw_col_ws, [f_body] * 6, [C_TEXT] * 6, bg=bg, radius=14)
        status_x = x0 + 8 + sum(draw_col_ws[:5])
        status_positions.append((status_x, y, draw_col_ws[5], status_text))
        y += ROW_H

    for status_x, row_y, col_w, status_text in status_positions:
        d.rounded_rectangle((status_x + 4, row_y + 5, status_x + col_w - 4, row_y + ROW_H - 7), radius=10, fill=(255, 255, 255))
        d.text((status_x + col_w // 2, row_y + ROW_H // 2 - 1), status_text, font=f_body, fill=C_TEXT, anchor="mm")

    return img


def compose_cf_image(name: str, uid: str, score: int, rank: int, hourly_speed: float, twenty_min_speed: float, 
                     play_count: int, avg_pt: float, last_pt: int, is_playing: bool, stop_duration,
                     wl_chapter_stats: Optional[List[dict]] = None) -> Image.Image:
    """用 PIL 绘制查房图片"""
    font_path      = str(FONT_PATH / "SourceHanSansCN-Medium.otf")
    font_bold_path = str(FONT_PATH / "SourceHanSansCN-Bold.otf")
    f_title  = _sk_font(font_bold_path, 22)
    f_label  = _sk_font(font_bold_path, 16)
    f_value  = _sk_font(font_path, 16)
    f_small  = _sk_font(font_path, 13)

    PAD_X    = 20
    PAD_Y    = 16
    LINE_H   = 38
    TITLE_H  = 58
    MIN_WIDTH = 520

    _tmp = Image.new("RGB", (1, 1))
    _d   = ImageDraw.Draw(_tmp)

    def tw(text: str, font) -> int:
        return int(_d.textlength(text, font=font))

    uid_display = f"****{str(uid)[-4:]}" if len(str(uid)) > 4 else str(uid)
    title_text = f"玩家 {name}(id={uid_display})"
    score_formatted = f"{score:,}"

    if is_playing:
        status_text = "周回中"
    else:
        if stop_duration:
            hours = int(stop_duration.total_seconds() // 3600)
            minutes = int((stop_duration.total_seconds() % 3600) // 60)
            seconds = int(stop_duration.total_seconds() % 60)
            duration_str = f"{hours}小时{minutes}分钟{seconds}秒" if hours > 0 else f"{minutes}分钟{seconds}秒"
            status_text = f"停车中（已停止 {duration_str}）"
        else:
            status_text = "停车中"

    lines = [
        ("总榜分数", score_formatted),
        ("总榜排名", f"T{rank}"),
        ("总榜时速", f"{hourly_speed:.1f}W/h"),
        ("20*3时速", f"{twenty_min_speed:.1f}W/h"),
        ("周回数", str(play_count)),
        ("近10次平均Pt", f"{avg_pt:.0f}"),
        ("最近一次Pt", str(last_pt)),
        ("状态", status_text),
    ]
    if wl_chapter_stats:
        for stat in wl_chapter_stats:
            value = '-'
            if stat.get('rank'):
                value = f"T{stat['rank']} / {stat['score'] / 10000:.2f}万 / {stat['hourly_speed']:.1f}W/h"
            lines.append((f"第{stat.get('chapter_no')}章", value))

    title_w = tw(title_text, f_title) + PAD_X * 2
    max_data_w = 0
    for label, value in lines:
        max_data_w = max(max_data_w, tw(label, f_label) + 24 + tw(value, f_value) + PAD_X * 2)

    total_w = max(title_w, max_data_w, MIN_WIDTH)
    total_h = TITLE_H + LINE_H * len(lines) + PAD_Y * 2 + 10

    C_TEXT  = (50, 30, 50)
    C_LABEL = (208, 95, 142)
    C_MUTED = (120, 80, 100)

    img = _sk_gradient_bg(total_w, total_h)
    d   = ImageDraw.Draw(img)

    _sk_panel(img, (10, 8, total_w - 10, TITLE_H + 4), radius=22, fill=(255, 255, 255, 222))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((PAD_X, 22, PAD_X + 62, 28), radius=3, fill=(255, 128, 178))
    d.text((PAD_X, TITLE_H // 2 + 8), _sk_fit_text(d, title_text, f_title, total_w - PAD_X * 2), font=f_title, fill=C_TEXT, anchor="lm")

    y = TITLE_H + PAD_Y
    for idx, (label, value) in enumerate(lines):
        row_fill = (255, 255, 255, 224) if idx % 2 == 0 else (255, 240, 248, 214)
        d.rounded_rectangle((PAD_X, y, total_w - PAD_X, y + LINE_H - 4), radius=15, fill=row_fill, outline=(255, 255, 255))
        d.text((PAD_X + 14, y + LINE_H // 2 - 2), label, font=f_label, fill=C_LABEL, anchor="lm")
        value_x = PAD_X + max(132, tw(label, f_label) + 28)
        d.text((value_x, y + LINE_H // 2 - 2), value, font=f_value, fill=C_TEXT, anchor="lm")
        y += LINE_H

    d.text((total_w - PAD_X, total_h - 12), "ACTIVITY CHECK", font=f_small, fill=C_MUTED, anchor="rm")
    return img


async def compose_csb_image(latest_name: str, latest_uid: str, latest_rank: int, latest_score: int,
                            hourly_counts: dict, start_date, update_time_str: str, stop_periods: list = None) -> Image.Image:
    """用 PIL 绘制查水表表格图片。"""
    if stop_periods is None:
        stop_periods = []

    font_path      = str(FONT_PATH / "SourceHanSansCN-Medium.otf")
    font_bold_path = str(FONT_PATH / "SourceHanSansCN-Bold.otf")
    f_title  = _sk_font(font_bold_path, 20)
    f_header = _sk_font(font_bold_path, 16)
    f_body   = _sk_font(font_path, 14)
    f_small  = _sk_font(font_path, 12)

    PAD_X    = 12
    ROW_H    = 32
    HEADER_H = 36
    TITLE_H  = 58
    FOOTER_H = 34
    STOP_H   = 28
    CELL_W   = 36
    OUT_PAD  = 18

    _tmp = Image.new("RGB", (1, 1))
    _d   = ImageDraw.Draw(_tmp)

    def tw(text: str, font) -> int:
        return int(_d.textlength(text, font=font))

    col0_w = max(tw("第99天", f_body), tw("时间", f_header)) + PAD_X * 2
    table_w = col0_w + CELL_W * 24
    title_text = f"【查水表】{latest_name} (ID: ****{str(latest_uid)[-4:]})"
    title_min_w = tw(title_text, f_title) + PAD_X * 2
    content_w = max(table_w, title_min_w)
    total_w = content_w + OUT_PAD * 2

    if hourly_counts:
        max_day = max([key[0] for key in hourly_counts.keys()])
        num_days = max_day + 1
    else:
        num_days = 1

    stop_cols = 2
    stop_rows = (len(stop_periods) + stop_cols - 1) // stop_cols if stop_periods else 0
    table_h = HEADER_H + ROW_H * num_days
    stop_h = STOP_H * stop_rows + (16 if stop_periods else 0)
    total_h = OUT_PAD * 2 + TITLE_H + 8 + table_h + stop_h + FOOTER_H

    C_HEAD_BG  = (230, 140, 170)
    C_HEAD_FG  = (255, 255, 255)
    C_ROW_ODD  = (255, 255, 255)
    C_ROW_EVEN = (255, 240, 248)
    C_STOP_BG  = (255, 246, 251)
    C_TEXT     = (50, 30, 50)
    C_MUTED    = (120, 80, 100)
    C_BORDER   = (235, 210, 226)

    img = _sk_gradient_bg(total_w, total_h)
    info_text = f"{title_text}  排名: T{latest_rank} | 分数: {latest_score}"
    _sk_title_panel(img, info_text, f_title, "CSB", f_small, pad=OUT_PAD, height=TITLE_H)
    d = ImageDraw.Draw(img)

    x0 = OUT_PAD
    y = OUT_PAD + TITLE_H + 8
    table_x1 = x0
    table_x2 = x0 + table_w
    _sk_panel(img, (table_x1, y - 4, table_x2, y + table_h + 4), radius=18, fill=(255, 255, 255, 150), outline=(255, 255, 255, 220))
    d = ImageDraw.Draw(img)

    # 表头行
    d.rounded_rectangle((table_x1 + 8, y, table_x2 - 8, y + HEADER_H - 2), radius=14, fill=C_HEAD_BG, outline=(255, 255, 255))
    d.text((table_x1 + col0_w // 2, y + HEADER_H // 2), "时间", font=f_header, fill=C_HEAD_FG, anchor="mm")
    for h in range(24):
        x = table_x1 + col0_w + h * CELL_W
        d.text((x + CELL_W // 2, y + HEADER_H // 2), f"{h}", font=f_header, fill=C_HEAD_FG, anchor="mm")
    y += HEADER_H

    if hourly_counts:
        max_day = max([key[0] for key in hourly_counts.keys()])
        for day in range(max_day + 1):
            bg = C_ROW_ODD if day % 2 == 0 else C_ROW_EVEN
            d.rounded_rectangle((table_x1 + 8, y, table_x2 - 8, y + ROW_H - 2), radius=12, fill=bg, outline=(255, 255, 255))
            day_text = f"第{day+1}天"
            d.text((table_x1 + col0_w // 2, y + ROW_H // 2), day_text, font=f_body, fill=C_TEXT, anchor="mm")
            for h in range(24):
                key = (day, h)
                count = hourly_counts.get(key, 0)
                x = table_x1 + col0_w + h * CELL_W
                fill = C_TEXT if count else C_MUTED
                d.text((x + CELL_W // 2, y + ROW_H // 2), str(count), font=f_body, fill=fill, anchor="mm")
            y += ROW_H

    if stop_periods:
        col_width = content_w // stop_cols
        panel_h = STOP_H * stop_rows + 8
        _sk_panel(img, (x0, y + 4, x0 + content_w, y + panel_h + 6), radius=18, fill=(255, 255, 255, 160), outline=(255, 255, 255, 220))
        d = ImageDraw.Draw(img)
        for i, period in enumerate(stop_periods):
            row = i // stop_cols
            col = i % stop_cols
            x_offset = x0 + col * col_width
            y_offset = y + 8 + row * STOP_H
            start_str = period['start'].strftime('%m-%d %H:%M')
            end_str = period['end'].strftime('%H:%M')
            stop_text = f"停车: {start_str} ~ {end_str} ({period['minutes']}分钟)"
            stop_text = _sk_fit_text(d, stop_text, f_body, col_width - 40)
            d.rounded_rectangle((x_offset + 8, y_offset + 2, x_offset + col_width - 8, y_offset + STOP_H - 2), radius=12, fill=C_STOP_BG, outline=C_BORDER)
            d.text((x_offset + 20, y_offset + STOP_H // 2), stop_text, font=f_body, fill=C_TEXT, anchor="lm")
        y += panel_h + 8

    footer_text = f"数据更新时间: {update_time_str}"
    _sk_chip(d, (x0, total_h - OUT_PAD - FOOTER_H + 4, x0 + content_w, total_h - OUT_PAD - 2), footer_text, f_small,
             fill=(255, 255, 255), outline=(255, 255, 255), text_fill=C_MUTED, anchor="lm")
    return img


@pjsk_csb.handle()
@cn_csb.handle()
@tw_csb.handle()
@pjsk_wlcsb.handle()
@cn_wlcsb.handle()
@tw_wlcsb.handle()
async def _(matcher: Matcher, event: MessageEvent, msg: Message = CommandArg(), cmd: Tuple[str, ...] = Command()):
    import datetime

    from .._sk_sql import query_latest_ranking, query_ranking
    pjsk_type = get_pjsk_type(cmd[0])
    server_name = SERVER_MAP.get(pjsk_type, 'jp')
    
    event_data = currentevent(pjsk_type)
    current_id = event_data['id']
    
    if _is_wl_shortcut_command(cmd[0]):
        msg = _with_default_wl_arg(msg)
    arg = msg.extract_plain_text().strip()
    
    # 如果没有参数，使用绑定的账号
    if not arg:
        qq_ls = get_message_at(event.raw_message)
        qid = qq_ls[0] if qq_ls and qq_ls[0] != event.self_id else event.user_id
        arg, isprivate = await PjskBind.get_user_bind(qid, pjsk_type)
        if not arg:
            await matcher.finish("请提供排名或由绑定的账号查询！")
        if isprivate and qid != event.user_id:
            await matcher.finish("查不到捏，可能是不给看", at_sender=True)
    
    # 确保 arg 是字符串类型（绑定表返回的是 int），并解析 WL 单角色榜参数
    arg = str(arg)
    current_id, arg, wl_chapter = _resolve_wl_query_event_id(arg, current_id, pjsk_type)
    if not arg:
        qq_ls = get_message_at(event.raw_message)
        qid = qq_ls[0] if qq_ls and qq_ls[0] != event.self_id else event.user_id
        arg, isprivate = await PjskBind.get_user_bind(qid, pjsk_type)
        if not arg:
            await matcher.finish("请提供排名或由绑定的账号查询！")
        if isprivate and qid != event.user_id:
            await matcher.finish("查不到捏，可能是不给看", at_sender=True)
        arg = str(arg)
    
    # 判断输入是排名还是ID
    if arg.isdigit():
        rank_or_id = int(arg)
        if rank_or_id <= 100:
            # 按排名查询
            latest_rankings = await query_latest_ranking(server_name, current_id, [rank_or_id])
            if not latest_rankings:
                await matcher.finish(f"没有排名{rank_or_id}的数据")
            latest = latest_rankings[0]
            uid = latest.uid
        else:
            # 按ID查询
            uid = arg
    else:
        await matcher.finish("请输入有效的排名（1-100）或玩家ID")
        return
    
    history = await query_ranking(server_name, current_id, uid=str(uid))
    if not history:
        await matcher.finish("没有该玩家的榜线历史记录（可能未进入记录的档线范围内）")
    
    history.sort(key=lambda x: x.time)
    latest = history[-1]
    
    # ================== 统计每小时的Pt变化次数 ================== #
    # 统计每个小时有多少次分数变化（即该小时游玩了多少次）
    hourly_counts = {}  # {(day, hour): count}
    start_date = history[0].time.date()
    
    for i in range(len(history) - 1):
        cur, nxt = history[i], history[i + 1]
        
        # 如果下一条记录分数增加，说明发生了游玩
        # 使用下一条记录的时间来确定是哪个小时的游玩
        if nxt.score > cur.score:
            day = (nxt.time.date() - start_date).days
            hour = nxt.time.hour
            key = (day, hour)
            
            # 初始化计数器
            if key not in hourly_counts:
                hourly_counts[key] = 0
            
            hourly_counts[key] += 1
    
    # ================== 计算停车时间段 ================== #
    stop_periods = []  # 存储停车时间段
    
    # 使用滑动窗口找停车区间
    l, r = None, None
    for rank in history:
        if not l:
            l = rank
        if not r:
            r = rank
        
        # 如果分数出现变化，结算当前停车区间
        if rank.score != r.score:
            if l != r:
                stop_duration = r.time - l.time
                stop_minutes = int(stop_duration.total_seconds() / 60)
                # 只记录停车时间超过 5 分钟的
                if stop_minutes >= 5:
                    stop_periods.append({
                        'start': l.time,
                        'end': r.time,
                        'minutes': stop_minutes
                    })
            l, r = rank, None
        # 否则认为正在停车，更新右边界
        else:
            r = rank
    
    # 处理最后一个停车区间
    if l and r and l != r:
        stop_duration = r.time - l.time
        stop_minutes = int(stop_duration.total_seconds() / 60)
        if stop_minutes >= 5:
            stop_periods.append({
                'start': l.time,
                'end': r.time,
                'minutes': stop_minutes
            })
    
    # 生成图片
    img = await compose_csb_image(
        latest.name,
        latest.uid,
        latest.rank,
        latest.score,
        hourly_counts,
        start_date,
        latest.time.strftime('%m-%d %H:%M:%S'),
        stop_periods
    )
    
    await matcher.finish(image(b64=pic2b64(img)))


# ---------------- cnskme：remote 自动打歌账号的个人排名曲线 ----------------

def _me_curve_helpers() -> dict:
    """把 sk 模块现成的绘制辅助函数交给 _me_curve 使用。"""
    return {
        'gradient_bg': _sk_gradient_bg,
        'panel': _sk_panel,
        'title_panel': _sk_title_panel,
        'chip': _sk_chip,
        'fit_text': _sk_fit_text,
        'wl_icon': lambda cid, size: _load_wl_chara_icon(cid, size=size),
    }


def _load_chara_color_map(pjsk_type: int) -> Dict[int, str]:
    """gameCharacterId -> 印象色 colorCode。

    gameCharacterUnits.json 里 V家角色会有多条 unit 记录，取第一条即可。
    """
    colors: Dict[int, str] = {}
    try:
        data = load_master_data('gameCharacterUnits.json', pjsk_type)
    except Exception as e:
        logger.debug(f"[cnskme] 读取角色印象色失败: {e}")
        return colors
    for item in data or []:
        if not isinstance(item, dict):
            continue
        cid = item.get('gameCharacterId')
        code = item.get('colorCode')
        if cid is not None and code and cid not in colors:
            colors[int(cid)] = code
    return colors


def _me_curve_fonts() -> dict:
    font_path = str(FONT_PATH / "SourceHanSansCN-Medium.otf")
    font_bold_path = str(FONT_PATH / "SourceHanSansCN-Bold.otf")
    return {
        'title': _sk_font(font_bold_path, 24),
        'label': _sk_font(font_path, 16),
        'small': _sk_font(font_path, 13),
    }


@pjsk_me_curve.handle()
@cn_me_curve.handle()
@tw_me_curve.handle()
async def _(matcher: Matcher, event: MessageEvent, msg: Message = CommandArg(), cmd: Tuple[str, ...] = Command()):
    from .._remote_sql import list_chapters, query_records
    from ..remote._config import REMOTE_ACCOUNT, REMOTE_REGION
    from ._me_curve import compose_total_curve, compose_wl_curve

    pjsk_type = get_pjsk_type(cmd[0])
    region = SERVER_MAP.get(pjsk_type, 'jp')

    arg = msg.extract_plain_text().strip()
    account = arg or REMOTE_ACCOUNT
    if not account:
        await matcher.finish(
            "未配置 remote 账号。请在 .env 设置 SEKAI_REMOTE_ACCOUNT，或直接 cnskme <账号id>"
        )
    # 未带参数且命令区服与 remote 配置区服一致时，沿用配置区服
    if not arg and REMOTE_REGION:
        region = REMOTE_REGION

    event_data = currentevent(pjsk_type)
    event_id = event_data.get('id', 0)
    if not event_id:
        await matcher.finish("当前没有进行中的活动")

    records = await query_records(region, str(account), event_id=event_id)
    if not records:
        await matcher.finish(
            f"没有 {region.upper()} #{account} 在活动 {event_id} 的打歌记录。\n"
            "先用 live on 跑起自动循环，产生记录后再查。"
        )

    event_name = await _get_event_name(event_id, pjsk_type)
    remain_text = _format_event_remaining(event_id, pjsk_type)
    time_range = _get_event_time_range(event_id, pjsk_type, {})
    helpers = _me_curve_helpers()
    fonts = _me_curve_fonts()

    images = []
    try:
        images.append(compose_total_curve(
            region, event_id, event_name, records,
            time_range, remain_text, helpers, fonts,
        ))
    except Exception as e:
        logger.error(f"[cnskme] 绘制总榜曲线失败: {e}")
        await matcher.finish(f"绘制总榜曲线失败：{e}")

    # WL 活动额外出一张分榜曲线
    if _is_world_bloom_event(event_id, pjsk_type):
        chapter_nos = await list_chapters(region, str(account), event_id)
        if chapter_nos:
            chapters = _get_wl_chapters(event_id, pjsk_type)
            current = _current_wl_chapter(chapters)
            current_no = current.get('chapterNo') if current else None
            chara_colors = _load_chara_color_map(pjsk_type)
            meta = {}
            for c in chapters:
                chapter_no = int(c.get('chapterNo', 0))
                cid = c.get('gameCharacterId')
                # chapterEndAt 比 aggregateAt 晚约 10 分钟，用 aggregateAt 作为区块右边界更贴合榜线
                end_ms = c.get('aggregateAt') or c.get('chapterEndAt')
                meta[chapter_no] = {
                    'gameCharacterId': cid,
                    'active': c.get('chapterNo') == current_no,
                    'start': int(c['chapterStartAt'] / 1000) if c.get('chapterStartAt') else None,
                    'end': int(end_ms / 1000) if end_ms else None,
                    'color': chara_colors.get(cid),
                }
            chapter_records = {
                no: [r for r in records if r.wl_chapter_no == no]
                for no in chapter_nos
            }
            chapter_records = {k: v for k, v in chapter_records.items() if v}
            if chapter_records:
                try:
                    images.append(compose_wl_curve(
                        region, event_id, event_name,
                        chapter_records, meta, time_range, remain_text, helpers, fonts,
                    ))
                except Exception as e:
                    logger.error(f"[cnskme] 绘制WL分榜曲线失败: {e}")

    for img in images:
        await matcher.send(image(b64=pic2b64(img)))
