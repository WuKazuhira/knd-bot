from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any

from nonebot import get_bot
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, Message, MessageEvent

from plugins.llm import ChatSession, ChatSessionResponse, download_image_to_b64, get_text_embedding
from plugins.llm.config import Config
from plugins.record import before_record_hook
from plugins.record.sql import query_recent_msg
from services.log import logger

from .rpc import rpc_method, start_rpc_service
from .state import enabled_autochat_groups, is_autochat_enabled, is_chat_enabled

config = Config("chat.autochat")
RPC_SERVICE = "autochat"
message_pool: dict[str, list[dict]] = {}


def _event_msg_segments(event: MessageEvent) -> list[dict]:
    return [{"type": seg.type, "data": dict(seg.data)} for seg in event.message]


@before_record_hook
async def record_new_message(bot: Bot, event: MessageEvent):
    if not isinstance(event, GroupMessageEvent):
        return
    if not is_chat_enabled(event.group_id) or not is_autochat_enabled(event.group_id):
        return
    sender = getattr(event, "sender", None)
    msg = {
        "msg_id": event.message_id,
        "time": event.time,
        "user_id": event.user_id,
        "group_id": event.group_id,
        "nickname": getattr(sender, "nickname", "") if sender else "",
        "msg": _event_msg_segments(event),
    }
    for cid in list(message_pool):
        message_pool[cid].append(msg)


def _on_connect(session):
    message_pool[session.id] = []


def _on_disconnect(session):
    message_pool.pop(session.id, None)


start_rpc_service(
    host=config.get("rpc.host", "127.0.0.1"),
    port=int(config.get("rpc.port", 8765) or 8765),
    token=config.get("rpc.token", ""),
    name=RPC_SERVICE,
    on_connect=_on_connect,
    on_disconnect=_on_disconnect,
)


def _bounded_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        return max(minimum, min(maximum, int(value)))
    except (TypeError, ValueError):
        return default


def _parse_json_reply(raw: str) -> Any:
    raw = (raw or "").strip()
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", raw, re.IGNORECASE | re.DOTALL)
    if fence:
        raw = fence.group(1).strip()
    decoder = json.JSONDecoder()
    for match in re.finditer(r"[\[{]", raw):
        try:
            value, _ = decoder.raw_decode(raw[match.start():])
            return value
        except json.JSONDecodeError:
            continue
    raise ValueError("解析回复为 json 失败")


@rpc_method(RPC_SERVICE, "get_self_info")
async def handle_get_self_info(cid: str, group_id: int):
    bot = get_bot()
    info = await bot.get_group_member_info(group_id=int(group_id), user_id=int(bot.self_id), no_cache=True)
    return {"self_id": int(bot.self_id), "nickname": info.get("card") or info.get("nickname")}


@rpc_method(RPC_SERVICE, "get_group_list")
async def handle_get_group_list(cid: str):
    bot = get_bot()
    groups = await bot.get_group_list()
    enabled = set(enabled_autochat_groups())
    return [g for g in groups if int(g.get("group_id")) in enabled]


@rpc_method(RPC_SERVICE, "send_group_msg")
async def handle_send_group_msg(cid: str, group_id: int, message: list[dict] | str):
    if not is_chat_enabled(group_id) or not is_autochat_enabled(group_id):
        logger.warning(f"自动聊天取消发送消息到未启用群组 {group_id}")
        return None
    bot = get_bot()
    msg = Message(message) if isinstance(message, str) else Message(message)
    return await bot.send_group_msg(group_id=int(group_id), message=msg)


@rpc_method(RPC_SERVICE, "get_group_history_msg")
async def handle_get_group_msg(cid: str, group_id: int, limit: int):
    msgs = await query_recent_msg(group_id, limit)
    ret = []
    for msg in msgs:
        if isinstance(msg.get("time"), datetime):
            msg["time"] = int(msg["time"].timestamp())
        ret.append(msg)
    return ret


@rpc_method(RPC_SERVICE, "query_llm")
async def handle_query_llm(cid: str, model: str | list[str], text: str, images: list[str], options: dict):
    options = options if isinstance(options, dict) else {}
    timeout = _bounded_int(options.get("timeout", 300), 300, 10, 900)
    max_tokens = _bounded_int(options.get("max_tokens", 2048), 2048, 1, 32768)
    json_reply = bool(options.get("json_reply", False))
    json_key_restraints = options.get("json_key_restraints", []) or []
    imgs = []
    for img in images or []:
        if not isinstance(img, str) or not img.startswith("http"):
            imgs.append(img)
            continue
        try:
            imgs.append(await download_image_to_b64(img))
        except Exception as exc:
            logger.warning(f"autochat 图片下载失败，将跳过该图片: {type(exc).__name__}: {exc}")
    session = ChatSession()
    session.append_user_content(text, imgs, verbose=False)

    def process(resp: ChatSessionResponse):
        if not json_reply:
            return resp.result
        data = _parse_json_reply(resp.result)
        for restraint in json_key_restraints:
            if not isinstance(restraint, dict):
                continue
            value: Any = data
            key_path = str(restraint.get("key", ""))
            for key in [part for part in key_path.split(".") if part]:
                if not isinstance(value, dict) or key not in value:
                    raise Exception(f"回复的json缺少字段: {key_path}")
                value = value[key]
        # RPC 客户端仍按字符串协议接收；这里返回规范化 JSON，避免 fenced JSON
        # 或前后说明文字继续污染下游解析。
        return json.dumps(data, ensure_ascii=False, separators=(",", ":"))

    # 部分供应方（尤其是 DeepSeek-R1 的兼容接口）不支持 response_format。
    # JSON 回复仍由下方 process 函数解析，因此只有调用方明确要求时才发送该参数。
    provider_extra_body = (
        {"sf": {"response_format": {"type": "json_object"}}}
        if json_reply and options.get("json_response_format", False)
        else None
    )
    resp = await session.get_response(
        model,
        process_func=process,
        timeout=timeout,
        max_tokens=max_tokens,
        provider_extra_body=provider_extra_body,
    )
    return resp.result


@rpc_method(RPC_SERVICE, "query_embedding")
async def handle_query_embedding(cid: str, texts: list[str], model_name: str):
    return await get_text_embedding(texts, model_name)


@rpc_method(RPC_SERVICE, "get_new_msgs")
async def handle_get_new_msgs(cid: str):
    msgs = message_pool.get(cid, [])
    message_pool[cid] = []
    return msgs
