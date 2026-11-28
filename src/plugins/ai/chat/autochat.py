from __future__ import annotations

import asyncio
import json
import os
import random
import re
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

from nonebot import get_bot, get_driver
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, Message, MessageEvent, MessageSegment

from config.path_config import CONFIG_PATH, DATA_PATH, PROJECT_ROOT
from plugins.ai.llm import ChatSession, ChatSessionResponse
from plugins.ai.llm.config import Config
from plugins.group.record import before_record_hook
from plugins.group.record.sql import query_recent_msg
from services.log import logger

from .state import is_autochat_enabled, is_chat_enabled

config = Config("chat.autochat")
config.path = Path(os.getenv("AUTOCHAT_CONFIG_PATH", CONFIG_PATH / "chat/autochat.yaml"))
if not config.path.is_file():
    config.path = PROJECT_ROOT / "example_config/chat/autochat.yaml"

AUTOCHAT_DATA_PATH = DATA_PATH / "chat/autochat"
GROUP_IDLE_TIMEOUT = 3 * 60 * 60


def truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit] + "..."


_REPLY_MARKER = re.compile(r"\[CQ:[^\]]*\]|\[@([0-9]+)\]|\[reply=([0-9]+)\]")


def _reply_segments(text: str) -> Message:
    message = Message()
    start = 0
    for marker in _REPLY_MARKER.finditer(text):
        qq, reply_id = marker.groups()
        if qq is None and reply_id is None:
            continue
        if marker.start() > start:
            message.append(MessageSegment.text(text[start:marker.start()]))
        if qq is not None:
            message.append(MessageSegment.at(qq))
        else:
            message.append(MessageSegment.reply(reply_id))
        start = marker.end()
    if start < len(text):
        message.append(MessageSegment.text(text[start:]))
    return message


def _bounded_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        return max(minimum, min(maximum, int(value)))
    except (TypeError, ValueError):
        return default


def _parse_reply(resp: ChatSessionResponse) -> str:
    text = resp.result.strip()
    fence = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", text, re.IGNORECASE | re.DOTALL)
    if fence:
        text = fence.group(1)
    decoder = json.JSONDecoder()
    for marker in re.finditer(r"[\[{]", text):
        try:
            data, _ = decoder.raw_decode(text, marker.start())
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict) and "reply" in data and "user_updates" in data:
            return json.dumps(data, ensure_ascii=False)
    raise ValueError("自动聊天回复缺少 reply 或 user_updates")


async def _query_llm(model, prompt: str, *, timeout, max_tokens, process_func=None):
    session = ChatSession()
    session.append_user_content(prompt, verbose=False)
    response = await session.get_response(
        model,
        timeout=_bounded_int(timeout, 300, 10, 900),
        max_tokens=_bounded_int(max_tokens, 2048, 1, 32768),
        process_func=process_func,
    )
    return response.result


class FileDB:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            try:
                self.data = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                self.data = {}
        else:
            self.data = {}

    def get(self, key: str, default=None):
        return self.data.get(key, default)

    def set(self, key: str, value):
        data = {**self.data, key: value}
        temp_path = None
        try:
            with NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=self.path.parent, delete=False
            ) as temp:
                temp_path = Path(temp.name)
                json.dump(data, temp, ensure_ascii=False, indent=2)
            os.replace(temp_path, self.path)
        finally:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)
        self.data = data


file_db = FileDB(AUTOCHAT_DATA_PATH / "db.json")
_memory_dbs: dict[int, FileDB] = {}


def get_memory_db(group_id: int) -> FileDB:
    if group_id not in _memory_dbs:
        _memory_dbs[group_id] = FileDB(AUTOCHAT_DATA_PATH / f"memory_{group_id}.json")
    return _memory_dbs[group_id]


