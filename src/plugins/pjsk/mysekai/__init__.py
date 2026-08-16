import asyncio
import base64
import time
from collections import OrderedDict
from datetime import datetime, timedelta
from io import BytesIO
from typing import Optional, Tuple

from nonebot import get_bot, on_command
from nonebot.adapters.onebot.v11 import (
    GroupMessageEvent,
    Message,
    MessageEvent,
    MessageSegment,
)
from nonebot.exception import (
    FinishedException,
    PausedException,
    RejectedException,
    StopPropagation,
)
from nonebot.internal.matcher import Matcher
from nonebot.params import Command, CommandArg
from nonebot.permission import SUPERUSER
from PIL import Image

from services.log import logger
from utils.imageutils import add_kndbot_watermark, pic2b64
from utils.message_builder import image
from utils.utils import scheduler

from .._config import SERVER_MAP
from .._gameapi import GameApiConfig, request_gameapi
from .._haruki_remote import render_mysekai
from .._utils import get_pjsk_type
from ._data import (
    MySekaiError,
    assert_cn_msr_allowed,
    async_load_cn_allowed_groups,
    async_save_cn_allowed_groups,
    get_bound_uid,
    get_mysekai_info,
    get_photo,
    get_profile_for_header,
    get_suite_data,
    profile_from_suite_data,
)
from ._draw import (
    compose_fixture_detail_image,
    compose_fixture_list_image,
    compose_gate_image,
    compose_map_image,
    compose_material_image,
    compose_musicrecord_image,
    compose_res_list_image,
    compose_summary_image,
    compose_talk_list_image,
)
from ._subscription import (
    add_msr_subscription,
    get_all_msr_subscriptions,
    remove_msr_subscription,
    update_msr_last_push,
)
from ._utils import UNIT_GATEID_MAP, get_cid_by_nickname, get_last_refresh_time, parse_unit_arg, server_name

__plugin_name__ = "MySekai/烤森查询"
__plugin_type__ = "烧烤相关&uni移植"
__plugin_version__ = 0.2
__plugin_usage__ = """
usage：
    msr / msmap / msa                 查询 MySekai 资源刷新（含来访角色 / 天气 / 稀有资源）
    msr all                           显示已采集资源
    msb                               查询自己蓝图收集
    msb <角色名>                      查询指定角色的对话进度（未读对话家具）
    msb <角色名> all                  查询指定角色的全部对话家具
    msf                               查询家具列表（按分类）
    msf 1 2 3                         查询家具详情（最多 10 个）
    msf <角色名>                      等同 msb <角色名>
    msp 1                             下载第 1 张 MySekai 照片
    msd                               查询 MySekai 抓包数据状态
    msr订阅 / msr取消订阅             订阅/取消 MySekai 数据更新自动推送
    msg / msgate                      查询门升级材料
    msg ln                            查询指定组合的门
    msm / mss / mssong                查询唱片收集
    烤森材料                          查询材料持有
    烤森材料 all                      查询所有历史获取过的材料

cn 群限制：cn 前缀（cnmsr 等）仅在已加入 CN 白名单的群可用。
管理指令（仅超级用户）：
    cnmsr启用 <群号>                  添加群到 CN MSR 白名单
    cnmsr禁用 <群号>                  从 CN MSR 白名单移除群
    cnmsr白名单                       查看当前白名单

指令通用前缀：cn / tw （日服无前缀），如 cnmsr、twmsf。
""".strip()
__plugin_settings__ = {
    "default_status": False,
    "cmd": ["mysekai", "烤森", "msr", "msmap", "msa", "msf", "msb", "msm", "mss", "mssong", "msg", "msgate", "msp", "msd", "烤森材料"],
}
__plugin_cd_limit__ = {"cd": 60, "count_limit": 2, "rst": "别急，等[cd]秒后再用！", "limit_type": "user"}
__plugin_block_limit__ = {"rst": "别急，还在查！"}


# 通用工具

def _jpeg_bytes(img) -> bytes:
    buf = BytesIO()
    add_kndbot_watermark(img.convert("RGB")).save(buf, format="JPEG", quality=82, optimize=True)
    return buf.getvalue()


def _jpeg_msg(payload: bytes) -> MessageSegment:
    return image(b64="base64://" + base64.b64encode(payload).decode())


