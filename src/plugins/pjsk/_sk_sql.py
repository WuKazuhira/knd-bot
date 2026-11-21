import asyncio
import os
import zlib
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional

import aiosqlite

from services.log import logger

from ._config import SERVER_MAP
from ._paths import DATABASE_PATH, PJSK_DATA_PATH

# 默认排名记录截取长度
RANKING_NAME_LEN_LIMIT = 32

DB_PATH = str(DATABASE_PATH / "sk_{region}" / "{event_id}_ranking.db")
ARCHIVE_DB_PATH = str(PJSK_DATA_PATH / "archive" / "ranking" / "sk_{region}" / "{event_id}_ranking.db")

_conns: Dict[str, aiosqlite.Connection] = {}
_ro_conns: Dict[str, aiosqlite.Connection] = {}
_created_table_keys: Dict[str, bool] = {}
_rle_paths: Dict[str, bool] = {}
_conn_init_lock = asyncio.Lock()
_write_locks: Dict[aiosqlite.Connection, asyncio.Lock] = {}
_ORDER_BY_COLUMNS = {"id", "uid", "name", "score", "rank", "ts"}
_ORDER_BY_DIRECTIONS = {"ASC", "DESC"}


def _get_write_lock(conn: aiosqlite.Connection) -> asyncio.Lock:
    lock = _write_locks.get(conn)
    if lock is None:
        lock = asyncio.Lock()
        _write_locks[conn] = lock
    return lock


def create_parent_folder(path: str):
    os.makedirs(os.path.dirname(path), exist_ok=True)


def _resolve_db_path(region: str, event_id: int, create: bool) -> Optional[str]:
    path = DB_PATH.format(region=region, event_id=event_id)
    if os.path.exists(path):
        return path
    if not create:
        arch = ARCHIVE_DB_PATH.format(region=region, event_id=event_id)
        if os.path.exists(arch):
            return arch
        return None
    return path


async def _detect_rle(conn: aiosqlite.Connection, path: str) -> bool:
    cached = _rle_paths.get(path)
    if cached is not None:
        return cached
    cur = await conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='runs' LIMIT 1"
    )
    row = await cur.fetchone()
    await cur.close()
    is_rle = row is not None
    _rle_paths[path] = is_rle
    return is_rle


async def get_conn(region: str, event_id: int, create: bool = True) -> Optional[aiosqlite.Connection]:
    path = _resolve_db_path(region, event_id, create=create)
    if path is None:
        return None
    if create:
        create_parent_folder(path)

    async with _conn_init_lock:
        if not create:
            conn = _ro_conns.get(path)
            if conn is None:
                try:
                    conn = await aiosqlite.connect(f"file:{path}?mode=ro", uri=True)
                except Exception:
                    conn = await aiosqlite.connect(path)
                try:
                    await _detect_rle(conn, path)
                except Exception:
                    await conn.close()
                    raise
                _ro_conns[path] = conn
                logger.info(f"连接sqlite数据库 {path} 成功")
            return conn

        conn = _conns.get(path)
        if conn is None:
            conn = await aiosqlite.connect(path)
            try:
                if not await _detect_rle(conn, path):
                    await conn.execute("PRAGMA journal_mode=WAL;")
            except Exception:
                await conn.close()
                raise
            _conns[path] = conn
            logger.info(f"连接sqlite数据库 {path} 成功")

        if await _detect_rle(conn, path):
            return conn

        cache_key = f"{region}_{event_id}"
        if not _created_table_keys.get(cache_key):
            try:
                # 建表
                await conn.execute("""
                    CREATE TABLE IF NOT EXISTS ranking (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        uid TEXT,
                        name TEXT,
                        score INTEGER,
                        rank INTEGER,
                        ts INTEGER
                    )
                """)
                # 创建索引
                await conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_ranking_rank_ts
                    ON ranking (rank, ts)
                """)
                await conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_ranking_uid_ts
                    ON ranking (uid, ts)
                """)
                await conn.commit()
            except BaseException:
                try:
                    await conn.rollback()
                except Exception:
                    pass
                raise
            _created_table_keys[cache_key] = True

        return conn


