import asyncio
import json
import time
from io import BytesIO
from pathlib import Path
from typing import Tuple

from nonebot import on_command
from nonebot.adapters.onebot.v11 import Message, MessageEvent
from nonebot.internal.matcher import Matcher
from nonebot.params import Command, CommandArg
from PIL import Image, ImageDraw

from services.log import logger
from utils.imageutils import pic2b64
from utils.message_builder import image

from .._autoask import pjsk_update_manager
from .._config import BUG_ERROR, SERVER_MAP, data_path, suite_path
from .._errors import apiCallError, maintenanceIn, pjskError, userIdBan
from .._haruki_remote import render_profile
from .._models import UserProfile
from .._utils import (
    generatehonor,
    get_pjsk_font,
    get_pjsk_type,
    get_userid_preprocess,
    master_data_by_id,
    open_pjsk_image,
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


_DIFF_NAMES = ["easy", "normal", "hard", "expert", "master"]


def _asset_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(data_path.parent.parent.resolve()))
    except Exception:
        return str(path)


async def _build_remote_profile_payload(profile: UserProfile, userid: str, is_private: bool, pjsk_type: int, server_name: str) -> dict:
    cards_by_id = master_data_by_id('cards.json', pjsk_type)
    pcards = []
    leader_image_path = _asset_path(data_path / 'chara' / 'chr_ts_1.png')

    for idx, card_id in enumerate(profile.userDecks[:5] if profile.userDecks else []):
        card = cards_by_id.get(card_id) or {}
        asset_name = card.get('assetbundleName')
        if not asset_name:
            continue
        is_after_training = bool(idx < len(profile.special_training) and profile.special_training[idx])
        suffix = 'after_training' if is_after_training else 'normal'
        try:
            await pjsk_update_manager.get_asset('startapp/thumbnail/chara', f'{asset_name}_{suffix}.png', pjsk_type=pjsk_type)
        except Exception:
            pass
        thumb_path = data_path / server_name / 'startapp' / 'thumbnail' / 'chara' / f'{asset_name}_{suffix}.png'
        if idx == 0:
            leader_image_path = _asset_path(thumb_path)
        rarity = card.get('cardRarityType', 'rarity_1')
        attr = card.get('attr', 'cool')
        train_rank = 5 if rarity in ('rarity_3', 'rarity_4') else None
        pcards.append({
            'card_id': card_id,
            'card_thumbnail_path': _asset_path(thumb_path),
            'rare': rarity,
            'frame_img_path': _asset_path(data_path / 'chara' / f'cardFrame_{rarity}.png'),
            'attr_img_path': _asset_path(data_path / 'chara' / f'icon_attribute_{attr}.png'),
            'rare_img_path': _asset_path(data_path / 'chara' / ('rarity_star_afterTraining.png' if is_after_training else 'rarity_star_normal.png')),
            'birthday_icon_path': _asset_path(data_path / 'chara' / 'rarity_birthday.png'),
            'train_rank': train_rank,
            'train_rank_img_path': _asset_path(data_path / 'chara' / f'train_rank_{train_rank}.png') if train_rank else None,
            'is_after_training': is_after_training,
            'is_pcard': True,
        })

    music_counts = []
    for idx, diff in enumerate(_DIFF_NAMES):
        def _int_count(values, default=0):
            try:
                value = values[idx]
                return value if isinstance(value, int) else default
            except Exception:
                return default
        music_counts.append({
            'difficulty': diff,
            'clear': _int_count(profile.clear),
            'fc': _int_count(profile.full_combo),
            'ap': _int_count(profile.full_perfect),
        })
    music_counts.append({'difficulty': 'append', 'clear': 0, 'fc': 0, 'ap': 0})

    character_rank = []
    for item in profile.characterRank or []:
        if not isinstance(item, dict):
            continue
        cid = item.get('characterId')
        if cid is None:
            continue
        character_rank.append({'character_id': cid, 'rank': item.get('characterRank', 0)})

    chara_icon_map = {
        str(cid): _asset_path(data_path / 'chara' / f'chr_ts_{cid}.png')
        for cid in range(1, 27)
    }

    return {
        'profile': {
            'id': str(userid),
            'region': server_name,
            'nickname': profile.name or '???',
            'is_hide_uid': is_private,
            'leader_image_path': leader_image_path,
            'has_frame': False,
        },
        'rank': profile.rank or 0,
        'twitter_id': profile.twitterId or '',
        'word': profile.word or '',
        'pcards': pcards,
        'honors': [],
        'music_difficulty_count': music_counts,
        'character_rank': character_rank,
        'update_time': profile.updatedAt or int(time.time()),
        'lv_rank_bg_path': _asset_path(data_path / 'pics' / 'bg.png'),
        'x_icon_path': _asset_path(data_path / 'pics' / 'youtube.png'),
        'icon_clear_path': _asset_path(data_path / 'pics' / 'icon_clear.png'),
        'icon_fc_path': _asset_path(data_path / 'pics' / 'icon_fullCombo.png'),
        'icon_ap_path': _asset_path(data_path / 'pics' / 'icon_allPerfect.png'),
        'chara_rank_icon_path_map': chara_icon_map,
    }


