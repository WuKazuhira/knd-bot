import datetime
import json
from typing import Any, Tuple

from nonebot import on_command, on_regex
from nonebot.adapters.onebot.v11 import ActionFailed, Message, MessageEvent
from nonebot.internal.matcher import Matcher
from nonebot.params import Command, CommandArg, RegexGroup

from services import logger
from utils.http_utils import AsyncHttpx
from utils.imageutils import pic2b64, text2image
from utils.message_builder import image

from .._config import SERVER_MAP, data_path
from .._event_utils import extract_ban_event_arg, get_event_music_ids
from .._models import PjskSongsAlias
from .._song_utils import PJSKINFO_CACHE_VERSION, get_songs_data, idtoname, info, parse_bpm, save_songs_data
from .._utils import async_load_master_data, get_pjsk_type, load_master_data

__plugin_name__ = "歌曲查询/pjskinfo"
__plugin_type__ = "烧烤相关&uni移植"
__plugin_version__ = 0.1
__plugin_usage__ = f"""
usage：
    查询烧烤曲目信息
    若群内已有unibot请勿开启此bot该功能
    私聊可用，限制每人1分钟只能查询4次
    指令：
        pjskinfo/song [曲目]                : 查看曲目详细信息
        pjskset [曲目别称] to [曲目]          : 给对应曲目添加别称
        pjskdel [曲目别称]                   : 删除曲目的对应别称
        pjskalias [曲目]                    : 查询曲目所有别称
        bpm/pjskbpm [曲目]                  : 查询曲目bpm
        查物量  [总combo数]                  : 查询对应物量的曲目
        查bpm  [bpm]                       : 查询对应bpm的曲目
    数据来源：
        pjsekai.moe
        unipjsk.com
""".strip()
__plugin_settings__ = {
    "default_status": False,
    "cmd": ["pjskinfo", "烧烤相关", "uni移植", "歌曲查询"],
}
__plugin_cd_limit__ = {
    "cd": 60, "count_limit": 4, "rst": "别急，等[cd]秒后再用！", "limit_type": "user"
}
__plugin_block_limit__ = {"rst": "别急，还在查！"}

def _format_song_name(candidate: dict) -> str:
    title = candidate.get('title') or ''
    translate = candidate.get('translate') or ''
    return f"{title} ({translate})" if translate and translate != title else title


def _is_ambiguous_song_match(data: dict) -> bool:
    if data.get('exact'):
        return False
    candidates = data.get('candidates') or []
    if len(candidates) < 2:
        return False
    first, second = candidates[0], candidates[1]
    first_score = float(first.get('match') or 0)
    second_score = float(second.get('match') or 0)
    return second_score >= 0.65 and (first_score - second_score <= 0.06 or (first_score >= 0.8 and second_score >= 0.8))


def _ambiguous_song_message(candidates: list[dict]) -> str:
    lines = ["你可能想找："]
    for candidate in candidates[:2]:
        lines.append(f"{_format_song_name(candidate)} ID:{candidate.get('musicId')}")
    return "\n".join(lines)


# pjskinfo
pjskinfo = on_command('pjskinfo', aliases={"song", "查曲"}, priority=5, block=True)
cn_pjskinfo = on_command('cnpjskinfo', aliases={"cnsong"}, priority=5, block=True)
tw_pjskinfo = on_command('twpjskinfo', aliases={"twsong"}, priority=5, block=True)

# pjskset
pjskset = on_regex(r'^(cn|tw)?pjskset(.+to.+)', priority=5, block=True)

# pjskdel
pjskdel = on_command('pjskdel', priority=5, block=True)

# pjskalias
pjskalias = on_command('pjskalias', aliases={"查别称"}, priority=5, block=True)
cn_pjskalias = on_command('cnpjskalias', priority=5, block=True)
tw_pjskalias = on_command('twpjskalias', priority=5, block=True)

# pjskbpm
pjskbpm = on_command('pjskbpm', aliases={'bpm', '查曲bpm'}, priority=5, block=True)
cn_pjskbpm = on_command('cnpjskbpm', aliases={'cnbpm'}, priority=5, block=True)
tw_pjskbpm = on_command('twpjskbpm', aliases={'twbpm'}, priority=5, block=True)