def update_user_memory(
    group_id: int,
    user_id: int,
    new_names=None,
    wrong_names=None,
    profile_update: str | None = None,
    event_update: str | None = None,
):
    db = get_memory_db(group_id)
    ums = dict(db.get("ums") or {})
    key = str(user_id)
    cur = dict(ums.get(key) or {})
    names = list(cur.get("names", []) or [])
    wrong = {name for name in wrong_names if isinstance(name, str)} if isinstance(wrong_names, list) else set()
    names = [n for n in names if n not in wrong]
    for name in new_names or []:
        name = str(name or "").strip()
        if name and name not in names and name not in wrong:
            names.append(name)
    max_names = int(config.get("chat.mem.um_max_names", 10) or 10)
    cur["names"] = names[-max_names:]
    if profile_update:
        cur["profile"] = str(profile_update).strip()
    if event_update:
        events = list(cur.get("recent_events", []) or [])
        events.append([time.time(), str(event_update).strip()])
        max_events = int(config.get("chat.mem.um_max_events", 5) or 5)
        cur["recent_events"] = events[-max_events:]
    ums[key] = cur
    db.set("ums", ums)


@dataclass
class ChatMessage:
    msg_id: int
    time: datetime
    user_id: int
    group_id: int
    nickname: str
    msg: list[dict]


def _to_message(msg: dict) -> ChatMessage:
    raw_msg = msg.get("msg", [])
    if isinstance(raw_msg, str):
        raw_msg = [{"type": "text", "data": {"text": raw_msg}}]
    return ChatMessage(
        msg_id=int(msg["msg_id"]),
        time=datetime.fromtimestamp(int(msg["time"])),
        user_id=int(msg["user_id"]),
        group_id=int(msg["group_id"]),
        nickname=msg.get("nickname", ""),
        msg=raw_msg,
    )


def get_group_persona(persona_cfg: Any, group_id: int) -> str:
    if not isinstance(persona_cfg, dict):
        return ""
    persona = persona_cfg.get(group_id)
    if persona:
        return str(persona)
    persona = persona_cfg.get(str(group_id))
    if persona:
        return str(persona)
    return str(persona_cfg.get("default", "") or "")


def get_plain_text(msg: ChatMessage) -> str:
    ret = ""
    for seg in msg.msg:
        if seg.get("type") == "text":
            ret += seg.get("data", {}).get("text", "")
    return ret.strip()


def format_segment(seg: dict) -> str:
    stype, data = seg.get("type"), seg.get("data", {})
    if stype == "text":
        return data.get("text", "")
    if stype == "at":
        return f"[@{data.get('qq')}]"
    if stype == "reply":
        return f"[reply={data.get('id')}]"
    if stype == "image":
        summary = data.get("summary")
        return f"[图片:{summary}]" if summary else "[图片]"
    if stype == "face":
        return "[表情]"
    if stype == "json":
        return "[分享消息]"
    if stype == "forward":
        return "[转发聊天记录]"
    return f"[{stype}]"


def format_msgs(msgs: list[ChatMessage]) -> str:
    lines = []
    for msg in msgs:
        body = "".join(format_segment(seg) for seg in msg.msg)
        lines.append(f"{msg.time.strftime('%m-%d %H:%M:%S')} [{msg.msg_id}] {msg.nickname}({msg.user_id}): {body}")
    return "\n".join(lines)


@dataclass
class GroupStatus:
    group_id: int
    willingness: float
    self_msg_ids: list[int]
    last_check_willing_time: float | None
    last_reply_time: float | None

    @staticmethod
    def load(group_id: int):
        data = file_db.get(f"status_{group_id}", {}) or {}
        return GroupStatus(
            group_id=group_id,
            willingness=float(data.get("willingness", 0.0)),
            self_msg_ids=list(data.get("self_msg_ids", [])),
            last_check_willing_time=data.get("last_check_willing_time"),
            last_reply_time=data.get("last_reply_time"),
        )

    def save(self):
        file_db.set(f"status_{self.group_id}", {
            "willingness": self.willingness,
            "self_msg_ids": self.self_msg_ids,
            "last_check_willing_time": self.last_check_willing_time,
            "last_reply_time": self.last_reply_time,
        })


