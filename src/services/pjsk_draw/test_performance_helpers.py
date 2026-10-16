import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

os.environ.setdefault('KNDBOT_SKIP_PJSK_PLUGIN_AUTOLOAD', '1')

import nonebot

nonebot.init()

from plugins.pjsk import _utils as plugin_utils
from services.pjsk_draw import local_data
from services.pjsk_draw.renderers.song import (
    _music_vocal_cards,
    _music_vocal_records,
    _musiclength,
)
from utils.pjsk_master_data import index_by_id as shared_index_by_id
from utils.pjsk_master_data import normalize_master_data


class SharedMasterDataTransformTests(unittest.TestCase):
    def test_unwrap_order_numeric_maps_and_metadata(self):
        wrapped = normalize_master_data({'items': [{'id': 2}], 'data': [{'id': 1}]})
        self.assertEqual(wrapped.wrapper_key, 'data')
        self.assertEqual(wrapped.data, [{'id': 1}])

        numeric_map = normalize_master_data({'2': {'id': 2}, '1': {'id': 1}})
        self.assertEqual(numeric_map.data, [{'id': 2}, {'id': 1}])
        self.assertEqual(normalize_master_data({}).data, [])

        single = normalize_master_data({'metadata': 'ignored', 'rows': [{'id': 3}]})
        self.assertTrue(single.unwrapped_single_container)
        self.assertEqual(single.data, [{'id': 3}])

        filtered = normalize_master_data({'metadata': 'ignored', 'left': [1], 'right': [2]})
        self.assertEqual(filtered.data, [[1], [2]])
        self.assertEqual(filtered.filtered_metadata_count, 1)

    def test_index_duplicate_policies_and_custom_keys(self):
        rows = [
            {'id': 1, 'name': 'first'},
            None,
            {'name': 'missing id'},
            {'id': 1, 'name': 'last'},
        ]
        self.assertEqual(shared_index_by_id(rows, duplicate_policy='first')[1]['name'], 'first')
        self.assertEqual(shared_index_by_id(rows, duplicate_policy='last')[1]['name'], 'last')
        self.assertEqual(shared_index_by_id([{'code': 'a'}], key='code'), {'a': {'code': 'a'}})

    def test_both_loaders_share_wrapped_json_result(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / 'wrapped_master.json'
            path.write_text(
                json.dumps({'data': {'2': {'id': 2}, '1': {'id': 1}}}),
                encoding='utf-8',
            )
            with (
                patch.object(local_data, '_server_dir', return_value=root),
                patch.object(plugin_utils, 'get_server_data_path', return_value=root),
            ):
                plugin_result = plugin_utils.load_master_data('wrapped_master.json')
                local_result = local_data.load_master_data('wrapped_master.json')

        self.assertEqual(plugin_result, local_result)
        self.assertEqual(plugin_result, [{'id': 2}, {'id': 1}])

    def test_top_level_string_error_policies_remain_distinct(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / 'string_master.json'
            path.write_text(json.dumps('invalid master shape'), encoding='utf-8')
            with (
                patch.object(local_data, '_server_dir', return_value=root),
                patch.object(plugin_utils, 'get_server_data_path', return_value=root),
            ):
                with self.assertRaises(ValueError):
                    plugin_utils.load_master_data('string_master.json')
                self.assertEqual(local_data.load_master_data('string_master.json'), 'invalid master shape')


class FirstMatchMasterDataIndexTests(unittest.TestCase):
    def _write_rows(self, path: Path, rows: list[dict]) -> None:
        path.write_text(json.dumps(rows), encoding='utf-8')

    def test_local_index_is_first_match_and_refreshes_with_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / 'musics.json'
            self._write_rows(path, [{'id': 7, 'title': 'first'}, {'id': 7, 'title': 'duplicate'}])
            with patch.object(local_data, '_server_dir', return_value=root):
                self.assertEqual(local_data.master_data_first_by_id('musics.json')[7]['title'], 'first')
                self.assertEqual(local_data.master_data_by_id('musics.json')[7]['title'], 'duplicate')
                self._write_rows(path, [{'id': 7, 'title': 'refreshed record'}])
                self.assertEqual(local_data.master_data_first_by_id('musics.json')[7]['title'], 'refreshed record')

    def test_plugin_indexes_preserve_duplicate_policy_and_refresh_with_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / 'musics.json'
            self._write_rows(path, [{'id': 11, 'title': 'first'}, {'id': 11, 'title': 'duplicate'}])
            with patch.object(plugin_utils, 'get_server_data_path', return_value=root):
                self.assertEqual(plugin_utils.master_data_first_by_id('musics.json')[11]['title'], 'first')
                self.assertEqual(plugin_utils.master_data_by_id('musics.json')[11]['title'], 'duplicate')
                self._write_rows(path, [{'id': 11, 'title': 'refreshed record'}])
                self.assertEqual(plugin_utils.master_data_first_by_id('musics.json')[11]['title'], 'refreshed record')
                self.assertEqual(plugin_utils.master_data_by_id('musics.json')[11]['title'], 'refreshed record')


class MusicVocalSelectionTests(unittest.TestCase):
    def test_filters_one_song_without_changing_source_order(self):
        first = {'musicId': 3, 'seq': 2}
        other = {'musicId': 4, 'seq': 1}
        second = {'musicId': 3, 'seq': 1}
        rows = [first, other, None, second, {'musicId': 3, 'seq': 3}]

        self.assertEqual(_music_vocal_records(3, rows), [first, second, rows[-1]])
        self.assertIs(_music_vocal_records(3, rows)[0], first)

    def test_empty_vocal_selection(self):
        self.assertEqual(_music_vocal_records(99, [{'musicId': 3}]), [])

    def test_passed_vocals_avoid_loading_and_scanning_master_data_again(self):
        load_master_data = Mock(return_value=[])
        async_load_master_data = AsyncMock(side_effect=AssertionError('unexpected vocals reload'))
        context = SimpleNamespace(
            load_master_data=load_master_data,
            async_load_master_data=async_load_master_data,
        )
        with patch('services.pjsk_draw.renderers.song.get_context', return_value=context):
            length = asyncio.run(_musiclength(3, music_vocals=[]))
            cards = _music_vocal_cards(3, music_vocals=[])

        self.assertEqual(length, 0)
        self.assertEqual(cards, [])
        async_load_master_data.assert_not_awaited()
        self.assertEqual(
            [call.args[0] for call in load_master_data.call_args_list],
            ['gameCharacters.json', 'outsideCharacters.json'],
        )


if __name__ == '__main__':
    unittest.main()
