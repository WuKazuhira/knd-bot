import json
import time
from typing import Tuple

from nonebot import on_command
from nonebot.adapters.onebot.v11 import Message, MessageEvent
from nonebot.internal.matcher import Matcher
from nonebot.params import Command, CommandArg
from PIL import Image, ImageDraw

from services.log import logger
from utils.imageutils import pic2b64
from utils.message_builder import image

from .._config import BUG_ERROR, SERVER_MAP, suite_path
from .._errors import pjskError
from .._models import UserProfile
from .._profile_header import build_header_data_from_profile, draw_pjsk_profile_header
from .._song_utils import jinduChart
from .._utils import get_pjsk_font, get_pjsk_type, get_userid_preprocess, master_data_by_id

__plugin_name__ = "烧烤进度/pjsk进度"
__plugin_type__ = "烧烤相关&uni移植"
__plugin_version__ = 0.1
__plugin_usage__ = f"""
usage：
    查询烧烤收歌进度
    若群内已有unibot请勿开启此bot该功能
    私聊可用，限制每人1分钟只能查询2次

    默认难度为master，若带参数ex、expert可以查询expert谱面收歌进度
    指令：
        烧烤进度/pjsk进度/pjskrop       ?[ex,ma]       :查看自己的收歌进度
        烧烤进度/pjsk进度/pjskrop @qq   ?[ex,ma]       :查看艾特用户的收歌进度(对方必须已绑定烧烤账户)
        烧烤进度/pjsk进度/pjskrop 烧烤id ?[ex,ma]       :查看对应烧烤账号的收歌进度
    数据来源：
        pjsekai.moe
        unipjsk.com
""".strip()
__plugin_settings__ = {
    "default_status": False,
    "cmd": ["pjsk进度", "烧烤进度", "烧烤相关"],
}
__plugin_cd_limit__ = {"cd": 60, "count_limit": 2, "rst": "别急，等[cd]秒后再用！", "limit_type": "user"}
__plugin_block_limit__ = {"rst": "别急，还在查！"}

# pjsk进度
pjsk_progress = on_command('pjsk进度', aliases={'pjskrop', "烧烤进度"}, priority=5, block=True)
cn_progress = on_command('cnpjsk进度', aliases={'cnpjskrop', "cn烧烤进度"}, priority=5, block=True)
tw_progress = on_command('twpjsk进度', aliases={'twpjskrop', "tw烧烤进度"}, priority=5, block=True)


def _rop_gradient_bg(width: int, height: int, diff: str) -> Image.Image:
    if diff == 'expert':
        top, bottom = (255, 246, 250), (255, 235, 242)
    else:
        top, bottom = (248, 246, 255), (236, 244, 255)
    img = Image.new('RGB', (width, height), top)
    draw = ImageDraw.Draw(img)
    for y in range(height):
        t = y / max(1, height - 1)
        color = tuple(int(top[i] * (1 - t) + bottom[i] * t) for i in range(3))
        draw.line((0, y, width, y), fill=color)
    return img


