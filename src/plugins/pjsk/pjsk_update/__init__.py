import asyncio
import json
import time
from typing import Tuple

from nonebot import on_command
from nonebot.adapters.onebot.v11 import Message, MessageEvent
from nonebot.internal.params import ArgStr
from nonebot.params import Command, CommandArg
from nonebot.permission import SUPERUSER
from nonebot.typing import T_State

from .._asset_dedup import deduplicate
from .._autoask import (
    check_cards_resources,
    check_event_resources,
    check_eventinfo_resources,
    check_pjsk_all_resources,
    check_pjskinfo_resources,
    check_profile_resources,
    check_songs_resources,
    check_trans_resources,
)
from .._config import SERVER_MAP
from .._utils import get_pjsk_type

__plugin_name__ = "pjsk数据更新 [Superuser]"
__plugin_type__ = "烧烤相关&uni移植"
__plugin_version__ = 0.1
__plugin_usage__ = f"""
usage：
    pjsk数据更新
    指令：
        pjsk更新
    参数：
        0: 以下全部
        1: 活动信息资源[events,rankMatchSeasons,cheerfulCarnivalTeams,bondsHonors]
        2: 活动相关资源[eventCards,eventDeckBonuses,gameCharacterUnits]
        3: 卡面相关资源[cardCostume3ds,costume3ds,gameCharacters,cards]
        4: 个人信息资源[honors,honorGroups]
        5: 谱面信息资源[musicVocals,outsideCharacters]
        6: 歌曲信息资源[musicDifficulties,musics]
        7: 游戏翻译资源[music_titles,event_name,card_prefix,cheerful_carnival_teams]
""".strip()
__plugin_settings__ = {
    "cmd": ["pjsk更新"],
}


# pjsk更新
pjsk_update = on_command('pjsk更新', permission=SUPERUSER, priority=3, block=True)
cn_pjsk_update = on_command('cnpjsk更新', permission=SUPERUSER, priority=3, block=True)
tw_pjsk_update = on_command('twpjsk更新', permission=SUPERUSER, priority=3, block=True)
pjsk_data_dedup = on_command(
    'pjsk数据去重',
    aliases={'pjsk资源去重'},
    permission=SUPERUSER,
    priority=3,
    block=True,
)


@pjsk_data_dedup.handle()
async def _(matcher, msg: Message = CommandArg()):
    tokens = msg.extract_plain_text().strip().lower().split()
    apply = any(token in {'执行', 'apply', 'run'} for token in tokens)
    regions = [token for token in tokens if token in {'cn', 'tw'}]
    if not regions:
        regions = ['cn', 'tw']
    stats = await asyncio.to_thread(deduplicate, regions=regions, apply=apply)
    mode = '已执行' if apply else '预览'
    lines = [f'PJSK 资源去重{mode}：']
    for region, item in stats.items():
        lines.append(
            f'{region.upper()}：扫描{item.scanned}，候选{item.candidates}，重复{item.duplicates}，'
            f'可节省{item.saved_bytes / 1024 / 1024:.2f}MiB，'
            f'硬链接{item.linked}，软链接{item.symlinked}，跳过{item.skipped}，错误{item.errors}'
        )
    if not apply:
        lines.append('如需实际替换，请发送：pjsk数据去重 执行')
    await matcher.finish('\n'.join(lines))


@pjsk_update.handle()
@cn_pjsk_update.handle()
@tw_pjsk_update.handle()
async def _(state: T_State, event: MessageEvent, msg: Message = CommandArg(), cmd: Tuple[str, ...] = Command()):
    pjsk_type = get_pjsk_type(cmd[0])
    state['pjsk_type'] = pjsk_type
    
    arg = msg.extract_plain_text().strip()
    if arg:
        state['pjsk_update_arg'] = arg


@pjsk_update.got("pjsk_update_arg", prompt="请发送需要更新的资源类型序号")
@cn_pjsk_update.got("pjsk_update_arg", prompt="请发送需要更新的资源类型序号")
@tw_pjsk_update.got("pjsk_update_arg", prompt="请发送需要更新的资源类型序号")
async def _(state: T_State, msg: str = ArgStr("pjsk_update_arg")):
    pjsk_type = state.get('pjsk_type', 0)
    server_name = SERVER_MAP.get(pjsk_type, 'jp')
    funcdic = {
        "1": check_event_resources,
        "2": check_eventinfo_resources,
        "3": check_cards_resources,
        "4": check_profile_resources,
        "5": check_pjskinfo_resources,
        "6": check_songs_resources,
        "7": check_trans_resources,
    }
    namedic={
        "1": "活动信息资源",
        "2": "活动相关资源",
        "3": "卡面相关资源",
        "4": "个人信息资源",
        "5": "谱面信息资源",
        "6": "歌曲信息资源",
        "7": "游戏翻译资源"
    }
    if msg != "0":
        func_keys = [i for i in msg if i in funcdic.keys()]
    else:
        func_keys = [i for i in funcdic.keys()]
    if func_keys:
        s = time.time()
        await pjsk_update.send(
            f"操作进行中，开始更新 {server_name} 以下资源:\n" +
            "，".join([namedic[i] for i in func_keys])
        )
        for key in func_keys:
            func = funcdic.get(key)
            await func.__call__(block=True, iswait=False, pjsk_type=pjsk_type)
        await pjsk_update.finish(f"已完成操作，耗时{int(time.time() - s)}秒。")
    else:
        await pjsk_update.finish("参数有误，操作取消")
