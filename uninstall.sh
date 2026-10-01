#!/bin/bash
# FeigeLite 卸载脚本：删除程序、图标、启动项；保留设置和已下载的文件。
set -e
APP_ID="io.github.wisehai.FeigeLite"

rm -rf "$HOME/.local/share/feige-lite"
rm -f "$HOME/.local/share/icons/hicolor/scalable/apps/$APP_ID.svg" \
      "$HOME/.local/share/applications/$APP_ID.desktop" \
      "$HOME/.local/bin/feige"

echo "已卸载 FeigeLite。"
echo "保留了：设置 ~/.config/feige-lite、下载 ~/Downloads/feige、日志 ~/.cache/feige-lite（不需要可手动删除）"
echo "如需关闭防火墙端口：sudo ufw delete allow 2425/tcp && sudo ufw delete allow 2425/udp"
