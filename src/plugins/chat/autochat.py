from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from nonebot import get_bot
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, Message, MessageEvent
from services.log import logger

from plugins.record import before_record_hook
from plugins.record.sql import query_recent_msg
from plugins.llm import ChatSession, ChatSessionResponse, download_image_to_b64, get_text_embedding
from .rpc import rpc_method, start_rpc_service
from .state import enabled_autochat_groups, is_autochat_enabled, is_chat_enabled
from plugins.llm.config import Config


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
    timeout = int(options.get("timeout", 300))
    max_tokens = int(options.get("max_tokens", 2048))
    json_reply = bool(options.get("json_reply", False))
    json_key_restraints = options.get("json_key_restraints", []) or []
    imgs = []
    for img in images or []:
        imgs.append(await download_image_to_b64(img) if isinstance(img, str) and img.startswith("http") else img)
    session = ChatSession()
    session.append_user_content(text, imgs, verbose=False)

    def process(resp: ChatSessionResponse):
        if not json_reply:
            return resp.result
        raw = resp.result
        start_idx, end_idx = raw.find("{"), raw.rfind("}")
        if start_idx < 0 or end_idx < 0:
            raise Exception("解析回复为json失败")
        data = json.loads(raw[start_idx:end_idx + 1])
        for restraint in json_key_restraints:
            value: Any = data
            for key in restraint.get("key", "").split("."):
                if key not in value:
                    raise Exception(f"回复的json缺少字段: {restraint.get('key')}")
                value = value[key]
        return data

    resp = await session.get_response(model, process_func=process, timeout=timeout, max_tokens=max_tokens)
    return resp.result


@rpc_method(RPC_SERVICE, "query_embedding")
async def handle_query_embedding(cid: str, texts: list[str], model_name: str):
    return await get_text_embedding(texts, model_name)


@rpc_method(RPC_SERVICE, "get_new_msgs")
async def handle_get_new_msgs(cid: str):
    msgs = message_pool.get(cid, [])
    message_pool[cid] = []
    return msgs
