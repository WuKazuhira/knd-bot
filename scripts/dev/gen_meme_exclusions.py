#!/usr/bin/env python
"""生成 meme_extra 排除表（src/plugins/fun/meme_extra/_exclusions.py），跳过老插件已有或冲突的表情。"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PETPET_DATA = PROJECT_ROOT / "src/plugins/fun/petpet/data_source.py"
MEMES_DATA = PROJECT_ROOT / "src/plugins/fun/memes/data_source.py"
OUTPUT = PROJECT_ROOT / "src/plugins/fun/meme_extra/_exclusions.py"

_KW = r'"((?:[^"\\]|\\.)*)"'


def _unquote(literal: str) -> str:
    return literal[2:-1] if literal.startswith('r"') else literal[1:-1]


def parse_petpet() -> tuple[list[str], set[str]]:
    """提取 petpet 的匹配规则和函数名。"""
    src = PETPET_DATA.read_text(encoding="utf-8")
    patterns: list[str] = []
    identities: set[str] = set()
    for m in re.finditer(
        r'Command\(\s*(\w+)\s*,\s*\(([^)]*)\)\s*(?:,\s*(r?"(?:[^"\\]|\\.)*"))?', src
    ):
        func, kw_group, pattern = m.group(1), m.group(2), m.group(3)
        keywords = re.findall(_KW, kw_group)
        patterns.append(_unquote(pattern) if pattern else "|".join(keywords))
        identities.add(func)
    return patterns, identities


def parse_memes() -> tuple[list[str], set[str]]:
    """提取 memes 的匹配规则和 key。"""
    src = MEMES_DATA.read_text(encoding="utf-8")
    patterns: list[str] = []
    identities: set[str] = set()
    for m in re.finditer(
        r'(Gif)?Meme\(\s*"([^"]+)"\s*,\s*\w+\s*,\s*\(([^)]*)\)\s*(?:,\s*(r?"(?:[^"\\]|\\.)*"))?',
        src,
    ):
        is_gif, key, kw_group, pattern = m.group(1), m.group(2), m.group(3), m.group(4)
        keywords = re.findall(_KW, kw_group)
        if is_gif:
            patterns.append(rf"(?:{'|'.join(keywords)})[\s\.]*gif")
        else:
            patterns.append(_unquote(pattern) if pattern else "|".join(keywords))
        identities.add(key)
    return patterns, identities


def main() -> int:
    try:
        import meme_generator
    except ImportError:
        print("需要先安装 meme-generator：pip install meme-generator", file=sys.stderr)
        return 1

    pp_patterns, pp_ids = parse_petpet()
    mm_patterns, mm_ids = parse_memes()
    compiled = [re.compile(p, re.IGNORECASE) for p in pp_patterns + mm_patterns]
    known_ids = pp_ids | mm_ids

    def taken(word: str) -> bool:
        # 老插件用 re.match，能匹配上就说明会冲突
        return any(p.match(word) for p in compiled)

    excluded: dict[str, str] = {}
    kept = 0
    for meme in meme_generator.get_memes():
        keywords = list(meme.info.keywords)
        if not keywords:
            excluded[meme.key] = "无触发词"
            continue
        if any(taken(k) for k in keywords):
            excluded[meme.key] = "触发词与老插件冲突"
        elif meme.key in known_ids:
            # 同 key 但触发词不同，也不重复注册
            excluded[meme.key] = "同表情已由老插件实现"
        else:
            kept += 1

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(excluded, ensure_ascii=False, indent=4, sort_keys=True)
    OUTPUT.write_text(
        '"""上游表情中不注册的部分——由 scripts/dev/gen_meme_exclusions.py 生成，请勿手改。\n\n'
        f"统计：上游 {len(excluded) + kept} 个，排除 {len(excluded)} 个，注册 {kept} 个。\n"
        '"""\n\n'
        f"EXCLUDED_MEME_KEYS: dict[str, str] = {body}\n",
        encoding="utf-8",
    )
    print(f"上游 {len(excluded) + kept} 个；排除 {len(excluded)} 个；将注册 {kept} 个")
    print(f"已写出 {OUTPUT.relative_to(PROJECT_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
