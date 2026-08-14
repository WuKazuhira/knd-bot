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
from typing import Any

import yaml


CONFIG_PATH = Path(
    os.getenv("AUTOCHAT_CONFIG_PATH", "config/chat/autochat.yaml")
)
if not CONFIG_PATH.exists():
    CONFIG_PATH = Path("example_config/chat/autochat.yaml")
DB_PATH = Path("data/chat/autochat/db.json")


def _load_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _get_path(data: dict, key: str, default=None):
    cur: Any = data
    for part in key.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return default
        cur = cur[part]
    return cur


class Config:
    def __init__(self):
        self.data = _load_yaml(CONFIG_PATH)

    def get(self, key: str, default=None):
        return _get_path(self.data, key, default)


config = Config()


def log(level: str, msg: str):
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] [{level}] {msg}", flush=True)


def info(msg: str): log("INFO", msg)
def warning(msg: str): log("WARN", msg)
def error(msg: str): log("ERROR", msg)


def truncate(text: str, limit: int) -> str:
    text = str(text)
    return text if len(text) <= limit else text[:limit] + "..."


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
        self.data[key] = value
        self.path.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")


file_db = FileDB(DB_PATH)
_memory_dbs: dict[int, FileDB] = {}


def get_memory_db(group_id: int) -> FileDB:
    if group_id not in _memory_dbs:
        _memory_dbs[group_id] = FileDB(Path(f"data/chat/autochat/memory_{group_id}.json"))
    return _memory_dbs[group_id]


def update_user_memory(group_id: int, user_id: int, new_names=None, wrong_names=None, profile_update: str | None = None, event_update: str | None = None):
    db = get_memory_db(group_id)
    ums = db.get("ums", {}) or {}
    key = str(user_id)
    cur = ums.get(key, {}) or {}
    names = list(cur.get("names", []) or [])
    wrong = set(wrong_names or [])
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
class Message:
    msg_id: int
    time: datetime
    user_id: int
    group_id: int
    nickname: str
    msg: list[dict] | str


class RpcNotConnectedError(Exception):
    pass


class RpcSession:
    def __init__(self, host: str, port: int, token: str, reconnect_interval: int):
        self.host = host
        self.port = int(port)
        self.token = token
        self.reconnect_interval = reconnect_interval
        self.session = None
        self.ws_client = None

    def is_connected(self):
        return self.session is not None

    async def connect(self):
        import aiorpcx
        await self.disconnect()
        ws = aiorpcx.connect_ws(f"ws://{self.host}:{self.port}")
        self.session = await ws.__aenter__()
        self.session.sent_request_timeout = 10000
        self.ws_client = ws
        info(f"成功连接到 RPC 服务器 {self.host}:{self.port}")

    async def disconnect(self):
        ws_client = self.ws_client
        self.session = None
        self.ws_client = None
        if ws_client is not None:
            try:
                await ws_client.__aexit__(None, None, None)
            except Exception:
                pass

    async def call(self, method: str, *args, timeout: int | None = None):
        if not self.is_connected():
            raise RpcNotConnectedError("RPC 未连接")
        timeout = timeout or int(config.get("rpc.default_timeout", 5) or 5)
        try:
            return await asyncio.wait_for(
                self.session.send_request(method, [self.token] + list(args)),
                timeout,
            )
        except Exception:
            await self.disconnect()
            raise

    async def run(self):
        while True:
            if not self.is_connected():
                try:
                    await self.connect()
                except Exception as e:
                    warning(f"连接 RPC 失败: {e}，稍后重试")
            await asyncio.sleep(int(self.reconnect_interval or 5))


rpc_session = RpcSession(
    config.get("rpc.host", "127.0.0.1"),
    config.get("rpc.port", 8765),
    os.getenv("AUTOCHAT_RPC_TOKEN") or config.get("rpc.token", ""),
    config.get("rpc.reconnect_interval", 5),
)


async def rpc_get_self_info(group_id: int):
    return await rpc_session.call("get_self_info", group_id)


async def rpc_send_group_msg(group_id: int, message: str):
    return await rpc_session.call("send_group_msg", group_id, message)


