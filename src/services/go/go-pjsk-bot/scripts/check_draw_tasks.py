#!/usr/bin/env python3
"""校验 go-pjsk-bot 使用的 pjsk-draw 任务名都存在于 Python 侧（出图委托一致）。

Go 业务侧不自建绘图，所有出图都 POST /render/{task} 委托 pjsk-draw。若 Go 调用了
一个 Python/渲染器不存在的 task 名（笔误等），该指令出图会整体失败。本脚本提取
两侧的 render/Render 调用 task 名，确认 Go 使用的每个 task 都在 Python 侧存在。

反向不要求相等：Python 有而 Go 没有的 task 属于仍保留 Python 的功能（guess/
谱面预览/曲线等），是预期的。

    python3 src/services/go/go-pjsk-bot/scripts/check_draw_tasks.py
"""

from __future__ import annotations

import pathlib
import re
import sys


def _repo_root() -> pathlib.Path:
    return pathlib.Path(__file__).resolve().parents[5]


# Go: .Render(ctx, "task" / .RenderMulti(ctx, "task" / .RenderWithMeta(ctx, "task"
_GO_RE = re.compile(r'\.Render(?:Multi|WithMeta)?\(ctx,\s*"([\w_]+)"')
# Go 里 mysekai msr 三图通过 tasks 切片 {"task", ...} 调用，单独补充识别。
_GO_LITERAL_RE = re.compile(r'\{"([a-z][\w_]*)",\s*merge\(')

# Python: render('task' / render_multi('task' / render_with_meta('task'
# 兼容单引号与双引号两种写法。
_PY_RE = re.compile(r"""render(?:_multi|_with_meta)?\(\s*['"]([\w_]+)['"]""")


def go_tasks(go_dir: pathlib.Path) -> set[str]:
    out: set[str] = set()
    for f in go_dir.glob("*.go"):
        if f.name.endswith("_test.go"):
            continue
        text = f.read_text(encoding="utf-8")
        out.update(_GO_RE.findall(text))
        out.update(_GO_LITERAL_RE.findall(text))
    return out


def py_tasks(py_root: pathlib.Path) -> set[str]:
    out: set[str] = set()
    for f in py_root.rglob("*.py"):
        out.update(_PY_RE.findall(f.read_text(encoding="utf-8")))
    return out


def main() -> int:
    root = _repo_root()
    go = go_tasks(root / "src/services/go/go-pjsk-bot/internal/pjsk")
    py = py_tasks(root / "src/plugins/pjsk")

    missing = sorted(go - py)
    print(f"Go 使用 {len(go)} 个 draw 任务；Python 侧共 {len(py)} 个")
    if missing:
        print("✗ Go 使用了 Python 侧不存在的 draw 任务（会导致出图失败）:")
        for t in missing:
            print(f"    {t}")
        print("\n请检查 Go 侧 Render 调用的 task 名拼写，或确认该渲染器已在 pjsk-draw 注册。")
        return 1
    print("✓ Go 使用的所有 draw 任务名都存在于 Python 侧")
    print("  仅 Python 使用（保留 Python 的功能）:", ", ".join(sorted(py - go)) or "(无)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
