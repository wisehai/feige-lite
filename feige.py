#!/usr/bin/env python3
"""feige-lite 命令行版。"""
import os
import sys
import threading
import time

from feige_core import PORT, Node, human

DOWNLOAD_DIR = os.path.expanduser("~/Downloads/feige")

HELP = """命令：
  ls                      在线列表
  scan                    重新扫描局域网
  msg <编号|IP> <文字>     发文字
  send <编号|IP> <文件...> 发文件
  files                   待接收的文件
  get <编号|all>          接收文件到 ~/Downloads/feige
  wait <秒>               等待（脚本用）
  quit                    退出"""


def main():
    incoming = []
    node = None

    def on_event(kind, *args):
        nick = lambda ip: node.peers.get(ip, {}).get("nick", ip)
        if kind == "peer_up":
            ip, p = args
            print(f"\n[+] 上线：{p['nick']} ({p['host']}) {ip}")
        elif kind == "peer_down":
            ip, p = args
            print(f"\n[-] 下线：{p['nick']} {ip}")
        elif kind == "message":
            ip, text = args
            print(f"\n[{nick(ip)}] {text}")
        elif kind == "ack":
            print(f"\n[✓] {nick(args[0])} 已收到")
        elif kind == "file_offer":
            t = args[0]
            incoming.append(t)
            print(f"\n[文件] {nick(t.ip)} 发来 {t.name} ({human(t.size)})，输入 get {len(incoming)} 接收")
        elif kind == "done":
            t = args[0]
            print(f"\n[✓] {t.name} " + ("已发送" if t.direction == "out" else f"已保存 → {t.path}"))
        elif kind == "failed":
            t, err = args
            print(f"\n[!] {t.name} 失败：{err}")

    try:
        node = Node(on_event)
    except OSError as e:
        sys.exit(f"无法占用端口 {PORT}：{e}\n请先退出 iptux / 其他飞秋客户端")
    node.start()

    def peer_ip(arg):
        if arg in node.peers or arg.count(".") == 3:
            return arg
        return sorted(node.peers)[int(arg) - 1]

    print(f"feige-lite 已启动：{node.user}@{node.host}，网段 "
          + ", ".join(str(n) for _, n in node.nets) + "\n输入 help 查看命令")
    interactive = sys.stdin.isatty()
    while True:
        try:
            line = input("> " if interactive else "")
        except (EOFError, KeyboardInterrupt):
            break
        cmd, _, rest = line.strip().partition(" ")
        try:
            if cmd in ("", "#"):
                continue
            elif cmd == "help":
                print(HELP)
            elif cmd == "ls":
                for i, ip in enumerate(sorted(node.peers), 1):
                    p = node.peers[ip]
                    print(f"  {i}. {p['nick']} ({p['host']}) {ip}")
                if not node.peers:
                    print("  （暂无，可输入 scan）")
            elif cmd == "scan":
                threading.Thread(target=node.scan, daemon=True).start()
            elif cmd == "msg":
                target, _, text = rest.partition(" ")
                node.send_text(peer_ip(target), text)
            elif cmd == "send":
                target, *paths = rest.split()
                paths = [os.path.expanduser(p) for p in paths]
                bad = [p for p in paths if not os.path.isfile(p)]
                if bad:
                    print(f"  不是文件：{' '.join(bad)}")
                    continue
                node.send_files(peer_ip(target), paths)
                print(f"  已发出 {len(paths)} 个文件，等待对方接收")
            elif cmd == "files":
                for i, t in enumerate(incoming, 1):
                    print(f"  {i}. {t.name} ({human(t.size)}) 来自 {node.peers.get(t.ip, {}).get('nick', t.ip)}")
                if not incoming:
                    print("  （无）")
            elif cmd == "get":
                picks = incoming[:] if rest == "all" else [incoming[int(rest) - 1]]
                for t in picks:
                    node.fetch(t, DOWNLOAD_DIR)
                    incoming.remove(t)
            elif cmd == "wait":
                time.sleep(float(rest or 1))
            elif cmd in ("quit", "exit"):
                break
            else:
                print("  未知命令，输入 help")
        except (IndexError, ValueError):
            print("  参数不对，输入 help")
        except OSError as e:
            print(f"  网络错误：{e}")
    node.quit()


if __name__ == "__main__":
    main()
