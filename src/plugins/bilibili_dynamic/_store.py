"""B站动态订阅与逐群投递状态（aiosqlite）。"""

import asyncio
import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import aiosqlite

from ._api import Dynamic


@dataclass
class Subscription:
    id: int
    bot_id: str
    group_id: int
    uid: str
    name: str
    cursor: int
    created_at: int
    failures: int
    retry_at: int
    skipped: int = 0


class SubscriptionStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._conn = None
        self._lock = asyncio.Lock()
        self._closed = False

    async def _connect(self):
        """调用方持锁，避免初始化和写事务交叉。"""
        if self._closed:
            raise RuntimeError('订阅数据库已关闭')
        if self._conn is None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            conn = await aiosqlite.connect(self.path)
            try:
                conn.row_factory = aiosqlite.Row
                await conn.execute('PRAGMA journal_mode=WAL')
                await conn.executescript("""
                    CREATE TABLE IF NOT EXISTS subscriptions (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        bot_id TEXT NOT NULL,
                        group_id INTEGER NOT NULL,
                        uid TEXT NOT NULL,
                        name TEXT NOT NULL,
                        cursor TEXT NOT NULL,
                        created_at INTEGER NOT NULL,
                        failures INTEGER NOT NULL DEFAULT 0,
                        retry_at INTEGER NOT NULL DEFAULT 0,
                        skipped INTEGER NOT NULL DEFAULT 0,
                        UNIQUE(bot_id, group_id, uid)
                    );
                    CREATE TABLE IF NOT EXISTS uid_heads (
                        uid TEXT PRIMARY KEY,
                        cursor TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS dynamics (
                        uid TEXT NOT NULL,
                        dynamic_id TEXT NOT NULL,
                        payload TEXT NOT NULL,
                        PRIMARY KEY(uid, dynamic_id)
                    );
                """)
                cursor = await conn.execute('PRAGMA table_info(subscriptions)')
                if 'skipped' not in {row['name'] for row in await cursor.fetchall()}:
                    await conn.execute('ALTER TABLE subscriptions ADD COLUMN skipped INTEGER NOT NULL DEFAULT 0')
                await conn.commit()
            except BaseException:
                await conn.close()
                raise
            self._conn = conn
        return self._conn

    @staticmethod
    def _subscription(row):
        if row is None:
            return None
        data = dict(row)
        data['cursor'] = int(data['cursor'])
        return Subscription(**data)

    async def list_subscriptions(self, bot_id=None, group_id=None):
        async with self._lock:
            conn = await self._connect()
            if bot_id is None:
                cursor = await conn.execute('SELECT * FROM subscriptions ORDER BY id')
            else:
                cursor = await conn.execute(
                    'SELECT * FROM subscriptions WHERE bot_id = ? AND group_id = ? ORDER BY id',
                    (str(bot_id), group_id),
                )
            return [self._subscription(row) for row in await cursor.fetchall()]

    async def get(self, subscription_id):
        async with self._lock:
            conn = await self._connect()
            cursor = await conn.execute('SELECT * FROM subscriptions WHERE id = ?', (subscription_id,))
            return self._subscription(await cursor.fetchone())

    async def add(self, bot_id, group_id, uid, name, baseline, created_at, limit=20):
        """返回 added / exists / limit；基线必须在成功获取接口后传入。"""
        async with self._lock:
            conn = await self._connect()
            try:
                await conn.execute('BEGIN IMMEDIATE')
                cursor = await conn.execute(
                    'SELECT uid FROM subscriptions WHERE bot_id = ? AND group_id = ?',
                    (str(bot_id), group_id),
                )
                rows = await cursor.fetchall()
                if any(row['uid'] == uid for row in rows):
                    await conn.rollback()
                    return 'exists'
                if len(rows) >= limit:
                    await conn.rollback()
                    return 'limit'
                await conn.execute(
                    'INSERT INTO subscriptions (bot_id, group_id, uid, name, cursor, created_at) '
                    'VALUES (?, ?, ?, ?, ?, ?)',
                    (str(bot_id), group_id, uid, name, str(baseline), created_at),
                )
                # 新群不能推进其它群的抓取游标；首次订阅才初始化 UID 基线。
                await conn.execute(
                    'INSERT OR IGNORE INTO uid_heads (uid, cursor) VALUES (?, ?)',
                    (uid, str(baseline)),
                )
                await conn.commit()
            except BaseException:
                await conn.rollback()
                raise
            return 'added'

    async def remove(self, bot_id, group_id, uid):
        async with self._lock:
            conn = await self._connect()
            cursor = await conn.execute(
                'DELETE FROM subscriptions WHERE bot_id = ? AND group_id = ? AND uid = ?',
                (str(bot_id), group_id, uid),
            )
            removed = cursor.rowcount > 0
            cursor = await conn.execute('SELECT id FROM subscriptions WHERE uid = ? LIMIT 1', (uid,))
            if await cursor.fetchone() is None:
                await conn.execute('DELETE FROM uid_heads WHERE uid = ?', (uid,))
                await conn.execute('DELETE FROM dynamics WHERE uid = ?', (uid,))
            await conn.commit()
            return removed

    async def head(self, uid):
        async with self._lock:
            conn = await self._connect()
            cursor = await conn.execute('SELECT cursor FROM uid_heads WHERE uid = ?', (uid,))
            row = await cursor.fetchone()
            return int(row['cursor']) if row else 0

    async def save_dynamics(self, uid, dynamics):
        """先缓存后推进抓取游标，失败群可独立读取缓存重试。"""
        high_watermark = getattr(dynamics, 'high_watermark', max((item.id for item in dynamics), default=0))
        if not high_watermark:
            return
        async with self._lock:
            conn = await self._connect()
            try:
                await conn.execute('BEGIN IMMEDIATE')
                cursor = await conn.execute('SELECT cursor FROM uid_heads WHERE uid = ?', (uid,))
                row = await cursor.fetchone()
                head = max(int(row['cursor']) if row else 0, high_watermark)
                await conn.executemany(
                    'INSERT OR IGNORE INTO dynamics (uid, dynamic_id, payload) VALUES (?, ?, ?)',
                    [(uid, str(item.id), json.dumps(asdict(item), ensure_ascii=False)) for item in dynamics],
                )
                await conn.execute(
                    'INSERT INTO uid_heads (uid, cursor) VALUES (?, ?) '
                    'ON CONFLICT(uid) DO UPDATE SET cursor = excluded.cursor',
                    (uid, str(head)),
                )
                if dynamics:
                    await conn.execute('UPDATE subscriptions SET name = ? WHERE uid = ?', (dynamics[-1].name, uid))
                await conn.commit()
            except BaseException:
                await conn.rollback()
                raise

    async def pending(self, subscription, limit=1000):
        async with self._lock:
            conn = await self._connect()
            baseline = str(subscription.cursor)
            cursor = await conn.execute(
                'SELECT payload FROM dynamics WHERE uid = ? '
                'AND (length(dynamic_id) > length(?) OR (length(dynamic_id) = length(?) AND dynamic_id > ?)) '
                "AND (json_extract(payload, '$.published_at') >= ? "
                "OR (json_extract(payload, '$.published_at') = 0 AND ? > 0)) "
                'ORDER BY length(dynamic_id), dynamic_id LIMIT ?',
                (
                    subscription.uid,
                    baseline,
                    baseline,
                    baseline,
                    subscription.created_at,
                    int(subscription.cursor > 0),
                    limit,
                ),
            )
            result = []
            for row in await cursor.fetchall():
                data = json.loads(row['payload'])
                data['images'] = tuple(data.get('images', ()))
                result.append(Dynamic(**data))
            return result

    async def mark_sent(self, subscription_id, dynamic_id):
        async with self._lock:
            conn = await self._connect()
            cursor = await conn.execute('SELECT cursor FROM subscriptions WHERE id = ?', (subscription_id,))
            row = await cursor.fetchone()
            if row:
                await conn.execute(
                    'UPDATE subscriptions SET cursor = ?, failures = 0, retry_at = 0 WHERE id = ?',
                    (str(max(int(row['cursor']), dynamic_id)), subscription_id),
                )
                await conn.commit()

    async def mark_failed(self, subscription_id):
        """短暂失败不删除订阅；长期不可达群最多每六小时重试一次。"""
        async with self._lock:
            conn = await self._connect()
            cursor = await conn.execute('SELECT failures FROM subscriptions WHERE id = ?', (subscription_id,))
            row = await cursor.fetchone()
            if row:
                failures = row['failures'] + 1
                delay = min(60 * 2 ** min(failures - 1, 9), 21600)
                await conn.execute(
                    'UPDATE subscriptions SET failures = ?, retry_at = ? WHERE id = ?',
                    (failures, int(time.time()) + delay, subscription_id),
                )
                await conn.commit()

    async def prune(self, max_items=1000, max_age=7 * 86400):
        """缓存最多保留七天/每UID一千条；过期缺口计入群订阅列表。"""
        async with self._lock:
            conn = await self._connect()
            cursor = await conn.execute('SELECT * FROM subscriptions')
            groups = {}
            for row in await cursor.fetchall():
                groups.setdefault(row['uid'], []).append(row)
            cursor = await conn.execute('SELECT DISTINCT uid FROM dynamics')
            uids = [row['uid'] for row in await cursor.fetchall()]
            cutoff = int(time.time()) - max_age
            try:
                for uid in uids:
                    cursor = await conn.execute(
                        'SELECT dynamic_id, payload FROM dynamics WHERE uid = ? '
                        'ORDER BY length(dynamic_id), dynamic_id',
                        (uid,),
                    )
                    items = [(int(row['dynamic_id']), json.loads(row['payload'])) for row in await cursor.fetchall()]
                    forced = [
                        identifier
                        for index, (identifier, item) in enumerate(items)
                        if index < len(items) - max_items or 0 < item.get('published_at', 0) < cutoff
                    ]
                    floor = max(forced, default=0)
                    subscribers = groups.get(uid, [])
                    for sub in subscribers:
                        skipped = sum(
                            1
                            for identifier, item in items
                            if int(sub['cursor']) < identifier <= floor
                            and (
                                item.get('published_at', 0) >= sub['created_at']
                                or not item.get('published_at')
                                and int(sub['cursor']) > 0
                            )
                        )
                        if floor > int(sub['cursor']):
                            await conn.execute(
                                'UPDATE subscriptions SET cursor = ?, skipped = skipped + ? WHERE id = ?',
                                (str(floor), skipped, sub['id']),
                            )
                    delete = []
                    for identifier, item in items:
                        if identifier <= floor or all(
                            identifier <= int(sub['cursor'])
                            or (item.get('published_at', 0) and item['published_at'] < sub['created_at'])
                            for sub in subscribers
                        ):
                            delete.append((uid, str(identifier)))
                    await conn.executemany('DELETE FROM dynamics WHERE uid = ? AND dynamic_id = ?', delete)
                await conn.execute('DELETE FROM uid_heads WHERE uid NOT IN (SELECT uid FROM subscriptions)')
                await conn.commit()
            except BaseException:
                await conn.rollback()
                raise

    async def close(self):
        async with self._lock:
            self._closed = True
            if self._conn is not None:
                await self._conn.close()
                self._conn = None
