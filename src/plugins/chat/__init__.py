from __future__ import annotations

from datetime import datetime, timedelta
import os
from pathlib import Path
from typing import Optional

from nonebot import get_driver, on_command, on_message
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, Message, MessageEvent, MessageSegment
from nonebot.exception import FinishedException
from nonebot.internal.matcher import Matcher
from nonebot.params import CommandArg
from nonebot.permission import SUPERUSER
from config.path_config import CONFIG_PATH, DATA_PATH
from services.log import logger
from utils.imageutils import text2image, pic2b64
from utils.message_builder import image

from plugins.llm import ChatSession, api_provider_mgr, get_model_preset, translate_text
from plugins.llm.config import Config
from .state import (
    clear_model, get_model, is_chat_enabled, set_autochat_enabled,
    set_chat_enabled, set_model,
)
from .autochat import *  # noqa: F401,F403 启动 RPC 服务并注册方法


__plugin_name__ = "大预言模型/Chat"
__plugin_type__ = "AI功能"
__plugin_version__ = 0.1
__plugin_usage__ = """
usage：
    /chat <内容>                  和大预言模型对话
    @bot <内容>                   群聊中 @ 机器人对话（需 /chat on）
    /cleanchat                    清空当前会话
    /chat_model                   查看当前模型
    /chat_model <模型名>          切换当前模型
    /allmodel                     查看可用模型
    /translate <文本> / /翻译 <文本>  翻译文本
    /um [@用户]                   查询 autochat 用户画像
    /chat on / /chat off          群聊启用/关闭 @ 触发聊天
    /autochat on / /autochat off  群聊启用/关闭 autochat RPC
""".strip()
__plugin_settings__ = {"default_status": False, "cmd": ["chat", "大预言", "llm", "translate", "翻译"]}
__plugin_cd_limit__ = {"cd": 10, "count_limit": 3, "rst": "别急，等[cd]秒后再问！", "limit_type": "user"}
__plugin_block_limit__ = {"rst": "上一条大预言还没结束，请稍等。"}


config = Config("chat.chat")
sessions: dict[str, ChatSession] = {}
SESSION_EXPIRE_TIME = timedelta(hours=12)
SYSTEM_PROMPT_PATH = Path(
    os.getenv("CHAT_SYSTEM_PROMPT_PATH", CONFIG_PATH / "chat/system_prompt.txt")
)
if not SYSTEM_PROMPT_PATH.exists():
    SYSTEM_PROMPT_PATH = Path("example_config/chat/system_prompt.txt")


def _is_group(event: MessageEvent) -> bool:
    return isinstance(event, GroupMessageEvent)


def _session_key(event: MessageEvent) -> str:
    return f"group:{event.group_id}" if _is_group(event) else f"private:{event.user_id}"


def _model_target(event: MessageEvent) -> tuple[bool, int]:
    return (True, event.group_id) if _is_group(event) else (False, event.user_id)


def _system_prompt() -> str:
    if SYSTEM_PROMPT_PATH.exists():
        return SYSTEM_PROMPT_PATH.read_text(encoding="utf-8")
    return "你是一个友好的聊天助手。"


def _is_superuser(event: MessageEvent) -> bool:
    return str(event.user_id) in {str(x) for x in get_driver().config.superusers}


def _get_session(event: MessageEvent) -> ChatSession:
    key = _session_key(event)
    sess = sessions.get(key)
    if sess is None or datetime.now() - sess.update_time > SESSION_EXPIRE_TIME:
        sess = ChatSession(_system_prompt())
        sessions[key] = sess
    return sess


def _get_event_model(event: MessageEvent, mode: str = "text"):
    is_group, target_id = _model_target(event)
    default = get_model_preset("chat.group" if is_group else "chat.private") or get_model_preset("basic_chat_mm")
    return get_model(target_id, is_group, mode, default)