def _img_msg(img, low_quality: bool = False) -> MessageSegment:
    img = img.convert("RGB")
    if not low_quality:
        return image(b64=pic2b64(img))
    return _jpeg_msg(_jpeg_bytes(img))


def _remote_img_msg(img_bytes: bytes) -> Optional[MessageSegment]:
    try:
        return image(b64=pic2b64(Image.open(BytesIO(img_bytes)).convert("RGB")))
    except Exception as e:
        logger.warning(f"[mysekai] 远端图片转换失败，回退本地实现: {e}")
        return None


async def _render_remote_mysekai(kind: str, payload: dict) -> Optional[bytes]:
    try:
        data = dict(payload)
        data['kind'] = kind
        return await render_mysekai(data)
    except Exception as e:
        logger.warning(f"[mysekai] 远端绘图失败，回退本地实现: {e}")
        return None


def _cmd_server(cmd: Tuple[str, ...]) -> int:
    return get_pjsk_type(cmd[0]) if cmd else 0


def _event_group_id(event: MessageEvent) -> Optional[int]:
    if isinstance(event, GroupMessageEvent):
        return event.group_id
    return None


async def _base_context(event: MessageEvent, pjsk_type: int):
    uid, private = await get_bound_uid(event.user_id, pjsk_type)
    profile = await get_profile_for_header(uid, pjsk_type)
    return uid, private, profile


async def _finish_error(matcher: Matcher, e: Exception):
    # nonebot 用 FinishedException/PausedException/RejectedException/StopPropagation
    # 作为控制流（matcher.finish 等都会抛这些异常），不要把它们当错误处理，
    # 否则会触发二次 finish 并把 traceback 打到日志里。
    if isinstance(e, (FinishedException, PausedException, RejectedException, StopPropagation)):
        raise e
    if isinstance(e, MySekaiError):
        await matcher.finish(str(e), at_sender=True)
    logger.exception(f"MySekai 指令执行失败: {e}")
    await matcher.finish(f"MySekai 查询失败：{e}", at_sender=True)


def _ensure_cn_allowed(event: MessageEvent, pjsk_type: int) -> None:
    """cn 服触发的指令需先通过群白名单校验；其它服直接放行。"""
    assert_cn_msr_allowed(_event_group_id(event), pjsk_type)


def _strip_keywords(args: str, keywords: list[str]) -> tuple[str, set[str]]:
    """把 ``args`` 中的 keyword 提取出来，返回 (其余文本, 命中的 keyword 集合)。"""
    hit: set[str] = set()
    for kw in keywords:
        if kw in args.split():
            hit.add(kw)
            args = " ".join(p for p in args.split() if p != kw)
    return args.strip(), hit


# 资源刷新

msr = on_command(
    "msr",
    aliases={
        "msmap", "msa",
        "pjsk mysekai res", "mysekai 资源",
        "cnmsr", "cnmsmap", "cnmsa",
        "twmsr", "twmsmap", "twmsa",
    },
    priority=5,
    block=True,
)


@msr.handle()
async def _(matcher: Matcher, event: MessageEvent, msg: Message = CommandArg(), cmd: Tuple[str, ...] = Command()):
    pjsk_type = _cmd_server(cmd)
    args = msg.extract_plain_text().strip().lower()
    show_all = "all" in args.split()
    try:
        _ensure_cn_allowed(event, pjsk_type)
        uid, private, profile = await _base_context(event, pjsk_type)
        mysekai_info, pmsg = await get_mysekai_info(uid, pjsk_type)
        suite_data, suite_msg = await get_suite_data(uid, pjsk_type)
        remote_pic = await _render_remote_mysekai('resource', {
            'uid': uid,
            'profile': profile,
            'is_private': private,
            'mysekai_info': mysekai_info,
            'suite_data': suite_data,
            'message': pmsg or suite_msg,
            'show_all': show_all,
            'pjsk_type': pjsk_type,
        })
        if remote_pic:
            remote_msg = _remote_img_msg(remote_pic)
            if remote_msg:
                await matcher.finish(remote_msg)

        imgs = await asyncio.gather(
            compose_summary_image(profile, private, mysekai_info, suite_data, pmsg or suite_msg, pjsk_type),
            compose_res_list_image(profile, private, mysekai_info, show_all, pmsg, pjsk_type),
            compose_map_image(profile, private, mysekai_info, show_all, pjsk_type),
        )
        out = Message()
        for i in imgs:
            out += _img_msg(i)
        await matcher.finish(out)
    except Exception as e:
        await _finish_error(matcher, e)


