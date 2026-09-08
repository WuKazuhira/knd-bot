from typing import Tuple

from nonebot import on_command
from nonebot.adapters.onebot.v11 import Message, MessageEvent
from nonebot.internal.matcher import Matcher
from nonebot.params import Command, CommandArg
from PIL import Image

from services.pjsk_draw import render
from services.pjsk_draw.profile_bg import (
    get_user_bg_settings,
    remove_user_bg,
    save_user_bg,
    set_user_bg_settings,
)
from services.pjsk_draw.renderers.profile import ProfileView
from utils.message_builder import image

from .._config import BUG_ERROR, SERVER_MAP
from .._errors import apiCallError, maintenanceIn, pjskError, userIdBan
from .._models import UserProfile
from .._utils import (
    get_pjsk_type,
    get_userid_preprocess,
)

__plugin_name__ = "烧烤档案/pjskprofile"
__plugin_type__ = "烧烤相关&uni移植"
__plugin_version__ = 0.1
__plugin_usage__ = f"""
usage：
    查询烧烤档案
    若群内已有unibot请勿开启此bot该功能
    私聊可用，限制每人1分钟只能查询2次
    指令：
        烧烤档案/个人消息/profile/pjskprofile              :查看自己的收歌情况
        烧烤档案/个人消息/profile/pjskprofile @qq          :查看艾特用户的收歌情况(对方必须已绑定烧烤账户)
        烧烤档案/个人消息/profile/pjskprofile 烧烤id        :查看对应烧烤账号的收歌情况
        烧烤档案/个人消息/profile/pjskprofile 活动排名       :查看当期活动排名对应烧烤用户的收歌情况
    注意：
        实时信息的ap数已经有了，所以pjskprofile2的指令不再有用
    数据来源：
        pjsekai.moe
        unipjsk.com
""".strip()
__plugin_settings__ = {
    "default_status": False,
    "cmd": ["pjskprofile", "烧烤相关", "烧烤档案", "profile", "个人信息"],
}
__plugin_cd_limit__ = {
    "cd": 60, "count_limit": 2, "rst": "别急，等[cd]秒后再用！", "limit_type": "user"
}
__plugin_block_limit__ = {"rst": "别急，还在查！"}



# ============ 新版个人信息 ============
from utils.http_utils import AsyncHttpx
from utils.utils import get_message_img

pjsk_profile = on_command('烧烤档案', aliases={"profile", "pjskprofile", "个人信息"}, priority=5, block=True)
cn_profile = on_command('cn烧烤档案', aliases={"cnprofile", "cnpjskprofile", "cn个人信息"}, priority=5, block=True)
tw_profile = on_command('tw烧烤档案', aliases={"twprofile", "twpjskprofile", "tw个人信息"}, priority=5, block=True)


@pjsk_profile.handle()
@cn_profile.handle()
@tw_profile.handle()
async def _(matcher: Matcher, event: MessageEvent, msg: Message = CommandArg(), cmd: Tuple[str, ...] = Command()):
    pjsk_type = get_pjsk_type(cmd[0])

    state = await get_userid_preprocess(event, msg, pjsk_type=pjsk_type)
    if reply := state['error']:
        await matcher.finish(reply, at_sender=True)
    userid = state['userid']
    isprivate = state['private']

    profile = UserProfile()
    try:
        await profile.getprofile(userid, 'profile', is_force_update=True, pjsk_type=pjsk_type)
    except pjskError as e:
        await matcher.finish(str(e))
    except (maintenanceIn, apiCallError, userIdBan) as e:
        await matcher.finish(str(e))
    except:
        await matcher.finish(BUG_ERROR)

    # 数据收集完成，出图交给绘图服务。
    pic = await render('profile', {
        'profile': ProfileView.payload_from_profile(profile),
        'userid': userid,
        'is_private': isprivate,
        'pjsk_type': pjsk_type,
    })
    await matcher.finish(image(pic))


# ============ 上传个人信息背景 ============
upload_profile_bg = on_command('上传个人信息背景', aliases={'上传个人背景', 'cn上传个人信息背景', 'cn上传个人背景', 'tw上传个人信息背景'}, priority=5, block=True)


@upload_profile_bg.handle()
async def _(matcher: Matcher, event: MessageEvent, msg: Message = CommandArg(), cmd: Tuple[str, ...] = Command()):
    pjsk_type = get_pjsk_type(cmd[0])
    server_name = SERVER_MAP.get(pjsk_type, 'jp')

    state = await get_userid_preprocess(event, msg, pjsk_type=pjsk_type)
    if reply := state['error']:
        await matcher.finish(reply, at_sender=True)
    userid = state['userid']

    # 获取图片URL
    img_urls = get_message_img(event.json())
    if not img_urls:
        await matcher.finish("请在指令中附带一张图片作为背景", at_sender=True)

    # 下载图片
    try:
        response = await AsyncHttpx.get(img_urls[0], timeout=30)
        from io import BytesIO
        bg_img = Image.open(BytesIO(response.content)).convert('RGB')
    except Exception as e:
        await matcher.finish(f"下载图片失败: {e}", at_sender=True)

    # 保存
    save_user_bg(userid, server_name, bg_img)
    await matcher.finish("背景设置成功！使用「cn调整个人信息」可以调整方向、模糊、透明度", at_sender=True)


