#!/bin/bash
# FeigeLite 安装脚本：普通用户运行，需要 sudo 的步骤会提示输入密码。
#   bash install.sh            完整安装
#   bash install.sh --no-sudo  跳过依赖安装和防火墙设置
set -e

SRC="$(cd "$(dirname "$0")" && pwd)"
APPDIR="$HOME/.local/share/feige-lite"
APP_ID="io.github.wisehai.FeigeLite"
NO_SUDO=0
[ "$1" = "--no-sudo" ] && NO_SUDO=1

if [ "$NO_SUDO" = 0 ]; then
  echo "==> 安装依赖（python-gobject / gtk4 / libadwaita）"
  sudo pacman -S --needed --noconfirm python python-gobject gtk4 libadwaita

  if systemctl is-active --quiet ufw; then
    echo "==> 防火墙放行 2425（TCP+UDP，飞秋/IPMsg 协议端口）"
    sudo ufw allow 2425/tcp comment 'feige-lite'
    sudo ufw allow 2425/udp comment 'feige-lite'
  fi
fi

echo "==> 复制程序到 $APPDIR"
mkdir -p "$APPDIR"
install -m 644 "$SRC"/feige_core.py "$SRC"/feige_gtk.py "$APPDIR"/
install -m 755 "$SRC"/feige.py "$APPDIR"/

echo "==> 安装图标和启动项"
install -Dm 644 "$SRC/data/$APP_ID.svg" "$HOME/.local/share/icons/hicolor/scalable/apps/$APP_ID.svg"
mkdir -p "$HOME/.local/share/applications"
sed "s|@APPDIR@|$APPDIR|g" "$SRC/data/$APP_ID.desktop.in" > "$HOME/.local/share/applications/$APP_ID.desktop"
command -v desktop-file-validate >/dev/null && desktop-file-validate "$HOME/.local/share/applications/$APP_ID.desktop"

echo "==> 安装命令行版：~/.local/bin/feige"
mkdir -p "$HOME/.local/bin"
printf '#!/bin/sh\nexec python3 "%s/feige.py" "$@"\n' "$APPDIR" > "$HOME/.local/bin/feige"
chmod 755 "$HOME/.local/bin/feige"

echo
echo "安装完成。按 Super+Space 搜索 FeigeLite（或 飞鸽 / feiq）打开。"
if pgrep -x iptux >/dev/null; then
  echo "注意：iptux 正在运行，它会占用 2425 端口，请先退出 iptux。"
fi
