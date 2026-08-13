import json
import os
import re
from hashlib import md5
from typing import Tuple, List
from nonebot import on_command
from nonebot.internal.matcher import Matcher
from nonebot.adapters.onebot.v11 import Message, MessageEvent
from nonebot.params import CommandArg, Command
from .._config import data_path, SERVER_MAP
from .._utils import currentevent, load_master_data, get_pjsk_type
from .._common_utils import callapi
from .._event_utils import drawevent, draweventall, extract_ban_event_arg
from .._models import EventInfo
from utils.message_builder import image

from ...image_management.pjsk_images.pjsk_db_source import PjskAlias

__plugin_name__ = "活动查询/event"
__plugin_type__ = "烧烤相关&uni移植"
__plugin_version__ = 0.1
__plugin_usage__ = f"""
usage：
    查询烧烤活动信息
    若群内已有unibot请勿开启此bot该功能
    私聊可用，限制每人1分钟只能查询4次
    指令：
        event ?[活动id]                     : 查看对应活动id的活动信息，无参数时默认为当前活动
        findevent/查活动/查询活动 [关键字]     : 通过关键字筛选活动概要信息，可用角色昵称/缩写筛选相关活动（如 查活动 tks）
        findevent/查活动/查询活动             : 直接获取上方指令中对于[关键字]的说明
    数据来源：
        pjsek.ai
        pjsekai.moe
        unipjsk.com
""".strip()
__plugin_settings__ = {
    "default_status": False,
    "cmd": ["event", "烧烤相关", "uni移植", "活动查询"],
}
__plugin_cd_limit__ = {"cd": 60, "count_limit": 4, "rst": "别急，等[cd]秒后再用！", "limit_type": "user"}
__plugin_block_limit__ = {"rst": "别急，还在查！"}


eventinfo = on_command('event', priority=5, block=True)
cn_eventinfo = on_command('cnevent', priority=5, block=True)
tw_eventinfo = on_command('twevent', priority=5, block=True)

findevent = on_command(
    'findevent',
    aliases={"查活动", "查询活动", "活动图鉴", "活动总览", "活动手册", "活动列表"},
    priority=4,
    block=True
)
cn_findevent = on_command(
    'cnfindevent',
    aliases={"cn查活动", "cn查询活动", "cn活动图鉴", "cn活动总览", "cn活动手册", "cn活动列表"},
    priority=4,
    block=True
)
tw_findevent = on_command(
    'twfindevent',
    aliases={"tw查活动", "tw查询活动", "tw活动图鉴", "tw活动总览", "tw活动手册", "tw活动列表"},
    priority=4,
    block=True
)


@eventinfo.handle()
@cn_eventinfo.handle()
@tw_eventinfo.handle()
async def _eventinfo(matcher: Matcher, event: MessageEvent, arg: Message = CommandArg(), cmd: Tuple[str, ...] = Command()):
    pjsk_type = get_pjsk_type(cmd[0])
    
    server_name = SERVER_MAP.get(pjsk_type, 'jp')

    raw = arg.extract_plain_text().strip()
    group_id = event.group_id if hasattr(event, 'group_id') else None
    ban_event, _, ban_error = await extract_ban_event_arg(raw, pjsk_type=pjsk_type, group_id=group_id)
    if ban_error:
        await matcher.finish(ban_error)
    if ban_event:
        eventid = ban_event['id']
    else:
        eventid = re.sub(r'\D', "", raw)
        if not eventid:
            eventid = currentevent(pjsk_type=pjsk_type)['id']
        else:
            eventid = int(eventid)
    # 检查本地是否已经有活动图片
    path = data_path / server_name / 'eventinfo'
    path.mkdir(parents=True, exist_ok=True)
    save_path = path / f'event_{eventid}.jpg'
    if save_path.exists():
        await matcher.finish(image(save_path))
    else:
        info_obj = EventInfo()
        if info_obj.getevent(eventid, pjsk_type=pjsk_type):
            pic = await drawevent(info_obj, pjsk_type=pjsk_type)
            pic.save(save_path)
            await matcher.finish(image(save_path))
        else:
            await matcher.finish("未找到活动或生成失败")


