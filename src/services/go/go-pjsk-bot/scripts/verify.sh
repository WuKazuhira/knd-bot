#!/usr/bin/env bash
# go-pjsk-bot 统一验证入口：一条命令跑完全部检查，便于 CI 与提交前自检。
#
#   bash src/services/go/go-pjsk-bot/scripts/verify.sh
#
# 依次执行：
#   1) gofmt 格式检查        （Go 代码规范）
#   2) go build ./...        （可编译）
#   3) go vet ./...          （静态检查）
#   4) go test ./...         （Go 单元测试）
#   5) check_ownership_sync  （Go/Python 命令所有权映射一致，防双回复漂移）
#   6) check_draw_tasks      （Go 出图 task 名都存在于 Python 侧）
#   7) Python go_ownership 单元测试（归一化/所有权/正则兜底）
#
# Go 步骤在 golang:1.25 容器里跑（无需本机 Go 工具链）；若已装本机 go，
# 设 USE_LOCAL_GO=1 直接用本机工具链。

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_ROOT="$(cd "$BOT_DIR/../../../.." && pwd)"

fail=0
step() { printf '\n=== %s ===\n' "$1"; }

run_go_checks() {
  # go test 不经管道过滤，避免 grep 的退出码掩盖测试失败（假绿灯）。
  # "no test files" 行仅是提示，一并显示无妨。
  local sh='set -e; if gofmt -l . | grep -q .; then echo "GOFMT 未通过:"; gofmt -l .; exit 1; fi; echo "GOFMT OK"; echo "--- build ---"; go build ./...; echo "--- vet ---"; go vet ./...; echo "--- test ---"; go test ./...'
  if [ "${USE_LOCAL_GO:-0}" = "1" ] && command -v go >/dev/null 2>&1; then
    ( cd "$BOT_DIR" && bash -c "$sh" )
  else
    docker run --rm -v "$BOT_DIR":/w -w /w \
      -e GOPROXY="${GOPROXY:-https://goproxy.cn,direct}" -e GOSUMDB=off \
      golang:1.25-alpine sh -c "$sh"
  fi
}

step "1-4) Go gofmt/build/vet/test"
run_go_checks || fail=1

step "5) 命令所有权映射一致性"
python3 "$SCRIPT_DIR/check_ownership_sync.py" || fail=1

step "6) 出图 task 名有效性"
python3 "$SCRIPT_DIR/check_draw_tasks.py" || fail=1

step "7) Python go_ownership 单元测试"
python3 "$REPO_ROOT/src/services/tests/test_go_ownership.py" || fail=1

printf '\n========================\n'
if [ "$fail" -eq 0 ]; then
  echo "✓ 全部验证通过"
else
  echo "✗ 有验证未通过（见上）"
fi
exit "$fail"