async def _answer(matcher: Matcher, event: MessageEvent, text: str, imgs: Optional[list[str]] = None):
    text = text.strip()
    if not text and not imgs:
        await matcher.finish("请输入要问的内容")
    sess = _get_session(event)
    mode = "mm" if imgs else "text"
    sess.append_user_content(text, imgs=imgs or [])
    limit = int(config.get("session_len_limit", 40) or 40)
    sess.limit_length(limit)
    model = _get_event_model(event, mode)
    resp = await sess.get_response(model)
    msg = Message()
    if config.get("output_reasoning_content", False) and resp.reasoning:
        msg += MessageSegment.text(f"【思考】\n{resp.reasoning}\n\n")
    msg += MessageSegment.text(resp.result or "（空回复）")
    for img in resp.images:
        import io
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        msg += MessageSegment.image(buf.getvalue())
    await matcher.finish(msg)


chat_cmd = on_command("chat", aliases={"大预言", "llm"}, priority=5, block=True)
clean_chat = on_command("cleanchat", aliases={"clean_chat", "cleanmode", "clean_mode"}, priority=5, block=True)
chat_model = on_command("chat_model", aliases={"chat model", "chatmodel"}, priority=5, block=True)
all_model = on_command("allmodel", aliases={"all_model", "all model"}, priority=5, block=True)
translate_cmd = on_command("translate", aliases={"翻译"}, priority=5, block=True)
autochat_um = on_command("um", aliases={"autochat um", "autochat usermemory", "usermemory"}, priority=5, block=True)
autochat_cmd = on_command("autochat", priority=5, block=True)
chat_enable = on_command("chat on", aliases={"chat_enable", "开启chat", "启用chat"}, permission=SUPERUSER, priority=5, block=True)
chat_disable = on_command("chat off", aliases={"chat_disable", "关闭chat", "禁用chat"}, permission=SUPERUSER, priority=5, block=True)
autochat_enable = on_command("autochat on", aliases={"autochat_enable", "开启autochat"}, permission=SUPERUSER, priority=5, block=True)
autochat_disable = on_command("autochat off", aliases={"autochat_disable", "关闭autochat"}, permission=SUPERUSER, priority=5, block=True)


@chat_cmd.handle()
async def _(matcher: Matcher, event: MessageEvent, arg: Message = CommandArg()):
    try:
        text = arg.extract_plain_text().strip()
        if text in {"on", "off"}:
            if not isinstance(event, GroupMessageEvent):
                await matcher.finish("只能在群聊中切换 chat")
            if not _is_superuser(event):
                await matcher.finish("只有超级用户可以切换 chat")
            set_chat_enabled(event.group_id, text == "on")
            await matcher.finish("已启用本群 chat @ 触发" if text == "on" else "已关闭本群 chat @ 触发")
        await _answer(matcher, event, text)
    except FinishedException:
        raise
    except Exception as e:
        logger.exception(f"chat 失败: {e}")
        await matcher.finish(f"大预言失败：{e}")


@clean_chat.handle()
async def _(matcher: Matcher, event: MessageEvent):
    sessions.pop(_session_key(event), None)
    await matcher.finish("已清空当前会话")


@autochat_cmd.handle()
async def _(matcher: Matcher, event: MessageEvent, arg: Message = CommandArg()):
    text = arg.extract_plain_text().strip().lower()
    if text not in {"on", "off"}:
        await matcher.finish("用法：/autochat on 或 /autochat off")
    if not isinstance(event, GroupMessageEvent):
        await matcher.finish("只能在群聊中切换 autochat")
    if not _is_superuser(event):
        await matcher.finish("只有超级用户可以切换 autochat")
    set_autochat_enabled(event.group_id, text == "on")
    if text == "on":
        set_chat_enabled(event.group_id, True)
    await matcher.finish("已启用本群 autochat RPC" if text == "on" else "已关闭本群 autochat RPC")


@chat_model.handle()
async def _(matcher: Matcher, event: MessageEvent, arg: Message = CommandArg()):
    text = arg.extract_plain_text().strip()
    is_group, target_id = _model_target(event)
    if not text:
        await matcher.finish(f"当前模型：{_get_event_model(event)}")
    try:
        api_provider_mgr.find_model(text)
    except Exception as e:
        await matcher.finish(f"模型不存在：{e}")
    set_model(target_id, is_group, "text", text)
    set_model(target_id, is_group, "mm", text)
    await matcher.finish(f"已切换模型为：{text}")


@all_model.handle()
async def _(matcher: Matcher):
    models = api_provider_mgr.all_models()
    lines = [m.get_full_name() for m in models]
    await matcher.finish("可用模型：\n" + "\n".join(lines[:200]))