async def close_connections():
    """关闭所有排名数据库连接。"""
    async with _conn_init_lock:
        conns = list(_conns.values()) + list(_ro_conns.values())
        _conns.clear()
        _ro_conns.clear()
        _created_table_keys.clear()
        _rle_paths.clear()
        _write_locks.clear()
    for conn in conns:
        await conn.close()

@dataclass
class Ranking:
    uid: str
    name: str
    score: int
    rank: int
    time: datetime
    id: Optional[int] = None

    @classmethod
    def from_row(cls, row):
        return cls(
            id=row[0],
            uid=row[1],
            name=row[2],
            score=row[3],
            rank=row[4],
            time=datetime.fromtimestamp(row[5])
        )
    
    @classmethod
    def from_sk(cls, data: dict, time: Optional[datetime] = None):
        return cls(
            uid=str(data["userId"]),
            name=data["name"],
            score=data["score"],
            rank=data["rank"],
            time=time or datetime.now(),
        )

def query_update_time(region: str, event_id: int) -> Optional[datetime]:
    path = _resolve_db_path(region, event_id, create=False)
    if path is None or not os.path.exists(path):
        return None
    ret = datetime.fromtimestamp(os.path.getmtime(path))
    if os.path.exists(path + "-wal"):
        ret = max(ret, datetime.fromtimestamp(os.path.getmtime(path + "-wal")))
    return ret


def _validate_order_by(order_by: str) -> str:
    parts = order_by.strip().split()
    if not parts or len(parts) > 2 or parts[0] not in _ORDER_BY_COLUMNS:
        raise ValueError(f"不支持的 order_by: {order_by}")
    if len(parts) == 2:
        direction = parts[1].upper()
        if direction not in _ORDER_BY_DIRECTIONS:
            raise ValueError(f"不支持的 order_by: {order_by}")
        return f"{parts[0]} {direction}"
    return parts[0]


async def _conn_is_rle(conn: aiosqlite.Connection) -> bool:
    for pool in (_conns, _ro_conns):
        for path, c in pool.items():
            if c is conn and path in _rle_paths:
                return _rle_paths[path]
    cur = await conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='runs' LIMIT 1"
    )
    row = await cur.fetchone()
    await cur.close()
    return row is not None


async def query_ranking(
    region: str, 
    event_id: int, 
    uid: str = None,
    name: str = None,
    rank: int = None,
    start_time: datetime = None,
    end_time: datetime = None,
    limit: int = None,
    order_by: str = None,
) -> List[Ranking]:
    conn = await get_conn(region, event_id, create=False)
    if not conn:
        return []

    if await _conn_is_rle(conn):
        sql = (
            "SELECT b.batch_id AS id, i.uid AS uid, i.name AS name, "
            "r.score AS score, r.rank AS rank, b.ts AS ts "
            "FROM runs AS r "
            "JOIN identities AS i ON i.id = r.identity_id "
            "JOIN sig_ranks AS sr ON sr.rank = r.rank "
            "JOIN batches AS b ON b.sig_id = sr.sig_id "
            "  AND b.batch_id BETWEEN r.start_batch AND r.end_batch "
            "WHERE 1=1"
        )
        args = []
        if uid is not None:
            sql += " AND i.uid = ?"
            args.append(str(uid))
        if name is not None:
            sql += " AND i.name = ?"
            args.append(name[:RANKING_NAME_LEN_LIMIT])
        if rank is not None:
            sql += " AND r.rank = ?"
            args.append(rank)
        if start_time is not None:
            sql += " AND b.ts >= ?"
            args.append(start_time.timestamp())
        if end_time is not None:
            sql += " AND b.ts <= ?"
            args.append(end_time.timestamp())
        if order_by is not None:
            validated = _validate_order_by(order_by)
            col_map = {"id": "b.batch_id", "uid": "i.uid", "name": "i.name", "score": "r.score", "rank": "r.rank", "ts": "b.ts"}
            parts = validated.split()
            mapped = col_map[parts[0]] + (f" {parts[1]}" if len(parts) == 2 else "")
            sql += f" ORDER BY {mapped}"
        if limit is not None:
            if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
                raise ValueError("limit 必须是正整数")
            sql += " LIMIT ?"
            args.append(limit)
        cursor = await conn.execute(sql, args)
        rows = await cursor.fetchall()
        await cursor.close()
        return [Ranking.from_row(row) for row in rows]

    sql = "SELECT * FROM ranking WHERE 1=1"
    args = []

    if uid is not None:
        sql += " AND uid = ?"
        args.append(str(uid))

    if name is not None:
        name = name[:RANKING_NAME_LEN_LIMIT]
        sql += " AND name = ?"
        args.append(name)

    if rank is not None:
        sql += " AND rank = ?"
        args.append(rank)

    if start_time is not None:
        sql += " AND ts >= ?"
        args.append(start_time.timestamp())

    if end_time is not None:
        sql += " AND ts <= ?"
        args.append(end_time.timestamp())

    if order_by is not None:
        sql += f" ORDER BY {_validate_order_by(order_by)}"

    if limit is not None:
        if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
            raise ValueError("limit 必须是正整数")
        sql += " LIMIT ?"
        args.append(limit)

    cursor = await conn.execute(sql, args)
    rows = await cursor.fetchall()
    await cursor.close()

    return [Ranking.from_row(row) for row in rows]