async def chat(msg: ChatMessage, self_id: str):
    if not is_chat_enabled(msg.group_id) or not is_autochat_enabled(msg.group_id):
        return
    plain = get_plain_text(msg)
    if str(msg.user_id) == self_id or plain.startswith("/"):
        return

    status = GroupStatus.load(msg.group_id)
    if status.last_reply_time and msg.time.timestamp() <= status.last_reply_time:
        return

    delta = 0.0
    if status.last_check_willing_time:
        elapsed = time.time() - status.last_check_willing_time
        delta -= min(float(config.get("chat.willing.decrease_per_minute", 0.005)) * elapsed / 60.0, status.willingness)
    delta += float(config.get("chat.willing.increase_per_msg", 0.005))

    for seg in msg.msg:
        stype, data = seg.get("type"), seg.get("data", {})
        if stype == "at" and str(data.get("qq")) == self_id:
            delta += float(config.get("chat.willing.increase_per_at", 1.0))
        reply_id = str(data.get("id", ""))
        if stype == "reply" and re.fullmatch(r"-?[0-9]+", reply_id) and int(reply_id) in status.self_msg_ids:
            delta += float(config.get("chat.willing.increase_per_reply", 1.0))

    plain = plain.lower()
    for kw, value in (config.get("chat.willing.increase_keywords", {}) or {}).items():
        if kw.lower() in plain:
            delta += float(value)
    group_scale = config.get("chat.willing.group_scale", {}) or {}
    delta *= float(group_scale.get(msg.group_id, group_scale.get(str(msg.group_id), 1.0)))

    old = status.willingness
    status.willingness = min(float(config.get("chat.willing.limit", 1.5)), max(0.0, status.willingness + delta))
    status.last_check_willing_time = time.time()
    status.save()
    if random.random() > min(status.willingness, 1.0):
        logger.info(f"群 {msg.group_id} 意愿值 {old:.4f}->{status.willingness:.4f}，不回复")
        return

    logger.info(f"群 {msg.group_id} 意愿值 {old:.4f}->{status.willingness:.4f}，决定回复 {msg.msg_id}")
    await asyncio.sleep(float(config.get("chat.get_history_msg_delay_seconds", 5) or 5))

    if not is_chat_enabled(msg.group_id) or not is_autochat_enabled(msg.group_id):
        return
    rows = await query_recent_msg(msg.group_id, int(config.get("chat.history_msg_num", 20) or 20))
    recent = [_to_message(row) for row in rows]
    recent = [m for m in recent if not get_plain_text(m).startswith("/")]
    if not any(m.msg_id == msg.msg_id for m in recent):
        recent.append(msg)
    recent.sort(key=lambda message: message.time)
    recent_text = format_msgs(recent)

    summary_prompt = config.get("summary.prompt", "请总结以下聊天记录：\n{text}").format(text=recent_text)
    summary = await _query_llm(
        config.get("summary.model"),
        summary_prompt,
        timeout=config.get("summary.timeout", 60),
        max_tokens=config.get("summary.max_tokens", 1024),
    )

    persona_cfg = config.get("chat.prompt.persona", {}) or {}
    persona = get_group_persona(persona_cfg, msg.group_id)
    framework = config.get("chat.prompt.framework", "{recent_text}\n请回复。")
    bot = get_bot(self_id)
    member = await bot.get_group_member_info(group_id=msg.group_id, user_id=int(self_id), no_cache=True)
    full_prompt = framework.format(
        self_id=self_id,
        self_name=member.get("card") or member.get("nickname") or "bot",
        persona=persona,
        recent_text=f"以下是最近的聊天记录:\n```\n{recent_text}\n```\n摘要：{summary}",
        em_text="",
        sm_text="",
        um_text="",
    )

    response = await _query_llm(
        config.get("chat.llm.model"),
        full_prompt,
        timeout=config.get("chat.llm.timeout", 180),
        max_tokens=config.get("chat.llm.max_tokens", 2048),
        process_func=_parse_reply,
    )
    if not is_chat_enabled(msg.group_id) or not is_autochat_enabled(msg.group_id):
        return
    resp = json.loads(response)
    user_updates = resp.get("user_updates", [])
    if isinstance(user_updates, list):
        recent_user_ids = {m.user_id for m in recent}
        for update in user_updates:
            if not isinstance(update, dict):
                continue
            try:
                uid = int(update.get("user_id"))
            except (TypeError, ValueError, OverflowError):
                continue
            if uid not in recent_user_ids:
                continue
            names = []
            if update.get("new_name"):
                names.append(update.get("new_name"))
            for m in reversed(recent):
                if m.user_id == uid and m.nickname:
                    names.append(m.nickname)
                    break
            update_user_memory(
                msg.group_id,
                uid,
                new_names=names,
                wrong_names=update.get("wrong_names"),
                profile_update=update.get("profile"),
                event_update=update.get("new_event"),
            )

    for idx, key in enumerate(("reply", "reply2"), start=1):
        text = resp.get(key)
        if not isinstance(text, str) or not text.strip():
            continue
        if not is_chat_enabled(msg.group_id) or not is_autochat_enabled(msg.group_id):
            return
        text = truncate(text.strip(), int(config.get("chat.reply_max_length", 512) or 512))
        ret = await get_bot(self_id).send_group_msg(group_id=msg.group_id, message=_reply_segments(text))
        try:
            status.self_msg_ids.append(int(ret["message_id"]))
        except (KeyError, TypeError, ValueError):
            pass
        status.self_msg_ids = status.self_msg_ids[-100:]
        status.last_reply_time = time.time()
        decay = float(config.get("chat.willing.decay_after_send", 0.6))
        decrease = float(config.get("chat.willing.decrease_after_send", 0.5))
        status.willingness = max(0.0, status.willingness * decay - decrease)
        status.save()
        if idx == 1:
            await asyncio.sleep(float(config.get("chat.reply_interval_seconds", 3) or 3))


