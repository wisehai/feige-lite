"""feige-lite 协议核心：IPMsg/飞秋 的发现、文字、文件收发。

协议：UDP 2425 收发控制包，TCP 2425 传文件。
包格式：版本:包编号:用户名:主机名:命令:附加数据

界面层通过 Node(on_event) 接收事件，回调在后台线程里触发：
  peer_up(ip, peer) / peer_down(ip, peer) / message(ip, text) / ack(ip)
  file_offer(t) / progress(t) / done(t) / failed(t, error)
"""
import getpass
import ipaddress
import itertools
import json
import os
import re
import socket
import subprocess
import threading
import time

PORT = 2425
ENC = "gbk"  # 飞秋默认 GBK
SKIP_NETS = [ipaddress.ip_network("198.18.0.0/15")]  # Clash TUN
SKIP_IFACES = {"lo", "Meta"}

# 命令（低 8 位）
BR_ENTRY, BR_EXIT, ANSENTRY, BR_ABSENCE = 0x01, 0x02, 0x03, 0x04
SENDMSG, RECVMSG = 0x20, 0x21
GETFILEDATA = 0x60
# 选项（高位）
SENDCHECKOPT = 0x100
FILEATTACHOPT = 0x200000
FILE_REGULAR = 0x1

FONT_RE = re.compile(r"\{/font;[^}]*\}")  # 飞秋附在消息末尾的字体信息
CHUNK = 256 * 1024
DEBUG = bool(os.environ.get("FEIGE_DEBUG"))
PROBE_INTERVAL = 30  # 秒
PROBE_MISSES = 2


def log(msg):
    print(time.strftime("%H:%M:%S"), msg, flush=True)


def dec(b):
    return b.decode(ENC, "replace")


def enc(s):
    return s.encode(ENC, "replace")


def parse(data):
    parts = data.split(b":", 5)
    if len(parts) < 5:
        return None
    try:
        cmd = int(parts[4])
    except ValueError:
        return None
    return {
        "ver": dec(parts[0]), "pkt": dec(parts[1]), "user": dec(parts[2]),
        "host": dec(parts[3]), "cmd": cmd, "extra": parts[5] if len(parts) > 5 else b"",
    }


def parse_attachments(blob):
    """fileID:文件名:大小:修改时间:属性[:...]:\\a ... （数值为十六进制，文件名中 : 转义为 ::）"""
    files = []
    for entry in blob.split(b"\x07"):
        entry = entry.strip(b"\0")
        if not entry:
            continue
        fields = [f.replace(b"\x01", b":") for f in entry.replace(b"::", b"\x01").split(b":")]
        if len(fields) < 5:
            continue
        try:
            files.append({
                "fid": dec(fields[0]), "name": dec(fields[1]),
                "size": int(fields[2], 16), "attr": int(fields[4], 16),
            })
        except ValueError:
            continue
    return files


def local_networks():
    """返回 [(本机IP, 网段)]，跳过回环和 Clash TUN。"""
    out = subprocess.run(["ip", "-j", "-4", "addr"], capture_output=True, text=True).stdout
    nets = []
    for iface in json.loads(out or "[]"):
        if iface.get("ifname") in SKIP_IFACES:
            continue
        for a in iface.get("addr_info", []):
            net = ipaddress.ip_interface(f"{a['local']}/{a['prefixlen']}").network
            if any(net.subnet_of(s) for s in SKIP_NETS if s.prefixlen <= net.prefixlen):
                continue
            nets.append((a["local"], net))
    return nets


def human(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f}{unit}" if unit == "B" else f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}TB"


def unique_path(path):
    base, ext = os.path.splitext(path)
    i = 1
    while os.path.exists(path):
        path = f"{base}({i}){ext}"
        i += 1
    return path


class Transfer:
    _ids = itertools.count(1)

    def __init__(self, direction, ip, name, size, path=None, pkt=None, fid=None):
        self.id = next(self._ids)
        self.direction = direction  # "in" / "out"
        self.ip, self.name, self.size = ip, name, size
        self.path, self.pkt, self.fid = path, pkt, fid
        self.done = 0
        self.state = "waiting"  # waiting / running / done / failed


