"""World Bloom 自定义边框命令（Python 回退运行模式）。"""

from __future__ import annotations

import asyncio

from nonebot import on_command
from nonebot.adapters.onebot.v11 import Message, MessageEvent
from nonebot.internal.matcher import Matcher
from nonebot.params import Command, CommandArg

from services.log import logger
from services.pjsk_draw import render
from utils.message_builder import image

from .._autoask import pjsk_update_manager
from .._config import SERVER_MAP, data_path
from .._event_utils import DEFAULT_CHARA_ALIAS_MAP
from .._models import PjskBind, UserProfile
from .._utils import get_chara_alias_map


__plugin_name__ = "World Bloom 自定义边框"
__plugin_type__ = "烧烤相关"
__plugin_usage__ = """World Bloom 自定义边框
把 WL 冰晶边框拼成完整长方形，中间嵌入角色立绘。

指令：wl边框 wl3 <颜色> <1或6个角色缩写> [卡牌ID] [花前/花后] [名=玩家名]
颜色：100 / tb（应援色）或 1000 / tk（类型色）
角色：填 1 个 → 六个部件同一角色；填 6 个 → 依次对应
      部件顺序 ① 顶部 ② 左上 ③ 右上 ④ 左下 ⑤ 右下 ⑥ 底部
卡牌：省略时用你绑定账号的队长卡，否则随机
玩家名：可用 名=xxx 自定义，省略则用角色名
生成卡含星级、随机专家等级、玩家名与该角色随机 3 个 WL 活动牌子

示例：wl边框 wl3 tk mnr
示例：wl边框 wl3 100 mnr hrk airi szk an emu 123 花后 名=小明
（国服 cnwl边框、台服 twwl边框）""".strip()
__plugin_settings__ = {"cmd": ["wl边框", "cnwl边框", "twwl边框"]}

FRAME_IDS = {"100": 20001, "tb": 20001, "1000": 20002, "tk": 20002}
FRAME_MASTERDATA = ("playerFrames.json", "playerFrameGroups.json", "playerFrameParts.json")


def parse_world_bloom_args(raw: str, alias_map: dict) -> dict | str:
    """校验 wl3/颜色/1或6个角色缩写，以及可选卡牌和特训状态。"""
    import shlex

    try:
        tokens = shlex.split(raw.strip())
    except ValueError:
        return "参数引号不匹配，请检查命令后重试"
    if not tokens:
        return "参数不足：请指定 wl3、边框颜色和 1 或 6 个角色缩写"
    if len(tokens) == 1 and tokens[0].casefold() in {"help", "帮助", "说明", "用法"}:
        return {"help": True}
    if len(tokens) < 3:
        return "参数不足：格式为 wl边框 wl3 100/tb <角色缩写> [卡牌ID] [花前/花后]"

    frame_type = tokens[0].casefold()
    if frame_type != "wl3":
        return "暂不支持该边框类型，目前仅支持 wl3"
    color = tokens[1].casefold()
    frame_id = FRAME_IDS.get(color)
    if frame_id is None:
        return "边框颜色仅支持 100/tb 或 1000/tk"

    if len(tokens) >= 8:
        role_tokens, optional_tokens = tokens[2:8], tokens[8:]
        if len(optional_tokens) > 3:
            return "角色缩写需要正好提供 1 个或 6 个，后面最多再写卡牌ID、花前/花后和玩家名"
    else:
        role_tokens, optional_tokens = tokens[2:3], tokens[3:]
        if len(tokens) > 3 and not tokens[3].isdigit() and tokens[3].casefold() in alias_map:
            return "角色缩写需要正好提供 1 个或 6 个"
        if len(optional_tokens) > 3:
            return "参数过多：最多提供 6 个角色缩写、1 个卡牌ID、1 个花前/花后和玩家名"

    character_ids = []
    for alias in role_tokens:
        key = alias.strip().casefold()
        char_id = alias_map.get(key, 0)
        if not char_id and key.isdigit() and 1 <= int(key) <= 26:
            char_id = int(key)
        try:
            char_id = int(char_id)
        except (TypeError, ValueError):
            char_id = 0
        if not 1 <= char_id <= 26:
            return f"无法识别角色缩写「{alias}」，请检查缩写后重试"
        character_ids.append(char_id)

    if len(character_ids) == 1:
        character_ids *= 6
    elif len(character_ids) != 6:
        return "角色缩写需要正好提供 1 个或 6 个"

    card_id = 0
    trained = None
    player_name = ""
    for token in optional_tokens:
        lowered = token.casefold()
        name_prefix = next((p for p in ("名字=", "名=", "name=", "昵称=", "id=") if lowered.startswith(p)), None)
        if name_prefix is not None:
            if player_name:
                return "玩家名只能指定一次"
            player_name = token[len(name_prefix):].strip()
            if not player_name:
                return "玩家名不能为空，示例：名=小明"
        elif lowered in {"花前", "normal"}:
            if trained is not None:
                return "花前/花后参数只能指定一次"
            trained = False
        elif lowered in {"花后", "after_training"}:
            if trained is not None:
                return "花前/花后参数只能指定一次"
            trained = True
        elif token.isdigit() and card_id == 0:
            card_id = int(token)
            if card_id <= 0:
                return "卡牌ID必须是大于 0 的数字"
        else:
            return f"无法识别参数「{token}」，卡牌ID后可选花前/花后，自定义玩家名用 名=xxx"

    return {
        "frame_id": frame_id,
        "character_ids": character_ids,
        "card_id": card_id,
        "trained": trained,
        "player_name": player_name,
    }