# ============ 清除个人信息背景 ============
clear_profile_bg = on_command('清除个人信息背景', aliases={'清空个人信息背景', '清除个人背景', 'cn清除个人信息背景', 'cn清空个人信息背景', 'cn清除个人背景', 'tw清除个人信息背景'}, priority=5, block=True)


@clear_profile_bg.handle()
async def _(matcher: Matcher, event: MessageEvent, msg: Message = CommandArg(), cmd: Tuple[str, ...] = Command()):
    pjsk_type = get_pjsk_type(cmd[0])
    server_name = SERVER_MAP.get(pjsk_type, 'jp')

    state = await get_userid_preprocess(event, msg, pjsk_type=pjsk_type)
    if reply := state['error']:
        await matcher.finish(reply, at_sender=True)
    userid = state['userid']

    remove_user_bg(userid, server_name)
    await matcher.finish("已清除个人信息背景，将使用默认背景", at_sender=True)


# ============ 调整个人信息 ============
adjust_profile = on_command('调整个人信息', aliases={'设置个人信息', 'cn调整个人信息', 'cn设置个人信息', 'tw调整个人信息'}, priority=5, block=True)


@adjust_profile.handle()
async def _(matcher: Matcher, event: MessageEvent, msg: Message = CommandArg(), cmd: Tuple[str, ...] = Command()):
    pjsk_type = get_pjsk_type(cmd[0])
    server_name = SERVER_MAP.get(pjsk_type, 'jp')

    state = await get_userid_preprocess(event, msg, pjsk_type=pjsk_type)
    if reply := state['error']:
        await matcher.finish(reply, at_sender=True)
    userid = state['userid']

    args = str(msg).strip()
    if not args:
        # 显示当前设置
        settings = get_user_bg_settings(userid, server_name)
        vertical = settings.get('vertical', False)
        blur_val = settings.get('blur', 1) or 1
        alpha_val = settings.get('alpha', 180) or 180
        transparency = 100 - int(alpha_val * 100 / 255)
        reply_msg = (
            f"当前个人信息设置:\n"
            f"方向: {'竖屏' if vertical else '横屏'}\n"
            f"模糊度: {blur_val}\n"
            f"透明度: {transparency}%\n"
            f"---\n"
            f"调整方向: cn调整个人信息 竖屏/横屏\n"
            f"调整模糊: cn调整个人信息 模糊 0~10\n"
            f"调整透明: cn调整个人信息 透明 0~100"
        )
        await matcher.finish(reply_msg, at_sender=True)

    vertical = None
    blur_val = None
    alpha_val = None

    # 解析方向
    if '竖屏' in args or '竖向' in args or '竖版' in args:
        vertical = True
    elif '横屏' in args or '横向' in args or '横版' in args:
        vertical = False

    # 解析模糊
    if '模糊' in args:
        try:
            parts = args.split('模糊')
            num_str = ''
            for c in parts[1].strip():
                if c.isdigit():
                    num_str += c
                elif num_str:
                    break
            if num_str:
                blur_val = max(0, min(10, int(num_str)))
        except Exception:
            pass

    # 解析透明度
    if '透明' in args:
        try:
            parts = args.split('透明')
            num_str = ''
            for c in parts[1].strip():
                if c.isdigit():
                    num_str += c
                elif num_str:
                    break
            if num_str:
                transparency = max(0, min(100, int(num_str)))
                alpha_val = (100 - transparency) * 255 // 100
        except Exception:
            pass

    if vertical is None and blur_val is None and alpha_val is None:
        await matcher.finish("无法识别参数，请使用: 竖屏/横屏/模糊N/透明N", at_sender=True)

    set_user_bg_settings(userid, server_name, vertical=vertical, blur=blur_val, alpha=alpha_val)

    # 显示更新后的设置
    settings = get_user_bg_settings(userid, server_name)
    v = settings.get('vertical', False)
    b = settings.get('blur', 1) or 1
    a = settings.get('alpha', 180) or 180
    t = 100 - int(a * 100 / 255)
    await matcher.finish(
        f"设置已更新: {'竖屏' if v else '横屏'} 模糊{b} 透明{t}%",
        at_sender=True
    )
