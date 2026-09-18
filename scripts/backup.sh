#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUTPUT=""
ONLINE=0
TEMP_DIR=""
PARTIAL=""
STOPPED_SERVICES=()

usage() {
  cat <<'EOF'
用法: scripts/backup.sh [--output FILE] [--online]

默认暂停除 Postgres 外的运行中 Compose 服务，以获得 data/config 的一致快照。
--online 明确接受应用数据在打包期间变化的风险，不暂停服务。
归档包含 database.sql、data/、config/、.env 和 manifest.json；不会打包
volumes/postgres 或任何 Postgres 数据目录。
EOF
}

fail() {
  printf '错误: %s\n' "$*" >&2
  exit 1
}

compose() {
  docker compose --project-directory "$ROOT_DIR" --file "$ROOT_DIR/docker-compose.yml" "$@"
}

restart_stopped() {
  if ((${#STOPPED_SERVICES[@]})); then
    printf '正在恢复先前运行的服务...\n' >&2
    compose up -d "${STOPPED_SERVICES[@]}" >/dev/null
    STOPPED_SERVICES=()
  fi
}

cleanup() {
  local status=$?
  if ((${#STOPPED_SERVICES[@]})); then
    restart_stopped || {
      printf '警告: 备份后未能自动恢复全部服务，请运行 docker compose up -d。\n' >&2
      status=1
    }
  fi
  [[ -z "$TEMP_DIR" ]] || rm -rf "$TEMP_DIR"
  [[ -z "$PARTIAL" || ! -e "$PARTIAL" ]] || rm -f "$PARTIAL"
  exit "$status"
}
trap cleanup EXIT

while (($#)); do
  case "$1" in
    --output)
      (($# >= 2)) || fail "--output 缺少文件名"
      OUTPUT=$2
      shift 2
      ;;
    --online)
      ONLINE=1
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

command -v docker >/dev/null 2>&1 || fail "未找到 docker"
command -v tar >/dev/null 2>&1 || fail "未找到 tar"
command -v python3 >/dev/null 2>&1 || fail "未找到 python3"
[[ -f "$ROOT_DIR/.env" ]] || fail "缺少 .env"
[[ -d "$ROOT_DIR/data" ]] || fail "缺少 data/"
[[ -d "$ROOT_DIR/config" ]] || fail "缺少 config/"

compose config >/dev/null || fail "Docker Compose 配置无效"
mapfile -t RUNNING_SERVICES < <(compose ps --services --filter status=running)
printf '%s\n' "${RUNNING_SERVICES[@]}" | python3 -c 'import sys; raise SystemExit(0 if "postgres" in {line.strip() for line in sys.stdin} else 1)' \
  || fail "Postgres 服务未运行"

if [[ -z "$OUTPUT" ]]; then
  OUTPUT="$ROOT_DIR/backups/kndbot-$(date -u +%Y%m%dT%H%M%SZ).tar.gz"
elif [[ "$OUTPUT" != /* ]]; then
  OUTPUT="$PWD/$OUTPUT"
fi
[[ ! -e "$OUTPUT" ]] || fail "目标文件已存在: $OUTPUT"
mkdir -p "$(dirname "$OUTPUT")"
PARTIAL="${OUTPUT}.partial"
[[ ! -e "$PARTIAL" ]] || fail "临时目标已存在: $PARTIAL"
TEMP_DIR="$(mktemp -d)"

if ((ONLINE == 0)); then
  for service in "${RUNNING_SERVICES[@]}"; do
    case "$service" in
      postgres|allium-deck-data-init) ;;
      *) STOPPED_SERVICES+=("$service") ;;
    esac
  done
  if ((${#STOPPED_SERVICES[@]})); then
    printf '正在暂停写入服务以创建一致快照...\n' >&2
    compose stop "${STOPPED_SERVICES[@]}" >/dev/null
  fi
else
  printf '警告: --online 模式不会暂停写入服务，data/ 快照可能跨越多个写入时点。\n' >&2
fi

printf '正在导出 PostgreSQL...\n' >&2
compose exec -T postgres sh -eu -c \
  'exec pg_dump --clean --if-exists --no-owner --no-privileges -U "$POSTGRES_USER" -d "$POSTGRES_DB"' \
  >"$TEMP_DIR/database.sql"
[[ -s "$TEMP_DIR/database.sql" ]] || fail "pg_dump 输出为空"

GIT_COMMIT="unknown"
if command -v git >/dev/null 2>&1; then
  GIT_COMMIT="$(git -C "$ROOT_DIR" rev-parse HEAD 2>/dev/null || printf unknown)"
fi
python3 - "$TEMP_DIR/database.sql" "$TEMP_DIR/manifest.json" "$GIT_COMMIT" "$ONLINE" <<'PY'
import datetime
import hashlib
import json
import pathlib
import sys

dump_path = pathlib.Path(sys.argv[1])
manifest_path = pathlib.Path(sys.argv[2])
manifest = {
    "schema_version": 1,
    "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    "repository_commit": sys.argv[3],
    "online_snapshot": sys.argv[4] == "1",
    "database": {
        "file": "database.sql",
        "format": "plain-sql",
        "sha256": hashlib.sha256(dump_path.read_bytes()).hexdigest(),
    },
    "included": [".env", "config", "data"],
    "excluded": ["volumes/postgres"],
}
manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
PY

printf '正在打包 data、config、.env 与清单...\n' >&2
tar -czf "$PARTIAL" \
  -C "$ROOT_DIR" .env config data \
  -C "$TEMP_DIR" database.sql manifest.json
mv "$PARTIAL" "$OUTPUT"
PARTIAL=""
restart_stopped
printf '备份完成: %s\n' "$OUTPUT"