async def rpc_query_llm(model: str | list[str], prompt: str, images: list[str] | None = None, options: dict | None = None):
    images = images or []
    options = options or {}
    return await rpc_session.call("query_llm", model, prompt, images, options, timeout=int(options.get("timeout", 300)) + 5)


async def rpc_get_group_history_msg(group_id: int, limit: int) -> list[Message]:
    msgs = await rpc_session.call("get_group_history_msg", group_id, limit, timeout=30)
    return [_to_message(m, group_id=group_id) for m in msgs]


async def rpc_get_new_msgs() -> list[Message]:
    msgs = await rpc_session.call("get_new_msgs", timeout=30)
    return [_to_message(m) for m in msgs]


def _to_message(msg: dict, group_id: int | None = None) -> Message:
    raw_msg = msg.get("msg", [])
    if isinstance(raw_msg, str):
        raw_msg = [{"type": "text", "data": {"text": raw_msg}}]
    return Message(
        msg_id=int(msg["msg_id"]),
        time=datetime.fromtimestamp(int(msg["time"])),
        user_id=int(msg["user_id"]),
        group_id=int(group_id or msg["group_id"]),
        nickname=msg.get("nickname", ""),
        msg=raw_msg,
    )


def get_plain_text(msg: Message) -> str:
    if isinstance(msg.msg, str):
        return msg.msg.strip()
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


async def format_msgs(msgs: list[Message]) -> str:
    lines = []
    for msg in sorted(msgs, key=lambda m: m.time):
        body = "".join(format_segment(seg) for seg in (msg.msg if isinstance(msg.msg, list) else []))
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


_self_infos: dict[int, dict] = {}