async def query_latest_rankings_by_uids(
    region: str,
    event_id: int,
    uids: List[str],
) -> Dict[str, Ranking]:
    """批量查询多个 UID 的最新排名记录。"""
    normalized_uids = list(dict.fromkeys(str(uid) for uid in uids))
    if not normalized_uids:
        return {}

    conn = await get_conn(region, event_id, create=False)
    if not conn:
        return {}

    is_rle = await _conn_is_rle(conn)
    result: Dict[str, Ranking] = {}
    # 为兼容 SQLite 默认的绑定参数数量限制，较大的 UID 集合分批查询。
    for start in range(0, len(normalized_uids), 900):
        batch = normalized_uids[start:start + 900]
        placeholders = ", ".join("?" for _ in batch)
        if is_rle:
            cursor = await conn.execute(f"""
                WITH max_b(bid) AS (SELECT MAX(batch_id) FROM batches)
                SELECT id, uid, name, score, rank, ts FROM (
                    SELECT
                        (CASE WHEN r.end_batch > mb.bid THEN mb.bid ELSE r.end_batch END) AS id,
                        i.uid AS uid, i.name AS name,
                        r.score AS score, r.rank AS rank, b.ts AS ts,
                        ROW_NUMBER() OVER (
                            PARTITION BY i.uid
                            ORDER BY (CASE WHEN r.end_batch > mb.bid THEN mb.bid ELSE r.end_batch END) DESC, r.start_batch DESC
                        ) AS rn
                    FROM identities AS i
                    JOIN runs AS r ON r.identity_id = i.id
                    CROSS JOIN max_b AS mb
                    JOIN batches AS b ON b.batch_id = (CASE WHEN r.end_batch > mb.bid THEN mb.bid ELSE r.end_batch END)
                    WHERE i.uid IN ({placeholders})
                )
                WHERE rn = 1
            """, batch)
        else:
            cursor = await conn.execute(f"""
                SELECT id, uid, name, score, rank, ts FROM (
                    SELECT
                        id, uid, name, score, rank, ts,
                        ROW_NUMBER() OVER (
                            PARTITION BY uid ORDER BY ts DESC, id DESC
                        ) AS rn
                    FROM ranking
                    WHERE uid IN ({placeholders})
                )
                WHERE rn = 1
            """, batch)
        rows = await cursor.fetchall()
        await cursor.close()
        for row in rows:
            ranking = Ranking.from_row(row)
            result[ranking.uid] = ranking
    return result


