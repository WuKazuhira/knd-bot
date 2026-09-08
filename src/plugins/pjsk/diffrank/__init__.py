import os
import time
from typing import Tuple

from nonebot import on_command
from nonebot.adapters.onebot.v11 import Message, MessageEvent
from nonebot.internal.matcher import Matcher
from nonebot.params import Command, CommandArg
from nonebot.permission import SUPERUSER

from manager import group_manager
from services.log import logger
from services.pjsk_draw import render
from utils.message_builder import image
from utils.utils import scheduler

from .._config import SERVER_MAP, data_path, suite_path
from .._models import PjskBind, UserProfile
from .._profile_header import build_header_payload
from .._song_utils import isleak
from .._utils import async_load_master_data, get_pjsk_type, load_master_data
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


# 旧版本默认关闭过 diffrank；现在不再兼容 unibot，启动时清理历史群关闭记录。
try:
    for _group_id in group_manager.get_all_group():
        group_manager.unblock_plugin('diffrank', _group_id)
except Exception as e:
    logger.debug(f"[diffrank] 清理历史关闭状态失败: {e}")


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
    # 取玩家成绩（可选）
    profile = None
    error = False
    userid, isprivate = await PjskBind.get_user_bind(event.user_id, pjsk_type=pjsk_type)
    music_result = None
    if userid and not isprivate:
        profile = UserProfile()
        try:
            await profile.getsuite(userid=userid, pjsk_type=pjsk_type)
            music_result = profile.musicResult
        except Exception as e:
            logger.warning(f"[diffrank] 获取 Suite 成绩数据失败 user={userid} server={server_name}: {e}")
            try:
                await profile.getprofile(userid=userid, query_type='rank', pjsk_type=pjsk_type)
            except Exception:
                profile.isNewData = True
                error = True

    has_profile_header = profile is not None and not error
    suite_data = {}
    suite_update_text = None
    user_suite_file = suite_path / server_name / f'{userid}.json' if userid else None
    if user_suite_file and user_suite_file.exists():
        mtime = user_suite_file.stat().st_mtime
        suite_data['upload_time'] = mtime
        suite_update_text = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(mtime))

    update_file = get_constants_csv_path(pjsk_type)
    if not update_file.exists():
        update_file = data_path / 'jp' / 'musicDifficulties.json'
    constants_time_text = time.strftime(
        "%Y-%m-%d %H:%M:%S", time.localtime(os.path.getmtime(update_file))
    )

    # 数据收集完成，出图交给绘图服务。
    pic = await render('diffrank', {
        'music_data': musicData,
        'difficulty': difficulty,
        'music_result': (
            {str(mid): value for mid, value in music_result.items()} if music_result else None
        ),
        'one_row_count': None if level != 0 else 5,
        'title': title.strip(),
        'mode_text': 'AP' if fcap == 2 else 'FC' if fcap == 1 else 'CLEAR',
        'header': (
            build_header_payload(profile, userid or '', isprivate, suite_data=suite_data)
            if has_profile_header else None
        ),
        'is_private': bool(isprivate),
        'server_label': server_name.upper(),
        'constants_time_text': constants_time_text,
        'suite_update_text': suite_update_text,
        'pjsk_type': pjsk_type,
    })
    await matcher.finish(image(pic))


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