# MSR 自动推送订阅

msr_subscribe = on_command(
    "msr订阅",
    aliases={"msr推送订阅", "msr自动推送", "cnmsr订阅", "twmsr订阅"},
    priority=5,
    block=True,
)

msr_unsubscribe = on_command(
    "msr取消订阅",
    aliases={"msr推送取消", "msr取消推送", "cnmsr取消订阅", "twmsr取消订阅"},
    priority=5,
    block=True,
)


@msr_subscribe.handle()
async def _(matcher: Matcher, event: MessageEvent, cmd: Tuple[str, ...] = Command()):
    try:
        if not isinstance(event, GroupMessageEvent):
            raise MySekaiError("MSR 自动推送只能在群聊中订阅")
        pjsk_type = _cmd_server(cmd)
        _ensure_cn_allowed(event, pjsk_type)
        cfg = GameApiConfig(pjsk_type)
        if not cfg.mysekai_upload_time_api_url:
            raise MySekaiError(f"{SERVER_MAP.get(pjsk_type, 'jp').upper()} 暂不支持 MySekai 自动推送")
        uid, _, _ = await _base_context(event, pjsk_type)
        ok = await add_msr_subscription(
            qq_id=str(event.user_id),
            group_id=str(event.group_id),
            server=SERVER_MAP.get(pjsk_type, "jp"),
            uid=str(uid),
            mode="latest",
        )
        if not ok:
            raise MySekaiError("订阅失败，请稍后再试")
        await matcher.finish(f"已订阅 {SERVER_MAP.get(pjsk_type, 'jp').upper()} MySekai 数据更新自动推送")
    except Exception as e:
        await _finish_error(matcher, e)


@msr_unsubscribe.handle()
async def _(matcher: Matcher, event: MessageEvent, cmd: Tuple[str, ...] = Command()):
    try:
        pjsk_type = _cmd_server(cmd)
        _ensure_cn_allowed(event, pjsk_type)
        server = SERVER_MAP.get(pjsk_type, "jp")
        ok = await remove_msr_subscription(str(event.user_id), server)
        if ok:
            await matcher.finish(f"已取消 {server.upper()} MySekai 自动推送订阅")
        await matcher.finish(f"你没有订阅 {server.upper()} MySekai 自动推送")
    except Exception as e:
        await _finish_error(matcher, e)


# 蓝图收集 / 角色对话

msb = on_command(
    "msb",
    aliases={
        "pjsk mysekai blueprint", "mysekai blueprint", "mysekai 蓝图",
        "cnmsb", "twmsb",
    },
    priority=5,
    block=True,
)


@msb.handle()
async def _(matcher: Matcher, event: MessageEvent, msg: Message = CommandArg(), cmd: Tuple[str, ...] = Command()):
    pjsk_type = _cmd_server(cmd)
    args = msg.extract_plain_text().strip().lower()
    try:
        _ensure_cn_allowed(event, pjsk_type)
        args, hits = _strip_keywords(args, ["all", "id"])
        show_all_talks = "all" in hits
        unit, args = parse_unit_arg(args)
        cid = get_cid_by_nickname(args, pjsk_type)
        uid, private, profile = await _base_context(event, pjsk_type)
        mysekai_info, _ = await get_mysekai_info(uid, pjsk_type)

        if not cid:
            img = await compose_fixture_list_image(
                profile, private, mysekai_info,
                only_craftable=True, pjsk_type=pjsk_type,
            )
        else:
            cuid = _resolve_chara_unit_id(cid, unit, pjsk_type)
            suite_data, _ = await get_suite_data(uid, pjsk_type)
            img = await compose_talk_list_image(
                profile, private, mysekai_info, suite_data,
                cuid=cuid, show_all_talks=show_all_talks, pjsk_type=pjsk_type,
            )
        await matcher.finish(_img_msg(img))
    except Exception as e:
        await _finish_error(matcher, e)


