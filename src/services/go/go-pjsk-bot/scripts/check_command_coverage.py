#!/usr/bin/env python3
"""审计 PJSK Python 命令是否已迁移到 Go 或列入正式保留矩阵。"""
from __future__ import annotations

import importlib.util
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[5]
SRC_ROOT = ROOT / "src"
PY_ROOT = SRC_ROOT / "plugins" / "pjsk"
GO_ROOT = SRC_ROOT / "services" / "go" / "go-pjsk-bot" / "internal" / "pjsk"

RETAINED = {
    "5v5人数", "cn5v5人数", "tw5v5人数", "组卡后端",
}


def main() -> int:
    py_text = "\n".join(p.read_text(errors="ignore") for p in PY_ROOT.glob("**/*.py"))
    go_text = "\n".join(p.read_text(errors="ignore") for p in GO_ROOT.glob("*.go"))
    python_commands = set(re.findall(r"\bon_command\(\s*['\"]([^'\"]+)", py_text))
    go_commands = set(re.findall(r"\br\.Register\(\s*['\"]([^'\"]+)", go_text))
    go_commands.update(re.findall(r"\br\.RegisterRegex\(\s*['\"]([^'\"]+)", go_text))
    ownership_path = SRC_ROOT / "services" / "go_ownership.py"
    spec = importlib.util.spec_from_file_location("go_ownership", ownership_path)
    ownership = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(ownership)
    # remote 控制命令的 Go 规范名使用 pjsk_remote* ownership key，
    # Python on_command 入口仍是 remote/live 等用户触发词；两侧通过别名表关联。
    go_triggers = set(go_commands)
    go_triggers.update(
        trigger for trigger, canonical in ownership._PJSK_ALIAS_TO_CANON.items()
        if canonical in go_commands
    )
    missing = sorted(
        command for command in python_commands
        if command not in go_triggers
        and command not in RETAINED
        and not command.startswith(("cn", "tw"))
    )
    unmapped_go = sorted(command for command in go_commands if command not in ownership._PJSK_ALIAS_TO_CANON)
    retained_owned = sorted(command for command in RETAINED if command in ownership._PJSK_ALIAS_TO_CANON)
    print(f"Python on_command 入口: {len(python_commands)}")
    print(f"Go Register 入口: {len(go_commands)}")
    print("未迁移且未列入保留矩阵:", missing or "无")
    print("未进入 Python ownership 映射:", unmapped_go or "无")
    print("误列入 Go ownership 的保留入口:", retained_owned or "无")
    return 1 if missing or unmapped_go or retained_owned else 0


if __name__ == "__main__":
    sys.exit(main())
