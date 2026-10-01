#!/bin/bash
# 打包：bash build.sh [版本号]  →  dist/feige-lite-<版本>.tar.gz
set -e
VERSION="${1:-0.1}"
cd "$(dirname "$0")"
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT
mkdir -p "$STAGE/feige-lite/data" dist
cp feige_core.py feige_gtk.py feige.py install.sh uninstall.sh README.md LICENSE "$STAGE/feige-lite/"
cp data/*.svg data/*.desktop.in "$STAGE/feige-lite/data/"
# 不记录本机用户名/属组
tar czf "dist/feige-lite-$VERSION.tar.gz" --owner=0 --group=0 --numeric-owner -C "$STAGE" feige-lite
echo "dist/feige-lite-$VERSION.tar.gz"