@translate_cmd.handle()
async def _(matcher: Matcher, arg: Message = CommandArg()):
    text = arg.extract_plain_text().strip()
    if not text:
        await matcher.finish("请输入要翻译的文本")
    ret = await translate_text(text, cache=False, default=None)
    await matcher.finish(ret or "翻译失败")


@autochat_um.handle()
async def _(matcher: Matcher, bot: Bot, event: MessageEvent):
    if not isinstance(event, GroupMessageEvent):
        await matcher.finish("/um 只能在群聊中查询")
    qid = event.user_id
    for seg in event.message:
        if seg.type == "at" and str(seg.data.get("qq", "")).isdigit():
            qid = int(seg.data["qq"])
            break
    try:
        info = await bot.get_group_member_info(group_id=event.group_id, user_id=qid, no_cache=True)
        nickname = info.get("card") or info.get("nickname") or str(qid)
    except Exception:
        nickname = str(qid)
    mem_path = DATA_PATH / "chat/autochat" / f"memory_{event.group_id}.json"
    um = None
    if mem_path.exists():
        import json
        try:
            mem = json.loads(mem_path.read_text(encoding="utf-8"))
            um = (mem.get("ums") or {}).get(str(qid))
        except Exception:
            um = None
    if not um:
        await matcher.finish(f"对@{nickname}的记忆: 无")

    lines = [f"对@{nickname}的记忆"]
    if names := um.get("names"):
        lines.append("【曾用名】")
        lines.append("、".join(str(n) for n in names))
    if profile := um.get("profile"):
        lines.append("【用户画像】")
        lines.append(str(profile))
    if recent_events := um.get("recent_events"):
        lines.append("【近期事件】")
        for ts, event_text in recent_events:
            try:
                ts_text = datetime.fromtimestamp(float(ts)).strftime("%m-%d %H:%M")
            except Exception:
                ts_text = "未知时间"
            lines.append(f"[{ts_text}] {event_text}")
    text = "\n".join(lines).strip()
    img = text2image(text, bg_color="white", padding=(24, 24), max_width=900)
    await matcher.finish(image(b64=pic2b64(img)))


@chat_enable.handle()
async def _(matcher: Matcher, event: MessageEvent):
    if not isinstance(event, GroupMessageEvent):
        await matcher.finish("只能在群聊中启用")
    set_chat_enabled(event.group_id, True)
    await matcher.finish("已启用本群 chat @ 触发")


@chat_disable.handle()
async def _(matcher: Matcher, event: MessageEvent):
    if not isinstance(event, GroupMessageEvent):
        await matcher.finish("只能在群聊中关闭")
    set_chat_enabled(event.group_id, False)
    await matcher.finish("已关闭本群 chat @ 触发")


@autochat_enable.handle()
async def _(matcher: Matcher, event: MessageEvent):
    if not isinstance(event, GroupMessageEvent):
        await matcher.finish("只能在群聊中启用")
    set_chat_enabled(event.group_id, True)
    set_autochat_enabled(event.group_id, True)
    await matcher.finish("已启用本群 autochat RPC")


@autochat_disable.handle()
async def _(matcher: Matcher, event: MessageEvent):
    if not isinstance(event, GroupMessageEvent):
        await matcher.finish("只能在群聊中关闭")
    set_autochat_enabled(event.group_id, False)
    await matcher.finish("已关闭本群 autochat RPC")


async def _at_bot_chat_rule(bot: Bot, event: MessageEvent) -> bool:
    if not isinstance(event, GroupMessageEvent):
        return False
    if not is_chat_enabled(event.group_id):
        return False
    return any(seg.type == "at" and str(seg.data.get("qq")) == str(bot.self_id) for seg in event.message)


at_chat = on_message(rule=_at_bot_chat_rule, priority=60, block=False)


@at_chat.handle()
async def _(matcher: Matcher, event: MessageEvent):
    text = event.get_plaintext().strip()
    try:
        await _answer(matcher, event, text)
    except FinishedException:
        raise
    except Exception as e:
        logger.warning(f"at chat 失败: {e}")
        await matcher.finish(f"大预言失败：{e}")
