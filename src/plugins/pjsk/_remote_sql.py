from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import aiosqlite

from services.log import logger

from ._paths import DATABASE_PATH

DB_PATH = str(DATABASE_PATH / "remote_live" / "{region}_{account}.db")

_conns: Dict[str, aiosqlite.Connection] = {}
_created: Dict[str, bool] = {}
_lock = asyncio.Lock()


def _db_path(region: str, account: str) -> str:
    safe_account = "".join(ch for ch in str(account) if ch.isalnum() or ch in "._-") or "unknown"
    return DB_PATH.format(region=region, account=safe_account)


async def get_conn(region: str, account: str, create: bool = True) -> Optional[aiosqlite.Connection]:
    path = _db_path(region, account)
    if not create and not os.path.exists(path):
        return None
    os.makedirs(os.path.dirname(path), exist_ok=True)

    async with _lock:
        conn = _conns.get(path)
        if conn is None:
            conn = await aiosqlite.connect(path)
            await conn.execute("PRAGMA journal_mode=WAL;")
            _conns[path] = conn
            logger.info(f"[remote] 连接排名记录数据库 {path}")

        if not _created.get(path):
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS live_records (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts INTEGER NOT NULL,
                    event_id INTEGER,
                    account_id TEXT,
                    live_id TEXT,
                    event_rank INTEGER,
                    event_point INTEGER,
                    wl_chapter_no INTEGER,
                    wl_chapter_rank INTEGER,
                    wl_chapter_point INTEGER,
                    score INTEGER
                )
                """
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_live_event_ts ON live_records (event_id, ts)"
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_live_chapter_ts ON live_records (event_id, wl_chapter_no, ts)"
            )
            await conn.commit()
            _created[path] = True

        return conn


async def close_connections() -> None:
    async with _lock:
        conns = list(_conns.values())
        _conns.clear()
        _created.clear()
    for conn in conns:
        try:
            await conn.close()
        except Exception:
            pass


@dataclass
class LiveRecord:
    ts: int
    event_id: Optional[int] = None
    account_id: Optional[str] = None
    live_id: Optional[str] = None
    event_rank: Optional[int] = None
    event_point: Optional[int] = None
    wl_chapter_no: Optional[int] = None
    wl_chapter_rank: Optional[int] = None
    wl_chapter_point: Optional[int] = None
    score: Optional[int] = None
    id: Optional[int] = None

    @classmethod
    def from_row(cls, row) -> "LiveRecord":
        return cls(
            id=row[0],
            ts=row[1],
            event_id=row[2],
            account_id=row[3],
            live_id=row[4],
            event_rank=row[5],
            event_point=row[6],
            wl_chapter_no=row[7],
            wl_chapter_rank=row[8],
            wl_chapter_point=row[9],
            score=row[10],
        )


_COLUMNS = (
    "id, ts, event_id, account_id, live_id, event_rank, event_point, "
    "wl_chapter_no, wl_chapter_rank, wl_chapter_point, score"
)


async def insert_record(region: str, account: str, record: LiveRecord) -> None:
    conn = await get_conn(region, account)
    if conn is None:
        return
    await conn.execute(
        """
        INSERT INTO live_records
            (ts, event_id, account_id, live_id, event_rank, event_point,
             wl_chapter_no, wl_chapter_rank, wl_chapter_point, score)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            record.ts,
            record.event_id,
            record.account_id,
            record.live_id,
            record.event_rank,
            record.event_point,
            record.wl_chapter_no,
            record.wl_chapter_rank,
            record.wl_chapter_point,
            record.score,
        ),
    )
    await conn.commit()


async def query_records(
    region: str,
    account: str,
    event_id: Optional[int] = None,
    since_ts: Optional[int] = None,
    chapter_no: Optional[int] = None,
) -> List[LiveRecord]:
    conn = await get_conn(region, account, create=False)
    if conn is None:
        return []

    sql = f"SELECT {_COLUMNS} FROM live_records WHERE 1=1"
    params: List[Any] = []
    if event_id is not None:
        sql += " AND event_id = ?"
        params.append(event_id)
    if since_ts is not None:
        sql += " AND ts >= ?"
        params.append(since_ts)
    if chapter_no is not None:
        sql += " AND wl_chapter_no = ?"
        params.append(chapter_no)
    sql += " ORDER BY ts ASC"

    async with conn.execute(sql, params) as cursor:
        rows = await cursor.fetchall()
    return [LiveRecord.from_row(row) for row in rows]


async def count_records(
    region: str,
    account: str,
    event_id: Optional[int] = None,
    since_ts: Optional[int] = None,
    chapter_no: Optional[int] = None,
) -> int:
    conn = await get_conn(region, account, create=False)
    if conn is None:
        return 0

    sql = "SELECT COUNT(1) FROM live_records WHERE 1=1"
    params: List[Any] = []
    if event_id is not None:
        sql += " AND event_id = ?"
        params.append(event_id)
    if since_ts is not None:
        sql += " AND ts >= ?"
        params.append(since_ts)
    if chapter_no is not None:
        sql += " AND wl_chapter_no = ?"
        params.append(chapter_no)

    async with conn.execute(sql, params) as cursor:
        row = await cursor.fetchone()
    return int(row[0]) if row else 0


async def list_chapters(region: str, account: str, event_id: int) -> List[int]:
    conn = await get_conn(region, account, create=False)
    if conn is None:
        return []
    async with conn.execute(
        "SELECT DISTINCT wl_chapter_no FROM live_records "
        "WHERE event_id = ? AND wl_chapter_no IS NOT NULL ORDER BY wl_chapter_no ASC",
        (event_id,),
    ) as cursor:
        rows = await cursor.fetchall()
    return [int(row[0]) for row in rows if row[0] is not None]