async def query_latest_ranking(region: str, event_id: int, ranks: List[int] = None) -> List[Ranking]:
    conn = await get_conn(region, event_id, create=False)
    if not conn:
        return []
    if await _conn_is_rle(conn):
        if ranks:
            values = ", ".join("(?, ?)" for _ in ranks)
            params = [value for i, rank in enumerate(ranks) for value in (rank, i)]
            cursor = await conn.execute(f"""
                WITH requested(rank, ordinal) AS (VALUES {values}),
                max_b(bid) AS (SELECT MAX(batch_id) FROM batches)
                SELECT (CASE WHEN r.end_batch > mb.bid THEN mb.bid ELSE r.end_batch END), i.uid, i.name, r.score, r.rank, b.ts
                FROM requested AS q
                CROSS JOIN max_b AS mb
                JOIN runs AS r ON r.rank = q.rank AND r.start_batch = (
                    SELECT start_batch FROM runs WHERE rank = q.rank ORDER BY start_batch DESC LIMIT 1
                )
                JOIN identities AS i ON i.id = r.identity_id
                JOIN batches AS b ON b.batch_id = (CASE WHEN r.end_batch > mb.bid THEN mb.bid ELSE r.end_batch END)
                ORDER BY q.ordinal
            """, params)
        else:
            cursor = await conn.execute("""
                WITH max_b(bid) AS (SELECT MAX(batch_id) FROM batches)
                SELECT (CASE WHEN r.end_batch > mb.bid THEN mb.bid ELSE r.end_batch END), i.uid, i.name, r.score, r.rank, b.ts
                FROM runs AS r
                CROSS JOIN max_b AS mb
                JOIN (
                    SELECT rank, MAX(start_batch) AS max_sb FROM runs GROUP BY rank
                ) AS m ON r.rank = m.rank AND r.start_batch = m.max_sb
                JOIN identities AS i ON i.id = r.identity_id
                JOIN batches AS b ON b.batch_id = (CASE WHEN r.end_batch > mb.bid THEN mb.bid ELSE r.end_batch END)
                ORDER BY r.rank
            """)
        rows = await cursor.fetchall()
        await cursor.close()
        return [Ranking.from_row(row) for row in rows]
    if ranks:
        # 单条 SELECT 让所有名次来自同一数据库读快照；子查询仍按 rank 索引取 1 条。
        # 同秒多批次按 ID 取最新，避免同一个秒级时间戳下混用两批结果。
        values = ", ".join("(?, ?)" for _ in ranks)
        params = [value for i, rank in enumerate(ranks) for value in (rank, i)]
        cursor = await conn.execute(f"""
            WITH requested(rank, ordinal) AS (VALUES {values})
            SELECT r.id, r.uid, r.name, r.score, r.rank, r.ts
            FROM requested AS q
            JOIN ranking AS r ON r.id = (
                SELECT id FROM ranking WHERE rank = q.rank ORDER BY ts DESC, id DESC LIMIT 1
            )
            ORDER BY q.ordinal
        """, params)
        rows = await cursor.fetchall()
        await cursor.close()
        return [Ranking.from_row(row) for row in rows]
    else:
        cursor = await conn.execute("""
            SELECT * FROM ranking WHERE id IN (
                SELECT MAX(id) FROM ranking GROUP BY rank
            ) ORDER BY rank
        """)
        rows = await cursor.fetchall()
        await cursor.close()
        return [Ranking.from_row(row) for row in rows]