def _resolve_chara_unit_id(cid: int, unit: Optional[str], pjsk_type: int) -> int:
    """把 chara_id（1~26）解析到 game_character_units.id。

    V 家角色（cid 21~26）会出现在多个组合，必须配合 unit 参数。
    """
    from .._utils import load_master_data
    from ._utils import get_by_id

    cu_list = [
        cu for cu in (load_master_data("gameCharacterUnits.json", pjsk_type) or [])
        if isinstance(cu, dict) and cu.get("gameCharacterId") == cid
    ]
    if not cu_list:
        raise MySekaiError(f"找不到角色 {cid} 的组合数据")

    # 过滤只保留 mysekai 中真正可见的 cuid
    try:
        gate_lottery = load_master_data("mysekaiGateCharacterLotteries.json", pjsk_type) or []
        valid_cuids = {item.get("gameCharacterUnitId") for item in gate_lottery if isinstance(item, dict)}
        if valid_cuids:
            cu_list = [cu for cu in cu_list if cu.get("id") in valid_cuids]
    except Exception:
        pass

    if not cu_list:
        raise MySekaiError("该角色暂无 mysekai 对话数据")

    if len(cu_list) == 1:
        return cu_list[0]["id"]

    if not unit:
        units = "/".join(cu.get("unit", "?") for cu in cu_list)
        raise MySekaiError(f"该角色存在多个组合（{units}），请同时指定组合，例如「/msb miku ln」")

    target = next((cu for cu in cu_list if cu.get("unit") == unit), None)
    if not target:
        raise MySekaiError(f"找不到「{unit}」组合下的该角色")
    return target["id"]


# 家具列表 / 家具详情

msf = on_command(
    "msf",
    aliases={
        "pjsk mysekai furniture", "pjsk mysekai fixture",
        "mysekai 家具", "家具列表",
        "cnmsf", "twmsf",
    },
    priority=5,
    block=True,
)


@msf.handle()
async def _(matcher: Matcher, event: MessageEvent, msg: Message = CommandArg(), cmd: Tuple[str, ...] = Command()):
    pjsk_type = _cmd_server(cmd)
    args = msg.extract_plain_text().strip().lower()
    try:
        _ensure_cn_allowed(event, pjsk_type)
        # 全数字参数视为家具 ID 详情
        fids: Optional[list[int]] = None
        try:
            tokens = args.split()
            if tokens and all(t.lstrip("-").isdigit() for t in tokens):
                fids = [int(t) for t in tokens]
        except Exception:
            fids = None

        if fids:
            if len(fids) > 10:
                raise MySekaiError("最多一次查询 10 个家具")
            img = await compose_fixture_detail_image(fids, pjsk_type)
            await matcher.finish(_img_msg(img))

        # 否则尝试把参数当作角色名查询对话进度
        rest, hits = _strip_keywords(args, ["all", "id"])
        show_all_talks = "all" in hits
        unit, rest = parse_unit_arg(rest)
        cid = get_cid_by_nickname(rest, pjsk_type)
        if cid:
            uid, private, profile = await _base_context(event, pjsk_type)
            mysekai_info, _ = await get_mysekai_info(uid, pjsk_type)
            cuid = _resolve_chara_unit_id(cid, unit, pjsk_type)
            suite_data, _ = await get_suite_data(uid, pjsk_type)
            img = await compose_talk_list_image(
                profile, private, mysekai_info, suite_data,
                cuid=cuid, show_all_talks=show_all_talks, pjsk_type=pjsk_type,
            )
            await matcher.finish(_img_msg(img))
            return

        # 缺省：全家具列表
        img = await compose_fixture_list_image(None, False, None, only_craftable=False, pjsk_type=pjsk_type)
        await matcher.finish(_img_msg(img))
    except Exception as e:
        await _finish_error(matcher, e)


# 照片

msp = on_command(
    "msp",
    aliases={
        "pjsk mysekai photo", "pjsk mysekai picture",
        "mysekai 照片", "cnmsp", "twmsp",
    },
    priority=5,
    block=True,
)