async def event_argparse(args: List = None, pjsk_type: int = 0):
    if not args:
        args = []
    event_type = None            # 活动类型
    event_attr = None            # 活动属性
    event_units_name = []        # 活动组合名称
    event_charas_id = []         # 活动出卡角色id
    isEqualAllUnits = True     # 活动组合是否需要完全等同所有组合名称
    isContainAllCharasId = True  # 活动出卡是否需要包含所有角色id
    islegal = True               # 参数是否合法
    isTeamEvent = None          # 是否指定箱活
    unit_dict = {
        'ln': 'light_sound', 'mmj': 'idol', 'vbs': 'street', 'ws': 'theme_park', '25h': 'school_refusal',
    }
    team_dict = {
        '箱活': True, '团队活': True, '混活': False, '团外活': False
    }
    event_type_dict = {
        '普活': 'marathon', '马拉松': 'marathon', 'marathon': 'marathon',
        '5v5': 'cheerful_carnival', 'cheerful_carnival': 'cheerful_carnival',
        'wl': 'world_bloom', 'worldlink': 'world_bloom', 'world_bloom': 'world_bloom', '世界链接': 'world_bloom'
    }
    event_attr_dict = {
        '蓝星': 'cool', '紫月': 'mysterious', '橙心': 'happy', '黄心': 'happy', '粉花': 'cute', '绿草': 'pure',
        '蓝': 'cool', '紫': 'mysterious', '橙': 'happy', '黄': 'happy', '粉': 'cute', '绿': 'pure',
        '星': 'cool', '月': 'mysterious', '心': 'happy', '花': 'cute', '草': 'pure',
        'cool': 'cool', 'mysterious': 'mysterious', 'happy': 'happy', 'cute': 'cute', 'pure': 'pure',
    }
    chara_dict = {
        'ick': 1, 'saki': 2, 'hnm': 3, 'shiho': 4,
        'mnr': 5, 'hrk': 6, 'airi': 7, 'szk': 8,
        'khn': 9, 'an': 10, 'akt': 11, 'toya': 12,
        'tks': 13, 'emu': 14, 'nene': 15, 'rui': 16,
        'knd': 17, 'mfy': 18, 'ena': 19, 'mzk': 20,
        'miku': 21, 'rin': 22, 'len': 23, 'luka': 24, 'meiko': 25, 'kaito': 26
    }
    chara2unit_dict = {
        'light_sound': [1,2,3,4],
        'idol': [5,6,7,8],
        'street': [9,10,11,12],
        'theme_park': [13,14,15,16],
        'school_refusal': [17,18,19,20]
    }
    for arg in args:
        # 参数是否指定了箱活或混活
        if arg in team_dict.keys():
            isTeamEvent = team_dict[arg]
            continue
        # 参数是否为活动类型，只能指定一种
        if _ := event_type_dict.get(arg):
            if event_type:
                islegal = False
                break
            else:
                event_type = _
                continue
        # 参数是否为活动属性，只能指定一种
        if _ := event_attr_dict.get(arg):
            if event_attr:
                islegal = False
                break
            else:
                event_attr = _
                continue
        # 参数是否为组合缩写(指定一个时为箱活，指定多个时为混活)
        if _ := unit_dict.get(arg):
            event_units_name.append(_)
            continue
        # 参数是否为组合缩写(对参数中含"混"、"加成"的额外再判定一次)
        # 末尾为"混"、"加成"，说明需要筛选带此组合任意角色玩的混活
        unit_rule = "|".join(unit_dict.keys())
        if match := re.match(rf'^({unit_rule})(?:混|加成)$', arg):
            try:
                event_units_name.append(unit_dict[match.group(1)])
            except KeyError:
                islegal = False
                break
            else:
                isEqualAllUnits = False
                continue
        # 中间为"混"
        if match := re.match(rf'^({unit_rule})混({unit_rule}).*$', arg):
            try:
                event_units_name.extend(unit_dict[j] for j in match.group().split('混'))
                continue
            except KeyError:
                islegal = False
                break
        # 参数是否是带附属组合的vs角色
        if match := re.match(rf"^({unit_rule})(.+)", arg):
            unit = match.group(1)
            alias = match.group(2)
            if alias not in [i for i in chara_dict.keys() if chara_dict[i] > 20]:
                alias = await PjskAlias.query_name(alias)
            if alias and chara_dict[alias] > 20:
                event_charas_id.append((chara_dict[alias], unit_dict[unit]))
                continue
            else:
                islegal = False
                break
        # 以上判定均无果，则认定为sekai角色或无附属组合的vs角色
        alias = arg
        if alias not in chara_dict.keys():
            alias = await PjskAlias.query_name(arg)
        if alias and chara_dict.get(alias):
            event_charas_id.append(chara_dict[alias])
        # 参数仍无法识别
        else:
            islegal = False
            break

    for i in event_charas_id:
        if isinstance(i, tuple):
            unit = i[1]
        elif i <= 20:
            unit = [x for x in chara2unit_dict.keys() if i in chara2unit_dict[x]][0]
        else:
            continue
        if len(event_units_name) != 0 and unit not in event_units_name:
            event_units_name.append(unit)
            isEqualAllUnits = False
    # 箱活标志只能与活动类型、活动属性搭配
    if isTeamEvent is not None and (len(event_units_name)>0 or len(event_charas_id)>0):
        islegal = False
    return {
        'event_type': event_type, 'event_attr': event_attr,
        'event_units_name': list(set(event_units_name)), 'event_charas_id': list(set(event_charas_id)),
        'islegal': islegal, 'isTeamEvent': isTeamEvent,
        'isEqualAllUnits': isEqualAllUnits, 'isContainAllCharasId': isContainAllCharasId

    }


