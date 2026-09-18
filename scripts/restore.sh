#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ARCHIVE=""
FORCE=0
TEMP_DIR=""
ROLLBACK_DIR=""
ORIGINAL_RUNNING=()
MOVED_PATHS=()
FILES_SWAPPED=0
DB_COMMITTED=0

usage() {
  cat <<'EOF'
用法: scripts/restore.sh --archive FILE --force

校验 backup.sh 生成的归档，将 .env、config/、data/ 和 PostgreSQL SQL
恢复到当前仓库。默认拒绝覆盖任何现有部署文件；必须显式传入 --force。
脚本不会直接复制或恢复 volumes/postgres。
EOF
}

fail() {
  printf '错误: %s\n' "$*" >&2
  exit 1
}

compose() {
  docker compose --project-directory "$ROOT_DIR" --file "$ROOT_DIR/docker-compose.yml" "$@"
}

rollback_files() {
  local item
  for item in .env config data; do
    rm -rf "$ROOT_DIR/$item"
  done
  for item in "${MOVED_PATHS[@]}"; do
    mv "$ROLLBACK_DIR/$item" "$ROOT_DIR/$item"
  done
}

cleanup() {
  local status=$?
  if ((status != 0 && DB_COMMITTED == 0 && FILES_SWAPPED == 1)) && [[ -n "$ROLLBACK_DIR" ]]; then
    printf '恢复失败，正在回滚部署文件...\n' >&2
    compose stop postgres >/dev/null 2>&1 || true
    if rollback_files; then
      FILES_SWAPPED=0
      rm -rf "$ROLLBACK_DIR"
      ROLLBACK_DIR=""
    else
      printf '警告: 自动回滚文件失败，请检查 %s。\n' "$ROLLBACK_DIR" >&2
    fi
    if ((${#ORIGINAL_RUNNING[@]})); then
      compose up -d "${ORIGINAL_RUNNING[@]}" >/dev/null 2>&1 \
        || printf '警告: 未能自动恢复原服务，请手工检查 Compose 状态。\n' >&2
    fi
  fi
  [[ -z "$TEMP_DIR" ]] || rm -rf "$TEMP_DIR"
  if [[ -n "$ROLLBACK_DIR" ]] && ((status == 0 || FILES_SWAPPED == 0)); then
    rm -rf "$ROLLBACK_DIR"
  fi
  exit "$status"
}
trap cleanup EXIT

while (($#)); do
  case "$1" in
    --archive)
      (($# >= 2)) || fail "--archive 缺少文件名"
      ARCHIVE=$2
      shift 2
      ;;
    --force)
      FORCE=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      fail "未知参数: $1"
      ;;
  esac
done

[[ -n "$ARCHIVE" ]] || fail "必须提供 --archive"
[[ "$ARCHIVE" == /* ]] || ARCHIVE="$PWD/$ARCHIVE"
[[ -f "$ARCHIVE" ]] || fail "备份归档不存在: $ARCHIVE"
((FORCE == 1)) || fail "恢复会覆盖 .env、config/ 和 data/；确认后使用 --force"
command -v docker >/dev/null 2>&1 || fail "未找到 docker"
command -v tar >/dev/null 2>&1 || fail "未找到 tar"
command -v python3 >/dev/null 2>&1 || fail "未找到 python3"

TEMP_DIR="$(mktemp -d)"
ROLLBACK_DIR="$(mktemp -d)"

python3 - "$ARCHIVE" <<'PY'
import pathlib
import sys
import tarfile

archive = pathlib.Path(sys.argv[1])
required = {".env", "database.sql", "manifest.json"}
seen = set()
with tarfile.open(archive, "r:gz") as bundle:
    for member in bundle.getmembers():
        name = member.name.removeprefix("./")
        path = pathlib.PurePosixPath(name)
        if not name or path.is_absolute() or ".." in path.parts:
            raise SystemExit(f"归档包含不安全路径: {member.name}")
        if member.issym() or member.islnk():
            raise SystemExit(f"归档包含不允许的链接: {member.name}")
        top = path.parts[0]
        if top not in {".env", "config", "data", "database.sql", "manifest.json"}:
            raise SystemExit(f"归档包含非预期内容: {member.name}")
        if top == "volumes" or "postgres" in path.parts[:2]:
            raise SystemExit("归档不得包含 Postgres 数据目录")
        seen.add(top)
missing = required - seen
if missing or not {"config", "data"}.issubset(seen):
    raise SystemExit("归档缺少必需内容: " + ", ".join(sorted(missing | ({"config", "data"} - seen))))
PY

tar -xzf "$ARCHIVE" -C "$TEMP_DIR"
python3 - "$TEMP_DIR/manifest.json" "$TEMP_DIR/database.sql" <<'PY'
import hashlib
import json
import pathlib
import sys

manifest = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
if manifest.get("schema_version") != 1:
    raise SystemExit("不支持的 manifest schema_version")
expected = manifest.get("database", {}).get("sha256")
actual = hashlib.sha256(pathlib.Path(sys.argv[2]).read_bytes()).hexdigest()
if not expected or expected != actual:
    raise SystemExit("database.sql 校验和不匹配")
if "volumes/postgres" not in manifest.get("excluded", []):
    raise SystemExit("manifest 未声明排除 volumes/postgres")
PY

if [[ -f "$ROOT_DIR/.env" ]]; then
  mapfile -t ORIGINAL_RUNNING < <(compose ps --services --filter status=running 2>/dev/null || true)
fi
if ((${#ORIGINAL_RUNNING[@]})); then
  printf '正在停止当前 Compose 服务...\n' >&2
  compose stop "${ORIGINAL_RUNNING[@]}" >/dev/null
fi

for item in .env config data; do
  if [[ -e "$ROOT_DIR/$item" || -L "$ROOT_DIR/$item" ]]; then
    mv "$ROOT_DIR/$item" "$ROLLBACK_DIR/$item"
    MOVED_PATHS+=("$item")
  fi
done
FILES_SWAPPED=1
for item in .env config data; do
  cp -a "$TEMP_DIR/$item" "$ROOT_DIR/$item"
done
chmod 600 "$ROOT_DIR/.env"

compose config >/dev/null || fail "归档中的 .env 与当前 Compose 配置不兼容"
printf '正在启动 PostgreSQL...\n' >&2
compose up -d postgres >/dev/null

READY=0
for _attempt in {1..30}; do
  if compose exec -T postgres sh -eu -c \
    'pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB"' >/dev/null 2>&1; then
    READY=1
    break
  fi
  sleep 2
done
((READY == 1)) || fail "PostgreSQL 未在 60 秒内就绪"

printf '正在通过 psql 恢复数据库...\n' >&2
compose exec -T postgres sh -eu -c \
  'exec psql -v ON_ERROR_STOP=1 --single-transaction -U "$POSTGRES_USER" -d "$POSTGRES_DB"' \
  <"$TEMP_DIR/database.sql"
DB_COMMITTED=1

if ((${#ORIGINAL_RUNNING[@]})); then
  printf '正在恢复先前运行的服务...\n' >&2
  compose up -d "${ORIGINAL_RUNNING[@]}" >/dev/null
fi
printf '恢复完成。建议立即运行 scripts/smoke_test.py。\n'
