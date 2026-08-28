import asyncio
import json
import os
import time
from collections import OrderedDict
from typing import Dict, Optional, Tuple

from nonebot import on_command
from nonebot.adapters.onebot.v11 import Message, MessageEvent
from nonebot.internal.matcher import Matcher
from nonebot.params import Command, CommandArg
from nonebot.permission import SUPERUSER
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from config.path_config import FONT_PATH
from manager import group_manager
from services.log import logger
from utils.imageutils import pic2b64
from utils.message_builder import image
from utils.utils import scheduler

from .._autoask import pjsk_update_manager
from .._config import SERVER_MAP, data_path, suite_path
from .._models import PjskBind, UserProfile
from .._profile_header import build_header_data_from_profile, draw_pjsk_profile_header
from .._song_utils import isleak
from .._utils import async_load_master_data, generatehonor, get_pjsk_type, load_master_data, run_pjsk_thread
from .data_source import (
    generate_diff_csv,
    generate_diff_json,
    get_constants_csv_path,
    load_constants,
    update_diff_from_sheet,
)

__plugin_name__ = "难度排行"
__plugin_type__ = "烧烤相关&uni移植"
__plugin_version__ = 0.1
__plugin_usage__ = f"""
usage：
    查询烧烤难度排行
    若群内已有unibot请勿开启此bot该功能
    私聊可用，限制每人1分钟只能查询2次
    
    定数必须指定，难度默认为ma，不带参数ap、fc时为综合排行
    指令：
        难度排行   [定数] [难度]
        ap难度排行 [定数] [难度]
        fc难度排行 [定数] [难度]
    示例：
        难度排行   26
        ap难度排行 27 ma
        fc难度排行 28 ex
    数据来源：
        pjsekai.moe
""".strip()
__plugin_superuser_usage__ = f"""
usage：
    手动更新歌曲难度定数，难度定数基本依靠手动修改csv文件
    指令：
        生成难度csv     ：根据json资源生成csv
        生成难度json    ：根据csv生成json资源
""".strip()
__plugin_settings__ = {
    "default_status": True,
    "cmd": ["难度排行", "烧烤相关", "uni移植"],
}
__plugin_cd_limit__ = {"cd": 60, "count_limit": 2, "rst": "别急，等[cd]秒后再用！", "limit_type": "user"}
__plugin_block_limit__ = {"rst": "别急，还在查！"}


DIFFRANK_ASSET_LIMIT = 12
DIFFRANK_JACKET_CACHE_LIMIT = 256
_DIFFRANK_JACKET_CACHE: OrderedDict[Tuple[int, int], Image.Image] = OrderedDict()
_DIFFRANK_ICON_CACHE: Dict[str, Image.Image] = {}
_DIFFRANK_FONT_CACHE: Dict[Tuple[str, int], ImageFont.FreeTypeFont] = {}

DIFFRANK_BG_TOP = (255, 246, 250)
DIFFRANK_BG_BOTTOM = (236, 244, 255)
DIFFRANK_PANEL = (255, 255, 255, 226)
DIFFRANK_PANEL_STRONG = (255, 255, 255, 234)
DIFFRANK_LINE = (255, 255, 255, 245)
DIFFRANK_TEXT = (44, 36, 58)
DIFFRANK_MUTED = (118, 112, 132)
DIFFRANK_ACCENT = (0, 204, 187)
DIFFRANK_WARN = (225, 80, 96)
DIFFRANK_CANVAS_MIN_W = 760
DIFFRANK_PAD = 36
DIFFRANK_HEADER_H = 258
DIFFRANK_STATUS_HEADER_H = 150
DIFFRANK_FOOTER_H = 118


# 旧版本默认关闭过 diffrank；现在不再兼容 unibot，启动时清理历史群关闭记录。
try:
    for _group_id in group_manager.get_all_group():
        group_manager.unblock_plugin('diffrank', _group_id)
except Exception as e:
    logger.debug(f"[diffrank] 清理历史关闭状态失败: {e}")


def _font(name: str, size: int) -> ImageFont.FreeTypeFont:
    key = (name, size)
    font = _DIFFRANK_FONT_CACHE.get(key)
    if font is None:
        font = ImageFont.truetype(str(FONT_PATH / name), size)
        _DIFFRANK_FONT_CACHE[key] = font
    return font