# 查物量
pjsknotecount = on_command('查物量', priority=5, block=True)
cn_pjsknotecount = on_command('cn查物量', priority=5, block=True)
tw_pjsknotecount = on_command('tw查物量', priority=5, block=True)

# 查bpm
pjskbpmfind = on_command('查bpm', priority=5, block=True)
cn_pjskbpmfind = on_command('cn查bpm', priority=5, block=True)
tw_pjskbpmfind = on_command('tw查bpm', priority=5, block=True)


@pjskinfo.handle()
@cn_pjskinfo.handle()
@tw_pjskinfo.handle()
async def _(matcher: Matcher, event: MessageEvent, msg: Message = CommandArg(), cmd: Tuple[str, ...] = Command()):
    pjsk_type = get_pjsk_type(cmd[0])
    
    server_name = SERVER_MAP.get(pjsk_type, 'jp')

    arg = msg.extract_plain_text().strip()
    if not arg:
        await matcher.finish("使用方法：pjskinfo + 曲名")

    group_id = event.group_id if hasattr(event, 'group_id') else None
    ban_event, _, ban_error = await extract_ban_event_arg(arg, pjsk_type=pjsk_type, group_id=group_id)
    if ban_error:
        await matcher.finish(ban_error)
    if ban_event:
        music_ids = get_event_music_ids(ban_event['id'], pjsk_type=pjsk_type)
        if not music_ids:
            await matcher.finish(f"{ban_event.get('name', '该活动')} Event ID:{ban_event['id']} 暂未找到活动歌曲")
        if len(music_ids) > 1:
            musics = await async_load_master_data('musics.json', pjsk_type)
            lines = [f"{ban_event.get('name', '该活动')} Event ID:{ban_event['id']} 的活动歌曲："]
            for music_id in music_ids:
                lines.append(f"{idtoname(music_id, musics, pjsk_type=pjsk_type) or music_id} ID:{music_id}")
            await matcher.finish("\n".join(lines))
        music_id = music_ids[0]
        leak, imgb64 = await info(music_id, pjsk_type=pjsk_type)
        musics = await async_load_master_data('musics.json', pjsk_type)
        title = idtoname(music_id, musics, pjsk_type=pjsk_type) or str(music_id)
        text = f"{ban_event.get('name', '该活动')} Event ID:{ban_event['id']}\n{title} ID:{music_id}"
        if leak:
            text += "\n⚠该内容为剧透内容"
        imgpath = data_path / server_name / "pics" / "pjskinfo" / f"pjskinfo_v{PJSKINFO_CACHE_VERSION}_{music_id}.png"
        if not imgpath.exists() and imgb64:
            img = image(b64=imgb64)
        else:
            img = image(imgpath)
        await matcher.finish(text + img)

    # 首先查询本地数据库有无对应别称id
    data = await get_songs_data(arg, isfuzzy=False, pjsk_type=pjsk_type)
    # 若无结果则在本地模糊搜索得到结果
    if data['status'] != 'success':
        data = await get_songs_data(arg, isfuzzy=True, pjsk_type=pjsk_type)
        if data['status'] != 'success':
            await matcher.finish('没有找到你要的歌曲哦')
    if _is_ambiguous_song_match(data):
        await matcher.finish(_ambiguous_song_message(data.get('candidates') or []))

    text = "你要找的可能是：" if data['match'] < 0.8 and not data.get('exact') else ""
    leak, imgb64 = await info(data['musicId'], pjsk_type=pjsk_type)
    if leak:
        text += f"匹配度:{round(data['match'], 4)}\n⚠该内容为剧透内容"
    elif data['translate'] == '':
        text += f"{data['title']}\n匹配度:{round(data['match'], 4)}"
    else:
        text += f"{data['title']} ({data['translate']})\n匹配度:{round(data['match'], 4)}"
    imgpath = data_path / server_name / "pics" / "pjskinfo" / f"pjskinfo_v{PJSKINFO_CACHE_VERSION}_{data['musicId']}.png"
    if not imgpath.exists() and imgb64:
        img = image(b64=imgb64)
    else:
        img = image(imgpath)
    await matcher.finish(text + img)