async def _ensure_frame_masterdata(pjsk_type: int) -> None:
    server = SERVER_MAP.get(pjsk_type, "jp")
    missing = [name for name in FRAME_MASTERDATA if not (data_path / server / name).is_file()]
    if not missing:
        return
    await asyncio.gather(
        *(pjsk_update_manager.update_server_game_data(name, pjsk_type, block=True) for name in missing)
    )
    still_missing = [name for name in missing if not (data_path / server / name).is_file()]
    if still_missing:
        raise FileNotFoundError("缺少边框主数据：" + ", ".join(still_missing))


async def _bound_leader_info(event: MessageEvent, pjsk_type: int) -> dict:
    """获取发令者绑定账号的队长卡与个人资料（卡ID/特训/等级/突破/玩家名/展示牌子）。

    查询失败返回空 dict，渲染器据此回退到随机卡牌与随机专家等级。
    """
    try:
        uid, _ = await PjskBind.get_user_bind(int(event.user_id), pjsk_type)
        if not uid:
            return {}
        profile = UserProfile()
        await profile.getprofile(str(uid), "profile", is_force_update=True, pjsk_type=pjsk_type)
        card_id = int(profile.userDecks[0] or 0) if profile.userDecks else 0
        if card_id <= 0:
            return {}
        honors = [h for h in (profile.userProfileHonors or []) if isinstance(h, dict)]
        return {
            "leader_card_id": card_id,
            "leader_trained": bool(profile.special_training[0]) if profile.special_training else False,
            "leader_level": int(profile.deck_card_levels[0]) if profile.deck_card_levels else 0,
            "leader_master_rank": int(profile.deck_master_ranks[0]) if profile.deck_master_ranks else 0,
            "profile_name": str(profile.name or ""),
            "honors": honors,
        }
    except Exception as exc:
        logger.debug(f"[wl边框] 获取绑定队长卡信息失败，改用随机卡牌: {exc}")
        return {}


wl_frame = on_command("wl边框", priority=5, block=True)
cn_wl_frame = on_command("cnwl边框", priority=5, block=True)
tw_wl_frame = on_command("twwl边框", priority=5, block=True)


@wl_frame.handle()
@cn_wl_frame.handle()
@tw_wl_frame.handle()
async def _(
    matcher: Matcher,
    event: MessageEvent,
    msg: Message = CommandArg(),
    cmd: tuple[str, ...] = Command(),
):
    command = cmd[0].casefold() if cmd else "wl边框"
    pjsk_type = 2 if command.startswith("cn") else (1 if command.startswith("tw") else 0)
    raw = msg.extract_plain_text().strip()
    aliases = {**DEFAULT_CHARA_ALIAS_MAP, **(get_chara_alias_map() or {})}
    parsed = parse_world_bloom_args(raw, aliases)
    if isinstance(parsed, str):
        await matcher.finish(parsed)

    # 帮助走 kndbot 原生 help 绘图（带专属背景与看板娘）。
    if parsed.get("help"):
        from basic_plugins.help.data_source import get_plugin_help

        res = await asyncio.to_thread(get_plugin_help, command)
        if res:
            await matcher.finish(image(b64=res))
        await matcher.finish(__plugin_usage__)

    try:
        await _ensure_frame_masterdata(pjsk_type)
        leader = {}
        if not parsed["card_id"]:
            leader = await _bound_leader_info(event, pjsk_type)
        payload = {
            "pjsk_type": pjsk_type,
            "frame_id": parsed["frame_id"],
            "character_ids": parsed["character_ids"],
            "card_id": parsed["card_id"],
            "trained": parsed["trained"],
            "player_name": parsed.get("player_name") or "",
            "leader_card_id": leader.get("leader_card_id", 0),
            "leader_trained": leader.get("leader_trained"),
            "leader_level": leader.get("leader_level", 0),
            "leader_master_rank": leader.get("leader_master_rank"),
            "profile_name": leader.get("profile_name", ""),
            "honors": leader.get("honors") or [],
        }
        picture = await render("world_bloom_frame", payload)
    except FileNotFoundError as exc:
        await matcher.finish(f"World Bloom 资源暂不可用：{exc}")
    except (ValueError, TypeError) as exc:
        await matcher.finish(f"World Bloom 参数无效：{exc}")
    except Exception as exc:
        logger.error(f"[wl边框] 生成失败: {exc}", exc_info=True)
        await matcher.finish("World Bloom 边框生成失败，资源下载或图片合成异常，请稍后重试")
    else:
        await matcher.finish(image(picture))
