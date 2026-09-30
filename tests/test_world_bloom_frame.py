"""World Bloom 参数、主数据映射、默认卡牌、对称拼装、牌子挑选与资源错误回归测试。"""

import ast
import asyncio
import random
import sys
import unittest
from unittest.mock import patch
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Sequence, Tuple


ROOT = Path(__file__).resolve().parents[1]
RENDERER_PATH = ROOT / "src/services/pjsk_draw/renderers/world_bloom_frame.py"
PLUGIN_PATH = ROOT / "src/plugins/pjsk/world_bloom/__init__.py"
HELP_DATA_SOURCE_PATH = ROOT / "src/basic_plugins/help/data_source.py"


def load_selected(path, names, namespace):
    module = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    selected = []
    for node in module.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names:
            selected.append(node)
        elif isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id in names for target in node.targets
        ):
            selected.append(node)
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(path), "exec"), namespace)
    return namespace


try:
    from PIL import Image, ImageDraw, ImageFont

    PIL_AVAILABLE = True
except ImportError:
    Image = SimpleNamespace(Image=object)
    ImageDraw = None
    ImageFont = None
    PIL_AVAILABLE = False


RENDERER_NAMES = {
    "PLAYER_FRAME_ASSET_ROOT", "MEMBER_CUTOUT_ASSET_ROOT", "PART_LAYER_NAMES",
    "PART_FRAME_SIZE", "PART_FRAME_INNER", "FRAME_SEGMENT_GAP", "FRAME_UPPER_TOP",
    "LEVEL_BY_RARITY", "STAR_BY_RARITY",
    "frame_id_for_color", "expand_character_ids", "resolve_frame_group",
    "resolve_frame_parts", "frame_part_asset_paths", "frame_base_asset_path",
    "require_asset", "choose_frame_card", "card_cutout_path",
    "_solid_bbox", "_fit_cover", "_compose_ring", "_world_bloom_honor_choices", "_png_bytes",
}
renderer_scope = load_selected(
    RENDERER_PATH,
    RENDERER_NAMES,
    {
        "Any": Any, "Dict": Dict, "List": List, "Optional": Optional,
        "Sequence": Sequence, "Tuple": Tuple, "Image": Image,
        "ImageDraw": ImageDraw, "ImageFont": ImageFont, "BytesIO": BytesIO, "random": random,
    },
)
help_usage_scope = load_selected(
    HELP_DATA_SOURCE_PATH,
    {"get_plugin_help"},
    {
        "Optional": Optional,
        "_get_help_module": lambda _message: "petpet",
        "_resolve_plugin": lambda _module: SimpleNamespace(
            module=SimpleNamespace(
                __plugin_command_usage__={"羁绊牌": "羁绊牌专属用法"},
                __plugin_usage__="petpet 全部用法",
            )
        ),
        "_help_command_candidates": lambda message: [message],
        "_get_result_by_usage": lambda usage: usage,
    },
)
plugin_scope = load_selected(PLUGIN_PATH, {"FRAME_IDS", "parse_world_bloom_args"}, {})


