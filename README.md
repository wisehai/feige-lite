# FeigeLite（飞鸽 Lite）

局域网传文件、发文字的小工具，兼容 Windows 上的**飞秋 / IP Messenger**（IPMsg 协议）。
GTK4 + libadwaita 界面，适用于 Omarchy / Arch Linux。

- 自动发现局域网里的飞秋 / FeigeLite / iptux，不用手动探测
- 发文字（带一键复制，适合传 Linux 命令）
- 把文件拖进窗口发送，带进度条；接收可手动点或设为自动
- 在线 / 离线状态（对方睡眠约 1～1.5 分钟后显示离线，唤醒后自动恢复）

## 安装

1. 下载安装包（或用 scp / U 盘从别的电脑复制过来）：
   ```bash
   curl -LO https://github.com/wisehai/feige-lite/releases/download/v0.1/feige-lite-0.1.tar.gz
   ```

2. 在另一台电脑的终端里运行：
   ```bash
   tar xzf feige-lite-0.1.tar.gz
   cd feige-lite
   bash install.sh
   ```
   脚本会提示输入 sudo 密码，用来：
   - 安装依赖 `python-gobject gtk4 libadwaita`（Omarchy 通常已装好）
   - 防火墙 ufw 放行 **2425 端口（TCP+UDP）**，这是飞秋协议的端口，不放行就互相看不到

3. 按 `Super + Space`，搜索 **FeigeLite**（或 `飞鸽` / `feiq` / `ipmsg`）打开。

安装位置：程序在 `~/.local/share/feige-lite/`，启动项和图标在 `~/.local/share/` 下，不改动系统目录。

## 使用

| 操作 | 方法 |
|---|---|
| 发文字 | 左侧选联系人 → 下方输入框输入 → 回车 |
| 复制收到的文字 | 点气泡旁的复制按钮 |
| 发文件 | 把文件拖进窗口，或点左下角回形针 |
| 收文件 | 点「接收」；收完可点「打开文件夹」 |
| 自动接收 | 右上角菜单 → 自动接收文件 |
| 下载位置 | 默认 `~/Downloads/feige`，菜单里可修改 |
| 刷新联系人 | 左上角刷新按钮（一般不需要，会自动探测） |

命令行版：终端里运行 `feige`，输入 `help` 查看命令。

## 注意事项

- **不能和 iptux 同时运行**：两者都要用 2425 端口。打开时提示"端口被占用"就先退出 iptux。
- **两台电脑要在同一网段**（如都是 `192.168.1.x`）。
- **Clash TUN**：程序会自动跳过 Clash 的虚拟网卡，并对整个网段逐个探测，所以开着 TUN 也能发现对方。
- **飞秋的中文编码**：使用 GBK，和飞秋互通不会乱码。
- 暂不支持传文件夹，可以先压缩成一个文件再发。

## 排查

- 日志：`~/.cache/feige-lite/feige.log`（记录上线/离线、错误）
- 调试模式（打印所有收到的包）：
  ```bash
  FEIGE_DEBUG=1 python3 ~/.local/share/feige-lite/feige_gtk.py
  ```
- 看不到对方：确认对方的飞秋已打开、Windows 防火墙放行了飞秋、本机 `sudo ufw status | grep 2425` 有规则。

## 卸载

```bash
cd feige-lite && bash uninstall.sh   # 在解压出来的目录里运行
```
会保留设置（`~/.config/feige-lite`）和下载的文件。防火墙规则需手动删除：
```bash
sudo ufw delete allow 2425/tcp && sudo ufw delete allow 2425/udp
```
