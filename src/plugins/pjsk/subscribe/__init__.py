"""pjsk 新曲/虚拟Live 订阅（移植自 nmbot 的 vlive/music 订阅功能）。"""
from typing import Tuple

from nonebot import on_command
from nonebot.adapters.onebot.v11 import GROUP, GroupMessageEvent
from nonebot.adapters.onebot.v11.permission import GROUP_ADMIN, GROUP_OWNER
from nonebot.internal.matcher import Matcher
from nonebot.params import Command
from nonebot.permission import SUPERUSER

from services import logger
from utils.imageutils import pic2b64
from utils.limit_utils import access_cd, access_count
from utils.message_builder import image

from .._config import SERVER_MAP
from .._utils import get_pjsk_type, run_pjsk_thread
from ._notify import SERVER_NAME_CN, draw_vlive_cards, fetch_vlive_banners, get_recent_vlives
from ._sub_sql import (
    KIND_MUSIC,
    KIND_VLIVE,
    add_notify_sub,
    get_group_sub_status,
    is_group_subbed,
    remove_group_subs,
    remove_notify_sub,
)

__plugin_name__ = '订阅通知'
__plugin_type__ = '烧烤相关&uni移植'
__plugin_version__ = 0.1
__plugin_usage__ = """
usage：
    pjsk新曲上线通知和虚拟Live开始/末场提醒，支持日服/国服/台服
    群订阅由管理员开启后全群可见，个人可再订阅@提醒
    指令：
        pjsk开启新曲通知          本群开启新曲上线推送（管理员）
        pjsk关闭新曲通知          本群关闭新曲上线推送（管理员）
        pjsk开启live通知          本群开启虚拟Live提醒（管理员）
        pjsk关闭live通知          本群关闭虚拟Live提醒（管理员）
        pjsk新曲提醒/pjsk取消新曲提醒     订阅/取消新曲推送时@我
        pjsklive提醒/pjsk取消live提醒    订阅/取消Live推送时@我
        pjsk订阅状态              查看本群订阅情况
        虚拟live/vlive           查看近期虚拟Live列表
    以上指令加 cn/tw 前缀可指定国服/台服（如 cnpjsk开启新曲通知），默认日服
""".strip()
__plugin_settings__ = {
    'default_status': False,
    'cmd': ['订阅通知', 'pjsk订阅', '新曲通知', '虚拟live'],
}
__plugin_cd_limit__ = {'cd': 10, 'rst': '别急，[cd]秒后再用！', 'limit_type': 'group'}

ADMIN_PERM = GROUP_ADMIN | GROUP_OWNER | SUPERUSER

_KIND_LABEL = {KIND_MUSIC: '新曲通知', KIND_VLIVE: '虚拟Live通知'}


def _make_cmds(base: str) -> set:
    return {base, f'cn{base}', f'tw{base}'}


sub_music_on = on_command('pjsk开启新曲通知', aliases=_make_cmds('pjsk开启新曲通知') | _make_cmds('pjsk新曲通知开启'),
                          permission=ADMIN_PERM, priority=5, block=True)
sub_music_off = on_command('pjsk关闭新曲通知', aliases=_make_cmds('pjsk关闭新曲通知') | _make_cmds('pjsk新曲通知关闭'),
                           permission=ADMIN_PERM, priority=5, block=True)
sub_vlive_on = on_command('pjsk开启live通知', aliases=_make_cmds('pjsk开启live通知') | _make_cmds('pjsk开启Live通知'),
                          permission=ADMIN_PERM, priority=5, block=True)
sub_vlive_off = on_command('pjsk关闭live通知', aliases=_make_cmds('pjsk关闭live通知') | _make_cmds('pjsk关闭Live通知'),
                           permission=ADMIN_PERM, priority=5, block=True)
user_music_on = on_command('pjsk新曲提醒', aliases=_make_cmds('pjsk新曲提醒'), permission=GROUP, priority=5, block=True)
user_music_off = on_command('pjsk取消新曲提醒', aliases=_make_cmds('pjsk取消新曲提醒'), permission=GROUP, priority=5, block=True)
user_vlive_on = on_command('pjsklive提醒', aliases=_make_cmds('pjsklive提醒'), permission=GROUP, priority=5, block=True)
user_vlive_off = on_command('pjsk取消live提醒', aliases=_make_cmds('pjsk取消live提醒'), permission=GROUP, priority=5, block=True)
sub_status = on_command('pjsk订阅状态', aliases=_make_cmds('pjsk订阅状态'), permission=GROUP, priority=5, block=True)
vlive_list = on_command('虚拟live', aliases={'虚拟live', 'vlive', 'pjsklive列表',
                                             'cn虚拟live', 'cnvlive', 'tw虚拟live', 'twvlive'},
                        priority=5, block=True)