def _rop_panel(base: Image.Image, xy, radius: int = 24, fill=(255, 255, 255, 218), outline=(255, 255, 255, 232)):
    overlay = Image.new('RGBA', base.size, (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    od.rounded_rectangle(xy, radius=radius, fill=fill, outline=outline)
    base.paste(overlay, (0, 0), overlay.split()[-1])


def _draw_stat_bar(draw: ImageDraw.ImageDraw, xy, ratio: float, color):
    x1, y1, x2, y2 = xy
    ratio = max(0.0, min(1.0, ratio))
    draw.rounded_rectangle(xy, radius=(y2 - y1) // 2, fill=(238, 234, 246))
    if ratio > 0:
        draw.rounded_rectangle((x1, y1, x1 + int((x2 - x1) * ratio), y2), radius=(y2 - y1) // 2, fill=color)


def _draw_level_card(draw: ImageDraw.ImageDraw, level: int, values, xy):
    x, y, w, h = xy
    ap, fc, clear, total = values
    total = max(int(total or 0), 1)
    draw.rounded_rectangle((x, y, x + w, y + h), radius=18, fill=(255, 255, 255, 224), outline=(255, 255, 255, 245))
    draw.rounded_rectangle((x + 12, y + 13, x + 78, y + h - 13), radius=16, fill=(244, 238, 255), outline=(224, 214, 246))
    draw.text((x + 45, y + h // 2), f"Lv.{level}", fill=(74, 54, 86), font=get_pjsk_font("SourceHanSansCN-Bold.otf", 18), anchor="mm")

    compact = h < 62
    font_label = get_pjsk_font("FOT-RodinNTLGPro-DB.ttf", 10 if compact else 11)
    font_num = get_pjsk_font("SourceHanSansCN-Bold.otf", 13 if compact else 15)
    stats = [
        ("AP", ap, (228, 159, 251)),
        ("FC", fc, (254, 143, 249)),
        ("CLEAR", clear, (255, 199, 92)),
    ]
    sx = x + 92
    bar_x1 = x + 186
    bar_x2 = x + w - 16
    row_gap = 14 if compact else 18
    start_y = y + 7 if compact else y + 11
    bar_h = 7 if compact else 9
    for idx, (label, value, color) in enumerate(stats):
        yy = start_y + idx * row_gap
        draw.text((sx, yy + bar_h // 2), label, fill=(130, 104, 138), font=font_label, anchor="lm")
        draw.text((sx + 58, yy + bar_h // 2), f"{int(value)}/{total}", fill=(64, 48, 72), font=font_num, anchor="mm")
        _draw_stat_bar(draw, (bar_x1, yy, bar_x2, yy + bar_h), int(value or 0) / total, color)


@pjsk_progress.handle()
@cn_progress.handle()
@tw_progress.handle()
async def _(matcher: Matcher, event: MessageEvent, msg: Message = CommandArg(), cmd: Tuple[str, ...] = Command()):
    pjsk_type = get_pjsk_type(cmd[0])
    
    server_name = SERVER_MAP.get(pjsk_type, 'jp')

    # 参数解析
    arg = msg.extract_plain_text().strip()
    if 'ex' in arg.lower() or 'expert' in arg.lower():
        diff = 'expert'
    else:
        diff = 'master'
    state = await get_userid_preprocess(event, msg, pjsk_type=pjsk_type)
    if reply := state['error']:
        await matcher.finish(reply, at_sender=True)
    userid = state['userid']
    isprivate = state['private']
    # 用户信息
    profile = UserProfile()
    try:
        await profile.getsuite(userid, pjsk_type=pjsk_type)
    except pjskError as e:
        await matcher.finish(str(e))
    except Exception as e:
        import traceback
        logger.error(f"[rop] 获取profile失败: {e}")
        logger.error(f"[rop] 错误堆栈: {traceback.format_exc()}")
        await matcher.finish(BUG_ERROR)
    
        # 生成图片
    img = _rop_gradient_bg(1050, 1000, diff)
    _rop_panel(img, (36, 226, 1014, 710), radius=26, fill=(255, 255, 255, 148), outline=(255, 255, 255, 220))
    _rop_panel(img, (36, 728, 1014, 948), radius=26, fill=(255, 255, 255, 178), outline=(255, 255, 255, 220))
    cards_by_id = master_data_by_id('cards.json', pjsk_type)
    card_asset_map = {cid: card.get('assetbundleName', '') for cid, card in cards_by_id.items() if isinstance(card, dict)}
    title = "MASTER PROGRESS" if diff == 'master' else "EXPERT PROGRESS"
    await draw_pjsk_profile_header(
        img,
        (36, 28, 1014, 192),
        build_header_data_from_profile(profile, userid, isprivate),
        module_label=title,
        pjsk_type=pjsk_type,
        card_asset_map=card_asset_map,
        compact=True,
        show_cutout=False,
    )
    draw = ImageDraw.Draw(img)
    if diff == 'master':
        levelmin = 26
    else:
        levelmin = 21
        profile.masterscore = profile.expertscore

    draw.text((64, 238), "LEVEL PROGRESS", fill=(74, 54, 86), font=get_pjsk_font("FOT-RodinNTLGPro-DB.ttf", 18))
    draw.text((920, 238), "AP / FC / CLEAR", fill=(130, 104, 138), font=get_pjsk_font("FOT-RodinNTLGPro-DB.ttf", 13), anchor="ra")

    for i in range(0, 5):
        level = i + levelmin
        values = profile.masterscore.get(level, [0, 0, 0, 0])
        _draw_level_card(draw, level, values, (64, 266 + i * 82, 430, 68))

    secondRawCount = 7 if diff == 'master' else 6
    for i in range(0, secondRawCount):
        level = i + levelmin + 5
        values = profile.masterscore.get(level, [0, 0, 0, 0])
        _draw_level_card(draw, level, values, (556, 266 + i * 60, 430, 54))
    chart = jinduChart(profile.masterscore)
    img.paste(chart, (58, 728), chart.split()[-1])
    draw.text((996, 918), "PROGRESS", fill=(120, 80, 100), font=get_pjsk_font("FOT-RodinNTLGPro-DB.ttf", 18), anchor="rm")
    # 上传时间
    if not profile.isNewData:
        font_style = get_pjsk_font("SourceHanSansCN-Bold.otf", 25)
        user_suite_file = suite_path / server_name / f'{userid}.json'
        if user_suite_file.exists():
            mtime = user_suite_file.stat().st_mtime
            updatetime = time.localtime(mtime)
            draw.text(
                (54, 960), '数据更新于：' + time.strftime("%Y-%m-%d %H:%M:%S", updatetime),
                fill=(92, 72, 98), font=font_style
            )
    # 发送图片
    await matcher.finish(image(b64=pic2b64(img)))
