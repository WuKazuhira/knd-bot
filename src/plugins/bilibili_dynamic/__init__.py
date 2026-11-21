import asyncio
from datetime import datetime

from nonebot import get_bots, get_driver, on_command, on_notice
from nonebot.adapters.onebot.v11 import (
    ActionFailed,
    Bot,
    GroupDecreaseNoticeEvent,
    GroupMessageEvent,
    Message,
    MessageEvent,
    MessageSegment,
)
from nonebot.internal.matcher import Matcher
from nonebot.params import CommandArg

from config.path_config import DATA_PATH
from manager import Config, group_manager, plugins2settings_manager, plugins_manager
from services import logger
from utils.utils import scheduler

from ._api import BilibiliError, Dynamic, DynamicClient, parse_uid
from ._notify import DynamicPushService
from ._store import SubscriptionStore

__plugin_name__ = 'B站动态订阅'
__plugin_type__ = '实用工具'
__plugin_version__ = 0.1
__plugin_usage__ = """
    帮助：knd B站动态订阅 help

usage：
    订阅Bilibili用户后，将他新发布的动态自动推送到本群。
    指令：
        订阅B站动态 <UID>         添加本群订阅（群主/管理员/超级用户）
        取消B站动态 <UID>         取消本群订阅（群主/管理员/超级用户）
        B站动态订阅列表           查看本群订阅
    支持bilibili别名，如：订阅bilibili动态 123。
    UID是用户空间地址中的数字；不支持昵称搜索，不订阅直播通知。
    首次订阅不补发历史，默认约60秒检查一次，每群每个UP每轮最多推送3条。
    推送文字、图片/封面及原文链接；图片失败时尝试改为文字。
    本群或插件关闭、机器人离线/退群时不推送；临时失败自动退避重试。
    重启保留订阅和逐群去重记录，接口受限时不会误报订阅成功。
    缓存最多保留7天/每个UP最近1000条；过期未投递数量会在订阅列表中提示。
    部署配置：config/config.yaml 的 bilibili_dynamic 节点，可配置 COOKIE、PROXY、
    POLL_INTERVAL（秒）、REQUEST_TIMEOUT（秒）、MAX_SUBSCRIPTIONS（每群数量）。
    Cookie可不填；如B站限制访问或要求登录，请由部署者提供有效Cookie后重启。
    不要在群里发送Cookie；本功能不会索要账号密码或自动处理验证码。
    离线期间尝试有限翻页补取；积压超出5页时记录错误，需部署者处理，不静默跳过。
""".strip()
__plugin_settings__ = {
    'default_status': True,
    'cmd': [
        'B站动态订阅',
        '订阅B站动态',
        '取消B站动态',
        'B站动态订阅列表',
        '订阅b站动态',
        '取消b站动态',
        'b站动态订阅列表',
        'b站动态订阅',
        'bilibili动态订阅',
        '订阅bilibili动态',
        '取消bilibili动态',
        'bilibili动态订阅列表',
    ],
}
__plugin_cd_limit__ = {'cd': 5, 'limit_type': 'user', 'rst': '别急，[cd]秒后再用！'}
__plugin_configs__ = {
    'COOKIE': {'value': '', 'help': '部署者提供的B站Cookie，仅用于请求B站，修改后重启。', 'default_value': ''},
    'PROXY': {'value': '', 'help': '可选HTTP代理地址，修改后重启。', 'default_value': ''},
    'POLL_INTERVAL': {'value': 60, 'help': '动态检查间隔（秒），最低30秒。', 'default_value': 60},
    'REQUEST_TIMEOUT': {'value': 20, 'help': 'B站请求超时（秒），修改后重启。', 'default_value': 20},
    'MAX_SUBSCRIPTIONS': {'value': 20, 'help': '每个Bot在每个群允许的最大订阅数量。', 'default_value': 20},
}

subscribe = on_command('订阅B站动态', aliases={'订阅b站动态', '订阅bilibili动态'}, priority=5, block=True)
unsubscribe = on_command('取消B站动态', aliases={'取消b站动态', '取消bilibili动态'}, priority=5, block=True)
subscription_list = on_command(
    'B站动态订阅列表', aliases={'b站动态订阅列表', 'bilibili动态订阅列表'}, priority=5, block=True
)
group_leave = on_notice(priority=5, block=False)

_service = None
_group_ids = {}
_stopping = False
_scheduled_interval = None


def _int_config(key, default, minimum, maximum):
    try:
        value = int(Config.get_config('bilibili_dynamic', key, default))
    except (ValueError, TypeError):
        value = default
    return max(minimum, min(maximum, value))