def _bold(size: int) -> ImageFont.FreeTypeFont:
    return _font('SourceHanSansCN-Bold.otf', size)


def _medium(size: int) -> ImageFont.FreeTypeFont:
    return _font('SourceHanSansCN-Medium.otf', size)


def _rodin(size: int) -> ImageFont.FreeTypeFont:
    return _font('FOT-RodinNTLGPro-DB.ttf', size)


def _get_font(size: int) -> ImageFont.FreeTypeFont:
    return _bold(size)


def _text_width(font, text: str) -> int:
    try:
        bbox = font.getbbox(str(text))
        return bbox[2] - bbox[0]
    except AttributeError:
        return font.getsize(str(text))[0]


def _truncate_text(draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> str:
    text = str(text or '')
    if draw.textlength(text, font=font) <= max_width:
        return text
    while text and draw.textlength(text + '…', font=font) > max_width:
        text = text[:-1]
    return text + '…' if text else '…'


def _make_gradient_background(width: int, height: int) -> Image.Image:
    img = Image.new('RGB', (width, height), DIFFRANK_BG_TOP)
    d = ImageDraw.Draw(img)
    for y in range(height):
        t = y / max(1, height - 1)
        color = tuple(int(DIFFRANK_BG_TOP[i] * (1 - t) + DIFFRANK_BG_BOTTOM[i] * t) for i in range(3))
        d.line((0, y, width, y), fill=color)
    glow = Image.new('RGBA', (width, height), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    gd.ellipse((-width // 5, -height // 5, width // 2, height // 3), fill=(255, 190, 220, 76))
    gd.ellipse((width // 2, height // 4, width + width // 4, height + height // 5), fill=(170, 210, 255, 68))
    gd.ellipse((width // 3, -height // 7, width, height // 2), fill=(210, 190, 255, 34))
    img.paste(glow, (0, 0), glow.split()[-1])
    return img.convert('RGBA')


def _soft_shadow(size: Tuple[int, int], radius: int = 24, alpha: int = 52) -> Image.Image:
    shadow = Image.new('RGBA', size, (0, 0, 0, 0))
    d = ImageDraw.Draw(shadow)
    d.rounded_rectangle((0, 0, size[0] - 1, size[1] - 1), radius=radius, fill=(70, 55, 90, alpha))
    return shadow.filter(ImageFilter.GaussianBlur(10))


def _draw_round_panel(base: Image.Image, xy: Tuple[int, int, int, int], radius: int = 24,
                      fill=DIFFRANK_PANEL, outline=DIFFRANK_LINE, shadow: bool = True):
    x1, y1, x2, y2 = xy
    w, h = x2 - x1, y2 - y1
    if shadow:
        sh = _soft_shadow((w, h), radius=radius, alpha=48)
        base.paste(sh, (x1 + 4, y1 + 7), sh.split()[-1])
    panel = Image.new('RGBA', (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(panel)
    d.rounded_rectangle((0, 0, w - 1, h - 1), radius=radius, fill=fill, outline=outline, width=1 if outline else 0)
    base.paste(panel, (x1, y1), panel.split()[-1])


def _rounded_image(img: Image.Image, radius: int = 16) -> Image.Image:
    img = img.convert('RGBA')
    mask = Image.new('L', img.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, img.width - 1, img.height - 1), radius=radius, fill=255)
    out = Image.new('RGBA', img.size, (0, 0, 0, 0))
    out.paste(img, (0, 0), mask)
    return out


def _resize_jacket_sync(jacket: Image.Image) -> Image.Image:
    return jacket.convert('RGBA').resize((120, 120), Image.Resampling.LANCZOS)


async def _load_jacket(music_id: int, pjsk_type: int) -> Image.Image:
    cache_key = (pjsk_type, music_id)
    cached = _DIFFRANK_JACKET_CACHE.get(cache_key)
    if cached is not None:
        _DIFFRANK_JACKET_CACHE.move_to_end(cache_key)
        return cached.copy()

    asset_name = f'jacket_s_{str(music_id).zfill(3)}'
    jacket = await pjsk_update_manager.get_asset(
        f'startapp/music/jacket/{asset_name}', f'{asset_name}.png', pjsk_type=pjsk_type
    )
    if jacket is None:
        jacket = await pjsk_update_manager.get_asset(
            'startapp/thumbnail/music_jacket', f'{asset_name}.png', pjsk_type=pjsk_type
        )
    if jacket is None:
        jacket = Image.new('RGBA', (120, 120), (230, 230, 230, 255))
        ImageDraw.Draw(jacket).text((28, 48), str(music_id), fill=(80, 80, 80), font=_get_font(45))
    elif jacket.size != (120, 120) or jacket.mode != 'RGBA':
        jacket = await run_pjsk_thread(_resize_jacket_sync, jacket)
    _DIFFRANK_JACKET_CACHE[cache_key] = jacket.copy()
    while len(_DIFFRANK_JACKET_CACHE) > DIFFRANK_JACKET_CACHE_LIMIT:
        _, stale = _DIFFRANK_JACKET_CACHE.popitem(last=False)
        stale.close()
    return jacket.copy()


async def _prefetch_jackets(music_ids, pjsk_type: int) -> Dict[int, Image.Image]:
    unique_ids = list(dict.fromkeys(music_ids))
    sem = asyncio.Semaphore(DIFFRANK_ASSET_LIMIT)

    async def _limited(mid: int):
        async with sem:
            try:
                return mid, await _load_jacket(mid, pjsk_type)
            except Exception as e:
                logger.warning(f"[diffrank] 加载歌曲封面失败 music_id={mid}: {e}")
                fallback = Image.new('RGBA', (120, 120), (230, 230, 230, 255))
                ImageDraw.Draw(fallback).text((28, 48), str(mid), fill=(80, 80, 80), font=_get_font(45))
                return mid, fallback

    results = await asyncio.gather(*(_limited(mid) for mid in unique_ids), return_exceptions=True)
    jackets = {}
    for result in results:
        if isinstance(result, Exception):
            continue
        mid, jacket = result
        jackets[mid] = jacket
    return jackets


def _get_result_icon(name: str) -> Image.Image:
    icon = _DIFFRANK_ICON_CACHE.get(name)
    if icon is None:
        icon = Image.open(data_path / f'pics/{name}').convert('RGBA')
        _DIFFRANK_ICON_CACHE[name] = icon
    return icon.copy()


def _fc_constant(play_level: int, ap_constant: float) -> float:
    """按现有 b30 逻辑从 AP 定数估算 FC 定数。"""
    return ap_constant - (1.5 if play_level <= 32 else 1)


def _apply_constants(diff_data: list, constants: dict) -> None:
    """将 realtime/constants.csv 的精确定数合并到 musicDifficulties。"""
    for item in diff_data:
        if not isinstance(item, dict):
            continue
        play_level = item.get('playLevel', 0) or 0
        music_id = item.get('musicId')
        difficulty = item.get('musicDifficulty')
        ap_constant = constants.get((music_id, difficulty))
        if ap_constant is None:
            item['_hasConstant'] = False
            item.setdefault('playLevelAdjust', 0)
            item.setdefault('fullComboAdjust', 0)
            item.setdefault('fullPerfectAdjust', 0)
            continue
        item['_hasConstant'] = True
        item['fullPerfectAdjust'] = ap_constant - play_level
        item['fullComboAdjust'] = _fc_constant(play_level, ap_constant) - play_level
        item['playLevelAdjust'] = item['fullComboAdjust'] * 2 / 3 + item['fullPerfectAdjust'] * 1 / 3


# pjsk难度排行（对齐 b30：使用 on_command，避免 regex matcher 被全局 hook 静默截断且避免 cn/tw 重复命中）
pjsk_diffrank = on_command('难度排行', aliases={'ap难度排行', 'fc难度排行'}, priority=2, block=True)
cn_pjsk_diffrank = on_command('cn难度排行', aliases={'cnap难度排行', 'cnfc难度排行'}, priority=2, block=True)
tw_pjsk_diffrank = on_command('tw难度排行', aliases={'twap难度排行', 'twfc难度排行'}, priority=2, block=True)

pjsk_gene_diffrank = on_command('生成难度csv', aliases={"生成难度json"}, priority=5, permission=SUPERUSER, block=True)
cn_pjsk_gene_diffrank = on_command('cn生成难度csv', aliases={"cn生成难度json"}, priority=5, permission=SUPERUSER, block=True)
tw_pjsk_gene_diffrank = on_command('tw生成难度csv', aliases={"tw生成难度json"}, priority=5, permission=SUPERUSER, block=True)


@pjsk_diffrank.handle()
@cn_pjsk_diffrank.handle()
@tw_pjsk_diffrank.handle()
async def _(matcher: Matcher, event: MessageEvent, arg: Message = CommandArg(), cmd: Tuple[str, ...] = Command()):
    command = cmd[0] if cmd else ''
    pjsk_type = get_pjsk_type(command)
    server_name = SERVER_MAP.get(pjsk_type, 'jp')

    # 参数识别
    fcap = 2 if 'ap' in command else 1 if 'fc' in command else 0
    tmp_arg = arg.extract_plain_text().strip()
    if not tmp_arg and fcap == 0:
        level = 0
        level_value = 0.0
        level_is_exact = False
        fcap = 2
        difficulty = 'master'
    else:
        difficulty_dict = {
            'ma': 'master', 'master': 'master',
            'ex': 'expert', 'expert': 'expert',
            'hd': 'hard', 'hard': 'hard',
            'nm': 'normal', 'normal': 'normal',
            'ez': 'easy', 'easy': 'easy'
        }
        for diff in difficulty_dict.keys():
            if tmp_arg.endswith(diff):
                difficulty = difficulty_dict[diff]
                level = tmp_arg.replace(diff, '').strip()
                break
            elif tmp_arg.startswith(diff):
                difficulty = difficulty_dict[diff]
                level = tmp_arg.replace(diff, '').strip()
                break
        else:
            difficulty = 'master'
            level = tmp_arg.strip()
        level_text = str(level).strip()
        try:
            level_value = float(level_text) if level_text else 0.0
        except ValueError:
            level_value = 0.0
        level = int(level_value) if level_value.is_integer() else level_value
        level_is_exact = '.' in level_text
        if not tmp_arg.strip() and level_value == 0 and fcap == 0:
            await matcher.finish(
                '参数错误，指令：难度排行 定数 难度\n'
                '难度支持的输入: easy/ez, normal/nm, hard/hd, expert/ex, master/ma，如：难度排行 28 expert'
            )
    logger.info(
        f"[diffrank] 查询开始 user={event.user_id} server={server_name} "
        f"level={level if level else 'all'} difficulty={difficulty} fcap={fcap}"
    )

    # 生成图片：读取原始难度表，并用 realtime/constants.csv 的定数覆盖。
    target = []
    data = [
        item.copy() if isinstance(item, dict) else item
        for item in load_master_data('musicDifficulties.json', pjsk_type)
    ]
    constants = load_constants(pjsk_type)
    if not constants and pjsk_type == 0:
        logger.info("[diffrank] realtime/constants.csv 不存在，尝试从 Google Sheets 下载...")
        await update_diff_from_sheet(pjsk_type=pjsk_type)
        constants = load_constants(pjsk_type)
    if not constants:
        logger.warning("[diffrank] 定数数据缺失，将使用整数 level 作为定数")
    _apply_constants(data, constants)
    if fcap == 0:
        title = f'{difficulty.upper()} {level if level != 0 else ""} 难度表（仅供参考）'
        playLevelKey = "playLevelAdjust"
    elif fcap == 1:
        title = f'{difficulty.upper()} {level if level != 0 else ""} FC难度表（仅供参考）'
        playLevelKey = "fullComboAdjust"
    else:
        title = f'{difficulty.upper()} {level if level != 0 else ""} AP难度表（仅供参考）'
        playLevelKey = "fullPerfectAdjust"

    musics = await async_load_master_data('musics.json', pjsk_type)
    for i in data:
        if isleak(i['musicId'], musics, pjsk_type=pjsk_type):
            continue
        if i['musicDifficulty'] != difficulty:
            continue
        if not i.get('playLevelAdjust'):
            for key in ["playLevelAdjust", "fullComboAdjust", "fullPerfectAdjust"]:
                i[key] = 0
        display_level = float(i['playLevel'] + i[playLevelKey])
        if level_value:
            if level_is_exact:
                if round(display_level, 1) != round(float(level_value), 1):
                    continue
            elif int(display_level) != int(level_value):
                continue
        target.append(i)

    logger.info(f"[diffrank] 命中歌曲难度数量: {len(target)}")
    if not target:
        await matcher.finish(
            f"没有找到 {difficulty.upper()} {level if level else ''} 的难度排行数据，"
            "请检查定数/难度参数是否正确。"
        )

    target.sort(key=lambda x: x['playLevel'] + x[playLevelKey], reverse=True)
    musicData = {}
    for music in target:
        if music.get('_hasConstant'):
            levelRound = f"{music['playLevel'] + music[playLevelKey]:.1f}"
        else:
            levelRound = str(music['playLevel']) + '.?'
        try:
            musicData[levelRound].append(music['musicId'])
        except KeyError:
            musicData[levelRound] = [music['musicId']]
    profile = None
    error = False
    userid, isprivate = await PjskBind.get_user_bind(event.user_id, pjsk_type=pjsk_type)
    if userid and not isprivate:
        profile = UserProfile()
        try:
            await profile.getsuite(userid=userid, pjsk_type=pjsk_type)
            rankPic = await singleLevelRankPic(musicData, difficulty, profile.musicResult, oneRowCount=None if level != 0 else 5, pjsk_type=pjsk_type)
        except Exception as e:
            logger.warning(f"[diffrank] 获取 Suite 成绩数据失败 user={userid} server={server_name}: {e}")
            try:
                await profile.getprofile(userid=userid, query_type='rank', pjsk_type=pjsk_type)
            except Exception:
                profile.isNewData = True
                error = True
            rankPic = await singleLevelRankPic(musicData, difficulty, oneRowCount=None if level != 0 else 5, pjsk_type=pjsk_type)
    else:
        rankPic = await singleLevelRankPic(musicData, difficulty, oneRowCount=None if level != 0 else 5, pjsk_type=pjsk_type)
    has_profile_header = profile is not None and not error
    header_h = DIFFRANK_HEADER_H if has_profile_header else DIFFRANK_STATUS_HEADER_H
    content_w = max(DIFFRANK_CANVAS_MIN_W - DIFFRANK_PAD * 2, rankPic.width)
    canvas_w = max(DIFFRANK_CANVAS_MIN_W, content_w + DIFFRANK_PAD * 2)
    rank_x = (canvas_w - rankPic.width) // 2
    title_y = header_h + 24
    rank_y = title_y + 86
    footer_y = rank_y + rankPic.height + 22
    canvas_h = footer_y + DIFFRANK_FOOTER_H + 26
    pic = _make_gradient_background(canvas_w, canvas_h)
    draw = ImageDraw.Draw(pic)

    cards = await async_load_master_data('cards.json', pjsk_type=pjsk_type)
    card_asset_map = {card.get('id'): card.get('assetbundleName', '') for card in cards if isinstance(card, dict)}
    user_suite_file = suite_path / server_name / f'{userid}.json' if userid else None
    suite_data = {}
    suite_update_text: Optional[str] = None
    if user_suite_file and user_suite_file.exists():
        mtime = user_suite_file.stat().st_mtime
        suite_data['upload_time'] = mtime
        suite_update_text = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(mtime))

    if has_profile_header:
        header_data = build_header_data_from_profile(profile, userid or '', isprivate, suite_data=suite_data)
        await draw_pjsk_profile_header(
            pic,
            (DIFFRANK_PAD, 24, canvas_w - DIFFRANK_PAD, header_h - 10),
            header_data,
            module_label='DIFFICULTY RANK',
            pjsk_type=pjsk_type,
            card_asset_map=card_asset_map,
            extra_badges=[('SERVER', server_name.upper())],
            show_cutout=True,
        )
    else:
        _draw_round_panel(pic, (DIFFRANK_PAD, 20, canvas_w - DIFFRANK_PAD, header_h - 8), radius=26, fill=DIFFRANK_PANEL, outline=DIFFRANK_LINE, shadow=True)
        status_title = '成绩已隐藏' if isprivate else '数据已无法获取'
        status_tip = '发送“给看”可查看歌曲成绩' if isprivate else '未读取到玩家打歌数据，将仅展示难度排序'
        icon_x = DIFFRANK_PAD + 58
        draw.rounded_rectangle((icon_x - 30, 46, icon_x + 30, 106), radius=18, fill=(255, 246, 251), outline=(245, 218, 232))
        draw.text((icon_x, 76), '♪', fill=DIFFRANK_ACCENT, font=_rodin(36), anchor='mm')
        draw.text((DIFFRANK_PAD + 112, 50), status_title, fill=DIFFRANK_TEXT, font=_bold(27), anchor='la')
        draw.text((DIFFRANK_PAD + 114, 92), status_tip, fill=DIFFRANK_MUTED, font=_medium(16), anchor='la')
        draw.rounded_rectangle((canvas_w - DIFFRANK_PAD - 144, 42, canvas_w - DIFFRANK_PAD - 24, 74), radius=16, fill=(88, 92, 118, 220))
        draw.text((canvas_w - DIFFRANK_PAD - 84, 58), server_name.upper(), fill=(255, 255, 255), font=_rodin(16), anchor='mm')
        draw.text((canvas_w - DIFFRANK_PAD - 24, header_h - 38), 'DIFFICULTY RANK', fill=DIFFRANK_MUTED, font=_rodin(16), anchor='ra')

    _draw_round_panel(pic, (DIFFRANK_PAD, title_y, canvas_w - DIFFRANK_PAD, title_y + 64), radius=24, fill=(255, 255, 255, 226), outline=DIFFRANK_LINE, shadow=True)
    draw.text((DIFFRANK_PAD + 28, title_y + 24), title.strip(), fill=DIFFRANK_TEXT, font=_bold(29), anchor='lm')
    draw.text((DIFFRANK_PAD + 30, title_y + 48), '按定数从高到低排列，定数非官方，仅供参考', fill=DIFFRANK_MUTED, font=_medium(13), anchor='lm')
    mode_text = 'AP' if fcap == 2 else 'FC' if fcap == 1 else 'CLEAR'
    mode_x1 = DIFFRANK_PAD + 36 + min(520, _text_width(_bold(29), title.strip()) + 20)
    draw.rounded_rectangle((mode_x1, title_y + 13, mode_x1 + 94, title_y + 45), radius=16, fill=DIFFRANK_ACCENT)
    draw.text((mode_x1 + 47, title_y + 29), mode_text, fill=(255, 255, 255), font=_rodin(17), anchor='mm')

    pic.paste(rankPic, (rank_x, rank_y), rankPic.split()[-1])

    update_file = get_constants_csv_path(pjsk_type)
    if not update_file.exists():
        update_file = data_path / 'jp' / 'musicDifficulties.json'
    updatetime = time.localtime(os.path.getmtime(update_file))
    _draw_round_panel(pic, (DIFFRANK_PAD, footer_y, canvas_w - DIFFRANK_PAD, footer_y + DIFFRANK_FOOTER_H - 18), radius=22, fill=(255, 255, 255, 205), outline=DIFFRANK_LINE, shadow=True)
    draw.text((DIFFRANK_PAD + 24, footer_y + 26), '定数来源：https://profile.pjsekai.moe/   ※三服共用 JP 定数，非官方', fill=DIFFRANK_ACCENT, font=_medium(17), anchor='lm')
    draw.text((DIFFRANK_PAD + 24, footer_y + 56), f'定数更新时间：{time.strftime("%Y-%m-%d %H:%M:%S", updatetime)}   ※定数每次统计时可能会改变', fill=DIFFRANK_MUTED, font=_medium(15), anchor='lm')
    if suite_update_text:
        draw.text((DIFFRANK_PAD + 24, footer_y + 82), f'用户数据上传时间：{suite_update_text}', fill=DIFFRANK_MUTED, font=_medium(15), anchor='lm')
    pic = pic.convert("RGB")
    await matcher.finish(image(b64=pic2b64(pic)))


@pjsk_gene_diffrank.handle()
@cn_pjsk_gene_diffrank.handle()
@tw_pjsk_gene_diffrank.handle()
async def _(matcher: Matcher, event: MessageEvent, cmd: Tuple[str, ...] = Command()):
    pjsk_type = get_pjsk_type(cmd[0])

    if 'csv' in cmd[0]:
        generate_diff_csv(pjsk_type=pjsk_type)
    else:
        generate_diff_json(pjsk_type=pjsk_type)
    await matcher.finish(f"成功{cmd[0]}")


# 每天凌晨3点自动从 Google Sheets 更新定数（仅JP服）
@scheduler.scheduled_job("cron", hour=3, minute=0)
async def _():
    logger.info("[diffrank] 开始从 Google Sheets 自动更新定数...")
    result = await update_diff_from_sheet(pjsk_type=0)
    if result:
        logger.info("[diffrank] 定数自动更新成功")
    else:
        logger.warning("[diffrank] 定数自动更新失败")


async def singleLevelRankPic(musicData, difficulty, musicResult=None, oneRowCount=None, pjsk_type: int = 0):
    diff = {
        'easy': 0,
        'normal': 1,
        'hard': 2,
        'expert': 3,
        'master': 4
    }
    color = {
        'master': (187, 51, 238),
        'expert': (238, 67, 102),
        'hard': (254, 170, 0),
        'normal': (51, 187, 238),
        'easy': (102, 221, 17),
    }
    iconName = {
        0: 'icon_notClear.png',
        1: 'icon_clear.png',
        2: 'icon_fullCombo.png',
        3: 'icon_allPerfect.png',
    }
    all_music_ids = [mid for ids in musicData.values() for mid in ids]
    jackets = await _prefetch_jackets(all_music_ids, pjsk_type)

    cover_size = 96
    cover_gap = 14
    label_w = 110
    top_pad = 20
    bottom_pad = 22
    block_gap = 18
    rank_blocks = []
    max_block_w = 0

    auto_row_count = oneRowCount is None
    if auto_row_count:
        max_group_count = max((len(ids) for ids in musicData.values()), default=1)
        oneRowCount = max(1, min(4, max_group_count))
    else:
        oneRowCount = max(1, min(4, oneRowCount))

    for rank, music_ids in musicData.items():
        rows = int((len(music_ids) - 1) / oneRowCount) + 1
        block_w = label_w + 28 + oneRowCount * cover_size + max(0, oneRowCount - 1) * cover_gap + 26
        block_h = top_pad + rows * cover_size + max(0, rows - 1) * cover_gap + bottom_pad
        block = Image.new('RGBA', (block_w, block_h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(block)
        _draw_round_panel(block, (0, 0, block_w - 8, block_h - 8), radius=24, fill=DIFFRANK_PANEL_STRONG, outline=(255, 255, 255, 245), shadow=True)

        diff_color = color[difficulty]
        badge_x = 18
        badge_y = 22
        draw.rounded_rectangle((badge_x, badge_y, badge_x + 76, badge_y + 42), radius=21, fill=diff_color)
        draw.text((badge_x + 38, badge_y + 20), str(rank), fill=(255, 255, 255), font=_rodin(25), anchor='mm')
        draw.text((badge_x + 38, badge_y + 66), difficulty.upper(), fill=DIFFRANK_MUTED, font=_rodin(13), anchor='mm')

        start_x = label_w + 22
        for idx, musicId in enumerate(music_ids):
            row = idx // oneRowCount
            col = idx % oneRowCount
            x = start_x + col * (cover_size + cover_gap)
            y = top_pad + row * (cover_size + cover_gap)
            jacket = jackets.get(musicId)
            if jacket is None:
                jacket = Image.new('RGBA', (120, 120), (230, 230, 230, 255))
                ImageDraw.Draw(jacket).text((28, 48), str(musicId), fill=(80, 80, 80), font=_bold(40))
            jacket = _rounded_image(jacket.resize((cover_size, cover_size), Image.Resampling.LANCZOS), radius=15)
            draw.rounded_rectangle((x - 3, y - 3, x + cover_size + 3, y + cover_size + 3), radius=18, fill=(255, 255, 255, 235))
            block.paste(jacket, (x, y), jacket.split()[-1])
            if musicResult is not None:
                try:
                    icon = _get_result_icon(iconName[musicResult[musicId][diff[difficulty]]]).resize((28, 28), Image.Resampling.LANCZOS)
                    block.paste(icon, (x + cover_size - 25, y + cover_size - 25), icon.split()[-1])
                except Exception:
                    pass
        rank_blocks.append(block)
        max_block_w = max(max_block_w, block_w)

    column_gap = 22
    columns = 2 if len(rank_blocks) > 1 else 1
    rows = [rank_blocks[i:i + columns] for i in range(0, len(rank_blocks), columns)]
    canvas_w = max_block_w * columns + column_gap * (columns - 1)
    canvas_h = sum(max(block.height for block in row) for row in rows) + max(0, len(rows) - 1) * block_gap
    pic = Image.new('RGBA', (canvas_w, max(1, canvas_h)), (0, 0, 0, 0))
    y = 0
    for row in rows:
        row_h = max(block.height for block in row)
        for col, block in enumerate(row):
            x = col * (max_block_w + column_gap)
            pic.paste(block, (x, y), block.split()[-1])
        y += row_h + block_gap

    return pic