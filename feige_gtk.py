#!/usr/bin/env python3
"""feige-lite 图形界面（GTK4 + libadwaita）。"""
import json
import os
import sys
import threading

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Pango  # noqa: E402

from feige_core import PORT, Node, human  # noqa: E402

APP_ID = "io.github.wisehai.FeigeLite"
CONFIG = os.path.expanduser("~/.config/feige-lite/settings.json")
DEFAULTS = {"download_dir": os.path.expanduser("~/Downloads/feige"), "auto_accept": False, "known_ips": []}

CSS = """
.bubble { padding: 8px 12px; border-radius: 12px; }
.bubble.in { background: alpha(@view_fg_color, 0.08); }
.bubble.out { background: alpha(@accent_bg_color, 0.30); }
.transfer { padding: 12px; }
.unread { color: @accent_color; font-size: 9px; }
.status { padding: 1px 8px; border-radius: 999px; font-size: smaller; font-weight: bold; }
.status.online { color: @success_color; background: alpha(@success_color, 0.15); }
.status.offline { color: alpha(@view_fg_color, 0.55); background: alpha(@view_fg_color, 0.08); }
"""


def load_settings():
    try:
        with open(CONFIG) as f:
            return {**DEFAULTS, **json.load(f)}
    except (OSError, ValueError):
        return dict(DEFAULTS)


def save_settings(settings):
    os.makedirs(os.path.dirname(CONFIG), exist_ok=True)
    with open(CONFIG, "w") as f:
        json.dump(settings, f, ensure_ascii=False, indent=2)


def copy_text(widget, text):
    widget.get_clipboard().set(text)


class TransferRow(Gtk.Box):
    def __init__(self, win, t):
        out = t.direction == "out"
        super().__init__(spacing=12, width_request=380, css_classes=["card", "transfer"],
                         halign=Gtk.Align.END if out else Gtk.Align.START)
        self.win, self.t, self.error = win, t, None
        self.append(Gtk.Image(icon_name="document-send-symbolic" if out else "folder-download-symbolic",
                              pixel_size=24, valign=Gtk.Align.CENTER))
        info = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, hexpand=True)
        info.append(Gtk.Label(label=t.name, xalign=0, ellipsize=Pango.EllipsizeMode.MIDDLE,
                              css_classes=["heading"], tooltip_text=t.name))
        self.bar = Gtk.ProgressBar()
        info.append(self.bar)
        self.status = Gtk.Label(xalign=0, css_classes=["caption", "dim-label"],
                                ellipsize=Pango.EllipsizeMode.END)
        info.append(self.status)
        self.append(info)
        self.button = Gtk.Button(valign=Gtk.Align.CENTER)
        self.button.connect("clicked", self.on_click)
        self.append(self.button)
        self.update()

    def update(self, error=None):
        t = self.t
        self.error = error or self.error
        out, size = t.direction == "out", human(t.size)
        self.bar.set_fraction(0 if t.state == "waiting" else (t.done / t.size if t.size else 1))
        self.status.set_label({
            "waiting": f"等待对方接收 · {size}" if out else size,
            "running": f"{human(t.done)} / {size}",
            "done": f"{'已发送' if out else '已保存'} · {size}",
            "failed": f"失败：{self.error}",
        }[t.state])
        label = None if out else {"waiting": "接收", "done": "打开文件夹", "failed": "重试"}.get(t.state)
        self.button.set_visible(label is not None)
        if label:
            self.button.set_label(label)
            if t.state == "waiting":
                self.button.add_css_class("suggested-action")
            else:
                self.button.remove_css_class("suggested-action")

    def on_click(self, _btn):
        if self.t.state in ("waiting", "failed"):
            self.win.start_fetch(self.t)
        elif self.t.state == "done":
            launcher = Gtk.FileLauncher.new(Gio.File.new_for_path(self.t.path))
            launcher.open_containing_folder(self.win, None, lambda src, res: _finish(src.open_containing_folder_finish, res))


def _finish(fn, res):
    try:
        fn(res)
    except GLib.Error:
        pass


