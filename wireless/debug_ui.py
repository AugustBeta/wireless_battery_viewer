"""完整的调试与检查窗口。

功能：
  * 列出当前所有 HID 设备/接口
  * 读取/转储 Feature Report，并探测 Report ID
  * 使用隔离的 overlapped 读传输试发原始报告
  * 监听 Input Report
  * 将手动标定（Report ID / 电量字节下标 / 倍率）保存为 JSON

所有可能阻塞的工作都在工作线程中执行，并通过 queue.Queue 返回结果；
工作线程绝不直接调用 Tk。
"""

from __future__ import annotations

import queue
import re
import threading
import tkinter as tk
from datetime import datetime
from tkinter import messagebox, ttk
from typing import Callable, Optional

from .models import ManualCalibration
from .winhid import (
    HidDeviceInfo,
    enumerate_hid_devices,
    get_feature_report,
    hex_bytes,
    listen_input,
    probe_feature_reports,
    send_raw_report,
)

BG = "#F2F5F9"
CARD = "#FFFFFF"
TEXT = "#172033"
MUTED = "#6B7280"
BORDER = "#E2E8F0"
ACCENT = "#2563EB"
DARK = "#0F172A"
DARK_TEXT = "#D7E2F0"
DANGER = "#DC2626"


def _parse_int(text: str, default: int = 0) -> int:
    text = (text or "").strip()
    if not text:
        return default
    if text.lower().startswith("0x"):
        return int(text, 16)
    return int(text, 10)


def _parse_hex_bytes(text: str) -> bytes:
    cleaned = re.sub(r"0x", "", text or "", flags=re.IGNORECASE)
    cleaned = re.sub(r"[,;:\-]", " ", cleaned)
    parts = cleaned.split()
    values = []
    for part in parts:
        if len(part) > 2:
            # 允许输入紧凑格式，例如 "1201"。
            for index in range(0, len(part), 2):
                values.append(int(part[index:index + 2], 16))
        else:
            values.append(int(part, 16))
    return bytes(value & 0xFF for value in values)


def _path_match(info: HidDeviceInfo) -> str:
    match = re.search(r"(mi_[0-9a-f]{2}&col[0-9a-f]{2})", info.path.lower())
    if match:
        return match.group(1)
    return info.path