@findevent.handle()
@cn_findevent.handle()
@tw_findevent.handle()
async def _findevent(matcher: Matcher, event: MessageEvent, cmd: Tuple = Command(), arg: Message = CommandArg()):
    pjsk_type = get_pjsk_type(cmd[0])
    
    server_name = SERVER_MAP.get(pjsk_type, 'jp')

    raw_args = arg.extract_plain_text().strip()
    group_id = event.group_id if hasattr(event, 'group_id') else None
    ban_event, _, ban_error = await extract_ban_event_arg(raw_args, pjsk_type=pjsk_type, group_id=group_id)
    if ban_error:
        await matcher.finish(ban_error)
    if ban_event:
        await _eventinfo(matcher, event, Message(str(ban_event['id'])), cmd=cmd)
        return

    args = raw_args
    if args.isdigit():
        # 这里需要注意 _eventinfo 需要 matcher, event 和 arg
        await _eventinfo(matcher, event, arg, cmd=cmd)
        return
    else:
        args = args.split()

    list_cmds = [
        "活动列表", "活动图鉴", "活动总览", "活动手册",
        "cn活动列表", "cn活动图鉴", "cn活动总览", "cn活动手册",
        "tw活动列表", "tw活动图鉴", "tw活动总览", "tw活动手册",
    ]
    is_event_list_cmd = cmd[0] in list_cmds
    is_all_event_list = is_event_list_cmd and len(args) == 1 and str(args[0]).lower() == 'all'
    if is_all_event_list:
        args = []
    display_limit = 50 if is_event_list_cmd and not args and not is_all_event_list else None

    params = await event_argparse(args, pjsk_type=pjsk_type)
    if not params['islegal']:
        tip_path = data_path / 'pics/findevent_tips.jpg'
        await matcher.finish(image(tip_path))
    # 没有参数且不是活动图鉴类指令时，按 event 指令查询默认活动。
    # 默认活动由 currentevent() 决定：有进行中活动取进行中，否则取下一期准备开始的活动。
    elif not args and not is_event_list_cmd:
        await _eventinfo(matcher, event, arg, cmd=cmd)
        return
    # 检查本地活动图鉴是否需要更新
    events = load_master_data('events.json', pjsk_type)
    count = len(events)
    path = data_path / server_name / 'findevent'
    path.mkdir(parents=True, exist_ok=True)
    # 图片路径格式
    # 备份
    _event_charas_id = params['event_charas_id'].copy()
    _event_units_name = params['event_units_name'].copy()
    charas_id_name = params['event_charas_id']
    params['event_units_name'].sort()
    for i in range(len(_event_charas_id)):
        if isinstance(_event_charas_id[i], tuple):
            _charaid = _event_charas_id[i][0] + ([
                 'light_sound','idol','street','theme_park','school_refusal'
            ].index(_event_charas_id[i][1])+1)*6
            charas_id_name[i] = _charaid
    charas_id_name.sort()
    save_file_prefix = md5((str(list(params.values())) + f'|limit={display_limit}|style=v8').encode()).hexdigest()
    save_path = path / f'{save_file_prefix}-{count}.jpg'
    # 还原
    params['event_charas_id'] = _event_charas_id
    params['event_units_name'] = _event_units_name
    list_tip = "如果想要查询所有活动请输入 活动列表all\n" if display_limit is not None else ""
    if save_path.exists():
        await matcher.finish(Message(list_tip) + image(save_path) if list_tip else image(save_path))
    else:
        # 开始生成新活动图鉴
        try:
            pic = await draweventall(events=events, pjsk_type=pjsk_type, display_limit=display_limit, **params)
        except Exception as e:
            raise e
        else:
            if pic:
                pic = pic.convert('RGB')
                pic.save(save_path, quality=70)
                await matcher.finish(Message(list_tip) + image(save_path) if list_tip else image(save_path))
            else:
                tip_path = data_path / 'pics/findevent_tips.jpg'
                await matcher.finish(image(tip_path))
        finally:
            # 因为需要更新，所以清除所有旧活动图鉴
            for file in os.listdir(path):
                if not file.split('.')[0].endswith(str(count)):
                    (path / file).unlink()