def _is_admin(bot, event):
    return event.sender.role in {'owner', 'admin'} or str(event.user_id) in bot.config.superusers


def _enabled(subscription):
    group_id = subscription.group_id
    level = group_manager.get_group_level(group_id)
    required = plugins2settings_manager.get_plugin_data('bilibili_dynamic').get('level', 5)
    return (
        level >= 0
        and level >= required
        and group_id in _group_ids.get(subscription.bot_id, set())
        and group_manager.check_group_bot_status(group_id)
        and group_manager.get_plugin_status('bilibili_dynamic', group_id)
        and group_manager.get_plugin_status('bilibili_dynamic', group_id, True)
        and plugins_manager.get_plugin_status('bilibili_dynamic', 'all')
        and plugins_manager.get_plugin_status('bilibili_dynamic', 'group')
    )


def _message(item: Dynamic, include_images=True):
    labels = {
        'DYNAMIC_TYPE_WORD': '发布了文字动态',
        'DYNAMIC_TYPE_DRAW': '发布了图文动态',
        'DYNAMIC_TYPE_AV': '发布了新投稿',
        'DYNAMIC_TYPE_ARTICLE': '发布了专栏',
        'DYNAMIC_TYPE_FORWARD': '转发了动态',
    }
    timestamp = datetime.fromtimestamp(item.published_at).strftime('%Y-%m-%d %H:%M') if item.published_at else ''
    text = f'{item.name}（UID:{item.uid}）{labels.get(item.kind, "发布了新动态")}\n{timestamp}\n{item.text}'
    # 外部文字必须作为text段传入，不能让动态正文里的CQ码变成QQ操作。
    message = Message(MessageSegment.text(text.strip()))
    if include_images:
        for url in item.images[:3]:
            message += MessageSegment.image(url)
    message += MessageSegment.text(f'\n{item.url}')
    return message


async def _send(bot_id, group_id, item):
    bot = get_bots().get(bot_id)
    if not isinstance(bot, Bot):
        raise RuntimeError('机器人已离线')
    try:
        await bot.send_group_msg(group_id=group_id, message=_message(item))
    except ActionFailed:
        if not item.images:
            raise
        await bot.send_group_msg(group_id=group_id, message=_message(item, include_images=False))


def _get_service():
    global _service
    if _stopping:
        raise RuntimeError('动态订阅服务正在关闭')
    if _service is None:
        client = DynamicClient(
            cookie=str(Config.get_config('bilibili_dynamic', 'COOKIE', '') or ''),
            proxy=str(Config.get_config('bilibili_dynamic', 'PROXY', '') or ''),
            timeout=_int_config('REQUEST_TIMEOUT', 20, 5, 120),
        )
        store = SubscriptionStore(DATA_PATH / 'bilibili_dynamic' / 'subscription.db')
        _service = DynamicPushService(store, client, _send, _enabled, logger)
    return _service


async def _check_group(matcher, event):
    if not isinstance(event, GroupMessageEvent):
        await matcher.finish('这个功能只能在群里使用哦。')


@subscribe.handle()
async def _(bot: Bot, matcher: Matcher, event: MessageEvent, msg: Message = CommandArg()):
    await _check_group(matcher, event)
    if not _is_admin(bot, event):
        await matcher.finish('请让群主、管理员或超级用户来添加动态订阅哦。')
    uid = parse_uid(msg.extract_plain_text().strip())
    if uid is None:
        await matcher.finish('使用方法：订阅B站动态 <UID>，UID需要是用户空间地址中的正整数。')
    limit = _int_config('MAX_SUBSCRIPTIONS', 20, 1, 100)
    try:
        result = await _get_service().subscribe(bot.self_id, event.group_id, uid, limit)
    except BilibiliError as error:
        await matcher.finish(f'订阅未保存：{error.message}（错误码：{error.code}）')
    except Exception as error:
        logger.warning(f'[B站动态订阅] 添加订阅失败：{type(error).__name__}')
        await matcher.finish('订阅失败，请稍后重试或联系部署者检查存储和网络配置。')
    if result == 'exists':
        await matcher.finish(f'本群已经订阅 UID {uid} 了哦。')
    if result == 'limit':
        await matcher.finish(f'本群最多订阅 {limit} 个B站用户，请先取消不需要的订阅。')
    logger.info(f'[B站动态订阅] 群 {event.group_id} 订阅 UID {uid}')
    await matcher.finish(f'已订阅 UID {uid} 的动态，之后的新动态会推送到本群，不补发历史。')


