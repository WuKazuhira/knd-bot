#!/usr/bin/env python3
"""静态校验真实 Go 注册、别名冲突与 COMMANDS.md 全量 canonical 示例契约。

只支持当前注册采用的字面量/常量语法；新增动态语法必须显式适配，不能静默漏检。
"""
from __future__ import annotations

import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[4]
GO_DIR = ROOT / "services/go/go-pjsk-bot/internal/pjsk"


def _source(text: str) -> str:
    token = r'`[^`]*`|"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|//[^\n]*|/\*[\s\S]*?\*/'
    return re.sub(token, lambda m: " " if m[0].startswith(("//", "/*")) else m[0], text)


def extract_go_registry(go_dir: pathlib.Path = GO_DIR):
    aliases: dict[str, str] = {}
    regexes: list[tuple[str, str]] = []
    numeric: set[str] = set()
    commands: dict[str, str] = {}
    expanded: dict[str, str] = {}
    call = re.compile(r'\br\.(Register|RegisterNumericSuffix|RegisterRegex)\s*\(')
    literal = re.compile(r'\br\.(Register|RegisterNumericSuffix)\s*\(\s*"([^"\\]+)"\s*,\s*(nil|\[\]string\s*\{([^}]*)\})\s*,')
    regex_call = re.compile(r'\br\.RegisterRegex\s*\(\s*"([^"\\]+)"\s*,\s*(`[^`]*`|[A-Za-z_]\w*)\s*,')
    for path in sorted(go_dir.glob("*.go")):
        if path.name.endswith("_test.go"):
            continue
        source = _source(path.read_text(encoding="utf-8"))
        constants = dict(re.findall(r'\b(\w+)\s*=\s*`([^`]*)`', source))
        parsed = set()
        for match in literal.finditer(source):
            parsed.add(match.start())
            kind, canonical, _, raw_aliases = match.groups()
            if canonical in commands:
                raise ValueError(f"重复 canonical 注册: {canonical} ({path.name})")
            commands[canonical] = path.stem
            if kind == "RegisterNumericSuffix":
                numeric.add(canonical)
            raw_aliases = raw_aliases or ""
            values = re.findall(r'"([^"\\]+)"', raw_aliases)
            if re.sub(r'"([^"\\]+)"|[,\s]', '', raw_aliases):
                raise ValueError(f"未解析动态 alias: {canonical}")
            for trigger in (canonical, *values):
                key = trigger.lower()
                if key in aliases and aliases[key] != canonical:
                    raise ValueError(f"alias 冲突: {trigger}")
                aliases[key] = canonical
                for prefix in ("", "cn", "tw"):
                    expanded_key = prefix + key
                    if expanded_key in expanded and expanded[expanded_key] != canonical:
                        raise ValueError(f"区服展开冲突: {expanded_key}")
                    expanded[expanded_key] = canonical
        for match in regex_call.finditer(source):
            parsed.add(match.start())
            canonical, expression = match.groups()
            pattern = expression[1:-1] if expression.startswith("`") else constants.get(expression)
            if pattern is None:
                raise ValueError(f"未解析动态正则: {path.name}: {expression}")
            pattern = pattern.removeprefix("(?i)")
            if not pattern.startswith("^") or not pattern.endswith("$"):
                raise ValueError(f"正则必须完整锚定: {canonical}")
            regexes.append((canonical, pattern))
            aliases.setdefault(canonical.lower(), canonical)
            commands.setdefault(canonical, path.stem)
        unparsed = [m[0] for m in call.finditer(source) if m.start() not in parsed]
        if unparsed:
            raise ValueError(f"未解析注册 {path.name}: {unparsed}")
    if not commands:
        raise ValueError("未找到任何 Go 注册")
    return aliases, regexes, numeric, commands


def extract_go_map(go_dir: pathlib.Path) -> dict[str, str]:
    return extract_go_registry(go_dir)[0]


def main() -> int:
    try:
        go_map, regexes, _, commands = extract_go_registry()
        errors = []
        document = (GO_DIR.parent.parent / "COMMANDS.md").read_text(encoding="utf-8")
        examples = re.findall(r"KND_GO_OWNED_COMMANDS='(\[[^\n]*\])'", document)
        if not examples:
            errors.append("COMMANDS.md 缺少可校验的全量 canonical 示例")
        for example in examples:
            keys = json.loads(example)
            if not isinstance(keys, list) or not all(isinstance(key, str) for key in keys):
                errors.append("COMMANDS.md ownership 示例不是字符串数组")
                continue
            if set(keys) != set(commands) or len(keys) != len(set(keys)):
                errors.append(
                    f"COMMANDS.md 全量 canonical 示例漂移: "
                    f"缺少={sorted(set(commands)-set(keys))}, "
                    f"非canonical={sorted(set(keys)-set(commands))}, "
                    f"重复={len(keys)-len(set(keys))}"
                )
        print(f"Go canonical: {len(commands)}, triggers: {len(go_map)}, regexes: {len(regexes)}")
        print("\n".join(errors) if errors else "Go 注册别名/区服展开无冲突，且与 COMMANDS.md 全量清单一致")
        return int(bool(errors))
    except (ValueError, SyntaxError) as error:
        print(f"校验失败: {error}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