# # ============ 旧版个人档案（已注释） ============
# pjsk_profile = on_command('烧烤档案', aliases={"profile", "pjskprofile", "个人信息"}, priority=5, block=True)
# cn_profile = on_command('cn烧烤档案', aliases={"cnprofile", "cnpjskprofile", "cn个人信息"}, priority=5, block=True)
# tw_profile = on_command('tw烧烤档案', aliases={"twprofile", "twpjskprofile", "tw个人信息"}, priority=5, block=True)
#
#
# @pjsk_profile.handle()
# @cn_profile.handle()
# @tw_profile.handle()
# async def _(matcher: Matcher, event: MessageEvent, msg: Message = CommandArg(), cmd: Tuple[str, ...] = Command()):
#     pjsk_type = get_pjsk_type(cmd[0])
#     
#     server_name = SERVER_MAP.get(pjsk_type, 'jp')
#
#     # 参数解析
#     state = await get_userid_preprocess(event, msg, pjsk_type=pjsk_type)
#     if reply := state['error']:
#         await matcher.finish(reply, at_sender=True)
#     userid = state['userid']
#     isprivate = state['private']
#     
#     # 获取信息
#     profile = UserProfile()
#     try:
#         await profile.getprofile(userid, 'profile', is_force_update=True, pjsk_type=pjsk_type)
#     except pjskError as e :
#         await matcher.finish(str(e))
#     except (maintenanceIn, apiCallError, userIdBan) as e:
#         await matcher.finish(str(e))
#     except:
#         await matcher.finish(BUG_ERROR)
#     
#     remote_pic = None
#     try:
#         remote_pic = await render_profile(
#             await _build_remote_profile_payload(profile, userid, isprivate, pjsk_type, server_name)
#         )
#     except Exception as e:
#         logger.warning(f"[profile] 远端绘图失败，回退本地实现: {e}")
#
#     if remote_pic:
#         try:
#             remote_img = Image.open(BytesIO(remote_pic)).convert('RGB')
#             await matcher.finish(image(b64=pic2b64(remote_img)))
#         except Exception as e:
#             logger.warning(f"[profile] 远端图片转换失败，回退本地实现: {e}")
#
#     # 生成图片
#     id = '保密' if isprivate else userid
#     img = open_pjsk_image(data_path / 'pics' / 'bg.png')
#     cards_by_id = master_data_by_id('cards.json', pjsk_type)
#
#     async def _get_deck_card(index: int):
#         try:
#             card_id = profile.userDecks[index]
#             card = cards_by_id.get(card_id) or {}
#             assetbundleName = card.get('assetbundleName', '')
#             if not assetbundleName:
#                 return None
#             suffix = 'after_training' if profile.special_training[index] else 'normal'
#             return await pjsk_update_manager.get_asset(
#                 r'startapp/thumbnail/chara', rf'{assetbundleName}_{suffix}.png',
#                 pjsk_type=pjsk_type
#             )
#         except (FileNotFoundError, AttributeError, IndexError, TypeError):
#             return None
#
#     deck_imgs = await asyncio.gather(*[_get_deck_card(i) for i in range(5)], return_exceptions=True)
#     main_card = deck_imgs[0] if deck_imgs and not isinstance(deck_imgs[0], Exception) else None
#     if main_card is not None:
#         cardimg = main_card.resize((151, 151))
#         img.paste(cardimg, (118, 51), cardimg.split()[-1])
#     draw = ImageDraw.Draw(img)
#     font_style = get_pjsk_font("SourceHanSansCN-Bold.otf", 45)
#     draw.text((295, 45), profile.name, fill=(0, 0, 0), font=font_style)
#     font_style = get_pjsk_font("FOT-RodinNTLGPro-DB.ttf", 20)
#     draw.text((298, 116), f'id:{id}', fill=(0, 0, 0), font=font_style)
#     font_style = get_pjsk_font("FOT-RodinNTLGPro-DB.ttf", 34)
#     draw.text((415, 157), str(profile.rank), fill=(255, 255, 255), font=font_style)
#     font_style = get_pjsk_font("FOT-RodinNTLGPro-DB.ttf", 22)
#     draw.text((182, 318), str(profile.twitterId), fill=(0, 0, 0), font=font_style)
#     font_style = get_pjsk_font("SourceHanSansCN-Medium.otf", 24)
#     if len(profile.word) > 17:
#         draw.text((132, 388), profile.word[:17], fill=(0, 0, 0), font=font_style)
#         draw.text((132, 424), profile.word[17:], fill=(0, 0, 0), font=font_style)
#     else:
#         draw.text((132, 388), profile.word, fill=(0, 0, 0), font=font_style)
#     error_flag = True
#     for i, cardimg in enumerate(deck_imgs):
#         if isinstance(cardimg, Exception) or cardimg is None:
#             if error_flag:
#                 await matcher.send("部分资源加载失败，重新发送中...")
#                 error_flag = False
#             continue
#         img.paste(cardimg, (111 + 128 * i, 488), cardimg.split()[-1])
#     font_style = get_pjsk_font("FOT-RodinNTLGPro-DB.ttf", 27)
#     for i in range(0, 5):
#         text_width = font_style.getsize(str(profile.clear[i]))
#         text_coordinate = (int(170 + 132 * i - text_width[0] / 2), int(735 - text_width[1] / 2))
#         draw.text(text_coordinate, str(profile.clear[i]), fill=(0, 0, 0), font=font_style)
#
#         text_width = font_style.getsize(str(profile.full_combo[i]))
#         text_coordinate = (int(170 + 132 * i - text_width[0] / 2), int(735 + 133 - text_width[1] / 2))
#         draw.text(text_coordinate, str(profile.full_combo[i]), fill=(0, 0, 0), font=font_style)
#
#         text_width = font_style.getsize(str(profile.full_perfect[i]))
#         text_coordinate = (int(170 + 132 * i - text_width[0] / 2), int(735 + 2 * 133 - text_width[1] / 2))
#         draw.text(text_coordinate, str(profile.full_perfect[i]), fill=(0, 0, 0), font=font_style)
#
#     character = 0
#     font_style = get_pjsk_font("FOT-RodinNTLGPro-DB.ttf", 29)
#     character_rank_map = {
#         i.get('characterId'): i.get('characterRank', 0)
#         for i in profile.characterRank
#         if isinstance(i, dict)
#     }
#     for i in range(0, 5):
#         for j in range(0, 4):
#             character = character + 1
#             characterRank = character_rank_map.get(character, 0)
#             text_width = font_style.getsize(str(characterRank))
#             text_coordinate = (int(920 + 183 * j - text_width[0] / 2), int(686 + 88 * i - text_width[1] / 2))
#             draw.text(text_coordinate, str(characterRank), fill=(0, 0, 0), font=font_style)
#
#     for i in range(0, 2):
#         for j in range(0, 4):
#             character = character + 1
#             characterRank = character_rank_map.get(character, 0)
#             text_width = font_style.getsize(str(characterRank))
#             text_coordinate = (int(920 + 183 * j - text_width[0] / 2), int(510 + 88 * i - text_width[1] / 2))
#             draw.text(text_coordinate, str(characterRank), fill=(0, 0, 0), font=font_style)
#             if character == 26:
#                 break
#     # 并行添加牌子图片
#     honor_tasks = []
#     for i in profile.userProfileHonors:
#         if i['seq'] == 1:
#             honor_tasks.append(generatehonor(i, True, profile.userHonorMissions, pjsk_type=pjsk_type))
#         elif i['seq'] in [2, 3]:
#             honor_tasks.append(generatehonor(i, False, profile.userHonorMissions, pjsk_type=pjsk_type))
#     
#     if honor_tasks:
#         honor_results = await asyncio.gather(*honor_tasks, return_exceptions=True)
#         honor_idx = 0
#         for i in profile.userProfileHonors:
#             if i['seq'] not in [1, 2, 3]: continue
#             res = honor_results[honor_idx]
#             honor_idx += 1
#             if isinstance(res, Exception): continue
#             
#             if i['seq'] == 1:
#                 res = res.resize((266, 56))
#                 img.paste(res, (104, 228), res.split()[-1])
#             elif i['seq'] == 2:
#                 res = res.resize((126, 56))
#                 img.paste(res, (375, 228), res.split()[-1])
#             elif i['seq'] == 3:
#                 res = res.resize((126, 56))
#                 img.paste(res, (508, 228), res.split()[-1])
#     # 添加文字
#     draw.text((952, 141), f'{profile.mvpCount}回', fill=(0, 0, 0), font=font_style)
#     draw.text((1259, 141), f'{profile.superStarCount}回', fill=(0, 0, 0), font=font_style)
#     try:
#         chara = open_pjsk_image(data_path / 'chara' / f'chr_ts_{profile.characterId}.png')
#         chara = chara.resize((70, 70))
#         img.paste(chara, (952, 293), chara.split()[-1])
#         draw.text((1032, 315), str(profile.highScore), fill=(0, 0, 0), font=font_style)
#     except:
#         pass
#     if not profile.isNewData:
#         font_style = get_pjsk_font("SourceHanSansCN-Bold.otf", 25)
#         user_suite_file = suite_path / server_name / f'{userid}.json'
#         if user_suite_file.exists():
#             mtime = user_suite_file.stat().st_mtime
#             updatetime = time.localtime(mtime)
#             draw.text(
#                 (68, 10), '数据上传时间：' + time.strftime("%Y-%m-%d %H:%M:%S", updatetime),
#                 fill=(100, 100, 100), font=font_style
#             )
#     # 发送图片
#     await matcher.finish(image(b64=pic2b64(img)))


# ============ 新版个人信息（替代旧版） ============
from utils.http_utils import AsyncHttpx
from utils.utils import get_message_img

from ._draw_new import (
    draw_new_profile,
    get_user_bg_settings,
    remove_user_bg,
    save_user_bg,
    set_user_bg_settings,
)

pjsk_profile = on_command('烧烤档案', aliases={"profile", "pjskprofile", "个人信息"}, priority=5, block=True)
cn_profile = on_command('cn烧烤档案', aliases={"cnprofile", "cnpjskprofile", "cn个人信息"}, priority=5, block=True)
tw_profile = on_command('tw烧烤档案', aliases={"twprofile", "twpjskprofile", "tw个人信息"}, priority=5, block=True)


@pjsk_profile.handle()
@cn_profile.handle()
@tw_profile.handle()
async def _(matcher: Matcher, event: MessageEvent, msg: Message = CommandArg(), cmd: Tuple[str, ...] = Command()):
    pjsk_type = get_pjsk_type(cmd[0])
    server_name = SERVER_MAP.get(pjsk_type, 'jp')

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

    img = await draw_new_profile(profile, userid, isprivate, pjsk_type)
    await matcher.finish(image(b64=pic2b64(img)))


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
