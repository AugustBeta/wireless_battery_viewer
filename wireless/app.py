"""应用控制器：协调 Tk 主线程、托盘线程和后台读取回调。"""

from __future__ import annotations

import ctypes
import queue
import threading
import tkinter as tk
from typing import Callable, Optional

from .config import AppConfig
from .models import Snapshot
from .repository import BatteryRepository
from .tray import TrayThread, WM_APP_UPDATE


class Application:
    def __init__(
        self,
        config: AppConfig,
        start_hidden: bool = False,
        open_debug: bool = False,
        debug: bool = False,
        debug_log: Optional[Callable[[str], None]] = None,
    ):
        self.config = config
        self.start_hidden = start_hidden
        self.open_debug_requested = open_debug
        self.debug = debug
        self.debug_log = debug_log or (lambda message: None)
        self.ui_queue: queue.Queue = queue.Queue()
        self.latest_snapshot: Optional[Snapshot] = None
        self._snapshot_lock = threading.Lock()
        self.quitting = False
        self._tray_stop_started = False
        self._refresh_inflight = False
        self.root = tk.Tk()
        self.root.withdraw()
        self.panel = None
        self.debug_window = None

        self.repository = BatteryRepository(
            config=config,
            on_snapshot=self._on_snapshot,
            debug=debug,
            debug_log=debug_log,
        )
        self.tray = TrayThread(
            self,
            debug=debug,
            debug_log=debug_log,
        )

        # 延迟导入，避免 config -> UI -> config 的循环导入。
        from .ui import Panel

        self.panel = Panel(self)
        self.root.protocol("WM_DELETE_WINDOW", self.panel.hide)

    def _on_snapshot(self, snapshot: Snapshot) -> None:
        with self._snapshot_lock:
            self.latest_snapshot = snapshot
        self._refresh_inflight = False
        self.ui_queue.put(("snapshot", snapshot))
        hwnd = self.tray.hwnd
        if hwnd:
            try:
                from .tray import user32
                user32.PostMessageW(hwnd, WM_APP_UPDATE, 0, 0)
            except Exception:
                pass

    def get_snapshot(self) -> Optional[Snapshot]:
        with self._snapshot_lock:
            return self.latest_snapshot

    # -- 可由托盘线程调用的操作 -------------------------------------------
    def request_refresh(self, reason: str = "manual") -> None:
        if self.quitting:
            return
        self._refresh_inflight = True
        self.ui_queue.put(("refreshing", reason))
        self.repository.request_refresh(reason)

    def show_panel_from_tray(self) -> None:
        self.ui_queue.put(("show", None))

    def open_debug_from_tray(self) -> None:
        self.ui_queue.put(("debug", None))

    def request_quit_from_tray(self) -> None:
        self.ui_queue.put(("quit", None))

    # -- Tk 线程操作 --------------------------------------------------------
    def show_panel(self) -> None:
        if self.quitting:
            return
        self.panel.show()

    def set_interval(self, seconds: int) -> None:
        seconds = max(30, min(1800, int(seconds)))
        self.config.refresh_seconds = seconds
        try:
            self.config.save()
        except Exception:
            pass
        self.tray.set_interval(seconds)

    def open_debug(self) -> None:
        if self.quitting:
            return
        from .debug_ui import DebugWindow
        if self.debug_window is not None:
            try:
                self.debug_window.show()
                return
            except tk.TclError:
                self.debug_window = None
        self.debug_window = DebugWindow(self)

    def debug_window_closed(self) -> None:
        self.debug_window = None

    def quit(self) -> None:
        if self.quitting:
            return
        self.quitting = True
        self.repository.stop()
        self._tray_stop_started = True
        self.tray.stop()
        # 设置只在真正发生变化时保存。不要因为程序退出就重写可能已经损坏的
        # 配置文件。
        self.root.after(40, self._wait_for_tray)

    def _wait_for_tray(self) -> None:
        if self.tray.is_alive():
            self.root.after(40, self._wait_for_tray)
            return
        try:
            self.root.destroy()
        except tk.TclError:
            pass

    def _pump(self) -> None:
        if self.quitting:
            return
        for _ in range(60):
            try:
                kind, payload = self.ui_queue.get_nowait()
            except queue.Empty:
                break
            try:
                if kind == "snapshot":
                    self.panel.update_snapshot(payload)
                elif kind == "refreshing":
                    self.panel.set_refreshing(True, payload)
                elif kind == "show":
                    self.panel.show()
                elif kind == "debug":
                    self.open_debug()
                elif kind == "quit":
                    self.quit()
                elif kind == "status":
                    self.panel.set_status(str(payload))
            except Exception:
                if self.debug:
                    self.debug_log("UI pump error: " + str(kind))
        self.root.after(100, self._pump)

    def run(self) -> int:
        try:
            self.panel.build()
            self.tray.start()
            if not self.tray.ready.wait(3.0):
                raise RuntimeError("托盘线程启动超时")
            if self.tray.startup_error:
                raise RuntimeError(f"托盘窗口创建失败：{self.tray.startup_error}")
            if self.start_hidden:
                self.root.withdraw()
            else:
                self.panel.show()
            if self.open_debug_requested:
                self.open_debug()
            self.request_refresh("startup")
            self.root.after(100, self._pump)
            self.root.mainloop()
        finally:
            self.repository.stop()
            self.tray.stop()
            try:
                if self.tray.is_alive():
                    self.tray.join(timeout=2.5)
            except Exception:
                pass
            try:
                if self.debug_window is not None:
                    self.debug_window.destroy()
            except Exception:
                pass
            try:
                if self.root.winfo_exists():
                    self.root.destroy()
            except Exception:
                pass
        return 0


def show_fatal_error(message: str) -> None:
    try:
        ctypes.windll.user32.MessageBoxW(
            None, message, "无线外设电量", 0x00000010
        )
    except Exception:
        pass
