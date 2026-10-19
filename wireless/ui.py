"""Simple panel: one card per wireless device, with a large percentage."""

from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk
from typing import Optional

from .config import (
    MAX_INTERVAL,
    MIN_INTERVAL,
    create_desktop_links,
    read_autostart_state,
    set_autostart_enabled,
)
from .icons import level_color, render_pixels
from .models import DeviceReading, Snapshot

BG = "#F2F5F9"
CARD = "#FFFFFF"
TEXT = "#172033"
MUTED = "#6B7280"
SUBTLE = "#8A94A6"
BORDER = "#E2E8F0"
ACCENT = "#2563EB"
ACCENT_DARK = "#1D4ED8"
DANGER = "#DC2626"
GREEN = "#16A34A"
YELLOW = "#D99A00"
RED = "#DC2626"
GRAY = "#9AA4B2"

ROLE_BG = {
    "keyboard": "#EAF1FF",
    "mouse": "#F2EBFF",
    "headset": "#E7F8F3",
    "other": "#F1F3F6",
}
ROLE_FG = {
    "keyboard": "#2457C5",
    "mouse": "#6D3AC7",
    "headset": "#0F766E",
    "other": "#4B5563",
}


def _font_family(root: tk.Misc) -> str:
    try:
        families = set(root.tk.call("font", "families"))
    except Exception:
        families = set()
    for candidate in ("Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI", "Arial"):
        if candidate in families:
            return candidate
    return "TkDefaultFont"