group_queues: dict[int, asyncio.Queue[tuple[ChatMessage, str]]] = {}
group_workers: dict[int, asyncio.Task[None]] = {}
_stopping = False


@before_record_hook
async def record_new_message(bot: Bot, event: MessageEvent):
    if _stopping or not isinstance(event, GroupMessageEvent):
        return
    group_id = event.group_id
    if not (is_chat_enabled(group_id) and is_autochat_enabled(group_id)):
        return
    msg = ChatMessage(
        msg_id=event.message_id,
        time=datetime.fromtimestamp(event.time),
        user_id=event.user_id,
        group_id=group_id,
        nickname=event.sender.nickname or "",
        msg=[{"type": seg.type, "data": dict(seg.data)} for seg in event.message],
    )
    queue = group_queues.get(group_id)
    if queue is None:
        queue = group_queues[group_id] = asyncio.Queue(maxsize=200)
        group_workers[group_id] = asyncio.create_task(group_message_worker(group_id, queue))
    if queue.full():
        try:
            queue.get_nowait()
            queue.task_done()
        except asyncio.QueueEmpty:
            pass
    try:
        queue.put_nowait((msg, bot.self_id))
    except asyncio.QueueFull:
        pass


async def group_message_worker(group_id: int, queue: asyncio.Queue[tuple[ChatMessage, str]]):
    try:
        while True:
            try:
                async with asyncio.timeout(GROUP_IDLE_TIMEOUT):
                    msg, self_id = await queue.get()
            except TimeoutError:
                if queue.empty():
                    return
                continue
            try:
                await chat(msg, self_id)
            except Exception:
                logger.exception(f"群 {group_id} 自动聊天失败")
            finally:
                queue.task_done()
    finally:
        group_queues.pop(group_id, None)
        group_workers.pop(group_id, None)


@get_driver().on_shutdown
async def _shutdown_workers():
    global _stopping
    _stopping = True
    tasks = tuple(group_workers.values())
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    group_workers.clear()
    group_queues.clear()