@msp.handle()
async def _(matcher: Matcher, event: MessageEvent, msg: Message = CommandArg(), cmd: Tuple[str, ...] = Command()):
    pjsk_type = _cmd_server(cmd)
    try:
        _ensure_cn_allowed(event, pjsk_type)
        seq = int(msg.extract_plain_text().strip())
        uid, _, _ = await _base_context(event, pjsk_type)
        photo, t = await get_photo(uid, seq, pjsk_type)
        out = Message()
        out += _img_msg(photo)
        out += f"拍摄时间：{t.strftime('%Y-%m-%d %H:%M')}"
        await matcher.finish(out)
    except ValueError:
        await matcher.finish("请输入正确的照片编号（从 1 或 -1 开始）", at_sender=True)
    except Exception as e:
        await _finish_error(matcher, e)


# 抓包状态

msd = on_command(
    "msd",
    aliases={
        "pjsk烤森抓包数据", "pjsk烤森抓包", "烤森抓包", "烤森抓包数据",
        "cnmsd", "twmsd",
    },
    priority=5,
    block=True,
)


@msd.handle()
async def _(matcher: Matcher, event: MessageEvent, cmd: Tuple[str, ...] = Command()):
    pjsk_type = _cmd_server(cmd)
    try:
        _ensure_cn_allowed(event, pjsk_type)
        uid, _, _ = await _base_context(event, pjsk_type)
        try:
            info, msg = await get_mysekai_info(uid, pjsk_type, filters=["upload_time"])
            import datetime
            ts = info.get("upload_time")
            up = datetime.datetime.fromtimestamp(ts).strftime("%m-%d %H:%M:%S") if ts else "未知"
            text = f"{uid} ({cmd[0] if cmd else 'jp'}) MySekai数据\n获取成功：{up}\n"
            if msg:
                text += f"提示：{msg}\n"
        except Exception as e:
            text = f"{uid} MySekai数据\n获取失败：{e}\n"
        text += "---\n发送 /抓包 获取抓包教程"
        await matcher.finish(text)
    except Exception as e:
        await _finish_error(matcher, e)


# 门升级材料

msgate = on_command(
    "msg",
    aliases={
        "msgate", "pjsk mysekai gate",
        "cnmsg", "cnmsgate", "twmsg", "twmsgate",
    },
    priority=5,
    block=True,
)


@msgate.handle()
async def _(matcher: Matcher, event: MessageEvent, msg: Message = CommandArg(), cmd: Tuple[str, ...] = Command()):
    pjsk_type = _cmd_server(cmd)
    args = msg.extract_plain_text().strip().lower()
    try:
        _ensure_cn_allowed(event, pjsk_type)
        uid, private, profile = await _base_context(event, pjsk_type)
        unit, _ = parse_unit_arg(args)
        gate_id = UNIT_GATEID_MAP.get(unit) if unit else None
        suite_data, suite_msg = await get_suite_data(uid, pjsk_type)
        remote_pic = await _render_remote_mysekai('gate', {
            'uid': uid,
            'profile': profile,
            'is_private': private,
            'suite_data': suite_data,
            'message': suite_msg,
            'gate_id': gate_id,
            'pjsk_type': pjsk_type,
        })
        if remote_pic:
            remote_msg = _remote_img_msg(remote_pic)
            if remote_msg:
                await matcher.finish(remote_msg)

        img = await compose_gate_image(profile, private, suite_data, gate_id, pjsk_type)
        await matcher.finish(_img_msg(img))
    except Exception as e:
        await _finish_error(matcher, e)


# 唱片

msm = on_command(
    "msm",
    aliases={
        "mss", "mssong", "pjsk mysekai musicrecord",
        "cnmsm", "cnmss", "cnmssong",
        "twmsm", "twmss", "twmssong",
    },
    priority=5,
    block=True,
)


@msm.handle()
async def _(matcher: Matcher, event: MessageEvent, msg: Message = CommandArg(), cmd: Tuple[str, ...] = Command()):
    pjsk_type = _cmd_server(cmd)
    args = msg.extract_plain_text().strip().lower()
    try:
        _ensure_cn_allowed(event, pjsk_type)
        uid, private, profile = await _base_context(event, pjsk_type)
        mysekai_info, _ = await get_mysekai_info(uid, pjsk_type)
        show_id = "id" in args.split()
        remote_pic = await _render_remote_mysekai('musicrecord', {
            'uid': uid,
            'profile': profile,
            'is_private': private,
            'mysekai_info': mysekai_info,
            'show_id': show_id,
            'pjsk_type': pjsk_type,
        })
        if remote_pic:
            remote_msg = _remote_img_msg(remote_pic)
            if remote_msg:
                await matcher.finish(remote_msg)

        img = await compose_musicrecord_image(
            profile, private, mysekai_info,
            show_id=show_id, pjsk_type=pjsk_type,
        )
        await matcher.finish(_img_msg(img))
    except Exception as e:
        await _finish_error(matcher, e)


