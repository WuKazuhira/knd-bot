"""验证 Python 回退查房不会把分数回退后的恢复重复计为周回。"""

import ast
import asyncio
import sqlite3
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace


SOURCE = Path(__file__).resolve().parents[1] / "src/plugins/pjsk/sk/__init__.py"
module = ast.parse(SOURCE.read_text(encoding="utf-8"), filename=str(SOURCE))
activity_stats = next(
    node for node in module.body
    if isinstance(node, ast.FunctionDef) and node.name == "_build_activity_stats"
)
namespace = {}
exec(compile(ast.Module(body=[activity_stats], type_ignores=[]), str(SOURCE), "exec"), namespace)


class ActivityStatsTests(unittest.TestCase):
    def test_only_new_score_highs_count_as_plays(self):
        base = datetime(2026, 9, 26, 10)
        cases = (
            ("stale recovery", [(70, 900), (65, 1100), (59, 1100), (40, 1200),
                                (39, 1100), (38, 1200), (0, 1350)], (2, 125, 150)),
            ("before window", [(70, 1200), (65, 1000), (59, 1000), (50, 1200),
                               (49, 1000), (48, 1200), (0, 1250)], (1, 50, 50)),
            ("normal", [(59, 100), (30, 120), (0, 145)], (2, 22.5, 25)),
        )
        for label, samples, expected in cases:
            with self.subTest(label=label):
                history = [SimpleNamespace(time=base - timedelta(minutes=minutes), score=score)
                           for minutes, score in samples]
                result = namespace["_build_activity_stats"](history, history[-1])
                self.assertEqual((result["play_count"], result["avg_pt"], result["last_pt"]), expected)


class CfSnapshotTests(unittest.TestCase):
    def test_range_skips_duplicate_player(self):
        unique_fn = next(
            node for node in module.body
            if isinstance(node, ast.FunctionDef) and node.name == "_unique_cf_rankings"
        )
        scope = {}
        exec(compile(ast.Module(body=[unique_fn], type_ignores=[]), str(SOURCE), "exec"), scope)
        rows = [SimpleNamespace(uid="player", rank=96),
                SimpleNamespace(uid="other", rank=97),
                SimpleNamespace(uid="player", rank=98)]
        self.assertEqual([row.rank for row in scope["_unique_cf_rankings"](rows)], [96, 97])

    def test_latest_range_reads_one_same_second_batch(self):
        sql_source = Path(__file__).resolve().parents[1] / "src/plugins/pjsk/_sk_sql.py"
        sql_module = ast.parse(sql_source.read_text(encoding="utf-8"), filename=str(sql_source))
        latest_fn = next(
            node for node in sql_module.body
            if isinstance(node, ast.AsyncFunctionDef) and node.name == "query_latest_ranking"
        )
        db = sqlite3.connect(":memory:")
        try:
            db.execute("CREATE TABLE ranking (id INTEGER PRIMARY KEY, uid TEXT, name TEXT, score INTEGER, rank INTEGER, ts INTEGER)")
            db.execute("CREATE INDEX idx_ranking_rank_ts ON ranking (rank, ts)")
            db.executemany("INSERT INTO ranking (uid, name, score, rank, ts) VALUES (?, ?, ?, ?, ?)", [
                ("A", "A", 1000, 96, 1790390497),
                ("B", "B", 1000, 98, 1790390497),
                ("B", "B", 1000, 96, 1790390497),
                ("A", "A", 1000, 98, 1790390497),
            ])

            class AsyncCursor:
                def __init__(self, cursor):
                    self.cursor = cursor

                async def fetchall(self):
                    return self.cursor.fetchall()

                async def close(self):
                    self.cursor.close()

            class AsyncConnection:
                calls = 0

                async def execute(self, sql, params):
                    self.calls += 1
                    return AsyncCursor(db.execute(sql, params))

            connection = AsyncConnection()

            async def get_conn(*_args, **_kwargs):
                return connection

            class Ranking:
                @staticmethod
                def from_row(row):
                    return SimpleNamespace(uid=row[1], rank=row[4])

            scope = {"List": list, "Ranking": Ranking, "get_conn": get_conn}
            exec(compile(ast.Module(body=[latest_fn], type_ignores=[]), str(sql_source), "exec"), scope)
            rows = asyncio.run(scope["query_latest_ranking"]("cn", 180, [98, 96, 97]))
            self.assertEqual([(row.rank, row.uid) for row in rows], [(98, "A"), (96, "B")])
            self.assertEqual(connection.calls, 1)
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main()