async def chat(msg: Message):
    if msg.group_id not in _self_infos:
        _self_infos[msg.group_id] = await rpc_get_self_info(msg.group_id)
    self_id = int(_self_infos[msg.group_id]["self_id"])
    self_name = _self_infos[msg.group_id].get("nickname") or "bot"
    if msg.user_id == self_id:
        return
    if get_plain_text(msg).startswith("/"):
        return

    status = GroupStatus.load(msg.group_id)
    if status.last_reply_time and msg.time.timestamp() <= status.last_reply_time:
        return

    delta = 0.0
    if status.last_check_willing_time:
        elapsed = time.time() - status.last_check_willing_time
        delta -= min(float(config.get("chat.willing.decrease_per_minute", 0.005)) * elapsed / 60.0, status.willingness)
    delta += float(config.get("chat.willing.increase_per_msg", 0.005))

    for seg in msg.msg if isinstance(msg.msg, list) else []:
        stype, data = seg.get("type"), seg.get("data", {})
        if stype == "at" and str(data.get("qq")) == str(self_id):
            delta += float(config.get("chat.willing.increase_per_at", 1.0))
        if stype == "reply" and int(data.get("id", 0) or 0) in status.self_msg_ids:
            delta += float(config.get("chat.willing.increase_per_reply", 1.0))

    plain = get_plain_text(msg).lower()
    for kw, value in (config.get("chat.willing.increase_keywords", {}) or {}).items():
        if kw.lower() in plain:
            delta += float(value)
    delta *= float((config.get("chat.willing.group_scale", {}) or {}).get(str(msg.group_id), 1.0))

    old = status.willingness
    status.willingness = min(float(config.get("chat.willing.limit", 1.5)), max(0.0, status.willingness + delta))
    status.last_check_willing_time = time.time()
    status.save()
    if random.random() > min(status.willingness, 1.0):
        info(f"群 {msg.group_id} 意愿值 {old:.4f}->{status.willingness:.4f}，不回复")
        return

    info(f"群 {msg.group_id} 意愿值 {old:.4f}->{status.willingness:.4f}，决定回复 {msg.msg_id}")
    await asyncio.sleep(float(config.get("chat.get_history_msg_delay_seconds", 5) or 5))

    recent = await rpc_get_group_history_msg(msg.group_id, int(config.get("chat.history_msg_num", 20) or 20))
    recent = [m for m in recent if not get_plain_text(m).startswith("/")]
    if not any(m.msg_id == msg.msg_id for m in recent):
        recent.append(msg)
    recent_text = await format_msgs(recent)

    summary_prompt = config.get("summary.prompt", "请总结以下聊天记录：\n{text}").format(text=recent_text)
    summary = await rpc_query_llm(
        config.get("summary.model"),
        summary_prompt,
        [],
        {"timeout": config.get("summary.timeout", 60), "max_tokens": config.get("summary.max_tokens", 1024)},
    )

    persona_cfg = config.get("chat.prompt.persona", {}) or {}
    persona = persona_cfg.get(str(msg.group_id)) or persona_cfg.get("default", "")
    framework = config.get("chat.prompt.framework", "{recent_text}\n请回复。")
    full_prompt = framework.format(
        self_id=self_id,
        self_name=self_name,
        persona=persona,
        recent_text=f"以下是最近的聊天记录:\n```\n{recent_text}\n```\n摘要：{summary}",
        em_text="",
        sm_text="",
        um_text="",
    )

    resp = await rpc_query_llm(
        config.get("chat.llm.model"),
        full_prompt,
        [],
        {
            "timeout": config.get("chat.llm.timeout", 180),
            "max_tokens": config.get("chat.llm.max_tokens", 2048),
            "json_reply": True,
            "json_key_restraints": [{"key": "reply", "type": "str"}, {"key": "user_updates", "type": "list"}],
        },
    )
    if isinstance(resp, str):
        try:
            resp = json.loads(resp)
        except Exception:
            resp = {"reply": resp}
    user_updates = resp.get("user_updates", []) if isinstance(resp, dict) else []
    if isinstance(user_updates, list):
        recent_user_ids = {m.user_id for m in recent}
        for update in user_updates:
            if not isinstance(update, dict):
                continue
            try:
                uid = int(update.get("user_id"))
            except Exception:
                continue
            if uid not in recent_user_ids and uid != msg.user_id:
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
        text = (resp.get(key) or "").strip()
        if not text:
            continue
        text = re.sub(r"\[@(\d+)\]", r"[CQ:at,qq=\1]", text)
        text = re.sub(r"\[reply=(\d+)\]", r"[CQ:reply,id=\1]", text)
        text = truncate(text, int(config.get("chat.reply_max_length", 512) or 512))
        ret = await rpc_send_group_msg(msg.group_id, text)
        try:
            status.self_msg_ids.append(int(ret["message_id"]))
        except Exception:
            pass
        status.self_msg_ids = status.self_msg_ids[-100:]
        status.last_reply_time = time.time()
        status.willingness = max(0.0, status.willingness * float(config.get("chat.willing.decay_after_send", 0.6)) - float(config.get("chat.willing.decrease_after_send", 0.5)))
        status.save()
        if idx == 1:
            await asyncio.sleep(float(config.get("chat.reply_interval_seconds", 3) or 3))


group_queues: dict[int, asyncio.Queue] = {}


async def group_message_worker(group_id: int, queue: asyncio.Queue):
    while True:
        try:
            msg = await asyncio.wait_for(queue.get(), timeout=3 * 60 * 60)
            try:
                await chat(msg)
            except Exception as e:
                error(f"群 {group_id} 消息处理异常: {e}")
            finally:
                queue.task_done()
        except asyncio.TimeoutError:
            group_queues.pop(group_id, None)
            info(f"群 {group_id} 闲置超时，worker 退出")
            break


async def main():
    asyncio.create_task(rpc_session.run())
    await asyncio.sleep(1)
    info("开始监听 autochat 新消息")
    while True:
        await asyncio.sleep(5)
        try:
            msgs = await rpc_get_new_msgs()
        except Exception as e:
            warning(f"获取新消息失败: {e}")
            continue
        for msg in msgs:
            queue = group_queues.get(msg.group_id)
            if queue is None:
                queue = asyncio.Queue()
                group_queues[msg.group_id] = queue
                asyncio.create_task(group_message_worker(msg.group_id, queue))
            queue.put_nowait(msg)


if __name__ == "__main__":
    asyncio.run(main())