# 烤森材料

msmat = on_command(
    "烤森材料",
    aliases={
        "mysekai材料", "pjsk mysekai材料",
        "cn烤森材料", "tw烤森材料",
    },
    priority=5,
    block=True,
)


@msmat.handle()
async def _(matcher: Matcher, event: MessageEvent, msg: Message = CommandArg(), cmd: Tuple[str, ...] = Command()):
    pjsk_type = _cmd_server(cmd)
    args = msg.extract_plain_text().strip().lower()
    try:
        _ensure_cn_allowed(event, pjsk_type)
        uid, private, profile = await _base_context(event, pjsk_type)
        suite_data, suite_msg = await get_suite_data(uid, pjsk_type)
        if not suite_data:
            raise MySekaiError(suite_msg or "未获取到 Suite 数据")
        show_all = "all" in args.split()
        remote_pic = await _render_remote_mysekai('material', {
            'uid': uid,
            'profile': profile,
            'is_private': private,
            'suite_data': suite_data,
            'message': suite_msg,
            'show_all': show_all,
            'pjsk_type': pjsk_type,
        })
        if remote_pic:
            remote_msg = _remote_img_msg(remote_pic)
            if remote_msg:
                await matcher.finish(remote_msg)

        img = await compose_material_image(
            profile, private, suite_data,
            show_all=show_all, pjsk_type=pjsk_type,
        )
        await matcher.finish(_img_msg(img))
    except Exception as e:
        await _finish_error(matcher, e)


# CN 服 MSR 群白名单管理

cnmsr_enable = on_command("cnmsr启用", priority=4, permission=SUPERUSER, block=True)
cnmsr_disable = on_command("cnmsr禁用", priority=4, permission=SUPERUSER, block=True)
cnmsr_list = on_command("cnmsr白名单", priority=4, permission=SUPERUSER, block=True)


def _parse_group_id(arg_text: str) -> int:
    arg = arg_text.strip()
    if not arg:
        raise MySekaiError("请提供群号，例如「/cnmsr启用 123456」")
    try:
        return int(arg)
    except ValueError as exc:
        raise MySekaiError("群号必须是数字") from exc


@cnmsr_enable.handle()
async def _(matcher: Matcher, event: MessageEvent, msg: Message = CommandArg()):
    try:
        gid = _parse_group_id(msg.extract_plain_text())
        groups = await async_load_cn_allowed_groups()
        if gid in groups:
            await matcher.finish(f"群 {gid} 已在 CN MSR 白名单中")
        groups.add(gid)
        await async_save_cn_allowed_groups(groups)
        await matcher.finish(f"已将群 {gid} 加入 CN MSR 白名单")
    except Exception as e:
        await _finish_error(matcher, e)


@cnmsr_disable.handle()
async def _(matcher: Matcher, event: MessageEvent, msg: Message = CommandArg()):
    try:
        gid = _parse_group_id(msg.extract_plain_text())
        groups = await async_load_cn_allowed_groups()
        if gid not in groups:
            await matcher.finish(f"群 {gid} 不在 CN MSR 白名单中")
        groups.discard(gid)
        await async_save_cn_allowed_groups(groups)
        await matcher.finish(f"已将群 {gid} 移出 CN MSR 白名单")
    except Exception as e:
        await _finish_error(matcher, e)


@cnmsr_list.handle()
async def _(matcher: Matcher, event: MessageEvent):
    try:
        groups = sorted(await async_load_cn_allowed_groups())
        if not groups:
            await matcher.finish("CN MSR 白名单为空")
        text = "CN MSR 白名单：\n" + "\n".join(str(g) for g in groups)
        await matcher.finish(text)
    except Exception as e:
        await _finish_error(matcher, e)

# MSR 自动推送任务