class WorldBloomFrameTests(unittest.TestCase):
    def test_color_aliases_map_to_frame_ids(self):
        frame_id_for_color = renderer_scope["frame_id_for_color"]
        for color in ("100", "tb"):
            self.assertEqual(frame_id_for_color(color), 20001)
        for color in ("1000", "tk"):
            self.assertEqual(frame_id_for_color(color), 20002)
        with self.assertRaises(ValueError):
            frame_id_for_color("500")

    def test_python_command_parser_maps_colors_roles_and_name(self):
        parse = plugin_scope["parse_world_bloom_args"]
        aliases = {"mnr": 5, "hrk": 6, "airi": 7, "szk": 8, "an": 10, "emu": 14}
        args = parse("wl3 tk mnr hrk airi szk an emu 123 花后 名=小明", aliases)
        self.assertEqual(args["frame_id"], 20002)
        self.assertEqual(args["character_ids"], [5, 6, 7, 8, 10, 14])
        self.assertEqual((args["card_id"], args["trained"]), (123, True))
        self.assertEqual(args["player_name"], "小明")
        before = parse("wl3 100 mnr 123 花前", aliases)
        self.assertEqual((before["frame_id"], before["character_ids"], before["trained"]),
                         (20001, [5] * 6, False))
        self.assertEqual(before["player_name"], "")
        quoted = parse('wl3 100 mnr name="Miku Fan"', aliases)
        self.assertEqual(quoted["player_name"], "Miku Fan")

    def test_one_or_six_roles_are_required(self):
        expand = renderer_scope["expand_character_ids"]
        self.assertEqual(expand([5]), [5] * 6)
        self.assertEqual(expand([1, 2, 3, 4, 5, 6]), [1, 2, 3, 4, 5, 6])
        with self.assertRaisesRegex(ValueError, "1 个或 6 个"):
            expand([1, 2])

    def test_six_roles_map_to_all_nine_vertical_part_layers(self):
        resolve_parts = renderer_scope["resolve_frame_parts"]
        part_rows = [
            {"id": 20000 + char_id, "playerFrameGroupId": 2, "gameCharacterId": char_id}
            for char_id in range(1, 27)
        ]
        selected = resolve_parts(2, [1, 2, 3, 4, 5, 6], part_rows)
        self.assertEqual([row["id"] for row in selected], list(range(20001, 20007)))
        paths = renderer_scope["frame_part_asset_paths"]("frame_0002", 20001, selected)
        self.assertEqual(len(paths), 9)
        self.assertEqual(
            [row[3] for row in paths],
            [
                "frame_parts1_center.png", "frame_parts1_left.png", "frame_parts1_right.png",
                "frame_parts2_left.png", "frame_parts3_right.png", "frame_parts4_left.png",
                "frame_parts5_right.png", "frame_parts6_left.png", "frame_parts6_right.png",
            ],
        )
        self.assertEqual(paths[0][2], "startapp/player_frame/frame_0002/20001/20001/vertical")
        self.assertEqual(paths[3][2], "startapp/player_frame/frame_0002/20001/20002/vertical")

    def test_frame_and_role_masterdata_must_map(self):
        resolve_group = renderer_scope["resolve_frame_group"]
        frames = [{"id": 20001, "playerFrameGroupId": 2, "partsCount": 6}]
        groups = [{"id": 2, "assetbundleName": "frame_0002"}]
        frame, group = resolve_group(20001, frames, groups)
        self.assertEqual((frame["id"], group["assetbundleName"]), (20001, "frame_0002"))
        with self.assertRaisesRegex(ValueError, "边框组"):
            resolve_group(20001, frames, [])
        with self.assertRaisesRegex(ValueError, "part1"):
            renderer_scope["resolve_frame_parts"](2, [1], [])

    def test_card_before_after_leader_and_character_fallback(self):
        choose = renderer_scope["choose_frame_card"]
        path_for_card = renderer_scope["card_cutout_path"]
        cards = [
            {"id": 10, "assetbundleName": "res001_no010", "cardRarityType": "rarity_4", "characterId": 1},
            {"id": 11, "assetbundleName": "res001_no011", "cardRarityType": "rarity_4", "characterId": 1},
            {"id": 12, "assetbundleName": "res002_no012", "cardRarityType": "rarity_2", "characterId": 2},
        ]
        leader, trained = choose(cards, leader_card_id=11, leader_trained=True)
        self.assertEqual(leader["id"], 11)
        self.assertTrue(trained)
        self.assertEqual(path_for_card(leader, trained),
                         ("startapp/character/member_cutout/res001_no011", "after_training.png"))
        before, trained = choose(cards, card_id=10, requested_trained=False)
        self.assertFalse(trained)
        self.assertEqual(path_for_card(before, trained)[1], "normal.png")
        low_rarity, trained = choose(cards, card_id=12, requested_trained=True)
        self.assertFalse(trained)
        # 随机时按角色过滤：角色 2 只有一张卡
        picked, _ = choose(cards, character_id=2, chooser=lambda values: values[0])
        self.assertEqual(picked["id"], 12)
        with self.assertRaisesRegex(ValueError, "卡牌 ID 999"):
            choose(cards, card_id=999)

    def test_world_bloom_honor_choices_only_top100_of_that_character(self):
        pick = renderer_scope["_world_bloom_honor_choices"]
        world_blooms = [
            {"gameCharacterId": 18, "worldBloomChapterType": "game_character", "eventId": 112, "chapterNo": 1},
            {"gameCharacterId": 18, "worldBloomChapterType": "game_character", "eventId": 112, "chapterNo": 2},
            {"gameCharacterId": 5, "worldBloomChapterType": "game_character", "eventId": 112, "chapterNo": 3},
            {"worldBloomChapterType": "finale", "eventId": 112},
        ]
        events = [{"id": 112, "name": "EV"}]
        honor_groups = [
            {"id": 352, "name": "EV 総合", "honorType": "event"},
            {"id": 353, "name": "EV チャプター1", "honorType": "event"},
            {"id": 354, "name": "EV チャプター2", "honorType": "event"},
            {"id": 900, "name": "别的活动 総合", "honorType": "event"},
        ]
        honors = [
            {"id": 3374, "groupId": 352, "assetbundleName": "honor_top_000100"},
            {"id": 3393, "groupId": 353, "assetbundleName": "honor_top_000100_ev_cp1"},
            {"id": 3400, "groupId": 354, "assetbundleName": "honor_top_000100_ev_cp2"},
            {"id": 5000, "groupId": 352, "assetbundleName": "honor_top_000020"},   # 非 TOP100
            {"id": 6000, "groupId": 900, "assetbundleName": "honor_top_000100"},   # 非该角色活动
        ]
        choices = pick(18, world_blooms, events, honor_groups, honors)
        self.assertEqual({h["id"] for h in choices}, {3374, 3393, 3400})
        self.assertEqual(pick(26, world_blooms, events, honor_groups, honors), [])

    def test_help_uses_native_help_logic_not_a_custom_renderer(self):
        self.assertEqual(help_usage_scope["get_plugin_help"]("羁绊牌"), "羁绊牌专属用法")
        plugin_source = PLUGIN_PATH.read_text(encoding="utf-8")
        self.assertNotIn("__plugin_help_renderer__", plugin_source)
        self.assertIn("get_plugin_help", plugin_source)

        tree = ast.parse(plugin_source)
        commands, aliases = [], []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name) or node.func.id != "on_command":
                continue
            if node.args and isinstance(node.args[0], ast.Constant):
                commands.append(node.args[0].value)
            for keyword in node.keywords:
                if keyword.arg == "aliases" and isinstance(keyword.value, ast.Dict):
                    aliases.extend(
                        key.value for key in keyword.value.keys if isinstance(key, ast.Constant)
                    )
        self.assertEqual(commands, ["wl边框", "cnwl边框", "twwl边框"])
        self.assertFalse(any(str(alias).endswith("帮助") for alias in aliases))

    def test_python_parser_rejects_invalid_input_and_missing_role_mapping(self):
        parse = plugin_scope["parse_world_bloom_args"]
        aliases = {"mnr": 5, "hrk": 6}
        for raw in (
            "wl2 100 mnr", "wl3 500 mnr", "wl3 100 mnr hrk",
            "wl3 100 unknown", "wl3 100 mnr 12 unknown",
        ):
            with self.subTest(raw=raw):
                self.assertIsInstance(parse(raw, aliases), str)

    def test_missing_asset_has_clear_error(self):
        with self.assertRaisesRegex(FileNotFoundError, "part3 资源缺失"):
            renderer_scope["require_asset"](None, "边框 part3 资源缺失")

    @unittest.skipUnless(PIL_AVAILABLE, "Pillow is required for image composition tests")
    def test_solid_bbox_ignores_soft_glow(self):
        image = Image.new("RGBA", (100, 100), (10, 20, 30, 20))
        image.putpixel((40, 30), (255, 255, 255, 255))
        self.assertEqual(renderer_scope["_solid_bbox"](image), (40, 30, 41, 31))

    @unittest.skipUnless(PIL_AVAILABLE, "Pillow is required for image composition tests")
    def test_compose_ring_is_left_right_symmetric(self):
        # 真实资源左右部件是镜像同形的；这里用左右同尺寸的实体块校验镜像放置逻辑。
        sizes = {
            (1, "center"): (90, 56),
            (1, "left"): (110, 64), (1, "right"): (110, 64),
            (2, "left"): (30, 280), (3, "right"): (30, 280),
            (4, "left"): (30, 260), (5, "right"): (30, 260),
            (6, "left"): (60, 60), (6, "right"): (60, 60),
        }
        # 每块用不透明矩形填充其实体区域，便于校验镜像放置。
        layers = []
        for (position, side), (w, h) in sizes.items():
            im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
            ImageDraw.Draw(im).rectangle((0, 0, w - 1, h - 1), fill=(80, 160, 220, 255))
            layers.append((position, side, im))
        ring = renderer_scope["_compose_ring"](layers)
        self.assertEqual(ring.size, tuple(renderer_scope["PART_FRAME_SIZE"]))
        width, height = ring.size
        alpha = list(ring.getchannel("A").getdata())
        total = diff = 0
        for y in range(0, height, 4):
            base = y * width
            for x in range(0, width // 2, 4):
                diff += abs(alpha[base + x] - alpha[base + (width - 1 - x)])
                total += 1
        # 左右镜像后平均差异应很小（放置完全对称）。
        self.assertLess(diff / total, 3.0)

    @unittest.skipUnless(PIL_AVAILABLE, "Pillow is required for renderer integration tests")
    def test_registered_renderer_produces_full_card_and_reports_missing_assets(self):
        try:
            import gino  # noqa: F401
        except ImportError:
            self.skipTest("renderer integration dependencies are provided by the bot image")
        source_root = str(ROOT / "src")
        if source_root not in sys.path:
            sys.path.insert(0, source_root)
        from importlib import import_module

        renderer = import_module("services.pjsk_draw.renderers.world_bloom_frame")
        from services.pjsk_draw.registry import renderer_names

        self.assertIn("world_bloom_frame", renderer_names())
        masterdata = {
            "playerFrames.json": [
                {"id": frame_id, "playerFrameGroupId": 2, "partsCount": 6}
                for frame_id in (20001, 20002)
            ],
            "playerFrameGroups.json": [{"id": 2, "assetbundleName": "frame_0002"}],
            "playerFrameParts.json": [
                {"id": 20000 + char_id, "playerFrameGroupId": 2, "gameCharacterId": char_id}
                for char_id in range(1, 27)
            ],
            "cards.json": [
                {"id": 123, "assetbundleName": "res001_no123", "cardRarityType": "rarity_4", "characterId": 1}
            ],
        }

        class FakeContext:
            async def async_load_master_data(self, filename, _pjsk_type):
                return masterdata.get(filename, [])

            def getcharaname(self, character_id, _characters, pjsk_type=0):
                return f"角色{character_id:02d}"

        asset_calls = []
        fail_part_three = {"enabled": False}

        async def get_asset(category, filename, **_kwargs):
            asset_calls.append((category, filename))
            if fail_part_three["enabled"] and filename == "frame_parts3_right.png":
                return None
            dimensions = {
                "frame_parts1_center.png": (108, 90),
                "frame_parts1_left.png": (130, 352),
                "frame_parts1_right.png": (130, 352),
                "frame_parts2_left.png": (130, 352),
                "frame_parts3_right.png": (130, 352),
                "frame_parts4_left.png": (226, 352),
                "frame_parts5_right.png": (72, 352),
                "frame_parts6_left.png": (226, 352),
                "frame_parts6_right.png": (72, 352),
                "after_training.png": (600, 576),
            }
            return Image.new("RGBA", dimensions.get(filename, (100, 160)), (30, 40, 50, 255))

        payload = {
            "pjsk_type": 0,
            "frame_id": 20001,
            "character_ids": [1, 2, 3, 4, 5, 6],
            "card_id": 123,
            "trained": True,
            "player_name": "测试",
        }
        expected_size = tuple(s * renderer.OUTPUT_SCALE for s in renderer.PART_FRAME_SIZE)
        with patch.object(renderer, "get_context", return_value=FakeContext()), \
             patch.object(renderer, "get_pjsk_asset_cached", side_effect=get_asset):
            output = asyncio.run(renderer.render_world_bloom_frame(payload))
            self.assertEqual(len([name for _, name in asset_calls if name.startswith("frame_parts")]), 9)
            self.assertIn(("startapp/character/member_cutout/res001_no123", "after_training.png"), asset_calls)
            self.assertEqual(Image.open(BytesIO(output)).size, expected_size)

            fail_part_three["enabled"] = True
            with self.assertRaisesRegex(FileNotFoundError, "part3 right 资源缺失"):
                asyncio.run(renderer.render_world_bloom_frame(payload))


if __name__ == "__main__":
    unittest.main()
