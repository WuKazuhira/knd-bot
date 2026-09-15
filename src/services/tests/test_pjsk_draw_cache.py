"""pjsk-draw 独立进程主数据缓存与卡牌分类回退测试。"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import pathlib
import sys
import tempfile
import types
import unittest
from unittest.mock import AsyncMock, patch

from PIL import Image


def _load_local_data_module():
    src_root = pathlib.Path(__file__).resolve().parents[1]
    services_root = src_root
    project_src = services_root.parent
    pjsk_draw_root = services_root / "pjsk_draw"
    if str(project_src) not in sys.path:
        sys.path.insert(0, str(project_src))

    services_pkg = sys.modules.get("services")
    if services_pkg is None:
        services_pkg = types.ModuleType("services")
        services_pkg.__path__ = [str(services_root)]
        sys.modules["services"] = services_pkg
    pjsk_pkg = sys.modules.get("services.pjsk_draw")
    if pjsk_pkg is None:
        pjsk_pkg = types.ModuleType("services.pjsk_draw")
        pjsk_pkg.__path__ = [str(pjsk_draw_root)]
        sys.modules["services.pjsk_draw"] = pjsk_pkg

    name = "services.pjsk_draw.local_data"
    module = sys.modules.get(name)
    if module is None:
        spec = importlib.util.spec_from_file_location(name, pjsk_draw_root / "local_data.py")
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return module


def _load_card_module():
    src_root = pathlib.Path(__file__).resolve().parents[1]
    services_root = src_root
    project_src = services_root.parent
    pjsk_draw_root = services_root / "pjsk_draw"
    for path in (project_src, project_src.parent):
        if str(path) not in sys.path:
            sys.path.insert(0, str(path))

    name = "services.pjsk_draw.card"
    module = sys.modules.get(name)
    if module is None:
        spec = importlib.util.spec_from_file_location(name, pjsk_draw_root / "card.py")
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return module


class PjskDrawCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        self.mod = _load_local_data_module()
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tempdir.name)
        (self.root / "jp").mkdir()
        self.old_path = self.mod.ONDEMAND_PATH
        self.old_server_map = self.mod.SERVER_MAP
        self.mod.ONDEMAND_PATH = self.root
        self.mod.SERVER_MAP = {0: "jp"}
        with self.mod._MASTER_CACHE_LOCK:
            self.mod._MASTER_CACHE.clear()
            self.mod._INDEX_CACHE.clear()
            self.mod._MASTER_CACHE_BYTES = 0

    def tearDown(self) -> None:
        with self.mod._MASTER_CACHE_LOCK:
            self.mod._MASTER_CACHE.clear()
            self.mod._INDEX_CACHE.clear()
            self.mod._MASTER_CACHE_BYTES = 0
        self.mod.ONDEMAND_PATH = self.old_path
        self.mod.SERVER_MAP = self.old_server_map
        self.tempdir.cleanup()

    def _write(self, filename: str, data) -> pathlib.Path:
        path = self.root / "jp" / filename
        path.write_text(json.dumps(data), encoding="utf-8")
        return path

    def test_loading_one_file_does_not_evict_another(self) -> None:
        self._write("cards.json", [{"id": 1, "name": "a"}])
        self._write("skills.json", [{"id": 2, "name": "b"}])

        cards = self.mod.load_master_data("cards.json")
        skills = self.mod.load_master_data("skills.json")

        self.assertIs(cards, self.mod.load_master_data("cards.json"))
        self.assertIs(skills, self.mod.load_master_data("skills.json"))

    def test_mtime_or_size_change_refreshes_only_changed_file(self) -> None:
        cards_path = self._write("cards.json", [{"id": 1}])
        self._write("skills.json", [{"id": 2}])
        cards_before = self.mod.load_master_data("cards.json")
        skills_before = self.mod.load_master_data("skills.json")

        cards_path.write_text(json.dumps([{"id": 1}, {"id": 3}]), encoding="utf-8")
        stat = cards_path.stat()
        os.utime(cards_path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))

        cards_after = self.mod.load_master_data("cards.json")
        skills_after = self.mod.load_master_data("skills.json")
        self.assertIsNot(cards_before, cards_after)
        self.assertIs(skills_before, skills_after)
        self.assertEqual(len(cards_after), 2)

    def test_id_index_is_cached_and_cardtype_matches_legacy_semantics(self) -> None:
        self._write("cards.json", [{"id": 11}, {"id": 12}])
        first = self.mod.master_data_by_id("cards.json")
        second = self.mod.master_data_by_id("cards.json")
        self.assertIs(first, second)
        self.assertEqual(first[11]["id"], 11)

        costume3ds = [
            {"id": 10, "partType": "hair"},
            {"id": 11, "partType": "body"},
        ]
        card_costumes = [
            {"cardId": 100, "costume3dId": 10},
            {"cardId": 200, "costume3dId": 11},
        ]
        self.assertEqual(self.mod._cardtype(100, card_costumes, costume3ds), 1)
        self.assertEqual(self.mod._cardtype(200, card_costumes, costume3ds), 0)

    def test_missing_asset_downloads_through_helper_before_reading(self) -> None:
        target = self.root / "jp" / "startapp" / "thumbnail" / "chara" / "missing.png"

        async def fake_download(path: str, raw: str, pjsk_type: int) -> bool:
            self.assertEqual((path, raw, pjsk_type), ("startapp/thumbnail/chara", "missing.png", 0))
            target.parent.mkdir(parents=True, exist_ok=True)
            Image.new("RGBA", (2, 2), (1, 2, 3, 255)).save(target)
            return True

        with patch.object(self.mod, "_request_asset_download", new=AsyncMock(side_effect=fake_download)) as download:
            image = asyncio.run(self.mod.get_asset("startapp/thumbnail/chara", "missing.png"))

        download.assert_awaited_once_with("startapp/thumbnail/chara", "missing.png", 0)
        self.assertIsNotNone(image)
        self.assertEqual(image.getpixel((0, 0)), (1, 2, 3, 255))

    def test_download_false_does_not_call_helper(self) -> None:
        with patch.object(self.mod, "_request_asset_download", new=AsyncMock()) as download:
            image = asyncio.run(
                self.mod.get_asset("startapp/thumbnail/chara", "missing.png", download=False)
            )

        download.assert_not_awaited()
        self.assertIsNone(image)

    def test_missing_card_thumbnail_is_not_cached_as_gray_placeholder(self) -> None:
        card_mod = _load_card_module()
        card = {
            "id": 100,
            "assetbundleName": "res001_no001",
            "cardRarityType": "rarity_4",
            "attr": "cool",
        }

        def fake_open(_path, mode=None, copy=True, size=None):
            return Image.new(mode or "RGBA", size or (64, 64), (255, 255, 255, 255))

        with (
            patch.object(card_mod, "get_cached_render_image", return_value=None),
            patch.object(card_mod, "get_pjsk_asset_cached", new=AsyncMock(return_value=None)),
            patch.object(card_mod, "open_pjsk_image", side_effect=fake_open),
            patch.object(card_mod, "put_cached_render_image") as cache_put,
        ):
            image = asyncio.run(card_mod.cardthumnail(100, cards=[card]))

        self.assertEqual(image.size, (64, 64))
        cache_put.assert_not_called()


if __name__ == "__main__":
    unittest.main()
