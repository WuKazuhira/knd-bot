#!/usr/bin/env bash
# 新环境初始化脚本
set -Eeuo pipefail

usage() {
    cat <<'EOF'
用法：scripts/ops/bootstrap.sh [选项]

功能：
  1. 如存在 Git submodule，默认执行 git submodule update --init --recursive
  2. 复制 .env.example -> .env（仅在 .env 不存在时）并收紧权限为 600
  3. 复制 example_config/ -> config/（仅在 config/ 不存在时）并去除组/其他写权限
  4. 创建 Compose 与各服务依赖的 data/ 与 volumes/ 目录骨架
  5. 可选：校验并解压静态资源包（支持本地 .tar.gz 或 http(s) URL）
  6. 运行 python3 scripts/ops/preflight.py 输出就绪诊断

选项：
  --resources PATH_OR_URL   从本地路径或 HTTP(S) URL 解压静态资源包（data/resources + data/pjsk/static）
  --sha256 VAL_OR_FILE      资源包的期望 SHA-256（64 位十六进制或 .sha256 文件路径）
  --no-submodules           跳过 git submodule 初始化
  --skip-preflight          初始化后跳过 preflight.py 检查
  -h, --help                显示帮助
EOF
}

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
PROJECT_ROOT=$(cd -- "$SCRIPT_DIR/../.." && pwd)

RESOURCES_SRC=""
EXPECTED_SHA256=""
INIT_SUBMODULES=1
RUN_PREFLIGHT=1
TEMP_ARCHIVE=""

cleanup() {
    if [[ -n "$TEMP_ARCHIVE" && -f "$TEMP_ARCHIVE" ]]; then
        rm -f "$TEMP_ARCHIVE"
    fi
}
trap cleanup EXIT

