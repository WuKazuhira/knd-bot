"""上游 allium-deck server HTTP 契约转换测试。"""

from __future__ import annotations

import importlib.util
import pathlib
import unittest


_MOD_PATH = (
    pathlib.Path(__file__).resolve().parents[2]
    / "plugins"
    / "pjsk"
    / "deck"
    / "_allium_http_contract.py"
)


def _load_module():
    spec = importlib.util.spec_from_file_location("deck_http_contract_under_test", _MOD_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class DeckHttpContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.m = _load_module()

    def test_translate_options_uses_pr39_names(self) -> None:
        result = self.m.translate_options_for_http(
            {
                "live_type": "multi",
                "event_id": 123,
                "world_bloom_chapter_no": 2,
                "forced_leader_character_id": 7,
                "multi_live_teammate_power": 180000,
                "music_id": 10000,
                "algorithm": "dfs",
                "member": 5,
            }
        )
        self.assertEqual(result["liveType"], "multi")
        self.assertEqual(result["eventId"], 123)
        self.assertEqual(result["worldBloomEventTurn"], 2)
        self.assertEqual(result["forcedLeaderCharacterId"], 7)
        self.assertEqual(result["multiLiveTeammatePower"], 180000)
        self.assertEqual(result["musicId"], 10000)
        self.assertNotIn("algorithm", result)
        self.assertNotIn("member", result)

    def test_normalize_legacy_batch_response(self) -> None:
        result = self.m.normalize_http_decks(
            [
                {
                    "result": {
                        "decks": [
                            {"score": 12, "total_power": 34, "cards": [{"card_id": 5}]}
                        ]
                    }
                }
            ]
        )
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["score"], 12)
        self.assertEqual(result[0]["cards"][0]["card_id"], 5)

    def test_normalize_pr39_response_for_renderer(self) -> None:
        result = self.m.normalize_http_decks(
            {
                "region": "cn",
                "decks": [
                    {
                        "rank": 1,
                        "targetValue": 987654,
                        "totalPower": 123456,
                        "eventBonusTotal": 125.0,
                        "liveScore": 654321,
                        "eventPoint": 321,
                        "cards": [
                            {"cardId": 1, "skillScoreUp": 80, "masterRank": 5},
                            {"cardId": 2, "skillScoreUp": 60},
                        ],
                    }
                ],
            }
        )
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["score"], 987654)
        self.assertEqual(result[0]["total_power"], 123456)
        self.assertEqual(result[0]["event_bonus_rate"], 125.0)
        self.assertEqual(result[0]["cards"][0]["card_id"], 1)
        self.assertEqual(result[0]["cards"][0]["master_rank"], 5)


if __name__ == "__main__":
    unittest.main()
