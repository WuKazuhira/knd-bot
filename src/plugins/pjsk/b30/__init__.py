import asyncio
import json
import random
import time
from typing import Any, Dict, Optional, Tuple

from nonebot import on_command
from nonebot.adapters.onebot.v11 import Message, MessageEvent
from nonebot.internal.matcher import Matcher
from nonebot.params import Command, CommandArg
from PIL import Image, ImageDraw, ImageFont

from config.path_config import FONT_PATH
from services.log import logger
from utils.imageutils import pic2b64
from utils.message_builder import image

from .._autoask import pjsk_update_manager
from .._common_utils import callapi
from .._config import BUG_ERROR, NOT_IMAGE_ERROR, SERVER_CONFIG, SERVER_MAP, api_base_url_list, data_path, suite_path
from .._errors import apiCallError, maintenanceIn, pjskError, userIdBan
from .._models import UserProfile
from .._profile_header import build_header_data_from_profile, draw_pjsk_profile_header
from .._utils import async_load_master_data, get_pjsk_type, get_server_data_path, get_userid_preprocess, open_pjsk_image

__plugin_name__ = "烧烤b30/pjskb30"
__plugin_type__ = "烧烤相关&uni移植"
__plugin_version__ = 0.1
__plugin_usage__ = f"""
usage：
    查询烧烤b30(仅供娱乐)
    若群内已有unibot请勿开启此bot该功能
    私聊可用，限制每人1分钟只能查询2次
    指令：
        b30/烧烤b30/pjsk b30           :查看自己的b30
        b30/烧烤b30/pjsk b30  @qq      :查看艾特用户的b30(对方必须已绑定烧烤账户)
        b30/烧烤b30/pjsk b30  烧烤id    :查看对应烧烤账号的b30
        b30/烧烤b30/pjsk b30  活动排名   :查看当期活动排名对应烧烤用户的b30
    数据来源：
        pjsekai.moe
        unipjsk.com
""".strip()
__plugin_settings__ = {
    "default_status": False,
    "cmd": ["b30", "pjskb30", "烧烤相关", "uni移植", "烧烤b30"],
}
__plugin_cd_limit__ = {"cd": 60, "count_limit": 2, "rst": "别急，等[cd]秒后再用！", "limit_type": "user"}
__plugin_block_limit__ = {"rst": "别急，还在查！"}

pjsk_b30 = on_command('pjsk b30', aliases={'pjskb30', '烧烤b30', '烧烤 b30', 'b30'}, priority=5, block=True)
cn_b30 = on_command('cnpjsk b30', aliases={'cnpjskb30', 'cn烧烤b30', 'cn烧烤 b30', 'cnb30'}, priority=5, block=True)
tw_b30 = on_command('twpjsk b30', aliases={'twpjskb30', 'tw烧烤b30', 'tw烧烤 b30', 'twb30'}, priority=5, block=True)


_FONT_CACHE: Dict[tuple[str, int], ImageFont.FreeTypeFont] = {}
_IMAGE_CACHE: Dict[str, Image.Image] = {}
B30_TASK_LIMIT = 8


def _get_font(font_name: str, size: int) -> ImageFont.FreeTypeFont:
    key = (font_name, size)
    font = _FONT_CACHE.get(key)
    if font is None:
        font = ImageFont.truetype(str(FONT_PATH / font_name), size)
        _FONT_CACHE[key] = font
    return font


def _get_cached_image(name: str) -> Image.Image:
    img = _IMAGE_CACHE.get(name)
    if img is None:
        img = open_pjsk_image(data_path / 'pics' / name, mode='RGBA')
        _IMAGE_CACHE[name] = img
    return img.copy()


