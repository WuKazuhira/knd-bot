from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

import aiosqlite

from services.log import logger

from .._paths import DATABASE_PATH

MSR_SUBSCRIPTION_DB_PATH = os.getenv(
    "MSR_SUBSCRIPTION_DB_PATH",
    str(DATABASE_PATH / "mysekai_msr_subscription.db"),
)

_conn: Optional[aiosqlite.Connection] = None


async def get_msr_subscription_conn() -> aiosqlite.Connection:
    global _conn
    if _conn is None:
        os.makedirs(os.path.dirname(MSR_SUBSCRIPTION_DB_PATH), exist_ok=True)
        _conn = await aiosqlite.connect(MSR_SUBSCRIPTION_DB_PATH)
        _conn.row_factory = aiosqlite.Row
        await _conn.execute("PRAGMA journal_mode=WAL;")
        await _conn.execute("""
            CREATE TABLE IF NOT EXISTS msr_subscriptions (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                qq_id           TEXT    NOT NULL,
                group_id        TEXT    NOT NULL,
                server          TEXT    NOT NULL,
                uid             TEXT    NOT NULL,
                mode            TEXT    NOT NULL DEFAULT 'latest',
                last_push_time  INTEGER NOT NULL DEFAULT 0,
                created_at      INTEGER NOT NULL,
                UNIQUE(qq_id, server)
            )
        """)
        await _conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_msr_subscriptions_server
            ON msr_subscriptions (server)
        """)
        await _conn.commit()
        logger.info(f"连接 MSR 订阅数据库 {MSR_SUBSCRIPTION_DB_PATH} 成功")
    return _conn


@dataclass
class MsrSubscription:
    id: int
    qq_id: str
    group_id: str
    server: str
    uid: str
    mode: str
    last_push_time: int
    created_at: datetime

    @classmethod
    def from_row(cls, row: aiosqlite.Row) -> "MsrSubscription":
        return cls(
            id=int(row["id"]),
            qq_id=str(row["qq_id"]),
            group_id=str(row["group_id"]),
            server=str(row["server"]),
            uid=str(row["uid"]),
            mode=str(row["mode"] or "latest"),
            last_push_time=int(row["last_push_time"] or 0),
            created_at=datetime.fromtimestamp(int(row["created_at"])),
        )


async def add_msr_subscription(qq_id: str, group_id: str, server: str, uid: str, mode: str = "latest") -> bool:
    try:
        conn = await get_msr_subscription_conn()
        now = int(datetime.now().timestamp())
        await conn.execute("""
            INSERT INTO msr_subscriptions (qq_id, group_id, server, uid, mode, last_push_time, created_at)
            VALUES (?, ?, ?, ?, ?, 0, ?)
            ON CONFLICT(qq_id, server) DO UPDATE SET
                group_id = excluded.group_id,
                uid = excluded.uid,
                mode = excluded.mode
        """, (str(qq_id), str(group_id), server, str(uid), mode or "latest", now))
        await conn.commit()
        logger.info(f"添加/更新 MSR 订阅: QQ={qq_id}, 群={group_id}, 服务器={server}, UID={uid}, mode={mode}")
        return True
    except Exception as e:
        logger.error(f"添加 MSR 订阅失败: {e}", exc_info=True)
        return False


async def remove_msr_subscription(qq_id: str, server: str) -> bool:
    try:
        conn = await get_msr_subscription_conn()
        cursor = await conn.execute("""
            DELETE FROM msr_subscriptions
            WHERE qq_id = ? AND server = ?
        """, (str(qq_id), server))
        await conn.commit()
        return cursor.rowcount > 0
    except Exception as e:
        logger.error(f"取消 MSR 订阅失败: {e}", exc_info=True)
        return False


async def get_all_msr_subscriptions(server: Optional[str] = None) -> list[MsrSubscription]:
    try:
        conn = await get_msr_subscription_conn()
        if server:
            cursor = await conn.execute("SELECT * FROM msr_subscriptions WHERE server = ?", (server,))
        else:
            cursor = await conn.execute("SELECT * FROM msr_subscriptions")
        rows = await cursor.fetchall()
        return [MsrSubscription.from_row(row) for row in rows]
    except Exception as e:
        logger.error(f"获取 MSR 订阅失败: {e}", exc_info=True)
        return []


async def update_msr_last_push(subscription_id: int, timestamp: Optional[int] = None) -> bool:
    try:
        conn = await get_msr_subscription_conn()
        ts = int(timestamp or datetime.now().timestamp())
        await conn.execute("""
            UPDATE msr_subscriptions
            SET last_push_time = ?
            WHERE id = ?
        """, (ts, int(subscription_id)))
        await conn.commit()
        return True
    except Exception as e:
        logger.error(f"更新 MSR 推送时间失败: {e}", exc_info=True)
        return False
