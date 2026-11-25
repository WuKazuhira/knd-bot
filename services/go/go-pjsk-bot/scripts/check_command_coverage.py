#!/usr/bin/env python3
"""校验真实 Go canonical/alias 在静态帮助索引中的可达性与无歧义性。"""
from __future__ import annotations

import sys

from check_ownership_sync import ROOT, extract_go_registry

# 技术 canonical 所有权名用对应的真实公开命令定位帮助，不新增 matcher。
CANONICAL_HELP_QUERIES = {
    "guess": "pjsk猜曲",
    "pjsk_remote": "remote",
    "pjsk_live": "live",
    "pjsk_remote_status": "remote状态",
    "pjsk_remote_token": "pjsktoken状态",
    "pjsk_remote_token_upload": "pjsk上传token",
}


def main() -> int:
    try:
        go_aliases, _, _, commands = extract_go_registry()
        sys.path.insert(0, str(ROOT / "src/utils"))
        from help_index import (
            command_candidates,
            entry_usage,
            find_entries,
            normalize,
            static_entries,
        )

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
        print(f"Go canonical: {len(commands)}；Go trigger: {len(go_aliases)}")
        print("未定位到具体帮助:", missing_help or "无")
        print("帮助歧义:", ambiguous_help or "无")
        return int(bool(missing_help or ambiguous_help))
    except (ValueError, SyntaxError, ImportError) as error:
        print(f"命令覆盖校验失败: {error}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