class Node:
    def __init__(self, on_event=None, nick=None, known_ips=()):
        self.on_event = on_event or (lambda *a: None)
        self.user = getpass.getuser()
        self.host = socket.gethostname()
        self.nick = nick or self.user
        self.pktno = int(time.time())
        self.lock = threading.Lock()
        self.peers = {}      # ip -> {nick, user, host}
        self.offers = {}     # 我发出的文件：(pktno, fid) -> Transfer
        self.nets = local_networks()
        self.local_ips = {ip for ip, _ in self.nets} | {"127.0.0.1"}
        self.known_ips = set(known_ips)  # 以前见过的联系人，优先单播探测
        self.last_seen = {}  # ip -> 最近一次确认在线的时间
        self.gone = {}       # 已离线的联系人 ip -> 信息，持续探测以便自动恢复

        # UDP 不设 SO_REUSEADDR：iptux 占着端口时直接报错，而不是两边抢包
        self.udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.udp.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        self.udp.bind(("", PORT))
        self.tcp = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.tcp.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.tcp.bind(("", PORT))
        self.tcp.listen(8)

    def start(self):
        threading.Thread(target=self.udp_loop, daemon=True).start()
        threading.Thread(target=self.tcp_loop, daemon=True).start()
        threading.Thread(target=self.discover_loop, daemon=True).start()

    def emit(self, kind, *args):
        try:
            self.on_event(kind, *args)
        except Exception as e:
            print(f"[!] 事件 {kind} 处理出错：{e}")

    # ---------- 发包 ----------
    def build(self, cmd, extra=b""):
        with self.lock:
            self.pktno += 1
            pkt = self.pktno
        if isinstance(extra, str):
            extra = enc(extra)
        return pkt, enc(f"1:{pkt}:{self.user}:{self.host}:{cmd}:") + extra

    def send(self, ip, cmd, extra=b""):
        pkt, data = self.build(cmd, extra)
        self.udp.sendto(data, (ip, PORT))
        return pkt

    def entry_extra(self):
        return enc(self.nick) + b"\0"

    def discover_loop(self):
        """启动时多扫几次，之后定期探测在线状态。

        飞秋不会每次都回应重复的 BR_ENTRY，所以在线判断改用 TCP 2425 能否连上：
        连续 PROBE_MISSES 次连不上视为离线（如 Windows 睡眠），离线后连得上就自动恢复。"""
        for delay in (0, 3, 7):
            time.sleep(delay)
            self.scan()
        while True:
            time.sleep(PROBE_INTERVAL)
            try:
                self.probe_all()
                deadline = time.monotonic() - PROBE_INTERVAL * PROBE_MISSES - 5
                for ip in [ip for ip in list(self.peers) if self.last_seen.get(ip, 0) < deadline]:
                    self.mark_gone(ip, "探测超时")
                self.scan(full=False)
            except Exception as e:  # 探测线程不能死，否则离线检测失效
                log(f"探测出错：{e!r}")

    def probe_all(self):
        """并行 TCP 探测所有在线和已离线的联系人。"""
        def probe(ip):
            try:
                socket.create_connection((ip, PORT), timeout=3).close()
            except OSError:
                return
            self.last_seen[ip] = time.monotonic()
            peer = self.gone.pop(ip, None)
            if peer and ip not in self.peers:
                log(f"{ip} 恢复在线")
                self.peers[ip] = peer
                self.emit("peer_up", ip, peer)

        threads = [threading.Thread(target=probe, args=(ip,), daemon=True)
                   for ip in set(self.peers) | set(self.gone)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

    def mark_gone(self, ip, reason):
        peer = self.peers.pop(ip, None)
        if peer:
            log(f"{ip} 离线：{reason}")
            self.gone[ip] = peer
            self.emit("peer_down", ip, peer)

    def scan(self, full=True):
        """单播探测已知联系人 + 网段广播；full 时再逐个单播整个网段
        （Clash TUN 会吞掉 255.255.255.255 广播）。"""
        _, data = self.build(BR_ENTRY, self.entry_extra())
        for ip in self.known_ips | set(self.peers):
            try:
                self.udp.sendto(data, (ip, PORT))
            except OSError:
                pass
        for _, net in self.nets:
            targets = [net.broadcast_address]
            if full and net.num_addresses <= 1024:
                targets += list(net.hosts())
            for i, ip in enumerate(targets):
                if str(ip) in self.local_ips:
                    continue
                try:
                    self.udp.sendto(data, (str(ip), PORT))
                except OSError:
                    pass
                if i % 64 == 63:
                    time.sleep(0.01)

    def send_text(self, ip, text):
        self.send(ip, SENDMSG | SENDCHECKOPT, text)

    def send_files(self, ip, paths, text=""):
        """发出文件邀请，返回 [Transfer]；对方来取时才真正传输。"""
        entries, transfers = [], []
        for fid, path in enumerate(paths):
            st = os.stat(path)
            name = os.path.basename(path)
            entries.append(enc(f"{fid}:{name.replace(':', '::')}:{st.st_size:x}:"
                               f"{int(st.st_mtime):x}:{FILE_REGULAR:x}:") + b"\x07")
            transfers.append(Transfer("out", ip, name, st.st_size, path=path, fid=fid))
        pkt, data = self.build(SENDMSG | SENDCHECKOPT | FILEATTACHOPT,
                               enc(text) + b"\0" + b"".join(entries) + b"\0")
        for t in transfers:
            t.pkt = pkt
            self.offers[(pkt, t.fid)] = t
        self.udp.sendto(data, (ip, PORT))
        return transfers

    # ---------- 收包 ----------
    def udp_loop(self):
        while True:
            data, (ip, _) = self.udp.recvfrom(65535)
            if DEBUG:
                log(f"<- {ip} {data[:100]!r}")
            if ip in self.local_ips:
                continue
            self.last_seen[ip] = time.monotonic()
            p = parse(data)
            if p:
                try:
                    self.handle(ip, p)
                except Exception as e:  # 单个坏包不影响主循环
                    print(f"[!] 处理 {ip} 的包出错：{e}")

    def add_peer(self, ip, nick, p):
        self.gone.pop(ip, None)
        new = ip not in self.peers
        self.peers[ip] = {"nick": nick or p["user"], "user": p["user"], "host": p["host"]}
        if new:
            self.emit("peer_up", ip, self.peers[ip])

    def handle(self, ip, p):
        mode, cmd = p["cmd"] & 0xFF, p["cmd"]
        parts = p["extra"].split(b"\0")
        if mode in (BR_ENTRY, ANSENTRY, BR_ABSENCE):
            self.add_peer(ip, dec(parts[0]), p)
            if mode == BR_ENTRY:
                self.send(ip, ANSENTRY, self.entry_extra())
        elif mode == BR_EXIT:
            self.mark_gone(ip, "对方退出")
        elif mode == SENDMSG:
            if cmd & SENDCHECKOPT:
                self.send(ip, RECVMSG, p["pkt"])
            if ip not in self.peers:
                self.add_peer(ip, "", p)
            text = FONT_RE.sub("", dec(parts[0])).strip()
            if text:
                self.emit("message", ip, text)
            if cmd & FILEATTACHOPT and len(parts) > 1:
                for f in parse_attachments(parts[1]):
                    if f["attr"] & 0xFF != FILE_REGULAR:
                        self.emit("message", ip, f"[对方发来文件夹 {f['name']}，暂不支持]")
                        continue
                    self.emit("file_offer", Transfer("in", ip, f["name"], f["size"],
                                                     pkt=p["pkt"], fid=f["fid"]))
        elif mode == RECVMSG:
            self.emit("ack", ip)

    # ---------- 文件：对方来取 ----------
    def tcp_loop(self):
        while True:
            conn, (ip, _) = self.tcp.accept()
            threading.Thread(target=self.serve_file, args=(conn, ip), daemon=True).start()

    def serve_file(self, conn, ip):
        with conn:
            conn.settimeout(30)
            p = parse(conn.recv(4096))
            if not p or p["cmd"] & 0xFF != GETFILEDATA:
                return
            try:
                pkt, fid, offset = (int(x, 16) for x in
                                    dec(p["extra"].split(b"\0")[0]).split(":")[:3])
            except ValueError:
                return
            t = self.offers.get((pkt, fid))
            if not t:
                return
            t.state, t.done = "running", offset
            try:
                with open(t.path, "rb") as f:
                    f.seek(offset)
                    self._pump(t, f.read, conn.sendall)
                t.state = "done"
                self.emit("done", t)
            except OSError as e:
                t.state = "failed"
                self.emit("failed", t, str(e))

    # ---------- 文件：去对方取 ----------
    def fetch(self, t, dest_dir):
        """阻塞下载，界面应在后台线程调用。"""
        os.makedirs(dest_dir, exist_ok=True)
        t.path = unique_path(os.path.join(dest_dir, os.path.basename(t.name)))
        t.state, t.done = "running", 0
        _, req = self.build(GETFILEDATA, f"{int(t.pkt):x}:{t.fid}:0")
        try:
            with socket.create_connection((t.ip, PORT), timeout=30) as s, open(t.path, "wb") as out:
                s.sendall(req)
                self._pump(t, lambda n: s.recv(min(n, t.size - t.done)), out.write)
            if t.done < t.size:
                raise OSError(f"连接中断，只收到 {human(t.done)} / {human(t.size)}")
            t.state = "done"
            self.emit("done", t)
        except OSError as e:
            t.state = "failed"
            try:
                os.remove(t.path)  # 不留半截文件，重试时重新下载
            except OSError:
                pass
            self.emit("failed", t, str(e))

    def _pump(self, t, read, write):
        last = 0.0
        while t.done < t.size:
            chunk = read(CHUNK)
            if not chunk:
                break
            write(chunk)
            t.done += len(chunk)
            now = time.monotonic()
            if now - last > 0.1:
                last = now
                self.emit("progress", t)

    def quit(self):
        _, data = self.build(BR_EXIT, self.entry_extra())
        targets = list(self.peers) + [str(net.broadcast_address) for _, net in self.nets]
        for ip in targets:
            try:
                self.udp.sendto(data, (ip, PORT))
            except OSError:
                pass
