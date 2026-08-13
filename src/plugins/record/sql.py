from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import aiosqlite


DB_PATH = Path("data/record/messages.db")
_conn: aiosqlite.Connection | None = None


async def get_conn() -> aiosqlite.Connection:
    global _conn
    if _conn is None:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        _conn = await aiosqlite.connect(DB_PATH)
        _conn.row_factory = aiosqlite.Row
        await _conn.execute("PRAGMA journal_mode=WAL;")
        await _conn.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                msg_id   INTEGER PRIMARY KEY,
                time     INTEGER NOT NULL,
                user_id  INTEGER NOT NULL,
                group_id INTEGER NOT NULL,
                nickname TEXT,
                msg      TEXT NOT NULL
            )
        """)
        await _conn.execute("CREATE INDEX IF NOT EXISTS idx_messages_group_time ON messages(group_id, time)")
        await _conn.commit()
    return _conn


async def insert_msg(msg_id: int, time: int, user_id: int, group_id: int, nickname: str, msg: Any):
    conn = await get_conn()
    msg_text = msg if isinstance(msg, str) else json.dumps(msg, ensure_ascii=False)
    await conn.execute("""
        INSERT OR REPLACE INTO messages (msg_id, time, user_id, group_id, nickname, msg)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (int(msg_id), int(time), int(user_id), int(group_id), nickname or "", msg_text))
    await conn.commit()


async def query_recent_msg(group_id: int, limit: int):
    conn = await get_conn()
    cursor = await conn.execute("""
        SELECT msg_id, time, user_id, group_id, nickname, msg
        FROM messages
        WHERE group_id = ?
        ORDER BY time DESC, msg_id DESC
        LIMIT ?
    """, (int(group_id), int(limit)))
    rows = await cursor.fetchall()
    ret = []
    for row in reversed(rows):
        raw_msg = row["msg"]
        try:
            msg = json.loads(raw_msg)
        except Exception:
            msg = raw_msg
        ret.append({
            "msg_id": row["msg_id"],
            "time": row["time"],
            "user_id": row["user_id"],
            "group_id": row["group_id"],
            "nickname": row["nickname"],
            "msg": msg,
        })
    return ret
