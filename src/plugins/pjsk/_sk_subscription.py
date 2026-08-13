import asyncio
import os
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional, Sequence, Tuple

import aiosqlite

from services.log import logger

from ._paths import DATABASE_PATH

SUBSCRIPTION_DB_PATH = str(DATABASE_PATH / "sk_subscription.db")

_conn: Optional[aiosqlite.Connection] = None
_conn_init_lock = asyncio.Lock()


async def get_subscription_conn() -> aiosqlite.Connection:
    """并发安全地获取订阅数据库连接。"""
    global _conn
    if _conn is not None:
        return _conn

    async with _conn_init_lock:
        if _conn is not None:
            return _conn

        os.makedirs(os.path.dirname(SUBSCRIPTION_DB_PATH), exist_ok=True)
        conn = await aiosqlite.connect(SUBSCRIPTION_DB_PATH)
        conn.row_factory = aiosqlite.Row
        await conn.execute("PRAGMA journal_mode=WAL;")
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS subscriptions (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                qq_id       TEXT    NOT NULL,
                group_id    TEXT,
                server      TEXT    NOT NULL,
                event_id    INTEGER NOT NULL,
                uid         TEXT    NOT NULL,
                last_score  INTEGER NOT NULL DEFAULT 0,
                last_rank   INTEGER NOT NULL DEFAULT 0,
                last_check_time INTEGER,
                created_at  INTEGER NOT NULL,
                UNIQUE(qq_id, server, event_id)
            )
        """)

        cursor = await conn.execute("PRAGMA table_info(subscriptions)")
        existing_cols = {row[1] for row in await cursor.fetchall()}
        await cursor.close()
        if 'group_id' not in existing_cols:
            await conn.execute("ALTER TABLE subscriptions ADD COLUMN group_id TEXT")
            logger.info("已添加 group_id 列到订阅表")

        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_subscriptions_server_event
            ON subscriptions (server, event_id)
        """)
        await conn.commit()
        _conn = conn
        logger.info(f"连接订阅数据库 {SUBSCRIPTION_DB_PATH} 成功")
        return conn


async def close_subscription_connection() -> None:
    """关闭共享订阅数据库连接。"""
    global _conn
    async with _conn_init_lock:
        conn, _conn = _conn, None
    if conn is not None:
        await conn.close()


@dataclass
class Subscription:
    """订阅信息"""
    id: Optional[int]
    qq_id: str
    group_id: Optional[str]
    server: str
    event_id: int
    uid: str
    last_score: int
    last_rank: int
    last_check_time: Optional[datetime]
    created_at: datetime

    @classmethod
    def from_row(cls, row: aiosqlite.Row) -> "Subscription":
        return cls(
            id=row["id"],
            qq_id=row["qq_id"],
            group_id=row["group_id"],
            server=row["server"],
            event_id=row["event_id"],
            uid=row["uid"],
            last_score=row["last_score"],
            last_rank=row["last_rank"],
            last_check_time=datetime.fromtimestamp(int(row["last_check_time"])) if row["last_check_time"] else None,
            created_at=datetime.fromtimestamp(int(row["created_at"])),
        )


async def add_subscription(qq_id: str, group_id: Optional[str], server: str, event_id: int, uid: str) -> bool:
    """添加订阅。已存在时只更新 group_id/uid，保留 last_score/last_rank 避免重复推送。"""
    try:
        conn = await get_subscription_conn()
        now = int(datetime.now().timestamp())

        cursor = await conn.execute("""
            SELECT id FROM subscriptions
            WHERE qq_id = ? AND server = ? AND event_id = ?
        """, (qq_id, server, event_id))
        existing = await cursor.fetchone()

        if existing:
            await conn.execute("""
                UPDATE subscriptions
                SET group_id = ?, uid = ?
                WHERE qq_id = ? AND server = ? AND event_id = ?
            """, (group_id, uid, qq_id, server, event_id))
            logger.info(f"更新订阅: QQ={qq_id}, 群={group_id}, 服务器={server}, 活动={event_id}, UID={uid}")
        else:
            await conn.execute("""
                INSERT INTO subscriptions
                    (qq_id, group_id, server, event_id, uid, last_score, last_rank, last_check_time, created_at)
                VALUES (?, ?, ?, ?, ?, 0, 0, ?, ?)
            """, (qq_id, group_id, server, event_id, uid, now, now))
            logger.info(f"添加订阅: QQ={qq_id}, 群={group_id}, 服务器={server}, 活动={event_id}, UID={uid}")

        await conn.commit()
        return True
    except Exception as e:
        logger.error(f"添加订阅失败: {e}")
        return False