async def query_first_ranking_after(
    region: str, 
    event_id: int, 
    after_time: datetime,
    ranks: List[int] = None,
) -> List[Ranking]:
    conn = await get_conn(region, event_id, create=False)
    if not conn:
        return []
    if await _conn_is_rle(conn):
        cur = await conn.execute(
            "SELECT COALESCE(MIN(batch_id), 0) FROM batches WHERE ts > ?",
            (after_time.timestamp(),),
        )
        row = await cur.fetchone()
        await cur.close()
        min_batch = int(row[0]) if row and row[0] else 0
        if min_batch == 0:
            return []
        if ranks:
            values = ", ".join("(?, ?)" for _ in ranks)
            params = [value for i, rank in enumerate(ranks) for value in (rank, i)] + [min_batch]
            cursor = await conn.execute(f"""
                WITH requested(rank, ordinal) AS (VALUES {values}),
                params(min_bid) AS (VALUES (?)),
                first_batch AS (
                    SELECT q.rank, q.ordinal, (
                        SELECT b.batch_id
                        FROM sig_ranks AS sr
                        JOIN batches AS b ON b.sig_id = sr.sig_id
                        WHERE sr.rank = q.rank AND b.batch_id >= (SELECT min_bid FROM params)
                        ORDER BY b.batch_id ASC LIMIT 1
                    ) AS bid
                    FROM requested AS q
                )
                SELECT fb.bid, i.uid, i.name, r.score, r.rank, b.ts
                FROM first_batch AS fb
                JOIN batches AS b ON b.batch_id = fb.bid
                JOIN runs AS r ON r.rank = fb.rank AND r.start_batch = (
                    SELECT start_batch FROM runs
                    WHERE rank = fb.rank AND start_batch <= fb.bid
                    ORDER BY start_batch DESC LIMIT 1
                ) AND r.end_batch >= fb.bid
                JOIN identities AS i ON i.id = r.identity_id
                ORDER BY fb.ordinal
            """, params)
        else:
            cursor = await conn.execute("""
                WITH first_batch AS (
                    SELECT sr.rank, MIN(b.batch_id) AS bid
                    FROM sig_ranks AS sr
                    JOIN batches AS b ON b.sig_id = sr.sig_id AND b.batch_id >= ?
                    GROUP BY sr.rank
                )
                SELECT fb.bid, i.uid, i.name, r.score, r.rank, b.ts
                FROM first_batch AS fb
                JOIN batches AS b ON b.batch_id = fb.bid
                JOIN runs AS r ON r.rank = fb.rank AND r.start_batch = (
                    SELECT start_batch FROM runs
                    WHERE rank = fb.rank AND start_batch <= fb.bid
                    ORDER BY start_batch DESC LIMIT 1
                ) AND r.end_batch >= fb.bid
                JOIN identities AS i ON i.id = r.identity_id
                ORDER BY fb.rank
            """, (min_batch,))
        rows = await cursor.fetchall()
        await cursor.close()
        return [Ranking.from_row(row) for row in rows]
    if ranks:
        placeholders = ", ".join("?" for _ in ranks)
        sql = f"""
            SELECT * FROM (
                SELECT
                    *,
                    ROW_NUMBER() OVER (PARTITION BY rank ORDER BY ts ASC) as rn
                FROM ranking
                WHERE rank IN ({placeholders}) AND ts > ?
            )
            WHERE rn = 1
            ORDER BY rank
        """
        params = ranks + [after_time.timestamp()]
        cursor = await conn.execute(sql, params)
        rows = await cursor.fetchall()
        await cursor.close()
        return [Ranking.from_row(row) for row in rows]
    else:
        cursor = await conn.execute("""
            SELECT * FROM ranking WHERE id IN (
                SELECT MIN(id) FROM ranking WHERE ts > ? GROUP BY rank
            ) ORDER BY rank
        """, (after_time.timestamp(),))
        rows = await cursor.fetchall()
        await cursor.close()
        return [Ranking.from_row(row) for row in rows]

_OPEN_RUN_END_BATCH = (1 << 63) - 1


