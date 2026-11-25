#!/usr/bin/env bash
# 打包 data/resources 和 data/pjsk/static 静态素材并生成 sha256
set -Eeuo pipefail

usage() {
    cat <<'EOF'
用法：scripts/ops/pack_resources.sh [OUTPUT_TAR_GZ]

说明：
  将 data/resources 与 data/pjsk/static 打包为 .tar.gz，并生成对应的 .sha256 校验文件。
  默认跳过临时目录、群白名单运行时文件和 Python 缓存（__pycache__/*.pyc）。

参数：
  OUTPUT_TAR_GZ   输出压缩包路径（默认：<仓库根目录>/kndbot-resources.tar.gz）
  -h, --help      显示帮助
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
    usage
    exit 0
fi

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
PROJECT_ROOT=$(cd -- "$SCRIPT_DIR/../.." && pwd)
OUTPUT=${1:-"$PROJECT_ROOT/kndbot-resources.tar.gz"}

case "$OUTPUT" in
    /*) ;;
    *) OUTPUT="$PWD/$OUTPUT" ;;
esac

OUTPUT_DIR=$(dirname -- "$OUTPUT")
OUTPUT_NAME=$(basename -- "$OUTPUT")
SHA_FILE="${OUTPUT}.sha256"

cd "$PROJECT_ROOT"

for dir in data/resources data/pjsk/static; do
    [[ -d "$dir" ]] || { echo "缺少目录：$dir" >&2; exit 1; }
done

mkdir -p "$OUTPUT_DIR"

tar czf "$OUTPUT" \
    --exclude='data/resources/temp' \
    --exclude='cn_msr_allowed_groups.json' \
    --exclude='__pycache__' \
    --exclude='*.pyc' \
    --exclude='.DS_Store' \
    data/resources \
    data/pjsk/static

if command -v sha256sum >/dev/null 2>&1; then
    (cd "$OUTPUT_DIR" && sha256sum "$OUTPUT_NAME" > "$(basename -- "$SHA_FILE")")
elif command -v shasum >/dev/null 2>&1; then
    (cd "$OUTPUT_DIR" && shasum -a 256 "$OUTPUT_NAME" > "$(basename -- "$SHA_FILE")")
else
    echo "警告：未找到 sha256sum 或 shasum，跳过生成 .sha256" >&2
    SHA_FILE=""
fi

du -h "$OUTPUT"
echo "已生成资源包：$OUTPUT"
if [[ -n "$SHA_FILE" ]]; then
    echo "已生成校验文件：$SHA_FILE"
    cat "$SHA_FILE"
fi
echo ""
echo "提示：静态字体与游戏图片素材建议发布至私有 GitHub Release（单文件上限 2GB），例如："
echo "  gh release upload <tag> \"$OUTPUT\"${SHA_FILE:+ \"$SHA_FILE\"} --clobber"