@unsubscribe.handle()
async def _(bot: Bot, matcher: Matcher, event: MessageEvent, msg: Message = CommandArg()):
    await _check_group(matcher, event)
    if not _is_admin(bot, event):
        await matcher.finish('请让群主、管理员或超级用户来取消动态订阅哦。')
    uid = parse_uid(msg.extract_plain_text().strip())
    if uid is None:
        await matcher.finish('使用方法：取消B站动态 <UID>')
    try:
        removed = await _get_service().unsubscribe(bot.self_id, event.group_id, uid)
    except Exception as error:
        logger.warning(f'[B站动态订阅] 取消订阅失败：{type(error).__name__}')
        await matcher.finish('取消失败，请稍后重试。')
    await matcher.finish(f'已取消本群对 UID {uid} 的动态订阅。' if removed else f'本群还没有订阅 UID {uid}。')


@subscription_list.handle()
async def _(bot: Bot, matcher: Matcher, event: MessageEvent):
    await _check_group(matcher, event)
    try:
        subscriptions = await _get_service().store.list_subscriptions(bot.self_id, event.group_id)
    except Exception as error:
        logger.warning(f'[B站动态订阅] 查询订阅失败：{type(error).__name__}')
        await matcher.finish('查询失败，请稍后重试。')
    if not subscriptions:
        await matcher.finish('本群还没有B站动态订阅，让管理员发送：订阅B站动态 <UID>')
    lines = ['本群B站动态订阅：']
    for sub in subscriptions:
        status = '（投递失败，等待重试）' if sub.failures else ''
        if sub.skipped:
            status += f'（{sub.skipped}条积压已过期跳过）'
        lines.append(f'· {sub.name}  UID:{sub.uid}{status}')
    await matcher.finish(MessageSegment.text('\n'.join(lines)))


@group_leave.handle()
async def _(bot: Bot, event: GroupDecreaseNoticeEvent):
    if str(event.user_id) == bot.self_id:
        _group_ids.get(bot.self_id, set()).discard(event.group_id)


def _schedule_poll(interval: int):
    global _scheduled_interval
    _scheduled_interval = interval
    scheduler.add_job(
        _poll,
        'interval',
        seconds=interval,
        id='bilibili_dynamic_poll',
        replace_existing=True,
        coalesce=True,
        max_instances=1,
        misfire_grace_time=30,
    )


async def _poll():
    global _scheduled_interval
    if _stopping:
        return
    interval = _int_config('POLL_INTERVAL', 60, 30, 3600)
    if (
        _scheduled_interval is not None
        and _scheduled_interval != interval
        and scheduler.get_job('bilibili_dynamic_poll')
    ):
        scheduler.reschedule_job('bilibili_dynamic_poll', trigger='interval', seconds=interval)
        _scheduled_interval = interval
    try:
        service = _get_service()
        subscriptions = await service.store.list_subscriptions()
    except Exception as error:
        logger.warning(f'[B站动态订阅] 读取订阅列表失败：{type(error).__name__}')
        return
    if not subscriptions or _stopping:
        return
    subscribed_bot_ids = {sub.bot_id for sub in subscriptions}
    targets = [
        (bot_id, bot)
        for bot_id, bot in list(get_bots().items())
        if isinstance(bot, Bot) and bot_id in subscribed_bot_ids
    ]
    if not targets:
        return
    online_bot_ids = set()

    async def _refresh_bot_groups(bot_id: str, bot: Bot):
        try:
            groups = await bot.get_group_list()
            _group_ids[bot_id] = {int(group['group_id']) for group in groups}
            online_bot_ids.add(bot_id)
        except Exception as error:
            logger.warning(f'[B站动态订阅] 获取Bot({bot_id})群列表失败：{type(error).__name__}')
            if bot_id not in _group_ids:
                cached = set(group_manager.get_group_list())
                if cached:
                    _group_ids[bot_id] = cached
            if _group_ids.get(bot_id):
                online_bot_ids.add(bot_id)

    await asyncio.gather(*(_refresh_bot_groups(bot_id, bot) for bot_id, bot in targets))
    if not online_bot_ids or _stopping:
        return
    try:
        await service.poll(online_bot_ids)
    except Exception as error:
        logger.warning(f'[B站动态订阅] 轮询失败：{type(error).__name__}')


@get_driver().on_startup
async def _startup():
    global _stopping
    _stopping = False
    _schedule_poll(_int_config('POLL_INTERVAL', 60, 30, 3600))


@get_driver().on_shutdown
async def _shutdown():
    global _service, _stopping, _scheduled_interval
    _stopping = True
    _scheduled_interval = None
    if scheduler.get_job('bilibili_dynamic_poll'):
        scheduler.remove_job('bilibili_dynamic_poll')
    if _service is not None:
        await _service.close()
        _service = None