MSR_PUSH_INTERVAL_MINUTES = 1
MSR_PUSH_RECENT_MINUTES = 10
MSR_PUSH_CONCURRENCY = 3
MSR_PUSH_IMAGE_CACHE_LIMIT = 16
_MSR_PUSH_IMAGE_CACHE: OrderedDict[tuple[str, int, str, bool, int], tuple[bytes, ...]] = OrderedDict()


def _msr_supported_servers() -> list[tuple[int, str, GameApiConfig]]:
    ret = []
    for pjsk_type, name in SERVER_MAP.items():
        cfg = GameApiConfig(pjsk_type)
        if cfg.mysekai_upload_time_api_url:
            ret.append((pjsk_type, name, cfg))
    return ret


async def _render_msr_push_assets(
    uid: str,
    pjsk_type: int,
    private: bool,
    upload_time_hint: Optional[int] = None,
) -> tuple[MessageSegment, ...]:
    started = time.perf_counter()
    server = server_name(pjsk_type)

    def cached_segments(key: tuple[str, int, str, bool, int]) -> Optional[tuple[MessageSegment, ...]]:
        payloads = _MSR_PUSH_IMAGE_CACHE.get(key)
        if payloads is None:
            return None
        _MSR_PUSH_IMAGE_CACHE.move_to_end(key)
        return tuple(_jpeg_msg(payload) for payload in payloads)

    if upload_time_hint:
        key = (server, pjsk_type, str(uid), private, int(upload_time_hint))
        cached = cached_segments(key)
        if cached is not None:
            logger.info(f"自动推送 {server.upper()} MSR 缓存命中 uid={uid}: 总耗时={time.perf_counter() - started:.2f}s")
            return cached

    suite_result, mysekai_result = await asyncio.gather(
        get_suite_data(str(uid), pjsk_type),
        get_mysekai_info(str(uid), pjsk_type, mode="latest", use_cache=False),
    )
    suite_data, suite_msg = suite_result
    mysekai_info, pmsg = mysekai_result
    actual_upload_time = int(mysekai_info.get("upload_time") or upload_time_hint or 0)
    key = (server, pjsk_type, str(uid), private, actual_upload_time)
    cached = cached_segments(key)
    if cached is not None:
        logger.info(f"自动推送 {server.upper()} MSR 数据校正后命中缓存 uid={uid}: 总耗时={time.perf_counter() - started:.2f}s")
        return cached

    profile = profile_from_suite_data(str(uid), suite_data)
    data_done = time.perf_counter()
    imgs = await asyncio.gather(
        compose_summary_image(profile, private, mysekai_info, suite_data, pmsg or suite_msg, pjsk_type),
        compose_res_list_image(profile, private, mysekai_info, False, pmsg, pjsk_type),
        compose_map_image(profile, private, mysekai_info, False, pjsk_type),
    )
    render_done = time.perf_counter()
    payloads = tuple(_jpeg_bytes(img) for img in imgs)
    _MSR_PUSH_IMAGE_CACHE[key] = payloads
    _MSR_PUSH_IMAGE_CACHE.move_to_end(key)
    while len(_MSR_PUSH_IMAGE_CACHE) > MSR_PUSH_IMAGE_CACHE_LIMIT:
        _MSR_PUSH_IMAGE_CACHE.popitem(last=False)
    segments = tuple(_jpeg_msg(payload) for payload in payloads)
    logger.info(
        f"自动推送 {server.upper()} MSR 阶段耗时 uid={uid}: "
        f"数据={data_done - started:.2f}s, 绘图={render_done - data_done:.2f}s, "
        f"编码={time.perf_counter() - render_done:.2f}s, "
        f"缓存字节={sum(map(len, payloads))}"
    )
    return segments