class PeerPage(Gtk.ScrolledWindow):
    """和某个联系人的收发记录。"""

    def __init__(self):
        super().__init__(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        self.box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10,
                           margin_top=16, margin_bottom=16, margin_start=16, margin_end=16)
        self.set_child(Adw.Clamp(maximum_size=820, child=self.box))

    def append(self, widget):
        self.box.append(widget)
        GLib.timeout_add(60, self._scroll_bottom)

    def _scroll_bottom(self):
        adj = self.get_vadjustment()
        adj.set_value(adj.get_upper())
        return False

    def add_text(self, text, out):
        row = Gtk.Box(spacing=4, halign=Gtk.Align.END if out else Gtk.Align.START)
        label = Gtk.Label(label=text, wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR, selectable=True,
                          xalign=0, max_width_chars=64,
                          css_classes=["bubble", "out" if out else "in", "monospace"])
        copy = Gtk.Button(icon_name="edit-copy-symbolic", tooltip_text="复制",
                          css_classes=["flat", "circular"], valign=Gtk.Align.CENTER)
        copy.connect("clicked", lambda *_: copy_text(copy, text))
        for w in ((copy, label) if out else (label, copy)):
            row.append(w)
        self.append(row)


class PeerRow(Adw.ActionRow):
    def __init__(self, ip, peer):
        super().__init__(title=GLib.markup_escape_text(peer["nick"]), subtitle_lines=1, title_lines=1)
        self.ip = ip
        self.avatar = Adw.Avatar(size=32, text=peer["nick"], show_initials=True)
        self.add_prefix(self.avatar)
        self.dot = Gtk.Label(label="●", css_classes=["unread"], visible=False)
        self.add_suffix(self.dot)
        self.status = Gtk.Label(css_classes=["status"], valign=Gtk.Align.CENTER)
        self.add_suffix(self.status)
        self.set_peer(peer, True)

    def set_peer(self, peer, online):
        self.peer, self.online = peer, online
        self.set_title(GLib.markup_escape_text(peer["nick"]))
        self.set_subtitle(GLib.markup_escape_text(f"{peer['host']} · {self.ip}"))
        self.status.set_label("在线" if online else "离线")
        self.status.set_css_classes(["status", "online" if online else "offline"])
        self.avatar.set_opacity(1 if online else 0.4)