async def remove_subscription(qq_id: str, server: str, event_id: int) -> bool:
    """取消订阅"""
    try:
        conn = await get_subscription_conn()
        cursor = await conn.execute("""
            DELETE FROM subscriptions
            WHERE qq_id = ? AND server = ? AND event_id = ?
        """, (qq_id, server, event_id))
        await conn.commit()
        deleted = cursor.rowcount > 0
        if deleted:
            logger.info(f"取消订阅: QQ={qq_id}, 服务器={server}, 活动={event_id}")
        return deleted
    except Exception as e:
        logger.error(f"取消订阅失败: {e}")
        return False


async def get_subscription(qq_id: str, server: str, event_id: int) -> Optional[Subscription]:
    """获取指定订阅"""
    try:
        conn = await get_subscription_conn()
        cursor = await conn.execute("""
            SELECT * FROM subscriptions
            WHERE qq_id = ? AND server = ? AND event_id = ?
        """, (qq_id, server, event_id))
        row = await cursor.fetchone()
        return Subscription.from_row(row) if row else None
    except Exception as e:
        logger.error(f"获取订阅失败: {e}")
        return None


async def get_all_subscriptions(server: str, event_id: int) -> List[Subscription]:
    """获取指定服务器和活动的所有订阅"""
    try:
        conn = await get_subscription_conn()
        cursor = await conn.execute("""
            SELECT * FROM subscriptions
            WHERE server = ? AND event_id = ?
        """, (server, event_id))
        rows = await cursor.fetchall()
        return [Subscription.from_row(row) for row in rows]
    except Exception as e:
        logger.error(f"获取所有订阅失败: {e}")
        return []


async def update_subscription_status(subscription_id: int, score: int, rank: int) -> bool:
    """更新订阅的最新分数和排名"""
    try:
        conn = await get_subscription_conn()
        now = int(datetime.now().timestamp())
        await conn.execute("""
            UPDATE subscriptions
            SET last_score = ?, last_rank = ?, last_check_time = ?
            WHERE id = ?
        """, (score, rank, now, subscription_id))
        await conn.commit()
        return True
    except Exception as e:
        logger.error(f"更新订阅状态失败: {e}")
        return False


async def update_subscription_statuses(
    statuses: Sequence[Tuple[int, int, int]],
) -> bool:
    """批量更新订阅状态，并在整轮结束后只提交一次。"""
    if not statuses:
        return True
    try:
        conn = await get_subscription_conn()
        now = int(datetime.now().timestamp())
        await conn.executemany(
            """
            UPDATE subscriptions
            SET last_score = ?, last_rank = ?, last_check_time = ?
            WHERE id = ?
            """,
            [(score, rank, now, subscription_id) for subscription_id, score, rank in statuses],
        )
        await conn.commit()
        return True
    except Exception as exc:
        logger.error(f"批量更新订阅状态失败: {exc}")
        return False


async def remove_subscriptions_by_event(server: str, event_id: int) -> int:
    """删除指定活动的所有订阅（活动结束时调用）"""
    try:
        conn = await get_subscription_conn()
        cursor = await conn.execute("""
            DELETE FROM subscriptions
            WHERE server = ? AND event_id = ?
        """, (server, event_id))
        await conn.commit()
        deleted = cursor.rowcount
        if deleted > 0:
            logger.info(f"删除活动订阅: 服务器={server}, 活动={event_id}, 数量={deleted}")
        return deleted
    except Exception as e:
        logger.error(f"删除活动订阅失败: {e}")
        return 0


async def clear_all_subscriptions() -> int:
    """清空所有订阅（管理员命令）"""
    try:
        conn = await get_subscription_conn()
        cursor = await conn.execute("DELETE FROM subscriptions")
        await conn.commit()
        deleted = cursor.rowcount
        logger.info(f"清空所有订阅: 数量={deleted}")
        return deleted
    except Exception as e:
        logger.error(f"清空订阅失败: {e}")
        return 0


def _register_shutdown_hook() -> None:
    try:
        from nonebot import get_driver

        get_driver().on_shutdown(close_subscription_connection)
    except (ImportError, RuntimeError, ValueError):
        return


_register_shutdown_hook()
