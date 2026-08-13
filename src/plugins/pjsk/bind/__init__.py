import re
import time
from typing import Tuple
from nonebot import on_command
from nonebot.params import CommandArg, Command
from nonebot.adapters.onebot.v11 import Message, MessageEvent
from .._config import BUG_ERROR, NOT_BIND_ERROR, REFUSED_ERROR, ID_ERROR
from .._utils import verifyid, get_userid_preprocess, gettime
from .._models import PjskBind
import json

__plugin_name__ = "绑定账号"
__plugin_type__ = "烧烤相关&uni移植"
__plugin_version__ = 0.1
__plugin_usage__ = f"""
usage：
    pjsk绑定账号，私聊可用
    若群内已有unibot请勿开启此bot该功能
    指令：
        绑定/bind [id]           绑定烧烤id
        解绑/unbind              解绑烧烤id
        给看/不给看               公开/隐藏自己的烧烤信息(默认为公开)
        查时间                   查询烧烤账号创建时间
""".strip()
__plugin_settings__ = {
    "default_status": False,
    "cmd": ["bind", "绑定账号", "烧烤相关", "uni移植"],
}


# pjsk绑定
pjsk_bind = on_command('bind', aliases={"绑定", "cn绑定", "tw绑定", "cnbind", "twbind"}, priority=5, block=True)

# pjsk解绑
pjsk_unbind = on_command('unbind', aliases={"解绑", "cn解绑", "tw解绑", "cnunbind", "twunbind"}, priority=5, block=True)

#pjsk给看
pjsk_look = on_command('给看', aliases={"不给看", "cn给看", "tw给看", "cn不给看", "tw不给看"}, priority=5, block=True)

#pjsk查时间
pjsk_ctime = on_command('查时间', aliases={"cn查时间", "tw查时间"}, priority=5, block=True)


@pjsk_bind.handle()
async def _(event: MessageEvent, msg: Message = CommandArg(), cmd: Tuple[str, ...] = Command()):
    text = msg.extract_plain_text().strip()
    cmd_name = cmd[0]
    
    # 默认为日服
    pjsk_type = 0
    server_name = '日服'
    
    if cmd_name.startswith('cn'):
        pjsk_type = 2
        server_name = '国服'
    elif cmd_name.startswith('tw'):
        pjsk_type = 1
        server_name = '台服'
    
    # 获取ID，优先从参数中提取，如果参数里还有前缀则进一步处理
    arg = re.sub(r'\D', "", text)
    
    if not arg:
        await pjsk_bind.finish("绑定成...？你id呢？", at_sender=True)
    
    if arg.isdigit() and verifyid(arg, pjsk_type):
        result = await PjskBind.add_bind(event.user_id, int(arg), pjsk_type)
        if result:
            await pjsk_bind.finish(f"绑定{server_name}服务器成功", at_sender=True)
        else:
            await pjsk_bind.finish(f"绑定{server_name}服务器失败，请稍后重试", at_sender=True)
    else:
        await pjsk_bind.finish(ID_ERROR, at_sender=True)


    '''
    arg = re.sub(r'\\D', "", msg.extract_plain_text().strip())      #id提取，只保留所有数字字符
    if not arg: 
        await pjsk_bind.finish("绑定成...？你id呢？", at_sender=True)
    if arg.isdigit() and verifyid(arg):
        await PjskBind.add_bind(event.user_id, int(arg))
        await pjsk_bind.finish(f"绑定成功", at_sender=True)
    else:
        await pjsk_bind.finish(ID_ERROR, at_sender=True)
    '''


@pjsk_unbind.handle()
async def _(event: MessageEvent, cmd: Tuple[str, ...] = Command()):
    cmd_name = cmd[0]
    pjsk_type = 0
    if cmd_name.startswith('cn'):
        pjsk_type = 2
    elif cmd_name.startswith('tw'):
        pjsk_type = 1
    flag = await PjskBind.del_bind(event.user_id, pjsk_type)
    if flag:
        await pjsk_unbind.finish(f"解绑成功", at_sender=True)
    else:
        await pjsk_unbind.finish("解绑成...？你还没绑定过呢", at_sender=True)


@pjsk_look.handle()
async def _(event: MessageEvent, cmd: Tuple[str, ...] = Command()):
    cmd_name = cmd[0]
    pjsk_type = 0
    if cmd_name.startswith('cn'):
        pjsk_type = 2
    elif cmd_name.startswith('tw'):
        pjsk_type = 1
    
    isprivate = '不给看' in cmd_name
    if not await PjskBind.check_exists(event.user_id, pjsk_type):
        await pjsk_bind.finish(NOT_BIND_ERROR)
    if await PjskBind.set_look(event.user_id, isprivate, pjsk_type):
        await pjsk_bind.finish(f"{'不给看！' if isprivate else '给看！'}")
    else:
        await pjsk_bind.finish(BUG_ERROR)


@pjsk_ctime.handle()
async def _(event: MessageEvent, msg: Message = CommandArg(), cmd: Tuple[str, ...] = Command()):
    cmd_name = cmd[0]
    pjsk_type = 0
    if cmd_name.startswith('cn'):
        pjsk_type = 2
    elif cmd_name.startswith('tw'):
        pjsk_type = 1
    state = await get_userid_preprocess(event, msg, pjsk_type=pjsk_type)
    if reply := state['error']:
        await pjsk_ctime.finish(reply, at_sender=True)
    userid = state['userid']
    isprivate = state['private']
    if isprivate:
        await pjsk_ctime.finish(REFUSED_ERROR)
    
    registertime = gettime(userid, pjsk_type)
    if not registertime:
        await pjsk_ctime.finish("计算时间失败，可能是ID无效")
        
    await pjsk_ctime.finish(
        time.strftime(
            '注册时间：%Y-%m-%d %H:%M:%S',
            time.localtime(registertime)
        )
    )