while [[ $# -gt 0 ]]; do
    case "$1" in
        --resources)
            [[ $# -ge 2 ]] || { echo "--resources 需要参数" >&2; exit 2; }
            RESOURCES_SRC=$2
            shift 2
            ;;
        --sha256)
            [[ $# -ge 2 ]] || { echo "--sha256 需要参数" >&2; exit 2; }
            EXPECTED_SHA256=$2
            shift 2
            ;;
        --no-submodules)
            INIT_SUBMODULES=0
            shift
            ;;
        --skip-preflight)
            RUN_PREFLIGHT=0
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "未知参数：$1" >&2
            usage >&2
            exit 2
            ;;
    esac
done

compute_sha256() {
    local target=$1
    if command -v sha256sum >/dev/null 2>&1; then
        sha256sum "$target" | awk '{print $1}'
    elif command -v shasum >/dev/null 2>&1; then
        shasum -a 256 "$target" | awk '{print $1}'
    else
        python3 - "$target" <<'PY'
import hashlib, sys
h = hashlib.sha256()
with open(sys.argv[1], "rb") as f:
    for chunk in iter(lambda: f.read(1024 * 1024), b""):
        h.update(chunk)
print(h.hexdigest())
PY
    fi
}

resolve_expected_sha256() {
    local spec=$1
    if [[ -f "$spec" ]]; then
        awk '{print $1; exit}' "$spec" | tr '[:upper:]' '[:lower:]'
    else
        printf '%s\n' "$spec" | tr '[:upper:]' '[:lower:]'
    fi
}

verify_tar_safety() {
    local archive=$1
    python3 - "$archive" <<'PY'
import pathlib, sys, tarfile

archive = sys.argv[1]
allowed_prefixes = ("data/resources", "data/pjsk/static")
with tarfile.open(archive, "r:*") as tf:
    for member in tf.getmembers():
        raw = member.name
        if raw.startswith("/"):
            raise SystemExit(f"拒绝解压包含绝对路径的成员: {raw}")
        parts = pathlib.PurePosixPath(raw).parts
        if ".." in parts:
            raise SystemExit(f"拒绝解压包含路径穿越的成员: {raw}")
        if not any(raw == p or raw.startswith(p + "/") for p in allowed_prefixes):
            raise SystemExit(f"拒绝解压预期目录之外的成员: {raw}")
        if member.issym() or member.islnk():
            raise SystemExit(f"拒绝解压包含链接的成员: {raw}")
        if not (member.isfile() or member.isdir()):
            raise SystemExit(f"拒绝解压非普通文件或目录的成员: {raw}")
PY
}

is_github_https_url() {
    python3 - "$1" <<'PY'
import sys
from urllib.parse import urlsplit

url = sys.argv[1]
if "\\" in url or any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in url):
    sys.exit(1)

try:
    target = urlsplit(url)
except ValueError:
    sys.exit(1)

github_hosts = {"github.com", "api.github.com", "raw.githubusercontent.com"}
if target.scheme != "https" or target.hostname not in github_hosts or target.username is not None:
    sys.exit(1)
PY
}

cd "$PROJECT_ROOT"

echo "[1/5] 检查 Git submodule..."
if [[ "$INIT_SUBMODULES" -eq 1 && -d ".git" && -f ".gitmodules" ]] && command -v git >/dev/null 2>&1; then
    git submodule update --init --recursive
else
    echo "跳过 submodule 初始化"
fi

echo "[2/5] 准备 .env 与 config/..."
if [[ ! -f ".env" ]]; then
    cp .env.example .env
    chmod 600 .env
    echo "已从 .env.example 创建 .env（请务必填写真实密码和 token）"
else
    echo ".env 已存在，保留现有文件"
fi

if [[ ! -d "config" ]]; then
    [[ -d "example_config" ]] || { echo "缺少 example_config/ 目录" >&2; exit 1; }
    cp -a example_config config
    chmod -R go-w config
    echo "已从 example_config 创建 config/"
else
    echo "config/ 已存在，保留现有配置"
fi

echo "[3/5] 创建数据与持久化目录..."
mkdir -p \
    data \
    data/manager \
    data/resources \
    data/pjsk \
    data/pjsk/ondemand \
    data/pjsk/static \
    data/meme_generator \
    volumes \
    volumes/postgres \
    volumes/haruki-3d-output \
    volumes/local-jp

echo "[4/5] 处理静态资源包..."
if [[ -n "$RESOURCES_SRC" ]]; then
    ARCHIVE_PATH=""
    if [[ "$RESOURCES_SRC" =~ ^https?:// ]]; then
        TEMP_ARCHIVE=$(mktemp "${TMPDIR:-/tmp}/kndbot-resources.XXXXXX.tar.gz")
        # -q 放首位，忽略本机 curlrc
        curl_args=(-q -fsSL --globoff --proto '=http,https')
        redirect_protocols='=http,https'
        if [[ -n "${GITHUB_TOKEN:-}" ]] && is_github_https_url "$RESOURCES_SRC"; then
            curl_args+=(-H "Authorization: Bearer ${GITHUB_TOKEN}")
            redirect_protocols='=https'
        fi
        echo "正在下载资源包：$RESOURCES_SRC"
        curl "${curl_args[@]}" --proto-redir "$redirect_protocols" -o "$TEMP_ARCHIVE" --url "$RESOURCES_SRC"
        ARCHIVE_PATH=$TEMP_ARCHIVE
    else
        case "$RESOURCES_SRC" in
            /*) ARCHIVE_PATH=$RESOURCES_SRC ;;
            *) ARCHIVE_PATH="$PWD/$RESOURCES_SRC" ;;
        esac
        [[ -f "$ARCHIVE_PATH" ]] || { echo "资源包不存在：$ARCHIVE_PATH" >&2; exit 1; }
        if [[ -z "$EXPECTED_SHA256" && -f "${ARCHIVE_PATH}.sha256" ]]; then
            EXPECTED_SHA256="${ARCHIVE_PATH}.sha256"
        fi
    fi

    if [[ -n "$EXPECTED_SHA256" ]]; then
        want=$(resolve_expected_sha256 "$EXPECTED_SHA256")
        got=$(compute_sha256 "$ARCHIVE_PATH")
        if [[ "$want" != "$got" ]]; then
            echo "SHA-256 校验失败！期望 $want，实际 $got" >&2
            exit 1
        fi
        echo "SHA-256 校验通过：$got"
    fi

    verify_tar_safety "$ARCHIVE_PATH"
    if tar --help 2>&1 | grep -q -- '--no-same-owner'; then
        tar --no-same-owner -xzf "$ARCHIVE_PATH" -C "$PROJECT_ROOT"
    else
        tar -xzf "$ARCHIVE_PATH" -C "$PROJECT_ROOT"
    fi
    echo "静态资源已解压到 $PROJECT_ROOT/data/"
else
    echo "未指定 --resources，跳过资源包解压（如后续下载 Release 资源包，可再次运行：scripts/ops/bootstrap.sh --resources kndbot-resources.tar.gz）"
fi

echo "[5/5] 运行部署前检查（scripts/ops/preflight.py）..."
preflight_ok=0
if [[ "$RUN_PREFLIGHT" -eq 1 ]]; then
    if python3 scripts/ops/preflight.py; then
        preflight_ok=1
    else
        preflight_ok=0
    fi
fi

echo ""
if [[ "$RUN_PREFLIGHT" -eq 1 && "$preflight_ok" -eq 1 ]]; then
    echo "初始化完成，preflight 检查已通过。可执行：docker compose up -d --build"
elif [[ "$RUN_PREFLIGHT" -eq 1 ]]; then
    echo "初始化骨架已就绪，请根据上方 preflight 提示补全 .env 密钥（如 POSTGRES_PASSWORD）或外部配置后，再次运行："
    echo "  python3 scripts/ops/preflight.py && docker compose up -d --build"
else
    echo "初始化完成（已跳过 preflight 检查）。"
fi