def _round_rect(canvas: tk.Canvas, x1, y1, x2, y2, radius, **kwargs):
    radius = max(1, min(radius, (x2 - x1) // 2, (y2 - y1) // 2))
    points = [
        x1 + radius, y1, x2 - radius, y1, x2, y1, x2, y1 + radius,
        x2, y2 - radius, x2, y2, x2 - radius, y2, x1 + radius, y2,
        x1, y2, x1, y2 - radius, x1, y1 + radius, x1, y1,
    ]
    return canvas.create_polygon(points, smooth=True, splinesteps=12, **kwargs)


class BatteryBar(tk.Canvas):
    def __init__(self, parent, width=260, height=12, bg=CARD):
        super().__init__(
            parent, width=width, height=height, bg=bg,
            highlightthickness=0, bd=0,
        )
        self._percent = 0
        self._color = GRAY
        self.bind("<Configure>", lambda event: self._draw())

    def set(self, percent: Optional[int], color: str) -> None:
        self._percent = max(0, min(100, int(percent or 0)))
        self._color = color
        self._draw()

    def _draw(self) -> None:
        self.delete("all")
        width = max(10, self.winfo_width())
        height = max(8, self.winfo_height())
        _round_rect(
            self, 1, 1, width - 1, height - 1, height // 2,
            fill="#E9EDF3", outline="",
        )
        if self._percent > 0:
            fill_width = max(height, int((width - 2) * self._percent / 100))
            _round_rect(
                self, 1, 1, fill_width, height - 1, height // 2,
                fill=self._color, outline="",
            )


class BatteryCard:
    def __init__(self, parent: tk.Misc, ui: "Panel", reading: Optional[DeviceReading] = None):
        self.ui = ui
        self.reading = reading
        self.outer = tk.Frame(
            parent, bg=CARD, highlightbackground=BORDER,
            highlightcolor=BORDER, highlightthickness=1, bd=0,
        )
        self.outer.grid_rowconfigure(0, weight=1)
        self.outer.grid_columnconfigure(1, weight=1)
        self.stripe = tk.Frame(self.outer, bg=GRAY, width=5)
        self.stripe.grid(row=0, column=0, sticky="ns")
        self.inner = tk.Frame(self.outer, bg=CARD)
        self.inner.grid(row=0, column=1, sticky="nsew", padx=18, pady=16)
        self.inner.grid_columnconfigure(0, weight=1)

        top = tk.Frame(self.inner, bg=CARD)
        top.grid(row=0, column=0, sticky="ew")
        top.grid_columnconfigure(0, weight=1)
        self.role_label = tk.Label(
            top, text="外设", bg=ROLE_BG["other"], fg=ROLE_FG["other"],
            font=(ui.family, 9, "bold"), padx=9, pady=3,
        )
        self.role_label.grid(row=0, column=0, sticky="w")
        self.state_label = tk.Label(
            top, text="正在读取", bg=CARD, fg=SUBTLE,
            font=(ui.family, 9),
        )
        self.state_label.grid(row=0, column=1, sticky="e")

        self.name_label = tk.Label(
            self.inner, text="正在查找无线设备…", bg=CARD, fg=TEXT,
            font=(ui.family, 11, "bold"), anchor="w", justify="left",
            wraplength=300,
        )
        self.name_label.grid(row=1, column=0, sticky="ew", pady=(10, 0))

        percent_row = tk.Frame(self.inner, bg=CARD)
        percent_row.grid(row=2, column=0, sticky="ew", pady=(5, 4))
        percent_row.grid_columnconfigure(1, weight=1)
        self.percent_label = tk.Label(
            percent_row, text="—", bg=CARD, fg=GRAY,
            font=(ui.family, 40, "bold"), anchor="w",
        )
        self.percent_label.grid(row=0, column=0, sticky="w")
        self.charge_label = tk.Label(
            percent_row, text="", bg=CARD, fg=GREEN,
            font=(ui.family, 9, "bold"), anchor="sw",
        )
        self.charge_label.grid(row=0, column=1, sticky="sw", padx=(10, 0), pady=(0, 8))

        self.bar = BatteryBar(self.inner, width=320, height=12)
        self.bar.grid(row=3, column=0, sticky="ew", pady=(2, 12))

        self.source_label = tk.Label(
            self.inner, text="数据来源：—", bg=CARD, fg=MUTED,
            font=(ui.family, 9), anchor="w", justify="left",
            wraplength=340,
        )
        self.source_label.grid(row=4, column=0, sticky="ew")
        self.updated_label = tk.Label(
            self.inner, text="更新时间：—", bg=CARD, fg=SUBTLE,
            font=(ui.family, 8), anchor="w", justify="left",
            wraplength=340,
        )
        self.updated_label.grid(row=5, column=0, sticky="ew", pady=(3, 0))

        if reading is not None:
            self.update(reading)

    def set_placeholder(self, role: str, name: str) -> None:
        role_key = role if role in ROLE_BG else "other"
        self.role_label.configure(
            text={"keyboard": "键盘", "mouse": "鼠标", "headset": "耳机"}.get(role, "外设"),
            bg=ROLE_BG[role_key], fg=ROLE_FG[role_key],
        )
        self.name_label.configure(text=name, fg=TEXT)
        self.percent_label.configure(text="—", fg=GRAY)
        self.charge_label.configure(text="")
        self.state_label.configure(text="等待设备", fg=SUBTLE)
        self.source_label.configure(text="数据来源：—")
        self.updated_label.configure(text="更新时间：—")
        self.stripe.configure(bg=GRAY)
        self.bar.set(0, GRAY)

    def update(self, reading: DeviceReading) -> None:
        self.reading = reading
        target = reading.target
        role_key = target.kind if target.kind in ROLE_BG else "other"
        self.role_label.configure(
            text=target.role, bg=ROLE_BG[role_key], fg=ROLE_FG[role_key]
        )
        self.name_label.configure(text=target.name)
        color = reading.color
        self.stripe.configure(bg=color)
        if reading.status == "ok":
            self.percent_label.configure(text=f"{reading.percent}%", fg=color)
            self.bar.set(reading.percent, color)
            self.state_label.configure(text="在线", fg=color)
            self.charge_label.configure(
                text="⚡ 充电中" if reading.charging else "",
                fg=GREEN if reading.charging else MUTED,
            )
            self.source_label.configure(text=f"数据来源：{reading.source}")
            if reading.updated:
                self.updated_label.configure(
                    text="更新时间：" + reading.updated.strftime("%Y-%m-%d %H:%M:%S")
                )
            else:
                self.updated_label.configure(text="更新时间：—")
        else:
            self.percent_label.configure(text="—", fg=GRAY)
            self.bar.set(0, GRAY)
            status = reading.status_text
            if reading.target.paired is False:
                status = "未配对"
            self.state_label.configure(text=status, fg=GRAY)
            self.charge_label.configure(text="")
            source = reading.source or target.source_hint
            message = reading.message or "设备休眠或未连接"
            self.source_label.configure(text=f"数据来源：{source} · {message}")
            self.updated_label.configure(
                text=("更新时间：" + reading.updated.strftime("%Y-%m-%d %H:%M:%S"))
                if reading.updated else "更新时间：—"
            )


class Panel:
    def __init__(self, app):
        self.app = app
        self.root = app.root
        self.family = _font_family(self.root)
        self.snapshot: Optional[Snapshot] = None
        self.cards: list[BatteryCard] = []
        self.card_keys: list[str] = []
        self._autostart_state = read_autostart_state()
        self._built = False
        self._refreshing = False
        self._positioned = False

    # -- construction ------------------------------------------------------
    def build(self) -> None:
        root = self.root
        root.title("无线外设电量")
        root.configure(bg=BG)
        root.minsize(820, 590)
        self._center_window(900, 660)
        root.update_idletasks()
        root.protocol("WM_DELETE_WINDOW", self.hide)
        root.bind("<Escape>", lambda event: self.hide())
        root.bind("<Control-q>", lambda event: self.quit_app())
        self._setup_style()
        self._set_window_icon(None)

        outer = tk.Frame(root, bg=BG)
        outer.pack(fill="both", expand=True, padx=24, pady=(20, 16))
        outer.grid_columnconfigure(0, weight=1)

        header = tk.Frame(outer, bg=BG)
        header.grid(row=0, column=0, sticky="ew")
        header.grid_columnconfigure(0, weight=1)
        title_box = tk.Frame(header, bg=BG)
        title_box.grid(row=0, column=0, sticky="w")
        tk.Label(
            title_box, text="无线外设电量", bg=BG, fg=TEXT,
            font=(self.family, 19, "bold"),
        ).pack(anchor="w")
        tk.Label(
            title_box, text="HID 直连 · 无第三方依赖 · 后台低占用",
            bg=BG, fg=MUTED, font=(self.family, 9),
        ).pack(anchor="w", pady=(3, 0))

        self.overall_label = tk.Label(
            header, text="正在启动", bg="#E8EDF5", fg=MUTED,
            font=(self.family, 9, "bold"), padx=12, pady=5,
        )
        self.overall_label.grid(row=0, column=1, sticky="e")

        self.cards_frame = tk.Frame(outer, bg=BG)
        self.cards_frame.grid(row=1, column=0, sticky="nsew", pady=(18, 0))
        outer.grid_rowconfigure(1, weight=1)
        self._build_skeleton()

        status_line = tk.Frame(outer, bg=BG)
        status_line.grid(row=2, column=0, sticky="ew", pady=(12, 8))
        status_line.grid_columnconfigure(0, weight=1)
        self.status_label = tk.Label(
            status_line, text="正在读取设备…", bg=BG, fg=MUTED,
            font=(self.family, 9), anchor="w",
        )
        self.status_label.grid(row=0, column=0, sticky="w")
        self.interval_hint = tk.Label(
            status_line, text="", bg=BG, fg=SUBTLE,
            font=(self.family, 8), anchor="e",
        )
        self.interval_hint.grid(row=0, column=1, sticky="e")

        separator = tk.Frame(outer, bg=BORDER, height=1)
        separator.grid(row=3, column=0, sticky="ew", pady=(0, 12))

        toolbar = tk.Frame(outer, bg=BG)
        toolbar.grid(row=4, column=0, sticky="ew")
        toolbar.grid_columnconfigure(1, weight=1)

        auto_box = tk.Frame(toolbar, bg=BG)
        auto_box.grid(row=0, column=0, sticky="w")
        self.autostart_var = tk.BooleanVar(value=self._autostart_state.enabled)
        self.autostart_check = tk.Checkbutton(
            auto_box, text="开机自启", variable=self.autostart_var,
            command=self._on_autostart_toggle, bg=BG, fg=TEXT,
            activebackground=BG, activeforeground=TEXT,
            selectcolor=CARD, font=(self.family, 9),
            highlightthickness=0, bd=0,
        )
        self.autostart_check.pack(side="left")
        self.autostart_note = tk.Label(
            auto_box, text="", bg=BG, fg=SUBTLE, font=(self.family, 8)
        )
        self.autostart_note.pack(side="left", padx=(8, 0))
        self._update_autostart_ui()

        interval_box = tk.Frame(toolbar, bg=BG)
        interval_box.grid(row=0, column=2, sticky="e", padx=(16, 0))
        tk.Label(
            interval_box, text="刷新间隔", bg=BG, fg=MUTED,
            font=(self.family, 9),
        ).pack(side="left")
        self.interval_var = tk.StringVar(value=str(self.app.config.refresh_seconds))
        self.interval_spin = tk.Spinbox(
            interval_box, from_=MIN_INTERVAL, to=MAX_INTERVAL,
            increment=30, width=6, textvariable=self.interval_var,
            justify="center", font=(self.family, 9),
            bg=CARD, fg=TEXT, relief="flat", highlightthickness=1,
            highlightbackground=BORDER, highlightcolor=ACCENT,
            buttonbackground="#E9EDF3",
        )
        self.interval_spin.pack(side="left", padx=(6, 3))
        self.interval_spin.bind("<Return>", lambda event: self._apply_interval())
        tk.Label(
            interval_box, text="秒", bg=BG, fg=MUTED, font=(self.family, 9)
        ).pack(side="left")
        ttk.Button(
            interval_box, text="应用", command=self._apply_interval,
            style="Small.TButton",
        ).pack(side="left", padx=(6, 0))

        button_box = tk.Frame(toolbar, bg=BG)
        button_box.grid(row=1, column=0, columnspan=3, sticky="e", pady=(14, 0))
        ttk.Button(
            button_box, text="立即刷新", command=self.refresh,
            style="Accent.TButton",
        ).pack(side="left", padx=(0, 8))
        ttk.Button(
            button_box, text="完整调试", command=self.app.open_debug,
            style="TButton",
        ).pack(side="left", padx=(0, 8))
        ttk.Button(
            button_box, text="创建桌面入口", command=self.create_links,
            style="TButton",
        ).pack(side="left", padx=(0, 8))
        ttk.Button(
            button_box, text="隐藏到托盘", command=self.hide,
            style="TButton",
        ).pack(side="left", padx=(0, 8))
        ttk.Button(
            button_box, text="退出托盘", command=self.quit_app,
            style="Danger.TButton",
        ).pack(side="left")

        self._built = True
        self._update_interval_hint()
        self._update_status()

    def _center_window(self, width: int, height: int) -> None:
        try:
            screen_w = self.root.winfo_screenwidth()
            screen_h = self.root.winfo_screenheight()
            x = max(0, (screen_w - width) // 2)
            y = max(0, (screen_h - height) // 2 - 20)
            self.root.geometry(f"{width}x{height}+{x}+{y}")
        except Exception:
            self.root.geometry(f"{width}x{height}")

    def _setup_style(self) -> None:
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure(
            "TButton",
            font=(self.family, 9),
            padding=(13, 7),
            borderwidth=0,
            background="#E9EDF3",
            foreground=TEXT,
        )
        style.map(
            "TButton",
            background=[("active", "#DDE4EE")],
            foreground=[("disabled", SUBTLE)],
        )
        style.configure(
            "Accent.TButton",
            font=(self.family, 9, "bold"),
            padding=(16, 7),
            background=ACCENT,
            foreground="#FFFFFF",
        )
        style.map(
            "Accent.TButton",
            background=[("active", ACCENT_DARK), ("pressed", ACCENT_DARK)],
            foreground=[("active", "#FFFFFF")],
        )
        style.configure(
            "Danger.TButton",
            font=(self.family, 9),
            padding=(13, 7),
            background="#FDECEC",
            foreground=DANGER,
        )
        style.map(
            "Danger.TButton",
            background=[("active", "#FADADA")],
            foreground=[("active", DANGER)],
        )
        style.configure(
            "Small.TButton",
            font=(self.family, 8),
            padding=(9, 5),
            background="#E9EDF3",
            foreground=TEXT,
        )
        style.map("Small.TButton", background=[("active", "#DDE4EE")])
        style.configure(
            "Card.TCheckbutton",
            background=BG,
            foreground=TEXT,
            font=(self.family, 9),
        )
        style.map(
            "Card.TCheckbutton",
            background=[("active", BG), ("!active", BG)],
            foreground=[("disabled", SUBTLE)],
        )

    def _build_skeleton(self) -> None:
        for card in self.cards:
            card.outer.destroy()
        self.cards = []
        self.card_keys = []
        placeholders = [
            ("keyboard", "正在查找键盘…"),
            ("mouse", "正在查找鼠标…"),
        ]
        for index, (role, name) in enumerate(placeholders):
            card = BatteryCard(self.cards_frame, self)
            card.set_placeholder(role, name)
            self._place_card(card, index)
            self.cards.append(card)
            self.card_keys.append("placeholder-" + role)
        self._configure_card_grid(len(self.cards))

    def _configure_card_grid(self, count: int) -> None:
        for column in range(2):
            self.cards_frame.grid_columnconfigure(column, weight=1, uniform="cards")
        rows = (max(1, count) + 1) // 2
        for row in range(rows):
            self.cards_frame.grid_rowconfigure(row, weight=1)

    def _place_card(self, card: BatteryCard, index: int) -> None:
        row, column = divmod(index, 2)
        padx = (0, 8) if column == 0 else (8, 0)
        pady = (0, 8)
        card.outer.grid(row=row, column=column, sticky="nsew", padx=padx, pady=pady)

    def _set_window_icon(self, percent: Optional[int]) -> None:
        try:
            status = "ok" if percent is not None else "offline"
            color = level_color(percent, status)
            text = str(percent) if percent is not None else "--"
            px, size = render_pixels(text, color, 32)
            image = tk.PhotoImage(width=size, height=size)
            for y in range(size):
                for x in range(size):
                    offset = (y * size + x) * 4
                    if px[offset + 3] == 0:
                        continue
                    red, green, blue = px[offset + 2], px[offset + 1], px[offset]
                    image.put(f"#{red:02x}{green:02x}{blue:02x}", to=(x, y))
            self.root.iconphoto(True, image)
            self._icon_photo = image
        except Exception:
            pass

    # -- updates -----------------------------------------------------------
    def update_snapshot(self, snapshot: Snapshot) -> None:
        self.snapshot = snapshot
        readings = snapshot.readings
        primary = next((item for item in readings if item.status == "ok"), None)
        self._set_window_icon(primary.percent if primary else None)
        if not readings:
            self._show_empty()
        else:
            keys = [reading.target.key for reading in readings]
            if keys != self.card_keys:
                for card in self.cards:
                    card.outer.destroy()
                self.cards = []
                for index, reading in enumerate(readings):
                    card = BatteryCard(self.cards_frame, self, reading)
                    self._place_card(card, index)
                    self.cards.append(card)
                self.card_keys = keys
                self._configure_card_grid(len(self.cards))
            else:
                for card, reading in zip(self.cards, readings):
                    card.update(reading)
        self._refreshing = False
        self._update_status()
        self._update_overall()

    def _show_empty(self) -> None:
        if self.card_keys == ["empty"]:
            return
        for card in self.cards:
            card.outer.destroy()
        self.cards = []
        frame = tk.Frame(
            self.cards_frame, bg=CARD, highlightbackground=BORDER,
            highlightthickness=1,
        )
        frame.grid(row=0, column=0, columnspan=2, sticky="nsew")
        inner = tk.Frame(frame, bg=CARD)
        inner.pack(fill="both", expand=True, padx=24, pady=32)
        tk.Label(
            inner, text="未发现支持的无线外设", bg=CARD, fg=TEXT,
            font=(self.family, 15, "bold"),
        ).pack(anchor="w")
        tk.Label(
            inner,
            text="请确认 2.4GHz 接收器已插入、设备已唤醒；"
                 "也可以在「完整调试」中查看全部 HID 接口并手动标定。",
            bg=CARD, fg=MUTED, font=(self.family, 9), justify="left",
            wraplength=700,
        ).pack(anchor="w", pady=(8, 0))
        ttk.Button(
            inner, text="打开完整调试", command=self.app.open_debug,
            style="Accent.TButton",
        ).pack(anchor="w", pady=(18, 0))
        self.card_keys = ["empty"]
        self.cards = []

    def set_refreshing(self, refreshing: bool, reason: str = "") -> None:
        self._refreshing = refreshing
        if refreshing:
            self.status_label.configure(text="正在后台读取设备…", fg=MUTED)
            self.overall_label.configure(text="刷新中", bg="#E8EDF5", fg=MUTED)
            if self._built:
                self.interval_hint.configure(text="HID 读取在后台线程进行")
        else:
            self._update_status()

    def _update_status(self) -> None:
        if not self._built:
            return
        snapshot = self.snapshot
        if snapshot is None:
            self.status_label.configure(text="正在读取设备…", fg=MUTED)
            return
        count = len(snapshot.readings)
        ok = snapshot.ok_count
        if snapshot.errors:
            text = " · ".join(snapshot.errors)
            self.status_label.configure(text=text, fg=RED)
        else:
            text = (
                f"上次刷新 {snapshot.finished.strftime('%H:%M:%S')} · "
                f"{count} 个无线设备，{ok} 个在线"
            )
            if self._refreshing:
                text = "正在刷新… · " + text
            self.status_label.configure(text=text, fg=MUTED)
        self._update_interval_hint()

    def _update_overall(self) -> None:
        snapshot = self.snapshot
        if snapshot is None:
            return
        if snapshot.ok_count:
            self.overall_label.configure(
                text=f"{snapshot.ok_count} 个设备在线", bg="#E7F7EE", fg=GREEN
            )
        else:
            self.overall_label.configure(text="设备未连接", bg="#EEF1F5", fg=MUTED)

    def _update_interval_hint(self) -> None:
        if not hasattr(self, "interval_hint"):
            return
        self.interval_hint.configure(
            text=f"每 {self.app.config.refresh_seconds} 秒自动刷新"
        )

    # -- actions -----------------------------------------------------------
    def refresh(self) -> None:
        self.set_refreshing(True)
        self.app.request_refresh("manual")

    def hide(self) -> None:
        self.root.withdraw()

    def show(self) -> None:
        if not self._built:
            return
        try:
            self.root.deiconify()
            if self.root.state() == "iconic":
                self.root.state("normal")
            if not self._positioned:
                # Do the centering after the first mapping.  Some Tk 9 /
                # multi-monitor combinations restore a withdrawn window on
                # the monitor under the cursor, ignoring the pre-map position.
                self.root.update_idletasks()
                self._center_window(900, 660)
                self._positioned = True
            self.root.lift()
            self.root.attributes("-topmost", True)
            self.root.after(120, self._drop_topmost)
            self.root.focus_force()
            self._autostart_state = read_autostart_state()
            self._update_autostart_ui()
        except tk.TclError:
            pass

    def _drop_topmost(self) -> None:
        try:
            self.root.attributes("-topmost", False)
        except tk.TclError:
            pass

    def _on_autostart_toggle(self) -> None:
        wanted = bool(self.autostart_var.get())
        try:
            state = set_autostart_enabled(wanted)
        except Exception as exc:
            messagebox.showerror("开机自启", f"无法修改启动项：\n{exc}", parent=self.root)
            self._autostart_state = read_autostart_state()
            self.autostart_var.set(self._autostart_state.enabled)
            self._update_autostart_ui()
            return
        self._autostart_state = state
        self.autostart_var.set(state.enabled)
        self._update_autostart_ui()
        target = "已创建启动文件夹快捷方式" if state.enabled else "已在任务管理器中禁用"
        self.set_status(f"开机自启：{target}")

    def _update_autostart_ui(self) -> None:
        state = self._autostart_state
        if state.enabled:
            note = "启动文件夹 · 已启用"
        elif state.approved_disabled and state.shortcut_exists:
            note = "任务管理器中已禁用"
        elif state.shortcut_exists and not state.shortcut_matches:
            note = "已有快捷方式指向其他目标"
        elif not state.shortcut_exists:
            note = "未创建启动项"
        else:
            note = "未启用"
        try:
            self.autostart_note.configure(text=note)
        except Exception:
            pass
        try:
            self.autostart_var.set(state.enabled)
        except Exception:
            pass

    def _apply_interval(self) -> None:
        raw = self.interval_var.get().strip()
        try:
            seconds = int(raw)
        except ValueError:
            messagebox.showerror(
                "刷新间隔", f"请输入 {MIN_INTERVAL}–{MAX_INTERVAL} 之间的整数秒。",
                parent=self.root,
            )
            self.interval_var.set(str(self.app.config.refresh_seconds))
            return
        seconds = max(MIN_INTERVAL, min(MAX_INTERVAL, seconds))
        self.interval_var.set(str(seconds))
        self.app.set_interval(seconds)
        self._update_interval_hint()
        self.set_status(f"刷新间隔已设为 {seconds} 秒")

    def create_links(self) -> None:
        try:
            result = create_desktop_links()
        except Exception as exc:
            messagebox.showerror("桌面入口", str(exc), parent=self.root)
            return
        messagebox.showinfo(
            "桌面入口",
            "已创建桌面链接，没有复制脚本：\n\n"
            f"目录：{result['directory']}\n{result['directory_link']}\n\n"
            f"启动脚本：{result['file']}\n{result['file_link']}",
            parent=self.root,
        )
        self.set_status("桌面入口已就绪")

    def quit_app(self) -> None:
        if not messagebox.askyesno(
            "退出托盘",
            "退出后会移除两个通知区域图标并结束进程。\n确定退出吗？",
            parent=self.root,
        ):
            return
        self.app.quit()

    def set_status(self, message: str) -> None:
        if self._built:
            self.status_label.configure(text=message, fg=MUTED)