async def _record_rankings_rle(conn: aiosqlite.Connection, rankings: List[Ranking]) -> None:
    dedup: List[Ranking] = []
    seen = set()
    for r in rankings:
        rk = int(r.rank)
        if rk <= 0 or rk in seen:
            continue
        seen.add(rk)
        dedup.append(r)
    if not dedup:
        return

    ts_val = int(dedup[0].time.timestamp())
    blob = zlib.compress(",".join(str(int(r.rank)) for r in dedup).encode("ascii"), level=6)

    async with _get_write_lock(conn):
        try:
            if not conn.in_transaction:
                await conn.execute("BEGIN IMMEDIATE")

            cur = await conn.execute("SELECT COALESCE(MAX(batch_id), 0) + 1 FROM batches")
            cur_batch = int((await cur.fetchone())[0])
            await cur.close()
            prev_batch = cur_batch - 1

            cur = await conn.execute("SELECT sig_id FROM batch_sigs WHERE ranks_blob = ?", (blob,))
            row = await cur.fetchone()
            await cur.close()
            if row is not None:
                sig_id = int(row[0])
            else:
                cur = await conn.execute("SELECT COALESCE(MAX(sig_id), 0) + 1 FROM batch_sigs")
                sig_id = int((await cur.fetchone())[0])
                await cur.close()
                await conn.execute("INSERT INTO batch_sigs(sig_id, ranks_blob) VALUES (?, ?)", (sig_id, blob))
                await conn.executemany(
                    "INSERT INTO sig_ranks(sig_id, rank, ordinal) VALUES (?, ?, ?)",
                    [(sig_id, int(r.rank), idx) for idx, r in enumerate(dedup)],
                )

            await conn.execute(
                "INSERT INTO batches(batch_id, ts, sig_id) VALUES (?, ?, ?)",
                (cur_batch, ts_val, sig_id),
            )

            cur = await conn.execute(
                "SELECT rank, start_batch, identity_id, score FROM runs WHERE end_batch = ?",
                (_OPEN_RUN_END_BATCH,),
            )
            active = {int(rk): (int(sb), int(iid), int(sc)) for rk, sb, iid, sc in await cur.fetchall()}
            await cur.close()

            for r in dedup:
                rk = int(r.rank)
                uid_s = str(r.uid)
                name_s = str(r.name)
                sc_i = int(r.score)
                cur = await conn.execute(
                    "SELECT id FROM identities WHERE uid = ? AND name = ? LIMIT 1",
                    (uid_s, name_s),
                )
                irow = await cur.fetchone()
                await cur.close()
                if irow is not None:
                    iid = int(irow[0])
                else:
                    cur = await conn.execute("SELECT COALESCE(MAX(id), 0) + 1 FROM identities")
                    iid = int((await cur.fetchone())[0])
                    await cur.close()
                    await conn.execute(
                        "INSERT INTO identities(id, uid, name) VALUES (?, ?, ?)",
                        (iid, uid_s, name_s),
                    )
                prev = active.get(rk)
                if prev is not None and prev[1] == iid and prev[2] == sc_i:
                    continue
                if prev is not None:
                    await conn.execute(
                        "UPDATE runs SET end_batch = ? WHERE rank = ? AND start_batch = ?",
                        (prev_batch, rk, prev[0]),
                    )
                await conn.execute(
                    "INSERT INTO runs(rank, start_batch, end_batch, identity_id, score) VALUES (?, ?, ?, ?, ?)",
                    (rk, cur_batch, _OPEN_RUN_END_BATCH, iid, sc_i),
                )

            for rk, (sb, _, _) in active.items():
                if rk not in seen:
                    await conn.execute(
                        "UPDATE runs SET end_batch = ? WHERE rank = ? AND start_batch = ?",
                        (prev_batch, rk, sb),
                    )
            await conn.commit()
        except BaseException:
            try:
                await conn.rollback()
            except Exception:
                pass
            raise


async def record_rankings(region: str, event_id: int, rankings: List[Ranking]):
    """记录一批榜线数据到数据库"""
    if not rankings:
        return
    conn = await get_conn(region, event_id, create=True)
    if conn is None:
        return
    if await _conn_is_rle(conn):
        await _record_rankings_rle(conn, rankings)
        return

    # 批量插入
    sql = "INSERT INTO ranking (uid, name, score, rank, ts) VALUES (?, ?, ?, ?, ?)"
    params = [(r.uid, r.name, r.score, r.rank, int(r.time.timestamp())) for r in rankings]

    async with _get_write_lock(conn):
        try:
            await conn.executemany(sql, params)
            await conn.commit()
        except BaseException:
            try:
                await conn.rollback()
            except Exception:
                pass
            raise


def _register_shutdown_hook() -> None:
    try:
        from nonebot import get_driver

        get_driver().on_shutdown(close_connections)
    except (ImportError, RuntimeError, ValueError):
        return


_register_shutdown_hook()