@pjskalias.handle()
@cn_pjskalias.handle()
@tw_pjskalias.handle()
async def _(matcher: Matcher, event: MessageEvent, msg: Message = CommandArg(), cmd: Tuple[str, ...] = Command()):
    pjsk_type = get_pjsk_type(cmd[0])

    arg = msg.extract_plain_text().strip()
    # 别称查询目前保持 JP 逻辑或全局逻辑，因为 PjskSongsAlias 是全局数据库
    if arg:
        # 首先查询本地数据库有无对应别称id
        data = await get_songs_data(arg, isfuzzy=False, pjsk_type=pjsk_type)
        # 若无结果则在本地模糊搜索得到结果
        if data['status'] != 'success':
            data = await get_songs_data(arg, isfuzzy=True, pjsk_type=pjsk_type)
            if data['status'] != 'success':
                await matcher.finish('没有找到你要的歌曲哦')
        if data['musicId'] == 0:
            await matcher.finish("没有找到你要的歌曲哦")
        musicid = data['musicId']
        if data['translate'] == '':
            returnstr = f"{data['title']}\n匹配度:{round(data['match'], 4)}\n"
        else:
            returnstr = f"{data['title']} ({data['translate']})\n匹配度:{round(data['match'], 4)}\n"
        try:
            await matcher.finish(returnstr)
        except ActionFailed:
            await matcher.finish(
                image(b64=pic2b64(text2image(returnstr))),
                at_sender=True
            )
    else:
        await matcher.finish("请使用正确格式：pjskalias 昵称")


@pjskset.handle()
async def _(matcher: Matcher, event: MessageEvent, reg_group: Tuple[Any, ...] = RegexGroup()):
    cmd_prefix = reg_group[0]
    pjsk_type = 0
    if cmd_prefix == 'cn':
        pjsk_type = 2
    elif cmd_prefix == 'tw':
        pjsk_type = 1
    
    msg = reg_group[1].strip()
    # 对别名和称呼做特殊处理，以防别名中本身含有关键词to
    index = 0
    oldalias = newalias = ""
    oldsid = 0
    for i in range(msg.count('to')):
        index = msg.find('to', index)
        tmp_new, tmp_old = msg[:index].strip(), msg[index + 2:].strip()
        index = index + 2
        # 一旦找到chara在已有称呼表内，则可以识别alias的位置
        oldsid = await PjskSongsAlias.query_sid(tmp_old)
        if oldsid:
            oldalias = tmp_old
            newalias = tmp_new
            break
    if not oldsid or not oldalias or not newalias:
        await matcher.finish("添加失败，可能是找不到对应称呼", at_sender=True)
    elif oldalias == newalias:
        await matcher.finish("添加失败，新称呼与旧称呼相同", at_sender=True)
    group_id = -1
    if hasattr(event, 'group_id'):
        group_id = event.group_id
    if await PjskSongsAlias.add_alias(
        oldsid, newalias, event.user_id, group_id, datetime.datetime.now(), False
    ):
        musics = await async_load_master_data('musics.json', pjsk_type)
        title = idtoname(oldsid, musics, pjsk_type=pjsk_type)
        await matcher.finish(f"设置成功！{newalias}->{title}")
    else:
        newsid = await PjskSongsAlias.query_sid(newalias)
        musics = await async_load_master_data('musics.json', pjsk_type)
        title = idtoname(newsid, musics, pjsk_type=pjsk_type)
        if title:
            await matcher.finish(f"添加失败，此称呼已经属于歌曲：{title}", at_sender=True)
        else:
            await matcher.finish(f"添加失败，此称呼已经属于其它歌曲", at_sender=True)


# pjskdel
pjskdel = on_command('pjskdel', priority=5, block=True)
cn_pjskdel = on_command('cnpjskdel', priority=5, block=True)
tw_pjskdel = on_command('twpjskdel', priority=5, block=True)