class Window(Adw.ApplicationWindow):
    def __init__(self, app, node, settings):
        super().__init__(application=app, title="飞鸽 Lite", default_width=960, default_height=640)
        self.node, self.settings = node, settings
        self.pages, self.rows, self.transfers = {}, {}, {}
        self.current = None

        # 侧栏：在线列表
        refresh = Gtk.Button(icon_name="view-refresh-symbolic", tooltip_text="重新扫描局域网")
        refresh.connect("clicked", self.on_refresh)
        menu = Gio.Menu()
        menu.append("自动接收文件", "win.auto-accept")
        menu.append("打开下载文件夹", "win.open-downloads")
        menu.append("更改下载位置…", "win.choose-downloads")
        sb_header = Adw.HeaderBar()
        sb_header.pack_start(refresh)
        sb_header.pack_end(Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu))
        self.peer_list = Gtk.ListBox(css_classes=["navigation-sidebar"])
        self.peer_list.set_placeholder(Gtk.Label(label="正在搜索局域网…", css_classes=["dim-label"],
                                                 margin_top=24))
        self.peer_list.set_sort_func(self.sort_peers)
        self.peer_list.connect("row-selected", self.on_peer_selected)
        sidebar = Adw.ToolbarView(content=Gtk.ScrolledWindow(child=self.peer_list, vexpand=True))
        sidebar.add_top_bar(sb_header)

        # 内容：收发记录 + 输入栏
        self.title = Adw.WindowTitle(title="飞鸽 Lite")
        header = Adw.HeaderBar(title_widget=self.title)
        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        self.stack.add_named(Adw.StatusPage(icon_name="network-workgroup-symbolic", title="选择一个联系人",
                                            description="在左侧选择在线设备，然后发文字，或者把文件拖到这里"),
                             "empty")
        self.entry = Gtk.Entry(hexpand=True, placeholder_text="输入文字或命令，回车发送")
        self.entry.connect("activate", self.on_send_text)
        attach = Gtk.Button(icon_name="mail-attachment-symbolic", tooltip_text="发送文件")
        attach.connect("clicked", self.on_attach)
        send = Gtk.Button(icon_name="document-send-symbolic", tooltip_text="发送",
                          css_classes=["suggested-action", "circular"])
        send.connect("clicked", self.on_send_text)
        self.bottom = Gtk.Box(spacing=6, margin_top=6, margin_bottom=6, margin_start=12, margin_end=12,
                              sensitive=False)
        for w in (attach, self.entry, send):
            self.bottom.append(w)
        content = Adw.ToolbarView(content=self.stack)
        content.add_top_bar(header)
        content.add_bottom_bar(self.bottom)
        drop = Gtk.DropTarget.new(Gdk.FileList, Gdk.DragAction.COPY)
        drop.connect("drop", self.on_drop)
        content.add_controller(drop)

        self.split = Adw.NavigationSplitView(min_sidebar_width=240, max_sidebar_width=320)
        self.split.set_sidebar(Adw.NavigationPage(title="飞鸽 Lite", child=sidebar))
        self.split.set_content(Adw.NavigationPage(title="收发", child=content))
        self.toasts = Adw.ToastOverlay(child=self.split)
        self.set_content(self.toasts)

        bp = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 560sp"))
        bp.add_setter(self.split, "collapsed", True)
        self.add_breakpoint(bp)

        auto = Gio.SimpleAction.new_stateful("auto-accept", None,
                                             GLib.Variant.new_boolean(settings["auto_accept"]))
        auto.connect("change-state", self.on_auto_accept)
        self.add_action(auto)
        for name, cb in (("open-downloads", self.on_open_downloads),
                         ("choose-downloads", self.on_choose_downloads)):
            act = Gio.SimpleAction.new(name, None)
            act.connect("activate", cb)
            self.add_action(act)

        self.connect("close-request", lambda *_: self.node.quit() or False)

    # ---------- 核心事件（后台线程 → 主线程）----------
    def on_core_event(self, kind, *args):
        GLib.idle_add(self.dispatch, kind, args)

    def dispatch(self, kind, args):
        if kind == "peer_up":
            ip, peer = args
            self.ensure_peer(ip, peer).set_peer(peer, True)
            if ip not in self.settings["known_ips"]:
                self.settings["known_ips"].append(ip)
                save_settings(self.settings)
            self.peer_list.invalidate_sort()
            self.refresh_title()
        elif kind == "peer_down":
            ip, peer = args
            if ip in self.rows:
                self.rows[ip].set_peer(peer, False)
                self.peer_list.invalidate_sort()
                self.refresh_title()
        elif kind == "message":
            ip, text = args
            self.page_for(ip).add_text(text, False)
            self.mark_unread(ip)
            self.notify(self.nick(ip), text)
        elif kind == "file_offer":
            t = args[0]
            self.add_transfer(t)
            self.mark_unread(t.ip)
            self.notify(self.nick(t.ip), f"发来文件 {t.name} ({human(t.size)})")
            if self.settings["auto_accept"]:
                self.start_fetch(t)
        elif kind in ("progress", "done", "failed"):
            t = args[0]
            row = self.transfers.get(t.id)
            if row:
                row.update(args[1] if kind == "failed" else None)
            if kind == "done" and t.direction == "in":
                self.toast(f"已保存 {t.name}")
            elif kind == "failed":
                self.toast(f"{t.name} 失败：{args[1]}")
        return False

    # ---------- 联系人 ----------
    @staticmethod
    def sort_peers(a, b):
        ka, kb = (not a.online, a.peer["nick"], a.ip), (not b.online, b.peer["nick"], b.ip)
        return (ka > kb) - (ka < kb)

    def nick(self, ip):
        row = self.rows.get(ip)
        return row.peer["nick"] if row else ip

    def ensure_peer(self, ip, peer):
        if ip not in self.rows:
            row = PeerRow(ip, peer)
            self.rows[ip] = row
            self.peer_list.append(row)
        return self.rows[ip]

    def page_for(self, ip):
        if ip not in self.pages:
            self.ensure_peer(ip, self.node.peers.get(ip) or {"nick": ip, "host": "?"})
            self.pages[ip] = PeerPage()
            self.stack.add_named(self.pages[ip], ip)
        return self.pages[ip]

    def mark_unread(self, ip):
        if ip != self.current:
            self.rows[ip].dot.set_visible(True)

    def on_peer_selected(self, _list, row):
        if not row:
            return
        self.current = row.ip
        row.dot.set_visible(False)
        self.stack.set_visible_child(self.page_for(row.ip))
        self.bottom.set_sensitive(True)
        self.refresh_title()
        self.split.set_show_content(True)
        self.entry.grab_focus()

    def refresh_title(self):
        row = self.rows.get(self.current)
        if row:
            self.title.set_title(row.peer["nick"])
            self.title.set_subtitle(f"{row.peer['host']} · {row.ip}" + ("" if row.online else " · 离线"))

    def on_refresh(self, _btn):
        threading.Thread(target=self.node.scan, daemon=True).start()
        self.toast("正在扫描局域网…")

    # ---------- 发送 ----------
    def on_send_text(self, *_):
        text = self.entry.get_text().strip()
        if not (self.current and text):
            return
        try:
            self.node.send_text(self.current, text)
        except OSError as e:
            self.toast(f"发送失败：{e}")
            return
        self.page_for(self.current).add_text(text, True)
        self.entry.set_text("")

    def send_paths(self, paths):
        if not self.current:
            self.toast("先在左侧选择一个联系人")
            return
        files = [p for p in paths if os.path.isfile(p)]
        if len(files) < len(paths):
            self.toast("暂不支持发送文件夹，已跳过")
        if not files:
            return
        try:
            for t in self.node.send_files(self.current, files):
                self.add_transfer(t)
        except OSError as e:
            self.toast(f"发送失败：{e}")

    def on_attach(self, _btn):
        dialog = Gtk.FileDialog(title="选择要发送的文件")
        dialog.open_multiple(self, None, self.on_attach_done)

    def on_attach_done(self, dialog, res):
        try:
            files = dialog.open_multiple_finish(res)
        except GLib.Error:
            return
        self.send_paths([f.get_path() for f in files if f.get_path()])

    def on_drop(self, _target, value, _x, _y):
        self.send_paths([f.get_path() for f in value.get_files() if f.get_path()])
        return True

    # ---------- 文件 ----------
    def add_transfer(self, t):
        row = TransferRow(self, t)
        self.transfers[t.id] = row
        self.page_for(t.ip).append(row)

    def start_fetch(self, t):
        t.state, t.done = "running", 0
        self.transfers[t.id].update()
        threading.Thread(target=self.node.fetch, args=(t, self.settings["download_dir"]), daemon=True).start()

    # ---------- 菜单 ----------
    def on_auto_accept(self, action, value):
        action.set_state(value)
        self.settings["auto_accept"] = value.get_boolean()
        save_settings(self.settings)

    def on_open_downloads(self, *_):
        os.makedirs(self.settings["download_dir"], exist_ok=True)
        Gtk.FileLauncher.new(Gio.File.new_for_path(self.settings["download_dir"])).launch(
            self, None, lambda src, res: _finish(src.launch_finish, res))

    def on_choose_downloads(self, *_):
        dialog = Gtk.FileDialog(title="选择下载位置",
                                initial_folder=Gio.File.new_for_path(self.settings["download_dir"]))
        dialog.select_folder(self, None, self.on_choose_done)

    def on_choose_done(self, dialog, res):
        try:
            folder = dialog.select_folder_finish(res)
        except GLib.Error:
            return
        self.settings["download_dir"] = folder.get_path()
        save_settings(self.settings)
        self.toast(f"下载位置：{folder.get_path()}")

    # ---------- 提示 ----------
    def toast(self, text):
        self.toasts.add_toast(Adw.Toast(title=GLib.markup_escape_text(text), timeout=3))

    def notify(self, title, body):
        if self.is_active():
            return
        n = Gio.Notification.new(title)
        n.set_body(body)
        self.get_application().send_notification(None, n)