def _parse_ctx(cmd: Tuple[str, ...]):
    cmd_name = cmd[0] if cmd else ''
    pjsk_type = get_pjsk_type(cmd_name)
    server = SERVER_MAP.get(pjsk_type, 'jp')
    return pjsk_type, server, SERVER_NAME_CN.get(server, server)


@sub_music_on.handle()
@sub_vlive_on.handle()
async def _(matcher: Matcher, event: GroupMessageEvent, cmd: Tuple[str, ...] = Command()):
    _, server, name = _parse_ctx(cmd)
    kind = KIND_MUSIC if '新曲' in (cmd[0] if cmd else '') else KIND_VLIVE
    if await add_notify_sub(str(event.group_id), None, server, kind):
        await matcher.finish(f'✅ 已开启本群{_KIND_LABEL[kind]}（{name}）')
    await matcher.finish(f'本群已开启{_KIND_LABEL[kind]}（{name}），无需重复操作')


@sub_music_off.handle()
@sub_vlive_off.handle()
async def _(matcher: Matcher, event: GroupMessageEvent, cmd: Tuple[str, ...] = Command()):
    _, server, name = _parse_ctx(cmd)
    kind = KIND_MUSIC if '新曲' in (cmd[0] if cmd else '') else KIND_VLIVE
    removed = await remove_group_subs(str(event.group_id), server, kind)
    if removed:
        await matcher.finish(f'✅ 已关闭本群{_KIND_LABEL[kind]}（{name}），并清理了 {removed - 1} 条个人提醒' if removed > 1
                             else f'✅ 已关闭本群{_KIND_LABEL[kind]}（{name}）')
    await matcher.finish(f'本群没有开启{_KIND_LABEL[kind]}（{name}）')


@user_music_on.handle()
@user_vlive_on.handle()
async def _(matcher: Matcher, event: GroupMessageEvent, cmd: Tuple[str, ...] = Command()):
    _, server, name = _parse_ctx(cmd)
    kind = KIND_MUSIC if '新曲' in (cmd[0] if cmd else '') else KIND_VLIVE
    if not await is_group_subbed(kind, server, str(event.group_id)):
        await matcher.finish(f'本群还没有开启{_KIND_LABEL[kind]}（{name}），请先让管理员发送 pjsk开启{"新曲" if kind == KIND_MUSIC else "live"}通知')
    if await add_notify_sub(str(event.group_id), str(event.user_id), server, kind):
        await matcher.finish(f'✅ 已订阅{_KIND_LABEL[kind]}（{name}）的@提醒', at_sender=True)
    await matcher.finish('你已经订阅过了哦', at_sender=True)


@user_music_off.handle()
@user_vlive_off.handle()
async def _(matcher: Matcher, event: GroupMessageEvent, cmd: Tuple[str, ...] = Command()):
    _, server, name = _parse_ctx(cmd)
    kind = KIND_MUSIC if '新曲' in (cmd[0] if cmd else '') else KIND_VLIVE
    if await remove_notify_sub(str(event.group_id), str(event.user_id), server, kind):
        await matcher.finish(f'✅ 已取消{_KIND_LABEL[kind]}（{name}）的@提醒', at_sender=True)
    await matcher.finish('你没有订阅过这个提醒哦', at_sender=True)


@sub_status.handle()
async def _(matcher: Matcher, event: GroupMessageEvent):
    subs = await get_group_sub_status(str(event.group_id))
    if not subs:
        await matcher.finish('本群没有任何pjsk订阅。可用：pjsk开启新曲通知 / pjsk开启live通知')
    lines = ['本群pjsk订阅：']
    for s in subs:
        name = SERVER_NAME_CN.get(s.server, s.server)
        label = _KIND_LABEL.get(s.kind, s.kind)
        if s.qq_id is None:
            lines.append(f'· {label}（{name}）：群推送已开启')
        else:
            lines.append(f'  - @提醒：{s.qq_id}（{label}/{name}）')
    await matcher.finish('\n'.join(lines))


@vlive_list.handle()
async def _(matcher: Matcher, event: GroupMessageEvent, cmd: Tuple[str, ...] = Command()):
    pjsk_type, server, name = _parse_ctx(cmd)
    access_count(vlive_list.plugin_name, event)
    access_cd(vlive_list.plugin_name, event)
    vlives = await get_recent_vlives(pjsk_type, within_days=7)
    if not vlives:
        await matcher.finish(f'当前{name}没有7天内的虚拟Live')
    banners = await fetch_vlive_banners(vlives, pjsk_type)
    img = await run_pjsk_thread(
        draw_vlive_cards, f'近期虚拟Live（{name}）', vlives, banners, 'KNDBOT · 虚拟Live'
    )
    await matcher.finish(image(b64=await run_pjsk_thread(pic2b64, img)))


# 注册定时任务
from . import _notify  # noqa: E402, F401

logger.info('订阅通知模块加载完成')