@pjskdel.handle()
@cn_pjskdel.handle()
@tw_pjskdel.handle()
async def _(matcher: Matcher, event: MessageEvent, msg: Message = CommandArg(), cmd: Tuple[str, ...] = Command()):
    pjsk_type = get_pjsk_type(cmd[0])
    arg = msg.extract_plain_text().strip()
    sid = await PjskSongsAlias.query_sid(arg)
    musics = await async_load_master_data('musics.json', pjsk_type)
    songname = idtoname(sid, musics, pjsk_type=pjsk_type)
    
    if await PjskSongsAlias.delete_alias(arg):
        await matcher.finish(
            f"已成功删除歌曲:{songname}的别称:{arg}"
            if songname else "删除成功！",
            at_sender=True
        )
        qq = event.user_id
        group = -1
        if hasattr(event, 'group_id'):
            group= event.group_id
        logger.info(f"USER {qq} GROUP {group} 删除了{songname}的称呼 {arg} ！")
    else:
        await pjskdel.finish(f"删除失败，找不到歌曲", at_sender=True)


@pjskbpm.handle()
@cn_pjskbpm.handle()
@tw_pjskbpm.handle()
async def _(matcher: Matcher, event: MessageEvent, msg: Message = CommandArg(), cmd: Tuple[str, ...] = Command()):
    pjsk_type = get_pjsk_type(cmd[0])

    arg = msg.extract_plain_text().strip()
    if not arg:
        await matcher.finish("使用方法：pjskbpm + 曲名")
    # 首先查询本地数据库有无对应别称id
    data = await get_songs_data(arg, isfuzzy=False, pjsk_type=pjsk_type)
    # 若无结果则在本地模糊搜索得到结果
    if data['status'] != 'success':
        data = await get_songs_data(arg, isfuzzy=True, pjsk_type=pjsk_type)
        if data['status'] != 'success':
            await matcher.finish('没有找到你要的歌曲哦')
    text = ''
    bpm = await parse_bpm(data['musicId'], pjsk_type=pjsk_type)
    for bpms in bpm[1]:
        text = text + ' - ' + str(bpms['bpm']).replace('.0', '')
    text = f"{data['title']}\n匹配度:{round(data['match'], 4)}\nBPM: " + text[3:]
    await matcher.finish(text)


@pjsknotecount.handle()
@cn_pjsknotecount.handle()
@tw_pjsknotecount.handle()
async def _(matcher: Matcher, event: MessageEvent, msg: Message = CommandArg(), cmd: Tuple[str, ...] = Command()):
    pjsk_type = get_pjsk_type(cmd[0])

    notes = msg.extract_plain_text().strip()
    try:
        notes = int(notes)
    except:
        await matcher.finish("请输入数字！")
    text = ''
    data = await async_load_master_data('musicDifficulties.json', pjsk_type)
    musics = await async_load_master_data('musics.json', pjsk_type)
    for i in data:
        if i['totalNoteCount'] == notes:
            text += f"{idtoname(i['musicId'], musics, pjsk_type=pjsk_type)}[{(i['musicDifficulty'].upper())} {i['playLevel']}]\n"
    if text == '':
        text = '没有找到'
    await matcher.finish(text)


@pjskbpmfind.handle()
@cn_pjskbpmfind.handle()
@tw_pjskbpmfind.handle()
async def _(matcher: Matcher, event: MessageEvent, msg: Message = CommandArg(), cmd: Tuple[str, ...] = Command()):
    pjsk_type = get_pjsk_type(cmd[0])

    targetbpm = msg.extract_plain_text().strip()
    try:
        targetbpm = int(targetbpm)
    except:
        await matcher.finish("请输入数字！")
    bpm = {}
    text = ''
    data = await async_load_master_data('musics.json', pjsk_type)
    for music in data:
        bpm[music['id']] = (await parse_bpm(music['id'], pjsk_type=pjsk_type))[1]
    for musicid in bpm:
        for i in bpm[musicid]:
            if int(i['bpm']) == targetbpm:
                bpmtext = ''
                for bpms in bpm[musicid]:
                    bpmtext += ' - ' + str(bpms['bpm']).replace('.0', '')
                text += f"{idtoname(musicid, pjsk_type=pjsk_type)}: {bpmtext[3:]}\n"
                break
    if text == '':
        text = '没有找到'
    await matcher.finish(text)

