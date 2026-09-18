#!/usr/bin/env python3
"""KNDBOT deployment preflight checks.

The checker intentionally reports variable names only; environment values are never
included in human-readable or JSON output.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import stat
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Iterable, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
WEAK_VALUES = {
    "123456",
    "admin",
    "changeme",
    "change-me",
    "default",
    "example",
    "kndbot",
    "password",
    "secret",
    "secret_string",
    "replace-with-a-long-random-secret",
    "test",
}
SECRET_SUFFIXES = ("_PASSWORD", "_SECRET", "_TOKEN", "_API_KEY")


@dataclass(frozen=True)
class CheckResult:
    name: str
    status: str
    message: str


Runner = Callable[[Sequence[str], Path], subprocess.CompletedProcess[str]]


def run_command(command: Sequence[str], cwd: Path = ROOT) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key.startswith("export "):
            key = key[7:].strip()
        if not key:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        values[key] = value
    return values


def merged_env(file_values: Mapping[str, str], process_env: Mapping[str, str]) -> dict[str, str]:
    merged = dict(file_values)
    merged.update({key: value for key, value in process_env.items() if value is not None})
    return merged


def result(name: str, status: str, message: str) -> CheckResult:
    return CheckResult(name=name, status=status, message=message)


def check_required_paths(root: Path) -> list[CheckResult]:
    required_files = (
        "docker-compose.yml",
        ".env",
        "Dockerfile",
        "Dockerfile.chromium",
        "config/config.yaml",
        "config/pjsk/servers.yaml",
        "config/sekai-api/config.yaml",
    )
    required_dirs = ("config", "data", "data/pjsk", "volumes")
    missing = [item for item in required_files if not (root / item).is_file()]
    missing += [item for item in required_dirs if not (root / item).is_dir()]
    if missing:
        return [result("required_paths", "fail", "缺少必需路径: " + ", ".join(missing))]
    return [result("required_paths", "pass", "必需文件和目录齐全")]


def check_docker(runner: Runner = run_command) -> list[CheckResult]:
    if shutil.which("docker") is None:
        return [
            result("docker", "fail", "未找到 docker 命令"),
            result("compose", "fail", "无法检查 Docker Compose"),
        ]
    docker = runner(("docker", "info"), ROOT)
    compose = runner(("docker", "compose", "version"), ROOT)
    return [
        result("docker", "pass" if docker.returncode == 0 else "fail", "Docker daemon 可用" if docker.returncode == 0 else "Docker daemon 不可用"),
        result("compose", "pass" if compose.returncode == 0 else "fail", "Docker Compose 插件可用" if compose.returncode == 0 else "Docker Compose 插件不可用"),
    ]


def check_submodules(root: Path, runner: Runner = run_command) -> list[CheckResult]:
    gitmodules = root / ".gitmodules"
    if not gitmodules.exists():
        return [result("git_submodule", "skip", "仓库未声明 Git submodule")]
    completed = runner(("git", "submodule", "status", "--recursive"), root)
    if completed.returncode != 0:
        return [result("git_submodule", "fail", "无法读取 submodule 状态")]
    lines = [line for line in completed.stdout.splitlines() if line.strip()]
    if not lines:
        return [result("git_submodule", "fail", "submodule 状态为空")]
    uninitialized = [line for line in lines if line.startswith("-")]
    conflicted = [line for line in lines if line.startswith("U")]
    drifted = [line for line in lines if line.startswith("+")]
    if uninitialized or conflicted:
        return [result("git_submodule", "fail", "存在未初始化或冲突的 submodule")]
    if drifted:
        return [result("git_submodule", "warn", "submodule 与锁定提交不一致")]
    return [result("git_submodule", "pass", "submodule 已初始化且与锁定提交一致")]


def check_secret_placeholders(env: Mapping[str, str]) -> list[CheckResult]:
    names = sorted(
        key
        for key, value in env.items()
        if key.endswith(SECRET_SUFFIXES) and value.strip().lower() in WEAK_VALUES
    )
    results: list[CheckResult] = []
    if names:
        results.append(result("weak_secrets", "fail", "以下变量仍使用占位或弱值: " + ", ".join(names)))
    else:
        results.append(result("weak_secrets", "pass", "未发现已填写的占位弱口令"))
    missing = [
        name
        for name in ("POSTGRES_PASSWORD", "SEKAI_API_JWT_SECRET")
        if not env.get(name, "").strip()
    ]
    if missing:
        results.append(result("required_secrets", "fail", "以下必填变量未设置: " + ", ".join(missing)))
    else:
        results.append(result("required_secrets", "pass", "必填秘密变量已设置（值未显示）"))
    if env.get("DB_PASSWORD") and env.get("POSTGRES_PASSWORD") and env["DB_PASSWORD"] != env["POSTGRES_PASSWORD"]:
        results.append(result("database_credentials", "warn", "DB_PASSWORD 与 POSTGRES_PASSWORD 不一致"))
    else:
        results.append(result("database_credentials", "pass", "数据库密码变量未发现明显不一致"))
    return results


def split_csv(value: str) -> set[str]:
    return {item.strip().lower() for item in value.split(",") if item.strip()}


def check_deck_profile(env: Mapping[str, str]) -> list[CheckResult]:
    backends = split_csv(env.get("DECK_BACKENDS", "allium")) or {"allium"}
    profiles = split_csv(env.get("COMPOSE_PROFILES", ""))
    invalid = sorted(backends - {"allium", "http", "both"})
    if invalid:
        return [result("deck_profile", "fail", "DECK_BACKENDS 包含不支持的模式")]
    needs_http = bool(backends & {"http", "both"})
    has_profile = "deck-http" in profiles
    if needs_http and not has_profile:
        return [result("deck_profile", "fail", "DECK_BACKENDS 使用 HTTP，但 COMPOSE_PROFILES 未启用 deck-http")]
    if needs_http and not env.get("DECK_SERVICE_URLS", "").strip():
        return [result("deck_profile", "fail", "DECK_BACKENDS 使用 HTTP，但 DECK_SERVICE_URLS 未设置")]
    if has_profile and not needs_http:
        return [result("deck_profile", "warn", "已启用 deck-http profile，但 DECK_BACKENDS 未使用 HTTP")]
    return [result("deck_profile", "pass", "DECK_BACKENDS 与 COMPOSE_PROFILES 匹配")]


def check_sekai_api(root: Path, env: Mapping[str, str], runner: Runner = run_command) -> list[CheckResult]:
    image = env.get("KND_SEKAI_API_IMAGE", "kndbot-sekai-api:local").strip()
    if not image:
        return [result("sekai_api", "fail", "未配置 sekai-api 镜像")]
    if shutil.which("docker") is None:
        return [result("sekai_api", "fail", "无法检查 sekai-api 本地镜像")]
    inspected = runner(("docker", "image", "inspect", image), root)
    if inspected.returncode == 0:
        return [result("sekai_api", "pass", "sekai-api 本地镜像可用")]

    source = root / "src/services/go/sekai-api"
    if (source / "go.mod").is_file() and (source / "Dockerfile").is_file():
        return [
            result(
                "sekai_api",
                "fail",
                "默认 Compose 所需镜像不可用；本地源码存在，可先使用 private-build overlay 构建",
            )
        ]
    return [result("sekai_api", "fail", "sekai-api 指定镜像不可用，且私有源码缺失")]


def port_is_free(host: str, port: int) -> bool:
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    with socket.socket(family, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, port))
        except OSError:
            return False
    return True


def docker_owned_ports(root: Path, runner: Runner = run_command) -> dict[str, str]:
    if shutil.which("docker") is None:
        return {}
    completed = runner(("docker", "ps", "--format", "{{.Names}}\t{{.Ports}}"), root)
    if completed.returncode != 0:
        return {}
    owned: dict[str, str] = {}
    for line in completed.stdout.splitlines():
        name, separator, published = line.partition("\t")
        if separator:
            owned[name] = published
    return owned


def _port_value(env: Mapping[str, str], name: str, default: int) -> tuple[int | None, CheckResult | None]:
    raw = env.get(name, str(default)).strip()
    try:
        port = int(raw)
    except ValueError:
        return None, result(f"port_{name.lower()}", "fail", f"{name} 不是有效端口")
    if not 1 <= port <= 65535:
        return None, result(f"port_{name.lower()}", "fail", f"{name} 超出有效端口范围")
    return port, None


def check_ports(root: Path, env: Mapping[str, str], runner: Runner = run_command) -> list[CheckResult]:
    ports: list[tuple[str, str, int, str]] = [
        ("KND_PORT", "0.0.0.0", 18081, "chromium"),
        ("PJSK_DRAW_PORT", "127.0.0.1", 45560, "pjsk-draw"),
        ("PJSK_HELPER_PORT", "127.0.0.1", 45558, "pjsk-helper"),
        ("SEKAI_API_PORT", "127.0.0.1", 9999, "sekai-api"),
    ]
    if "deck-http" in split_csv(env.get("COMPOSE_PROFILES", "")):
        ports.append(("DECK_RECOMMENDER_PORT", "127.0.0.1", 45557, "allium-deck"))
    fixed = [("GO_PJSK_PORT", "127.0.0.1", 3001, "go-pjsk-bot")]
    docker_ports = docker_owned_ports(root, runner)
    prefix = env.get("KND_PREFIX", "kndbot").strip() or "kndbot"
    results: list[CheckResult] = []
    for name, host, default, service in [*ports, *fixed]:
        port, invalid = _port_value(env, name, default)
        if invalid is not None:
            results.append(invalid)
            continue
        assert port is not None
        check_name = f"port_{port}"
        if port_is_free(host, port):
            results.append(result(check_name, "pass", f"端口 {port} 可用"))
        elif str(port) in docker_ports.get(f"{prefix}-{service}", ""):
            results.append(result(check_name, "pass", f"端口 {port} 已由当前部署容器使用"))
        else:
            results.append(result(check_name, "fail", f"端口 {port} 已被其他进程占用"))
    return results


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def _summarize_paths(paths: Sequence[str], limit: int = 8) -> str:
    visible = list(paths[:limit])
    if len(paths) > limit:
        visible.append(f"另有 {len(paths) - limit} 个")
    return ", ".join(visible)


def check_permissions(root: Path) -> list[CheckResult]:
    results: list[CheckResult] = []
    config_files = [
        path
        for path in (root / "config").rglob("*")
        if path.is_file() and "__pycache__" not in path.parts
    ]
    world_writable = sorted(str(path.relative_to(root)) for path in config_files if _mode(path) & 0o002)
    group_writable = sorted(
        str(path.relative_to(root))
        for path in config_files
        if _mode(path) & 0o020 and not _mode(path) & 0o002
    )
    if world_writable:
        results.append(
            result(
                "config_permissions",
                "fail",
                "存在其他用户可写的配置文件: " + _summarize_paths(world_writable),
            )
        )
    elif group_writable:
        results.append(
            result(
                "config_permissions",
                "warn",
                "存在组用户可写的配置文件: " + _summarize_paths(group_writable),
            )
        )
    else:
        results.append(result("config_permissions", "pass", "配置文件不存在组/其他用户可写权限"))
    env_file = root / ".env"
    if env_file.is_file() and _mode(env_file) & 0o077:
        results.append(result("env_permissions", "warn", ".env 可被当前用户以外的账户读取；建议 chmod 600 .env"))
    elif env_file.is_file():
        results.append(result("env_permissions", "pass", ".env 权限已限制"))
    pjsk = root / "data/pjsk"
    if not pjsk.is_dir():
        results.append(result("pjsk_permissions", "fail", "data/pjsk 不存在"))
    elif os.access(pjsk, os.R_OK | os.W_OK | os.X_OK):
        results.append(result("pjsk_permissions", "pass", "data/pjsk 对当前部署用户可读写进入"))
    else:
        results.append(result("pjsk_permissions", "fail", "data/pjsk 对当前部署用户不可读写进入"))
    return results


def run_checks(root: Path = ROOT, process_env: Mapping[str, str] | None = None, runner: Runner = run_command) -> list[CheckResult]:
    file_env = parse_env_file(root / ".env")
    env = merged_env(file_env, process_env if process_env is not None else os.environ)
    checks: list[CheckResult] = []
    checks.extend(check_required_paths(root))
    checks.extend(check_docker(runner))
    checks.extend(check_submodules(root, runner))
    checks.extend(check_secret_placeholders(env))
    checks.extend(check_deck_profile(env))
    checks.extend(check_sekai_api(root, env, runner))
    checks.extend(check_ports(root, env, runner))
    checks.extend(check_permissions(root))
    return checks


def print_human(results: Iterable[CheckResult]) -> None:
    labels = {"pass": "PASS", "warn": "WARN", "fail": "FAIL", "skip": "SKIP"}
    for item in results:
        print(f"[{labels[item.status]}] {item.name}: {item.message}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="检查 KNDBOT 部署前置条件")
    parser.add_argument("--root", type=Path, default=ROOT, help="仓库根目录")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    args = parser.parse_args(argv)
    results = run_checks(args.root.resolve())
    counts = {status: sum(item.status == status for item in results) for status in ("pass", "warn", "fail", "skip")}
    if args.json:
        print(json.dumps({"ok": counts["fail"] == 0, "summary": counts, "checks": [asdict(item) for item in results]}, ensure_ascii=False, indent=2))
    else:
        print_human(results)
        print(f"汇总: {counts['pass']} 通过, {counts['warn']} 警告, {counts['fail']} 失败, {counts['skip']} 跳过")
    return 1 if counts["fail"] else 0


if __name__ == "__main__":
    sys.exit(main())