def _gradient_bg(width: int, height: int) -> Image.Image:
    top = (255, 246, 250)
    bottom = (236, 244, 255)
    img = Image.new("RGB", (width, height), top)
    draw = ImageDraw.Draw(img)
    for y in range(height):
        t = y / max(1, height - 1)
        color = tuple(int(top[i] * (1 - t) + bottom[i] * t) for i in range(3))
        draw.line((0, y, width, y), fill=color)
    glow = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    gd.ellipse((-width // 4, -height // 8, width // 2, height // 4), fill=(255, 190, 220, 70))
    gd.ellipse((width // 2, height // 4, width + width // 5, height + height // 6), fill=(170, 210, 255, 58))
    img.paste(glow, (0, 0), glow.split()[-1])
    return img


def _panel(base: Image.Image, xy, radius: int = 24, fill=(255, 255, 255, 218), outline=(255, 255, 255, 232)):
    overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    od.rounded_rectangle(xy, radius=radius, fill=fill, outline=outline)
    base.paste(overlay, (0, 0), overlay.split()[-1])


def _fit_text(draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> str:
    text = str(text or '')
    if draw.textlength(text, font=font) <= max_width:
        return text
    while text and draw.textlength(text + '…', font=font) > max_width:
        text = text[:-1]
    return text + '…' if text else '…'


def _paste_round(base: Image.Image, img: Image.Image, xy: Tuple[int, int], size: Tuple[int, int], radius: int = 18):
    img = img.convert('RGBA').resize(size, Image.Resampling.LANCZOS)
    mask = Image.new('L', size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, size[0], size[1]), radius=radius, fill=255)
    base.paste(img, xy, mask)


def _build_music_title_map(musics) -> Dict[int, str]:
    return {
        music.get('id'): music.get('title', '')
        for music in musics
        if isinstance(music, dict) and music.get('id') is not None
    }


def _build_card_asset_map(cards) -> Dict[int, str]:
    return {
        card.get('id'): card.get('assetbundleName', '')
        for card in cards
        if isinstance(card, dict) and card.get('id') is not None
    }


def _build_b30_profile(profile: UserProfile, userid: str, isprivate: bool, suite_data: dict, suite_raw_data: dict = None) -> dict:
    if suite_raw_data is None:
        suite_raw_data = {}
    # userMusicResults 在根层（suite_raw_data），gamedata 层（suite_data）里没有
    music_results = (
        suite_raw_data.get('userMusicResults')
        or suite_data.get('userMusicResults')
        or []
    )
    return {
        'userid': '保密' if isprivate else userid,
        'name': profile.name or suite_data.get('name') or suite_raw_data.get('name') or '???',
        'rank': profile.rank or suite_data.get('rank', 0) or suite_raw_data.get('rank', 0),
        'userDecks': profile.userDecks or suite_data.get('userDecks', []) or suite_raw_data.get('userDecks', []),
        'special_training': profile.special_training or suite_data.get('special_training', []),
        'userProfileHonors': profile.userProfileHonors or suite_data.get('userProfileHonors', []) or suite_raw_data.get('userProfileHonors', []),
        'userHonorMissions': profile.userHonorMissions or suite_data.get('userHonorMissions', []) or suite_raw_data.get('userHonorMissions', []),
        'suite_update_time': suite_data.get('upload_time') or suite_raw_data.get('upload_time') or suite_data.get('updatedAt') or suite_raw_data.get('updatedAt') or getattr(profile, 'updatedAt', 0),
        'music_results': music_results,
    }


def fcrank(playlevel, rank):
    if playlevel <= 32:
        return rank - 1.5
    else:
        return rank - 1


async def b30single(diff, music_title_map: Dict[int, str], pjsk_type: int = 0):
    color = {
        'master': (187, 51, 238),
        'expert': (238, 67, 102),
        'hard': (254, 170, 0),
        'normal': (51, 187, 238),
        'easy': (102, 221, 17),
    }
    musictitle = music_title_map.get(diff['musicId'], '') or f"Music {diff['musicId']}"
    accent = color.get(diff['musicDifficulty'], (230, 140, 170))
    pic = Image.new("RGBA", (310, 120), (0, 0, 0, 0))
    draw = ImageDraw.Draw(pic)
    draw.rounded_rectangle((0, 0, 310, 120), radius=18, fill=(255, 255, 255, 236), outline=(255, 255, 255, 255))

    try:
        jacket = await pjsk_update_manager.get_asset(
            'startapp/thumbnail/music_jacket', f'jacket_s_{str(diff["musicId"]).zfill(3)}.png',
            pjsk_type=pjsk_type
        )
        _paste_round(pic, jacket, (10, 10), (100, 100), radius=16)
    except Exception:
        draw.rounded_rectangle((10, 10, 110, 110), radius=16, fill=(235, 235, 245))
        draw.text((60, 60), "♪", fill=(160, 150, 180), font=_get_font('SourceHanSansCN-Bold.otf', 38), anchor="mm")

    draw.rounded_rectangle((124, 14, 178, 42), radius=14, fill=accent)
    draw.text((151, 27), str(diff['playLevel']), fill=(255, 255, 255), font=_get_font('SourceHanSansCN-Bold.otf', 20), anchor="mm")
    draw.text((186, 16), diff['musicDifficulty'].upper(), fill=accent, font=_get_font('FOT-RodinNTLGPro-DB.ttf', 14))

    font_title = _get_font('SourceHanSansCN-Bold.otf', 18)
    draw.text((124, 48), _fit_text(draw, musictitle, font_title, 170), fill=(42, 32, 48), font=font_title)

    result_text = 'AP' if diff.get('result') == 2 else 'FC' if diff.get('result') == 1 else '--'
    result_fill = (255, 110, 170) if result_text == 'AP' else (85, 170, 245)
    draw.rounded_rectangle((124, 82, 170, 108), radius=13, fill=result_fill)
    draw.text((147, 94), result_text, fill=(255, 255, 255), font=_get_font('FOT-RodinNTLGPro-DB.ttf', 15), anchor="mm")
    base_const = diff.get('aplevel+', diff.get('playLevel', 0))
    weight = diff.get('rank') or 0
    const_text = f"{base_const:.1f}→{weight:.1f}"
    draw.text((182, 80), const_text, fill=(70, 52, 78), font=_get_font('SourceHanSansCN-Bold.otf', 17))
    draw.text((182, 101), "constant weight", fill=(142, 118, 150), font=_get_font('FOT-RodinNTLGPro-DB.ttf', 10))
    return pic


@pjsk_b30.handle()
@cn_b30.handle()
@tw_b30.handle()
async def _(matcher: Matcher, event: MessageEvent, msg: Message = CommandArg(), cmd: Tuple[str, ...] = Command()):
    pjsk_type = get_pjsk_type(cmd[0])
    
    server_name = SERVER_MAP.get(pjsk_type, 'jp')

    # 获取用户 ID
    state = await get_userid_preprocess(event, msg, pjsk_type=pjsk_type)
    if reply := state['error']:
        await matcher.finish(reply, at_sender=True)
    userid = state['userid']
    isprivate = state['private']
    
    # 获取基础资料和 suite 数据
    profile = UserProfile()
    try:
        await profile.getprofile(userid, 'profile', is_force_update=True, pjsk_type=pjsk_type)
    except pjskError as e:
        await matcher.finish(str(e))
    except (maintenanceIn, apiCallError, userIdBan) as e:
        await matcher.finish(str(e))
    except Exception as e:
        import traceback
        logger.error(f"[b30] 获取profile失败: {e}")
        logger.error(f"[b30] 错误堆栈: {traceback.format_exc()}")
        await matcher.finish(BUG_ERROR)

    try:
        await profile.getsuite(userid, pjsk_type=pjsk_type)
    except Exception as e:
        logger.warning(f"[b30] 获取suite失败，后续将使用profile数据兜底: {e}")

    suite_raw_data = getattr(profile, 'suite_raw_data', None) or {}
    suite_data = getattr(profile, 'suite_data', None) or {}
    # suite_data 是 gamedata 层，suite_raw_data 是根层
    # userMusicResults 在根层，需要从 suite_raw_data 取
    if not isinstance(suite_raw_data, dict):
        suite_raw_data = {}
    if not isinstance(suite_data, dict):
        suite_data = {}

    profile_data = _build_b30_profile(profile, userid, isprivate, suite_data, suite_raw_data)

    cards = await async_load_master_data('cards.json', pjsk_type)
    card_asset_map = _build_card_asset_map(cards)

    # 设置文字
    pic = _gradient_bg(1120, 1810)
    draw = ImageDraw.Draw(pic)

    # 获取定数表，缺失时自动下载
    from ..diffrank.data_source import load_constants, update_diff_from_sheet
    constants = load_constants(pjsk_type)
    if not constants:
        logger.info("[b30] realtime/constants.csv 不存在，尝试从 Google Sheets 下载...")
        await update_diff_from_sheet(pjsk_type=pjsk_type)
        constants = load_constants(pjsk_type)
        if not constants:
            logger.warning("[b30] 定数数据获取失败，将使用整数 level 作为定数")

    # 读取原始难度列表（musicDifficulties.json）
    diff = [
        item.copy()
        for item in await async_load_master_data('musicDifficulties.json', pjsk_type)
        if isinstance(item, dict)
    ]
    diff_index = {}
    for diff_item in diff:
        mid = diff_item.get('musicId')
        d = diff_item.get('musicDifficulty')
        play_level = diff_item.get('playLevel', 0)
        # AP 定数：有精确定数则用，否则用整数 level
        ap = constants.get((mid, d), play_level)
        diff_item['result'] = 0
        diff_item['rank'] = 0
        diff_item['has_constant'] = (mid, d) in constants
        diff_item['aplevel+'] = ap
        # FC 定数 = AP 定数 - 1（level≥33）或 - 1.5（level<33）
        diff_item['fclevel+'] = fcrank(play_level, ap)
        diff_index[(mid, d)] = diff_item
    diff.sort(key=lambda x: x["aplevel+"], reverse=True)
    highest = 0
    top_count = min(30, len(diff))
    for i in range(top_count):
        highest = highest + diff[i]['aplevel+']
    highest = round(highest / top_count, 2) if top_count else 0
    musics = await async_load_master_data('musics.json', pjsk_type)
    music_title_map = _build_music_title_map(musics)
    # userMusicResults 在根层（suite_raw_data），兼容 suite_data 层
    music_results = (
        suite_raw_data.get('userMusicResults')
        or suite_data.get('userMusicResults')
        or []
    )
    for music in music_results:
        playResult = music.get('playResult')
        musicId = music.get('musicId')
        # Suite API 用 musicDifficultyType，兼容旧格式的 musicDifficulty
        musicDifficulty = music.get('musicDifficultyType') or music.get('musicDifficulty')
        diff_item = diff_index.get((musicId, musicDifficulty))
        if diff_item is None:
            continue
        if playResult == 'full_perfect':
            diff_item['result'] = 2
            diff_item['rank'] = diff_item['aplevel+']
        elif playResult == 'full_combo':
            if diff_item['result'] < 1:
                diff_item['result'] = 1
                diff_item['rank'] = diff_item['fclevel+']
    diff.sort(key=lambda x: x["rank"], reverse=True)
    rank = 0
    
    _panel(pic, (36, 330, 1084, 1668), radius=28, fill=(255, 255, 255, 132), outline=(255, 255, 255, 210))

    # 并行生成所有b30歌曲图片
    b30_tasks = []
    for i in range(0, 30):
        if i >= len(diff): break
        b30_tasks.append(b30single(diff[i], music_title_map, pjsk_type=pjsk_type))
    
    if b30_tasks:
        sem = asyncio.Semaphore(B30_TASK_LIMIT)

        async def _limited(task_coro):
            async with sem:
                return await task_coro

        b30_results = await asyncio.gather(*[_limited(task) for task in b30_tasks], return_exceptions=True)
        valid_count = 0
        for i, single in enumerate(b30_results):
            if isinstance(single, Exception):
                logger.error(f"Error generating b30 single {i}: {single}")
                continue

            valid_count += 1
            rank = rank + diff[i]['rank']
            pic.paste(single, ((int(53 + (i % 3) * 342)), int(356 + int(i / 3) * 130)), single.split()[-1])
    
    rank = round(rank / valid_count, 2) if valid_count else 0
    header_data = build_header_data_from_profile(profile, userid, isprivate, suite_data, suite_raw_data)
    await draw_pjsk_profile_header(
        pic,
        (36, 28, 1084, 286),
        header_data,
        module_label="BEST 30 REPORT",
        pjsk_type=pjsk_type,
        card_asset_map=card_asset_map,
        extra_badges=[("B30", str(rank))],
    )

    font_style = _get_font('SourceHanSansCN-Medium.otf', 15)
    draw.text((50, 1716), f'注：33+FC权重减1，其他减1.5，非官方算法，仅供参考娱乐，当前理论值为{highest}', fill=(92, 72, 98),
              font=font_style)
    draw.text((50, 1742), '※定数非官方 仅供参考娱乐 请勿当真', fill=(130, 104, 138),
              font=font_style)
    draw.text((1070, 1744), "BEST 30", fill=(120, 80, 100), font=_get_font('FOT-RodinNTLGPro-DB.ttf', 18), anchor="rm")
    logger.debug(f"[b30] profile_data keys={list(profile_data.keys())}")
    logger.debug(f"[b30] profile name={profile.name!r}, rank={profile.rank!r}, userDecks={profile.userDecks[:1] if profile.userDecks else []}")
    if isinstance(suite_data, dict):
        logger.debug(f"[b30] suite keys={list(suite_data.keys())[:20]}, musicResults={len(suite_data.get('userMusicResults', [])) if isinstance(suite_data.get('userMusicResults', []), list) else 'n/a'}")

    # 上传时间
    try:
        if not profile.isNewData:
            font_style = _get_font('SourceHanSansCN-Bold.otf', 25)
            user_suite_file = suite_path / server_name / f'{userid}.json'
            if user_suite_file.exists():
                mtime = user_suite_file.stat().st_mtime
                updatetime = time.localtime(mtime)
                draw.text(
                    (68, 20), '数据更新于：' + time.strftime("%Y-%m-%d %H:%M:%S", updatetime),
                    fill=(100, 100, 100), font=font_style
                )
    except Exception as e:
        logger.debug(f"[b30] 写入更新时间失败: {e}")
    pic = pic.convert("RGB")

    await matcher.finish(image(b64=pic2b64(pic)))

