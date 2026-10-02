#!/usr/bin/env python3
"""校验 Python 字面入口迁移矩阵和真实 Go canonical/alias 的帮助可达性。

不导入 nonebot 或业务插件；复用运行时同一只读帮助索引。正则入口由
check_ownership_sync 的显式 body 清单校验，而非把 regex 展开为无限命令。
"""
from __future__ import annotations

import ast
import pathlib
import sys

from check_ownership_sync import ROOT, extract_go_registry, load_ownership

# 技术 canonical 所有权名用对应的真实公开命令定位帮助，不新增 matcher。
CANONICAL_HELP_QUERIES = {
    "guess": "pjsk猜曲",
    "pjsk_remote": "remote",
    "pjsk_live": "live",
    "pjsk_remote_status": "remote状态",
    "pjsk_remote_token": "pjsktoken状态",
    "pjsk_remote_token_upload": "pjsk上传token",
}

RETAINED = {
    "5v5人数", "cn5v5人数", "tw5v5人数",
    "活动组卡", "挑战组卡", "长草组卡", "加成组卡", "组卡后端",
}


def main() -> int:
    try:
        go_aliases, _, _, commands = extract_go_registry()
        ownership = load_ownership()
        sys.path.insert(0, str(ROOT / "src/utils"))
        from help_index import static_entries, find_entries, entry_usage, literal, source_values, command_candidates, normalize

        python_commands = set()
        retained_triggers = set()
        def alias_value(node, values, dynamic_prefixes):
            if isinstance(node, (ast.Set, ast.List, ast.Tuple)):
                result = [literal(part, values) for part in node.elts]
                return result if all(isinstance(item, str) for item in result) else None
            if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
                left = alias_value(node.left, values, dynamic_prefixes)
                right = alias_value(node.right, values, dynamic_prefixes)
                return list(left) + list(right) if left is not None and right is not None else None
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in dynamic_prefixes and len(node.args) == 1 and not node.keywords:
                base = literal(node.args[0], values)
                return [base, "cn" + base, "tw" + base] if isinstance(base, str) else None
            return literal(node, values)

        for path in sorted((ROOT / "src/plugins/pjsk").rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            values = source_values(tree)
            # 当前唯一动态 alias helper：对照其实际源码，变更即 fail closed。
            expected = ast.dump(ast.parse("{base, f'cn{base}', f'tw{base}'}", mode="eval").body)
            dynamic_prefixes = {node.name for node in tree.body
                                if isinstance(node, ast.FunctionDef) and node.name == "_make_cmds"
                                and len(node.body) == 1 and isinstance(node.body[0], ast.Return)
                                and ast.dump(node.body[0].value) == expected}
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name) or node.func.id != "on_command":
                    continue
                if not node.args:
                    raise ValueError(f"未解析 Python on_command: {path}")
                command = literal(node.args[0], values)
                if not isinstance(command, str):
                    raise ValueError(f"未显式登记动态 on_command: {path}:{node.lineno}")
                route_triggers = {command}
                base = command[2:] if command.startswith(("cn", "tw")) else command
                retained = command in RETAINED or base in RETAINED
                for keyword in node.keywords:
                    if keyword.arg == "aliases":
                        aliases = alias_value(keyword.value, values, dynamic_prefixes)
                        if not isinstance(aliases, (list, tuple, set)) or not all(isinstance(a, str) for a in aliases):
                            raise ValueError(f"未显式登记动态 aliases: {path}:{node.lineno}")
                        route_triggers.update(aliases)
                python_commands.update(route_triggers)
                if retained:
                    retained_triggers.update(route_triggers)
        missing = sorted(command for command in python_commands
                         if ownership.pjsk_canonical_command(command) not in commands and command not in retained_triggers)
        retained_owned = sorted(command for command in retained_triggers if ownership.pjsk_canonical_command(command) is not None)
        entries = static_entries(ROOT / "src")
        missing_help = []
        ambiguous_help = []
        for trigger, canonical in sorted(go_aliases.items()):
            query = CANONICAL_HELP_QUERIES.get(trigger, trigger)
            category, matches = find_entries(entries, query, 2)
            # 类别列表和自动生成的“暂未提供参数说明”不是具体命令帮助。
            documented = []
            for entry in matches:
                candidates = command_candidates(query) if entry.pjsk else [normalize(query)]
                usage_map = {normalize(key): value for key, value in entry.command_usage.items()}
                if entry.usage.strip() or entry.super_usage.strip() or any(
                    isinstance(usage_map.get(candidate), str) and usage_map[candidate].strip()
                    for candidate in candidates
                ):
                    documented.append(entry)
            if category or not documented or not all(entry_usage(entry, query, 2).strip() for entry in documented):
                missing_help.append(f"{trigger} -> {canonical}")
            elif len(documented) > 1:
                ambiguous_help.append(f"{trigger}: {', '.join(entry.module for entry in documented)}")
        print(f"Python 字面 canonical/aliases: {len(python_commands)}；Go canonical: {len(commands)}；Go trigger: {len(go_aliases)}")
        print("未迁移且未列入保留矩阵:", missing or "无")
        print("误接管保留入口:", retained_owned or "无")
        print("未定位到具体帮助:", missing_help or "无")
        print("帮助歧义:", ambiguous_help or "无")
        return int(bool(missing or retained_owned or missing_help or ambiguous_help))
    except (ValueError, SyntaxError, ImportError) as error:
        print(f"命令覆盖校验失败: {error}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
