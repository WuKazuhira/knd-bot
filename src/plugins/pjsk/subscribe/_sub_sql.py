"""新曲/虚拟Live 订阅数据库（aiosqlite）。

表结构：
    pjsk_notify_subscriptions(id, group_id, qq_id NULL, server, kind, created_at)
    qq_id 为 NULL 表示群订阅（推送开关），非 NULL 表示该群内用户的 @ 提醒。
"""
import asyncio
import os
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional

import aiosqlite

from services.log import logger

from .._paths import DATABASE_PATH

NOTIFY_SUB_DB_PATH = str(DATABASE_PATH / 'notify_subscription.db')

KIND_MUSIC = 'music'
KIND_VLIVE = 'vlive'
VALID_KINDS = (KIND_MUSIC, KIND_VLIVE)

_conn: Optional[aiosqlite.Connection] = None
_conn_init_lock = asyncio.Lock()


async def get_notify_sub_conn() -> aiosqlite.Connection:
    global _conn
    if _conn is not None:
        return _conn
    async with _conn_init_lock:
        if _conn is not None:
            return _conn
        os.makedirs(os.path.dirname(NOTIFY_SUB_DB_PATH), exist_ok=True)
        conn = await aiosqlite.connect(NOTIFY_SUB_DB_PATH)
        conn.row_factory = aiosqlite.Row
        await conn.execute('PRAGMA journal_mode=WAL;')
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS pjsk_notify_subscriptions (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                group_id   TEXT NOT NULL,
                qq_id      TEXT,
                server     TEXT NOT NULL,
                kind       TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                UNIQUE(group_id, qq_id, server, kind)
            )
        """)
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_notify_sub_kind_server
            ON pjsk_notify_subscriptions (kind, server)
        """)
        await conn.commit()
        _conn = conn
        logger.info(f'连接新曲/vlive订阅数据库 {NOTIFY_SUB_DB_PATH} 成功')
        return conn


async def close_notify_sub_conn() -> None:
    global _conn
    async with _conn_init_lock:
        conn, _conn = _conn, None
    if conn is not None:
        await conn.close()


@dataclass
class NotifySubscription:
    id: Optional[int]
    group_id: str
    qq_id: Optional[str]
    server: str
    kind: str
    created_at: datetime

    @classmethod
    def from_row(cls, row: aiosqlite.Row) -> 'NotifySubscription':
        return cls(
            id=row['id'],
            group_id=row['group_id'],
            qq_id=row['qq_id'],
            server=row['server'],
            kind=row['kind'],
            created_at=datetime.fromtimestamp(int(row['created_at'])),
        )


async def add_notify_sub(group_id: str, qq_id: Optional[str], server: str, kind: str) -> bool:
    """添加订阅；已存在时返回 False。"""
    try:
        conn = await get_notify_sub_conn()
        now = int(datetime.now().timestamp())
        cursor = await conn.execute(
            'SELECT id FROM pjsk_notify_subscriptions WHERE group_id = ? AND qq_id IS ? AND server = ? AND kind = ?',
            (group_id, qq_id, server, kind),
        )
        if await cursor.fetchone():
            return False
        await conn.execute(
            'INSERT INTO pjsk_notify_subscriptions (group_id, qq_id, server, kind, created_at) VALUES (?, ?, ?, ?, ?)',
            (group_id, qq_id, server, kind, now),
        )
        await conn.commit()
        logger.info(f'添加订阅: 群={group_id}, QQ={qq_id}, 服务器={server}, 类型={kind}')
        return True
    except Exception as e:
        logger.error(f'添加新曲/vlive订阅失败: {e}')
        return False


async def remove_notify_sub(group_id: str, qq_id: Optional[str], server: str, kind: str) -> bool:
    try:
        conn = await get_notify_sub_conn()
        cursor = await conn.execute(
            'DELETE FROM pjsk_notify_subscriptions WHERE group_id = ? AND qq_id IS ? AND server = ? AND kind = ?',
            (group_id, qq_id, server, kind),
        )
        await conn.commit()
        return cursor.rowcount > 0
    except Exception as e:
        logger.error(f'取消新曲/vlive订阅失败: {e}')
        return False


async def remove_group_subs(group_id: str, server: str, kind: str) -> int:
    """关闭群订阅时连带清理该群该类型的所有个人 @ 提醒。"""
    try:
        conn = await get_notify_sub_conn()
        cursor = await conn.execute(
            'DELETE FROM pjsk_notify_subscriptions WHERE group_id = ? AND server = ? AND kind = ?',
            (group_id, server, kind),
        )
        await conn.commit()
        return cursor.rowcount
    except Exception as e:
        logger.error(f'清理群订阅失败: {e}')
        return 0


async def get_group_subs(kind: str, server: str) -> List[NotifySubscription]:
    """获取某类型某服务器的全部群订阅（qq_id IS NULL）。"""
    try:
        conn = await get_notify_sub_conn()
        cursor = await conn.execute(
            'SELECT * FROM pjsk_notify_subscriptions WHERE kind = ? AND server = ? AND qq_id IS NULL',
            (kind, server),
        )
        return [NotifySubscription.from_row(r) for r in await cursor.fetchall()]
    except Exception as e:
        logger.error(f'获取群订阅失败: {e}')
        return []


async def get_user_subs(kind: str, server: str, group_id: str) -> List[NotifySubscription]:
    """获取某群某类型的全部用户 @ 订阅。"""
    try:
        conn = await get_notify_sub_conn()
        cursor = await conn.execute(
            'SELECT * FROM pjsk_notify_subscriptions WHERE kind = ? AND server = ? AND group_id = ? AND qq_id IS NOT NULL',
            (kind, server, group_id),
        )
        return [NotifySubscription.from_row(r) for r in await cursor.fetchall()]
    except Exception as e:
        logger.error(f'获取用户订阅失败: {e}')
        return []


async def is_group_subbed(kind: str, server: str, group_id: str) -> bool:
    try:
        conn = await get_notify_sub_conn()
        cursor = await conn.execute(
            'SELECT 1 FROM pjsk_notify_subscriptions WHERE kind = ? AND server = ? AND group_id = ? AND qq_id IS NULL',
            (kind, server, group_id),
        )
        return await cursor.fetchone() is not None
    except Exception as e:
        logger.error(f'查询群订阅失败: {e}')
        return False


async def get_group_sub_status(group_id: str) -> List[NotifySubscription]:
    """获取本群全部订阅记录（含个人）。"""
    try:
        conn = await get_notify_sub_conn()
        cursor = await conn.execute(
            'SELECT * FROM pjsk_notify_subscriptions WHERE group_id = ? ORDER BY kind, server, qq_id',
            (group_id,),
        )
        return [NotifySubscription.from_row(r) for r in await cursor.fetchall()]
    except Exception as e:
        logger.error(f'获取群订阅状态失败: {e}')
        return []


def _register_shutdown_hook() -> None:
    try:
        from nonebot import get_driver

        get_driver().on_shutdown(close_notify_sub_conn)
    except (ImportError, RuntimeError, ValueError):
        return


_register_shutdown_hook()
