import asyncio
import os
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional

import aiosqlite

from services.log import logger

from ._config import SERVER_MAP
from ._paths import DATABASE_PATH

# 默认排名记录截取长度
RANKING_NAME_LEN_LIMIT = 32

DB_PATH = str(DATABASE_PATH / "sk_{region}" / "{event_id}_ranking.db")

_conns: Dict[str, aiosqlite.Connection] = {}
_created_table_keys: Dict[str, bool] = {}
_conn_init_lock = asyncio.Lock()
_ORDER_BY_COLUMNS = {"id", "uid", "name", "score", "rank", "ts"}
_ORDER_BY_DIRECTIONS = {"ASC", "DESC"}


def create_parent_folder(path: str):
    os.makedirs(os.path.dirname(path), exist_ok=True)

async def get_conn(region: str, event_id: int, create: bool = True) -> Optional[aiosqlite.Connection]:
    path = DB_PATH.format(region=region, event_id=event_id)
    create_parent_folder(path)
    if not create and not os.path.exists(path):
        return None

    async with _conn_init_lock:
        conn = _conns.get(path)
        if conn is None:
            conn = await aiosqlite.connect(path)
            await conn.execute("PRAGMA journal_mode=WAL;")
            _conns[path] = conn
            logger.info(f"连接sqlite数据库 {path} 成功")

        cache_key = f"{region}_{event_id}"
        if not _created_table_keys.get(cache_key):
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
                CREATE INDEX IF NOT EXISTS idx_ranking_uid
                ON ranking (uid)
            """)
            await conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_ranking_uid_ts
                ON ranking (uid, ts)
            """)
            await conn.commit()
            _created_table_keys[cache_key] = True

        return conn


async def close_connections():
    """关闭所有排名数据库连接。"""
    async with _conn_init_lock:
        conns = list(_conns.values())
        _conns.clear()
        _created_table_keys.clear()
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
    path = DB_PATH.format(region=region, event_id=event_id)
    if not os.path.exists(path):
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

    result: Dict[str, Ranking] = {}
    # 为兼容 SQLite 默认的绑定参数数量限制，较大的 UID 集合分批查询。
    for start in range(0, len(normalized_uids), 900):
        batch = normalized_uids[start:start + 900]
        placeholders = ", ".join("?" for _ in batch)
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
    if ranks:
        # 按少量指定名次查询时，逐个 rank 使用 (rank, ts) 索引倒序取 1 条，
        # 比窗口函数对每个 rank 分区扫描所有历史记录更轻。
        rows = []
        for rank in ranks:
            cursor = await conn.execute(
                "SELECT * FROM ranking WHERE rank = ? ORDER BY ts DESC LIMIT 1",
                (rank,),
            )
            row = await cursor.fetchone()
            await cursor.close()
            if row:
                rows.append(row)
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

async def record_rankings(region: str, event_id: int, rankings: List[Ranking]):
    """记录一批榜线数据到数据库"""
    if not rankings:
        return
    conn = await get_conn(region, event_id, create=True)
    
    # 批量插入
    sql = "INSERT INTO ranking (uid, name, score, rank, ts) VALUES (?, ?, ?, ?, ?)"
    params = [(r.uid, r.name, r.score, r.rank, int(r.time.timestamp())) for r in rankings]
    
    await conn.executemany(sql, params)
    await conn.commit()


def _register_shutdown_hook() -> None:
    try:
        from nonebot import get_driver

        get_driver().on_shutdown(close_connections)
    except (ImportError, RuntimeError, ValueError):
        return


_register_shutdown_hook()