class App(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID)
        self.win = None

    def do_activate(self):
        if self.win:
            self.win.present()
            return
        provider = Gtk.CssProvider()
        provider.load_from_string(CSS)
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), provider,
                                                  Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        settings = load_settings()
        try:
            node = Node(known_ips=settings["known_ips"])
        except OSError as e:
            self.win = Adw.ApplicationWindow(application=self, title="飞鸽 Lite",
                                             default_width=480, default_height=360)
            page = Adw.StatusPage(icon_name="dialog-error-symbolic", title=f"端口 {PORT} 被占用",
                                  description=f"请先退出 iptux 或其他飞秋客户端，再重新打开。\n（{e}）")
            view = Adw.ToolbarView(content=page)
            view.add_top_bar(Adw.HeaderBar())
            self.win.set_content(view)
            self.win.present()
            return
        self.win = Window(self, node, settings)
        node.on_event = self.win.on_core_event
        node.start()
        self.win.present()


if __name__ == "__main__":
    if not sys.stdout.isatty():  # 从启动器打开时，输出写进日志
        log_dir = os.path.expanduser("~/.cache/feige-lite")
        os.makedirs(log_dir, exist_ok=True)
        sys.stdout = sys.stderr = open(os.path.join(log_dir, "feige.log"), "a", buffering=1)
    sys.exit(App().run(sys.argv))
