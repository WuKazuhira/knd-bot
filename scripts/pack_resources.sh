#!/usr/bin/env bash
# 打包 Git 不跟踪的大体积静态资源，供 GitHub Release 分发。
# 部署方下载后在仓库根目录解压：tar xzf kndbot-resources.tar.gz
set -Eeuo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
PROJECT_ROOT=$(cd -- "$SCRIPT_DIR/.." && pwd)
OUTPUT=${1:-"$PROJECT_ROOT/kndbot-resources.tar.gz"}

cd "$PROJECT_ROOT"

for dir in data/resources data/pjsk/masterdata; do
    [[ -d "$dir" ]] || { echo "缺少目录：$dir" >&2; exit 1; }
done

tar czf "$OUTPUT" \
    --exclude='data/resources/temp' \
    data/resources \
    data/pjsk/masterdata

du -h "$OUTPUT"
echo "已生成资源包：$OUTPUT"
