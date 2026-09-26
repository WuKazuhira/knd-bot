"""验证 Python 回退查房不会把分数回退后的恢复重复计为周回。"""

import ast
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


if __name__ == "__main__":
    unittest.main()
