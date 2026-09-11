"""go_ownership 命令所有权归一化与判定的单元测试。

覆盖 go-pjsk-bot 灰度互斥依赖的核心逻辑：
- 别名 / cn·tw 前缀 -> 规范名归一化
- owned 列表判定（on_command 类）
- 正则触发命令的原文兜底判定（on_regex 类）
- 空 owned 列表零影响、未知命令绝不误吞

用 importlib 从文件路径直接加载，避免 services 包 __init__ 的重依赖。
"""

import importlib.util
import os
import pathlib
import unittest

_MOD_PATH = pathlib.Path(__file__).resolve().parents[1] / "go_ownership.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("go_ownership_under_test", _MOD_PATH)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class GoOwnershipNormalizeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.m = _load_module()

    def test_canonical_alias_and_prefix(self) -> None:
        canon = self.m.pjsk_canonical_command
        self.assertEqual(canon("bind"), "bind")
        self.assertEqual(canon("绑定"), "bind")
        self.assertEqual(canon("cnbind"), "bind")
        self.assertEqual(canon("tw绑定"), "bind")
        self.assertEqual(canon("时速"), "sks")
        self.assertEqual(canon("cn时速"), "sks")
        self.assertEqual(canon("profile"), "烧烤档案")
        self.assertEqual(canon("remote"), "pjsk_remote")
        self.assertEqual(canon("live"), "pjsk_live")
        self.assertEqual(canon("remote状态"), "pjsk_remote_status")
        self.assertEqual(canon("pjsktoken状态"), "pjsk_remote_token")
        self.assertEqual(canon("pjsk上传token"), "pjsk_remote_token_upload")

    def test_canonical_unknown_returns_none(self) -> None:
        self.assertIsNone(self.m.pjsk_canonical_command("不存在xyz"))
        self.assertIsNone(self.m.pjsk_canonical_command(""))
        for trigger, expected in [("挑战组卡", "挑战组卡"), ("活动组卡", "活动组卡"), ("组卡", "活动组卡"), ("cn活动组卡", "活动组卡"), ("长草组卡", "长草组卡"), ("加成组卡", "加成组卡"), ("来点提示", "来点提示")]:
            self.assertEqual(self.m.pjsk_canonical_command(trigger), expected, trigger)


class GoOwnershipOwnedTests(unittest.TestCase):
    def setUp(self) -> None:
        self._old = os.environ.get("KND_GO_OWNED_COMMANDS")
        os.environ["KND_GO_OWNED_COMMANDS"] = '["bind","sks","烧烤档案","pjsk抽卡","pjskset","wlsk","pjsk_remote","pjsk_live","pjsk_remote_status","pjsk_remote_token","pjsk_remote_token_upload"]'
        self.m = _load_module()  # 导入时读取环境变量

    def tearDown(self) -> None:
        if self._old is None:
            os.environ.pop("KND_GO_OWNED_COMMANDS", None)
        else:
            os.environ["KND_GO_OWNED_COMMANDS"] = self._old

    def test_command_owned_true(self) -> None:
        owned = self.m.pjsk_command_owned_by_go
        for t in [
            "bind", "绑定", "cnbind", "sks", "cn时速", "烧烤档案", "个人信息",
            "remote", "live", "remote状态", "pjsktoken状态", "pjsk上传token",
        ]:
            self.assertTrue(owned(t), t)

    def test_command_owned_false(self) -> None:
        owned = self.m.pjsk_command_owned_by_go
        # 已迁移但不在 owned 列表 -> Python 处理
        for t in ["unbind", "解绑", "查曲"]:
            self.assertFalse(owned(t), t)
        # 未知命令 -> 绝不误吞
        self.assertFalse(owned("不存在xyz"))

    def test_regex_text_owned_true(self) -> None:
        t = self.m.pjsk_text_owned_by_go
        for s in ["pjsk抽卡", "cn pjsk 抽卡", "pjsk十连", "pjsk10连", "sekai抽卡",
                  "pjsk反向抽卡", "/pjsk抽卡"]:
            self.assertTrue(t(s), s)
        for s in ["pjskset 新 to 旧", "cnpjskset abc to def"]:
            self.assertTrue(t(s), s)
        for s in ["wlsk100", "cnwlsk1-10", "wlsk 100"]:
            self.assertTrue(t(s), s)

    def test_regex_text_not_swallowing_guess(self) -> None:
        # guess 未迁移命令的触发文本绝不能被抽卡/pjskset/wlsk 正则误吞
        t = self.m.pjsk_text_owned_by_go
        for s in ["pjsk猜卡面", "pjsk猜卡面 3", "cn pjsk 阴间猜卡面", "pjsk猜曲",
                  "pjsk听歌猜曲", "pjsk倒放猜曲 5", "pjsk猜谱面", "pjsk猜曲排行榜",
                  "pjsk猜卡面排名榜 10", "sekai猜曲", "来点提示", "结束猜曲", ""]:
            self.assertFalse(t(s), s)


class GoOwnershipEmptyListTests(unittest.TestCase):
    def setUp(self) -> None:
        self._old = os.environ.pop("KND_GO_OWNED_COMMANDS", None)
        self.m = _load_module()

    def tearDown(self) -> None:
        if self._old is not None:
            os.environ["KND_GO_OWNED_COMMANDS"] = self._old

    def test_empty_owned_zero_impact(self) -> None:
        # 未配置 owned -> 全部命令仍由 Python 处理（零影响）
        self.assertFalse(self.m.pjsk_command_owned_by_go("bind"))
        self.assertFalse(self.m.pjsk_command_owned_by_go("绑定"))
        self.assertFalse(self.m.pjsk_text_owned_by_go("pjsk抽卡"))


if __name__ == "__main__":
    unittest.main()
