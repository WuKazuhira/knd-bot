"""PJSK Python/Go 运行模式解析。"""

from __future__ import annotations


def pjsk_plugin_load_plan(runtime: str) -> tuple[bool, bool]:
    """返回（加载完整 PJSK 包，仅加载 deck）策略。"""
    if runtime == "python":
        return True, False
    if runtime == "go":
        return False, True
    raise ValueError(f"unsupported PJSK runtime {runtime!r}")


def resolve_pjsk_runtime(
    runtime_value: str | None,
    standalone_value: str | None,
) -> str:
    """解析共同运行模式，并兼容旧 PJSKBOT_STANDALONE 开关。"""
    runtime = (runtime_value or "").strip().lower()
    standalone_raw = (standalone_value or "").strip()
    standalone_set = bool(standalone_raw)
    legacy_standalone = standalone_raw == "1"

    if not runtime:
        return "go" if legacy_standalone else "python"
    if runtime not in {"go", "python"}:
        raise ValueError(
            f"invalid KNDBOT_PJSK_RUNTIME {runtime_value!r} (want 'go' or 'python')"
        )

    want_standalone = runtime == "go"
    if standalone_set and legacy_standalone != want_standalone:
        expected = "1" if want_standalone else "0"
        raise ValueError(
            "conflicting PJSK runtime configuration: "
            f"KNDBOT_PJSK_RUNTIME={runtime!r} requires "
            f"PJSKBOT_STANDALONE={expected} when both are set, "
            f"got {standalone_value!r}"
        )
    return runtime