class DebugWindow:
    def __init__(self, app):
        self.app = app
        self.root = app.root
        self.family = app.panel.family if app.panel else "Microsoft YaHei UI"
        self.win = tk.Toplevel(self.root)
        self.win.title("无线外设电量 · 完整调试")
        self.win.configure(bg=BG)
        self.win.minsize(1080, 760)
        self.win.geometry("1280x900")
        self.win.protocol("WM_DELETE_WINDOW", self.close)
        self.win.bind("<Escape>", lambda event: self.close())
        self.queue: queue.Queue = queue.Queue()
        self.devices: list[HidDeviceInfo] = []
        self.selected: Optional[HidDeviceInfo] = None
        self.busy = False
        self.closed = False
        self._after_id = None
        self._build()
        self.refresh_devices()
        self._pump()

    # -- 界面 ---------------------------------------------------------------
    def _build(self) -> None:
        outer = tk.Frame(self.win, bg=BG)
        outer.pack(fill="both", expand=True, padx=16, pady=14)
        outer.grid_columnconfigure(0, weight=1)
        outer.grid_rowconfigure(2, weight=3)
        outer.grid_rowconfigure(3, weight=1)

        header = tk.Frame(outer, bg=BG)
        header.grid(row=0, column=0, sticky="ew")
        header.grid_columnconfigure(0, weight=1)
        tk.Label(
            header, text="完整调试", bg=BG, fg=TEXT,
            font=(self.family, 17, "bold"),
        ).grid(row=0, column=0, sticky="w")
        tk.Label(
            header,
            text="所有 HID 接口 · Feature Report · 原始命令 · Input Report · 手动标定",
            bg=BG, fg=MUTED, font=(self.family, 9),
        ).grid(row=1, column=0, sticky="w", pady=(2, 0))
        self.busy_label = tk.Label(
            header, text="", bg=BG, fg=MUTED, font=(self.family, 9)
        )
        self.busy_label.grid(row=0, column=1, rowspan=2, sticky="e")

        toolbar = tk.Frame(outer, bg=BG)
        toolbar.grid(row=1, column=0, sticky="ew", pady=(10, 8))
        ttk.Button(
            toolbar, text="刷新 HID 列表", command=self.refresh_devices
        ).pack(side="left")
        ttk.Button(
            toolbar, text="清空输出", command=self.clear_output
        ).pack(side="left", padx=(8, 0))
        tk.Label(
            toolbar, text="选中一个接口后，下方操作会作用于该 HID 句柄。",
            bg=BG, fg=MUTED, font=(self.family, 8),
        ).pack(side="left", padx=(14, 0))

        paned = ttk.Panedwindow(outer, orient="vertical")
        paned.grid(row=2, column=0, sticky="nsew")

        tree_frame = tk.Frame(paned, bg=CARD)
        columns = ("product", "vidpid", "usage", "reports", "location", "state")
        self.tree = ttk.Treeview(
            tree_frame, columns=columns, show="headings", height=11,
            selectmode="browse",
        )
        headings = {
            "product": ("设备 / 接口", 240),
            "vidpid": ("VID:PID", 105),
            "usage": ("Usage Page / Usage", 150),
            "reports": ("In / Out / Feature", 145),
            "location": ("位置", 120),
            "state": ("句柄", 76),
        }
        for column, (label, width) in headings.items():
            self.tree.heading(column, text=label)
            self.tree.column(
                column, width=width, minwidth=width, anchor="w",
                stretch=(column == "product"),
            )
        yscroll = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        xscroll = ttk.Scrollbar(tree_frame, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        yscroll.grid(row=0, column=1, sticky="ns")
        xscroll.grid(row=1, column=0, sticky="ew")
        tree_frame.grid_rowconfigure(0, weight=1)
        tree_frame.grid_columnconfigure(0, weight=1)
        self.tree.bind("<<TreeviewSelect>>", self._on_select)
        paned.add(tree_frame, weight=3)

        bottom = ttk.Panedwindow(paned, orient="horizontal")

        detail_frame = tk.Frame(bottom, bg=CARD)
        detail_header = tk.Frame(detail_frame, bg=CARD)
        detail_header.pack(fill="x", padx=10, pady=(8, 2))
        tk.Label(
            detail_header, text="接口详情", bg=CARD, fg=TEXT,
            font=(self.family, 10, "bold"),
        ).pack(side="left")
        ttk.Button(
            detail_header, text="复制路径", command=self.copy_path,
        ).pack(side="right")
        self.detail_text = tk.Text(
            detail_frame, bg=CARD, fg=TEXT, font=("Consolas", 9),
            relief="flat", wrap="word", height=13, width=48, padx=10, pady=8,
            highlightthickness=0,
        )
        detail_scroll = ttk.Scrollbar(
            detail_frame, orient="vertical", command=self.detail_text.yview
        )
        self.detail_text.configure(yscrollcommand=detail_scroll.set)
        self.detail_text.pack(side="left", fill="both", expand=True)
        detail_scroll.pack(side="right", fill="y")
        bottom.add(detail_frame, weight=2)

        actions = ttk.Notebook(bottom)
        self._build_feature_tab(actions)
        self._build_send_tab(actions)
        self._build_listen_tab(actions)
        self._build_calibration_tab(actions)
        bottom.add(actions, weight=3)
        paned.add(bottom, weight=3)

        output_frame = tk.Frame(outer, bg=BG)
        output_frame.grid(row=3, column=0, sticky="nsew", pady=(10, 0))
        output_header = tk.Frame(output_frame, bg=BG)
        output_header.grid(row=0, column=0, columnspan=2, sticky="ew")
        tk.Label(
            output_header, text="输出", bg=BG, fg=TEXT,
            font=(self.family, 10, "bold"),
        ).pack(side="left")
        tk.Label(
            output_header, text="不写日志文件；仅显示在本窗口。",
            bg=BG, fg=MUTED, font=(self.family, 8),
        ).pack(side="left", padx=(8, 0))
        self.output_text = tk.Text(
            output_frame, bg=DARK, fg=DARK_TEXT, insertbackground="#FFFFFF",
            font=("Consolas", 9), relief="flat", wrap="none", height=12,
            padx=10, pady=8, highlightthickness=0,
        )
        output_y = ttk.Scrollbar(
            output_frame, orient="vertical", command=self.output_text.yview
        )
        output_x = ttk.Scrollbar(
            output_frame, orient="horizontal", command=self.output_text.xview
        )
        self.output_text.configure(
            yscrollcommand=output_y.set, xscrollcommand=output_x.set,
            state="disabled",
        )
        self.output_text.grid(row=1, column=0, sticky="nsew")
        output_y.grid(row=1, column=1, sticky="ns")
        output_x.grid(row=2, column=0, sticky="ew")
        output_frame.grid_rowconfigure(1, weight=1)
        output_frame.grid_columnconfigure(0, weight=1)

    def _build_feature_tab(self, notebook: ttk.Notebook) -> None:
        frame = tk.Frame(notebook, bg=CARD)
        notebook.add(frame, text="Feature Report")
        body = tk.Frame(frame, bg=CARD)
        body.pack(fill="both", expand=True, padx=14, pady=14)
        tk.Label(
            body, text="Report ID", bg=CARD, fg=MUTED, font=(self.family, 9)
        ).grid(row=0, column=0, sticky="w")
        self.feature_report_id = tk.StringVar(value="0")
        tk.Entry(
            body, textvariable=self.feature_report_id, width=8,
            font=("Consolas", 10), relief="flat", highlightthickness=1,
            highlightbackground=BORDER, highlightcolor=ACCENT,
        ).grid(row=0, column=1, sticky="w", padx=(8, 0))
        ttk.Button(
            body, text="转储 Feature Report", command=self.dump_feature,
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(10, 0))
        ttk.Button(
            body, text="探测 Report ID 0–255", command=self.probe_features,
        ).grid(row=2, column=0, columnspan=2, sticky="w", pady=(6, 0))
        self.feature_info = tk.Label(
            body, text="", bg=CARD, fg=MUTED, font=(self.family, 8),
            justify="left", wraplength=480,
        )
        self.feature_info.grid(row=3, column=0, columnspan=4, sticky="w", pady=(12, 0))
        tk.Label(
            body,
            text="Feature Report 通过 dwDesiredAccess=0 的句柄读取，"
                 "因此系统独占的键盘/鼠标顶层集合也能安全转储。",
            bg=CARD, fg=MUTED, font=(self.family, 8),
            justify="left", wraplength=480,
        ).grid(row=4, column=0, columnspan=4, sticky="w", pady=(18, 0))

    def _build_send_tab(self, notebook: ttk.Notebook) -> None:
        frame = tk.Frame(notebook, bg=CARD)
        notebook.add(frame, text="试发命令")
        body = tk.Frame(frame, bg=CARD)
        body.pack(fill="both", expand=True, padx=14, pady=14)
        tk.Label(
            body, text="Report ID", bg=CARD, fg=MUTED, font=(self.family, 9)
        ).grid(row=0, column=0, sticky="w")
        self.send_report_id = tk.StringVar(value="2")
        tk.Entry(
            body, textvariable=self.send_report_id, width=8,
            font=("Consolas", 10), relief="flat", highlightthickness=1,
            highlightbackground=BORDER, highlightcolor=ACCENT,
        ).grid(row=0, column=1, sticky="w", padx=(8, 12))
        tk.Label(
            body, text="Payload (hex)", bg=CARD, fg=MUTED,
            font=(self.family, 9),
        ).grid(row=0, column=2, sticky="w")
        self.send_payload = tk.StringVar(value="12 01")
        tk.Entry(
            body, textvariable=self.send_payload, width=28,
            font=("Consolas", 10), relief="flat", highlightthickness=1,
            highlightbackground=BORDER, highlightcolor=ACCENT,
        ).grid(row=0, column=3, sticky="w", padx=(8, 0))
        ttk.Button(
            body, text="发送并读取 1 秒", command=self.send_command,
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(12, 0))
        tk.Label(
            body,
            text="命令使用独立的 write 句柄发送，响应由独立的 "
                 "FILE_FLAG_OVERLAPPED read 句柄接收；超时用 CancelIoEx 取消。",
            bg=CARD, fg=MUTED, font=(self.family, 8),
            justify="left", wraplength=560,
        ).grid(row=2, column=0, columnspan=4, sticky="w", pady=(18, 0))
        tk.Label(
            body,
            text="注意：任意厂商命令都可能修改设备设置，请确认协议后再发送。",
            bg=CARD, fg="#B45309", font=(self.family, 8),
            justify="left", wraplength=560,
        ).grid(row=3, column=0, columnspan=4, sticky="w", pady=(8, 0))

    def _build_listen_tab(self, notebook: ttk.Notebook) -> None:
        frame = tk.Frame(notebook, bg=CARD)
        notebook.add(frame, text="监听 Input")
        body = tk.Frame(frame, bg=CARD)
        body.pack(fill="both", expand=True, padx=14, pady=14)
        tk.Label(
            body, text="监听秒数", bg=CARD, fg=MUTED, font=(self.family, 9)
        ).grid(row=0, column=0, sticky="w")
        self.listen_seconds = tk.StringVar(value="2")
        tk.Spinbox(
            body, from_=1, to=10, width=6, textvariable=self.listen_seconds,
            font=("Consolas", 10), relief="flat", highlightthickness=1,
            highlightbackground=BORDER, highlightcolor=ACCENT,
        ).grid(row=0, column=1, sticky="w", padx=(8, 0))
        ttk.Button(
            body, text="开始监听", command=self.listen_reports,
        ).grid(row=0, column=2, padx=(12, 0))
        tk.Label(
            body,
            text="监听使用独立的 overlapped 读句柄，不占用发送句柄；"
                 "到时间后用 CancelIoEx 结束，不会阻塞界面。",
            bg=CARD, fg=MUTED, font=(self.family, 8),
            justify="left", wraplength=540,
        ).grid(row=1, column=0, columnspan=3, sticky="w", pady=(18, 0))

    def _build_calibration_tab(self, notebook: ttk.Notebook) -> None:
        frame = tk.Frame(notebook, bg=CARD)
        notebook.add(frame, text="手动标定")
        body = tk.Frame(frame, bg=CARD)
        body.pack(fill="both", expand=True, padx=14, pady=12)
        fields = [
            ("名称", "cal_name"),
            ("Report ID", "cal_report"),
            ("命令 (hex)", "cal_command"),
            ("电量字节下标", "cal_offset"),
            ("倍率", "cal_scale"),
            ("充电字节下标", "cal_charge"),
            ("包长度", "cal_packet"),
            ("接口匹配子串", "cal_match"),
        ]
        self.cal_vars: dict[str, tk.StringVar] = {}
        for row, (label, key) in enumerate(fields):
            var = tk.StringVar(value="")
            self.cal_vars[key] = var
            tk.Label(
                body, text=label, bg=CARD, fg=MUTED, font=(self.family, 8)
            ).grid(row=row, column=0, sticky="w", pady=2)
            columnspan = 3 if key == "cal_match" else 1
            tk.Entry(
                body, textvariable=var,
                width=50 if key == "cal_match" else 26,
                font=("Consolas", 9), relief="flat", highlightthickness=1,
                highlightbackground=BORDER, highlightcolor=ACCENT,
            ).grid(
                row=row, column=1, columnspan=columnspan,
                sticky="w", padx=(8, 0), pady=2,
            )
        tk.Label(
            body, text="类型", bg=CARD, fg=MUTED, font=(self.family, 8)
        ).grid(row=len(fields), column=0, sticky="w", pady=2)
        self.cal_kind = ttk.Combobox(
            body, values=("keyboard", "mouse", "headset", "other"),
            state="readonly", width=12,
        )
        self.cal_kind.set("other")
        self.cal_kind.grid(
            row=len(fields), column=1, sticky="w", padx=(8, 0), pady=2
        )
        ttk.Button(
            body, text="保存标定并刷新", command=self.save_calibration,
        ).grid(row=len(fields) + 1, column=0, columnspan=2, sticky="w", pady=(10, 2))
        tk.Label(
            body,
            text="保存到 %LOCALAPPDATA%\\WirelessBatteryViewer\\config.json。"
                 "手动标定优先级最低，只用于没有命中标准/厂商协议的接口。",
            bg=CARD, fg=MUTED, font=(self.family, 8),
            justify="left", wraplength=540,
        ).grid(row=len(fields) + 2, column=0, columnspan=4, sticky="w", pady=(8, 0))

    # -- 后台任务 -----------------------------------------------------------
    def _start(self, label: str, work: Callable, formatter: Callable) -> None:
        if self.busy:
            return
        self.busy = True
        self._set_busy(True, label)

        def runner():
            try:
                result = work()
                self.queue.put(("task_ok", label, result, formatter))
            except Exception as exc:
                self.queue.put(("task_error", label, exc, formatter))

        threading.Thread(target=runner, name="debug-task", daemon=True).start()

    def _pump(self) -> None:
        if self.closed:
            return
        for _ in range(40):
            try:
                message = self.queue.get_nowait()
            except queue.Empty:
                break
            kind = message[0]
            if kind == "devices":
                self._fill_devices(message[1])
            elif kind == "devices_error":
                self._set_busy(False, "")
                self._write("枚举失败：" + str(message[1]))
            elif kind == "task_ok":
                _, label, result, formatter = message
                self.busy = False
                self._set_busy(False, "")
                try:
                    text = formatter(result)
                except Exception as exc:
                    text = f"{label} 完成，但格式化失败：{exc}"
                if text:
                    self._write(text)
            elif kind == "task_error":
                _, label, exc, formatter = message
                self.busy = False
                self._set_busy(False, "")
                self._write(f"{label} 失败：{exc}")
            elif kind == "write":
                self._write(message[1])
            elif kind == "clear":
                self._clear_output()
        try:
            self._after_id = self.win.after(100, self._pump)
        except tk.TclError:
            pass

    def _set_busy(self, busy: bool, label: str) -> None:
        self.busy_label.configure(text=label if busy else "")
        # _start 中的忙碌保护才是权威判断；切换鼠标指针只是为了给出即时
        # 反馈，不触碰任何由工作线程持有的控件。
        if busy:
            self.win.configure(cursor="watch")
        else:
            self.win.configure(cursor="")

    # -- 设备列表 -----------------------------------------------------------
    def refresh_devices(self) -> None:
        if self.busy:
            return
        self.busy = True
        self._set_busy(True, "正在枚举 HID 接口…")

        def worker():
            try:
                devices = enumerate_hid_devices()
                self.queue.put(("devices", devices))
            except Exception as exc:
                self.queue.put(("devices_error", exc))

        threading.Thread(target=worker, name="debug-enum", daemon=True).start()

    def _fill_devices(self, devices: list[HidDeviceInfo]) -> None:
        self.devices = devices
        for item in self.tree.get_children():
            self.tree.delete(item)
        for index, info in enumerate(devices):
            product = info.product or info.manufacturer or "(无产品字符串)"
            state = "可访问" if info.accessible else "受限"
            self.tree.insert(
                "", "end", iid=str(index),
                values=(
                    product,
                    info.vid_pid,
                    f"{info.usage_page:04X} / {info.usage:04X}",
                    f"{info.input_len} / {info.output_len} / {info.feature_len}",
                    info.short_location,
                    state,
                ),
            )
        self.busy = False
        self._set_busy(False, "")
        self._write(f"已枚举 {len(devices)} 个 HID 接口")
        if devices:
            self.tree.selection_set("0")
            self.tree.focus("0")

    def _on_select(self, event=None) -> None:
        selection = self.tree.selection()
        if not selection:
            return
        try:
            info = self.devices[int(selection[0])]
        except (ValueError, IndexError):
            return
        self.selected = info
        self._show_details(info)
        self._prepare_calibration(info)
        self.feature_info.configure(
            text=f"Feature ReportByteLength = {info.feature_len}"
                 + ("" if info.feature_len else "（该接口没有 Feature Report）")
        )

    def _show_details(self, info: HidDeviceInfo) -> None:
        lines = [
            f"路径       : {info.path}",
            f"VID:PID    : {info.vid_pid}   version={info.version:04X}",
            f"厂商       : {info.manufacturer}",
            f"产品       : {info.product}",
            f"序列号     : {info.serial}",
            f"Usage      : page=0x{info.usage_page:04X} usage=0x{info.usage:04X}",
            f"报告长度   : input={info.input_len} output={info.output_len} feature={info.feature_len}",
            f"位置       : {info.short_location}",
            f"可访问     : {info.accessible}",
        ]
        if info.meta_error:
            lines.append(f"打开错误   : {info.meta_error}")
        self.detail_text.configure(state="normal")
        self.detail_text.delete("1.0", "end")
        self.detail_text.insert("1.0", "\n".join(lines))
        self.detail_text.configure(state="disabled")

    def _prepare_calibration(self, info: HidDeviceInfo) -> None:
        self.cal_vars["cal_name"].set(
            (info.product or f"HID {info.vid_pid}") + " (手动)"
        )
        self.cal_vars["cal_report"].set("0")
        self.cal_vars["cal_command"].set("12 01")
        self.cal_vars["cal_offset"].set("0")
        self.cal_vars["cal_scale"].set("1.0")
        self.cal_vars["cal_charge"].set("")
        self.cal_vars["cal_packet"].set(str(info.output_len or 64))
        self.cal_vars["cal_match"].set(_path_match(info))
        if info.usage_page == 0x0001 and info.usage == 0x0006:
            self.cal_kind.set("keyboard")
        elif info.usage_page == 0x0001 and info.usage == 0x0002:
            self.cal_kind.set("mouse")
        else:
            self.cal_kind.set("other")

    def copy_path(self) -> None:
        if self.selected:
            self.win.clipboard_clear()
            self.win.clipboard_append(self.selected.path)
            self._write("路径已复制到剪贴板")

    # -- 操作 ---------------------------------------------------------------
    def _require_selected(self) -> Optional[HidDeviceInfo]:
        if self.selected is None:
            messagebox.showinfo("完整调试", "请先在列表中选择一个 HID 接口。", parent=self.win)
            return None
        return self.selected

    def dump_feature(self) -> None:
        info = self._require_selected()
        if info is None:
            return
        try:
            report_id = _parse_int(self.feature_report_id.get(), 0)
        except ValueError:
            messagebox.showerror("Feature Report", "Report ID 必须是数字或 0x 十六进制。", parent=self.win)
            return

        def work():
            return get_feature_report(info, report_id)

        def fmt(data):
            if data is None:
                return f"Report 0x{report_id:02X}: 无数据（接口可能不支持该 Report ID）"
            return f"Feature Report 0x{report_id:02X} ({len(data)} bytes):\n{hex_bytes(data)}"

        self._start(f"转储 Feature Report 0x{report_id:02X}", work, fmt)

    def probe_features(self) -> None:
        info = self._require_selected()
        if info is None:
            return
        if not info.feature_len:
            messagebox.showinfo("Feature Report", "该接口 FeatureReportByteLength=0。", parent=self.win)
            return

        def work():
            return probe_feature_reports(info)

        def fmt(found):
            if not found:
                return "0–255 探测完成：没有可读的 Feature Report。"
            lines = [f"探测完成：找到 {len(found)} 个可读 Feature Report"]
            for report_id, data in sorted(found.items()):
                lines.append(f"  0x{report_id:02X} ({len(data)}): {hex_bytes(data, 48)}")
            return "\n".join(lines)

        self._start("探测 Feature Report ID", work, fmt)

    def send_command(self) -> None:
        info = self._require_selected()
        if info is None:
            return
        try:
            report_id = _parse_int(self.send_report_id.get(), 0)
            payload = _parse_hex_bytes(self.send_payload.get())
        except ValueError as exc:
            messagebox.showerror("试发命令", f"无法解析输入：{exc}", parent=self.win)
            return
        if not payload:
            messagebox.showerror("试发命令", "Payload 不能为空。", parent=self.win)
            return

        def work():
            return send_raw_report(info, report_id, payload, timeout_ms=1000)

        def fmt(result):
            lines = [
                f"OUT report=0x{report_id:02X} len={len(payload) + 1}: "
                f"{hex_bytes(result.written)}"
            ]
            if result.nak:
                lines.append("设备返回 NAK (FF AA)")
            if not result.reports:
                lines.append("1 秒内没有收到 Input Report。")
            for index, report in enumerate(result.reports):
                lines.append(f"IN[{index}] {len(report)}: {hex_bytes(report)}")
            return "\n".join(lines)

        self._start(f"试发命令 report=0x{report_id:02X}", work, fmt)

    def listen_reports(self) -> None:
        info = self._require_selected()
        if info is None:
            return
        try:
            seconds = float(self.listen_seconds.get())
        except ValueError:
            seconds = 2.0
        seconds = max(1.0, min(10.0, seconds))

        def work():
            return listen_input(info, seconds=seconds)

        def fmt(reports):
            if not reports:
                return f"{seconds:g} 秒内没有收到 Input Report。"
            lines = [f"{seconds:g} 秒内收到 {len(reports)} 个 Input Report"]
            for index, report in enumerate(reports[:100]):
                lines.append(f"IN[{index}] {len(report)}: {hex_bytes(report)}")
            if len(reports) > 100:
                lines.append(f"……省略 {len(reports) - 100} 条")
            return "\n".join(lines)

        self._start("监听 Input Report", work, fmt)

    def save_calibration(self) -> None:
        info = self._require_selected()
        if info is None:
            return
        try:
            name = self.cal_vars["cal_name"].get().strip() or "手动标定设备"
            report_id = _parse_int(self.cal_vars["cal_report"].get(), 0)
            command = _parse_hex_bytes(self.cal_vars["cal_command"].get())
            offset = int(self.cal_vars["cal_offset"].get().strip() or "0")
            scale = float(self.cal_vars["cal_scale"].get().strip() or "1")
            charging_text = self.cal_vars["cal_charge"].get().strip()
            charging_offset = int(charging_text) if charging_text else None
            packet_size = int(self.cal_vars["cal_packet"].get().strip() or (info.output_len or 64))
            match = self.cal_vars["cal_match"].get().strip() or _path_match(info)
        except ValueError as exc:
            messagebox.showerror("手动标定", f"输入格式错误：{exc}", parent=self.win)
            return
        if offset < 0 or offset > 63:
            messagebox.showerror("手动标定", "电量字节下标必须在 0–63。", parent=self.win)
            return
        calibration = ManualCalibration(
            name=name,
            vendor_id=info.vendor_id,
            product_id=info.product_id,
            path_contains=match,
            report_id=report_id,
            battery_offset=offset,
            scale=scale,
            command=command,
            charging_offset=charging_offset,
            packet_size=packet_size,
            kind=self.cal_kind.get() or "other",
        )
        try:
            self.app.config.add_calibration(calibration)
        except Exception as exc:
            messagebox.showerror("手动标定", f"保存失败：{exc}", parent=self.win)
            return
        self._write(
            f"已保存手动标定：{name}\n"
            f"  VID:PID={info.vid_pid} path~={match}\n"
            f"  report=0x{report_id:02X} command={command.hex(' ').upper()} "
            f"offset={offset} scale={scale:g}"
        )
        self.app.repository.request_refresh("calibration")
        messagebox.showinfo("手动标定", "已保存，正在刷新设备列表。", parent=self.win)

    # -- 输出 ---------------------------------------------------------------
    def _write(self, text: str) -> None:
        if self.closed:
            return
        stamp = datetime.now().strftime("%H:%M:%S")
        self.output_text.configure(state="normal")
        self.output_text.insert("end", f"[{stamp}] {text}\n")
        self.output_text.see("end")
        self.output_text.configure(state="disabled")

    def _clear_output(self) -> None:
        self.output_text.configure(state="normal")
        self.output_text.delete("1.0", "end")
        self.output_text.configure(state="disabled")

    def clear_output(self) -> None:
        self._clear_output()

    def show(self) -> None:
        self.win.deiconify()
        self.win.lift()
        self.win.focus_force()

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        if self._after_id:
            try:
                self.win.after_cancel(self._after_id)
            except tk.TclError:
                pass
        try:
            self.win.destroy()
        except tk.TclError:
            pass
        self.app.debug_window_closed()

    def destroy(self) -> None:
        self.close()
