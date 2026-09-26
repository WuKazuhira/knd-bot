"""捡车牌群开关与消息处理的回归测试（python -m unittest discover -s tests）。"""

import asyncio
import base64
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from nonebot.adapters.onebot.v11 import GroupMessageEvent, Message, PrivateMessageEvent


ROOT = Path(__file__).resolve().parents[1]


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def event(text, original=None, group=True, segments=None):
    original = original if original is not None else text
    data = {
        "time": 1, "self_id": 42, "post_type": "message", "sub_type": "normal",
        "user_id": 43, "message_id": 44, "message": Message(original),
        "raw_message": original,
        "font": 0, "sender": {"user_id": 43}, "to_me": original != text,
        "message_type": "group" if group else "private",
    }
    if group:
        data["group_id"] = 123
    parsed = (GroupMessageEvent if group else PrivateMessageEvent).model_validate(data)
    # 适配器先复制原始消息，再由昵称预处理修改 message。
    parsed.message = segments or Message(text)
    return parsed


class PlatePickerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.closed = {"plate_picker"}
        cls.manager = SimpleNamespace()
        cls.manager.get_task_data = lambda: {}
        cls.manager.get_plugin_status = lambda module, group_id, is_super=False: module not in cls.closed
        cls.manager.block_plugin = lambda module, group_id: cls.closed.add(module)
        cls.manager.unblock_plugin = lambda module, group_id: cls.closed.discard(module)
        settings = {
            "plate_picker": {"cmd": ["捡车牌", "群相关"], "default_status": False},
            "other": {"cmd": ["其他功能", "群相关"], "default_status": True},
        }
        settings_manager = SimpleNamespace(
            get_data=lambda: settings,
            get_plugin_module=lambda cmd, is_all=False: [
                name for name, config in settings.items() if cmd in config["cmd"]
            ],
        )
        cls.global_manager = SimpleNamespace(block_plugin=MagicMock(), unblock_plugin=MagicMock())
        cls.handlers = []

        class Matcher:
            send = AsyncMock()

            def handle(self):
                def register(handler):
                    cls.handlers.append(handler)
                    return handler
                return register

        cls.matcher = Matcher()
        replacements = {}

        def stub(name, **attrs):
            module = ModuleType(name)
            module.__dict__.update(attrs)
            replacements[name] = module

        stub("manager", group_manager=cls.manager, plugins2settings_manager=settings_manager,
             plugins_manager=cls.global_manager, Config=MagicMock())
        stub("services.log", logger=MagicMock())
        stub("config.path_config", DATA_PATH=ROOT / "data")
        stub("models.group_member_info", GroupInfoUser=MagicMock())
        stub("models.level_user", LevelUser=MagicMock())
        stub("services.db_context", db=MagicMock())
        stub("utils.http_utils", AsyncHttpx=MagicMock())
        stub("utils.imageutils", BuildImage=MagicMock())
        stub("utils.message_builder", image=MagicMock())
        stub("utils.utils", get_message_text=lambda data: " ".join(
            segment["data"]["text"].strip() for segment in json.loads(data)["message"]
            if segment["type"] == "text"
        ).strip(), is_number=str.isdigit, get_matchers=MagicMock())

        with patch.dict(sys.modules, replacements), patch("nonebot.on_message", return_value=cls.matcher):
            cls.rule = load_module("plate_switch_rule_test", "src/basic_plugins/admin_bot_manage/rule.py")
            cls.source = load_module("plate_switch_source_test", "src/basic_plugins/admin_bot_manage/_data_source.py")
            cls.plate = load_module("plate_picker_test", "src/plugins/plate_picker/__init__.py")

    def setUp(self):
        self.closed.clear()
        self.closed.add("plate_picker")
        self.rule.cmd = []
        self.global_manager.block_plugin.reset_mock()
        self.global_manager.unblock_plugin.reset_mock()
        self.matcher.send.reset_mock()
        images_dir = tempfile.TemporaryDirectory()
        self.addCleanup(images_dir.cleanup)
        image_path_patch = patch.object(self.plate, "_PICK_IMAGES_PATH", Path(images_dir.name))
        image_path_patch.start()
        self.addCleanup(image_path_patch.stop)

    def switch(self, text, original=None, group=True):
        state = {}
        matched = self.rule.switch_rule(event(text, original, group), state)
        return matched, state

    def test_nickname_removed_but_original_command_works(self):
        for action in ("开启", "关闭"):
            with self.subTest(action=action):
                self.assertEqual(self.switch(f"{action} 捡车牌", f"knd {action} 捡车牌"),
                                 (True, {"cmd": f"{action}捡车牌"}))

    def test_private_or_non_explicit_commands_do_not_toggle(self):
        for message, original, group in (
            ("开启 捡车牌", "开启 捡车牌", True),
            ("关闭 捡车牌", "关闭 捡车牌", True),
            ("开启 捡车牌", "knd 开启 捡车牌", False),
            ("knd 开启 捡车牌 123", None, True),
        ):
            with self.subTest(message=message, original=original, group=group):
                self.assertEqual(self.switch(message, original, group), (False, {}))
        self.assertEqual(self.switch("开启 其他功能"), (True, {"cmd": "开启其他功能"}))

    def test_only_five_ascii_digits_in_one_group_text_segment(self):
        self.assertTrue(self.plate.plate_message(event("12345")))
        for text in ("1234", "123456", "１２３４５", "١٢٣٤٥", " 12345", "12345\n"):
            with self.subTest(text=text):
                self.assertFalse(self.plate.plate_message(event(text)))
        self.assertFalse(self.plate.plate_message(event("12345", group=False)))
        self.assertFalse(self.plate.plate_message(event("12345", segments=Message("12345") + Message("6"))))

    def test_default_closed_and_explicit_group_switch(self):
        bot = SimpleNamespace(get_group_info=AsyncMock(return_value={"group_name": "原群名"}),
                              set_group_name=AsyncMock())
        message = event("12345")
        asyncio.run(self.handlers[0](bot, message))
        bot.get_group_info.assert_not_awaited()
        self.matcher.send.assert_not_awaited()
        self.assertIn("开启 捡车牌", asyncio.run(self.source.change_group_switch("开启捡车牌", 123)))
        asyncio.run(self.handlers[0](bot, message))
        bot.set_group_name.assert_awaited_once_with(group_id=123, group_name="【12345】原群名")
        reply = self.matcher.send.await_args.args[0]
        self.assertEqual(len(reply), 1)
        self.assertEqual(reply[0].data["text"], "请...记得补火")
        self.assertIn("关闭 捡车牌", asyncio.run(self.source.change_group_switch("关闭捡车牌", 123)))
        asyncio.run(self.handlers[0](bot, message))
        bot.set_group_name.assert_awaited_once()
        self.matcher.send.assert_awaited_once()

    def test_replaces_existing_plate_prefixes_without_changing_other_text(self):
        self.closed.clear()
        for previous, expected in (
            ("【30726】【33121】终章", "【12345】终章"),
            ("【33121】终章", "【12345】终章"),
            ("【旧群名】【33121】终章", "【12345】【旧群名】【33121】终章"),
            ("【33121】终章【54321】", "【12345】终章【54321】"),
            ("终章", "【12345】终章"),
        ):
            with self.subTest(previous=previous):
                bot = SimpleNamespace(get_group_info=AsyncMock(return_value={"group_name": previous}),
                                      set_group_name=AsyncMock())
                asyncio.run(self.handlers[0](bot, event("12345")))
                bot.set_group_name.assert_awaited_once_with(group_id=123, group_name=expected)

    def test_success_replies_with_random_pick_image_and_reminder(self):
        self.closed.clear()
        images_dir = self.plate._PICK_IMAGES_PATH
        first = images_dir / "first.PNG"
        second = images_dir / "second.webp"
        first.write_bytes(b"first-image")
        second.write_bytes(b"second-image")
        (images_dir / "not-an-image.txt").write_bytes(b"ignored")
        bot = SimpleNamespace(get_group_info=AsyncMock(return_value={"group_name": "终章"}),
                              set_group_name=AsyncMock())
        with patch.object(self.plate.random, "choice", return_value=second) as choose:
            asyncio.run(self.handlers[0](bot, event("12345")))
        self.assertEqual(set(choose.call_args.args[0]), {first, second})
        bot.set_group_name.assert_awaited_once_with(group_id=123, group_name="【12345】终章")
        self.matcher.send.assert_awaited_once()
        reply = self.matcher.send.await_args.args[0]
        self.assertEqual([segment.type for segment in reply], ["text", "image"])
        self.assertEqual(reply[0].data["text"], "请...记得补火")
        self.assertEqual(base64.b64decode(reply[1].data["file"].removeprefix("base64://")), b"second-image")

    def test_rename_failure_does_not_send_success_reply(self):
        self.closed.clear()
        bot = SimpleNamespace(get_group_info=AsyncMock(return_value={"group_name": "终章"}),
                              set_group_name=AsyncMock(side_effect=RuntimeError("无管理权限")))
        asyncio.run(self.handlers[0](bot, event("12345")))
        self.matcher.send.assert_awaited_once_with("捡车牌失败：无法修改群名称，请确认机器人拥有群管理权限。")

    def test_bulk_and_global_switch_cannot_enable_plate_picker(self):
        asyncio.run(self.source.change_group_switch("开启全部功能", 123))
        self.assertIn("plate_picker", self.closed)
        asyncio.run(self.source.change_group_switch("开启群相关", 123))
        self.assertIn("plate_picker", self.closed)
        asyncio.run(self.source.set_plugin_status([], "开启捡车牌"))
        self.global_manager.unblock_plugin.assert_not_called()


if __name__ == "__main__":
    unittest.main()