async def _compose_msr_push_message(
    qq_id: str,
    uid: str,
    pjsk_type: int,
    server: str,
    upload_time_hint: Optional[int] = None,
    shared_tasks: Optional[dict[tuple[str, int, bool, int], asyncio.Task]] = None,
) -> Message:
    private = False
    try:
        bound_uid, is_private = await get_bound_uid(int(qq_id), pjsk_type)
        if str(bound_uid) == str(uid):
            private = bool(is_private)
    except Exception:
        private = False

    key = (str(uid), pjsk_type, private, int(upload_time_hint or 0))
    if shared_tasks is not None:
        task = shared_tasks.get(key)
        if task is None:
            task = asyncio.create_task(
                _render_msr_push_assets(str(uid), pjsk_type, private, upload_time_hint)
            )
            shared_tasks[key] = task
        assets = await task
    else:
        assets = await _render_msr_push_assets(str(uid), pjsk_type, private, upload_time_hint)

    out = Message()
    out += MessageSegment.at(int(qq_id))
    out += f" 的 {server.upper()} MSR 数据已更新"
    for asset in assets:
        out += asset
    return out


@scheduler.scheduled_job("interval", minutes=MSR_PUSH_INTERVAL_MINUTES)
async def _msr_auto_push_job():
    try:
        for pjsk_type, server, cfg in _msr_supported_servers():
            subs = await get_all_msr_subscriptions(server)
            if not subs:
                continue

            uid_modes = sorted({(sub.uid, sub.mode or "latest") for sub in subs if sub.uid})
            if not uid_modes:
                continue

            if cfg.update_msr_sub_api_url:
                try:
                    await request_gameapi(cfg.update_msr_sub_api_url, method="PUT", json=uid_modes)
                except Exception as e:
                    logger.warning(f"更新 {server.upper()} MySekai 订阅信息失败: {e}")

            try:
                upload_times = await request_gameapi(
                    cfg.mysekai_upload_time_api_url,
                    method="POST",
                    json=uid_modes,
                )
            except Exception as e:
                logger.warning(f"获取 {server.upper()} MySekai 上传时间失败: {e}")
                continue

            upload_time_map: dict[tuple[str, str], int] = {}
            for uid_mode, ts in zip(uid_modes, upload_times or []):
                try:
                    upload_time_map[(str(uid_mode[0]), str(uid_mode[1]))] = int(ts)
                except Exception:
                    continue

            now = datetime.now()
            last_refresh_time = get_last_refresh_time(pjsk_type, now)
            need_push: set[tuple[str, str]] = set()
            for uid_mode, ts in upload_time_map.items():
                update_time = datetime.fromtimestamp(ts)
                if update_time > last_refresh_time and now - update_time < timedelta(minutes=MSR_PUSH_RECENT_MINUTES):
                    need_push.add(uid_mode)

            if not need_push:
                continue

            tasks = []
            for sub in subs:
                uid_mode = (str(sub.uid), str(sub.mode or "latest"))
                if uid_mode not in need_push:
                    continue
                if sub.last_push_time:
                    last_push_time = datetime.fromtimestamp(sub.last_push_time)
                    if last_push_time >= last_refresh_time:
                        continue
                await update_msr_last_push(sub.id, int(now.timestamp()))
                tasks.append(sub)

            if not tasks:
                continue

            bot = get_bot()
            sem = asyncio.Semaphore(MSR_PUSH_CONCURRENCY)
            shared_tasks: dict[tuple[str, int, bool, int], asyncio.Task] = {}

            async def push_one(sub):
                async with sem:
                    try:
                        logger.info(f"自动推送 {server.upper()} MSR: group={sub.group_id} qq={sub.qq_id} uid={sub.uid}")
                        uid_mode = (str(sub.uid), str(sub.mode or "latest"))
                        msg = await _compose_msr_push_message(
                            sub.qq_id,
                            sub.uid,
                            pjsk_type,
                            server,
                            upload_time_map.get(uid_mode),
                            shared_tasks,
                        )
                        await bot.send_group_msg(group_id=int(sub.group_id), message=msg)
                    except Exception as e:
                        logger.warning(f"自动推送 {server.upper()} MSR 失败 group={sub.group_id} qq={sub.qq_id}: {e}", exc_info=True)
                        try:
                            fail_msg = Message([
                                MessageSegment.at(int(sub.qq_id)),
                                MessageSegment.text(f" 的 {server.upper()} MSR 自动推送失败：{e}"),
                            ])
                            await bot.send_group_msg(group_id=int(sub.group_id), message=fail_msg)
                        except Exception:
                            pass

            await asyncio.gather(*(push_one(sub) for sub in tasks))
    except Exception as e:
        logger.error(f"MSR 自动推送任务异常: {e}", exc_info=True)

