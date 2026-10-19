"""Win32 notification-area host.

The tray lives on its own thread so that GetMessageW can block at zero CPU
while Tkinter runs its own main loop on the main thread.  All communication in
either direction is via PostMessage/queue.Queue; Tk objects are never touched
from this thread.

Lifecycle rules implemented here:

* NIM_MODIFY updates an existing icon; NIM_DELETE is only used when a slot is
  actually removed, and a re-added slot always goes through NIM_ADD again.
* WM_CLOSE destroys the window; WM_DESTROY calls PostQuitMessage(0), which is
  what lets the thread (and therefore the process) really exit.
"""

from __future__ import annotations

import ctypes
import threading
import traceback
from ctypes import wintypes as w
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Optional

from .icons import TrayIconFactory
from .models import Snapshot
from .winhid import GUID

user32 = ctypes.WinDLL("user32", use_last_error=True)
shell32 = ctypes.WinDLL("shell32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

TRAY_WINDOW_CLASS = "WirelessBatteryViewer.TrayWindow.v1"
TRAY_WINDOW_TITLE = "WirelessBatteryViewer"

WM_CLOSE = 0x0010
WM_DESTROY = 0x0002
WM_TIMER = 0x0113
WM_CONTEXTMENU = 0x007B
WM_LBUTTONUP = 0x0202
WM_RBUTTONUP = 0x0205
WM_ENDSESSION = 0x0016
WM_QUERYENDSESSION = 0x0011

NIN_SELECT = 0x0400
NIN_KEYSELECT = 0x0401

WM_APP = 0x8000
WM_APP_UPDATE = WM_APP + 1
WM_APP_SHOW = WM_APP + 2
WM_APP_SET_INTERVAL = WM_APP + 3
WM_APP_QUIT = WM_APP + 4
WM_TRAY_CALLBACK = WM_APP + 10

TIMER_ID = 1

NIF_MESSAGE = 0x00000001
NIF_ICON = 0x00000002
NIF_TIP = 0x00000004
NIF_STATE = 0x00000008
NIF_INFO = 0x00000010
NIF_SHOWTIP = 0x00000080
NIM_ADD = 0x00000000
NIM_MODIFY = 0x00000001
NIM_DELETE = 0x00000002
NIM_SETVERSION = 0x00000004
NOTIFYICON_VERSION_4 = 4
MF_STRING = 0x00000000
TPM_RIGHTBUTTON = 0x0002
TPM_RETURNCMD = 0x0100


class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [
        ("cbSize", w.DWORD),
        ("hWnd", w.HWND),
        ("uID", w.UINT),
        ("uFlags", w.UINT),
        ("uCallbackMessage", w.UINT),
        ("hIcon", w.HANDLE),
        ("szTip", w.WCHAR * 128),
        ("dwState", w.DWORD),
        ("dwStateMask", w.DWORD),
        ("szInfo", w.WCHAR * 256),
        ("uVersion", w.UINT),
        ("szInfoTitle", w.WCHAR * 64),
        ("dwInfoFlags", w.DWORD),
        ("guidItem", GUID),
        ("hBalloonIcon", w.HANDLE),
    ]


class NOTIFYICONIDENTIFIER(ctypes.Structure):
    _fields_ = [
        ("cbSize", w.DWORD),
        ("hWnd", w.HWND),
        ("uID", w.UINT),
        ("guidItem", GUID),
    ]


class WNDCLASSW(ctypes.Structure):
    _fields_ = [
        ("style", w.UINT),
        ("lpfnWndProc", ctypes.c_void_p),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", w.HINSTANCE),
        ("hIcon", w.HANDLE),
        ("hCursor", w.HANDLE),
        ("hbrBackground", w.HANDLE),
        ("lpszMenuName", w.LPCWSTR),
        ("lpszClassName", w.LPCWSTR),
    ]


class MSG(ctypes.Structure):
    _fields_ = [
        ("hwnd", w.HWND),
        ("message", w.UINT),
        ("wParam", w.WPARAM),
        ("lParam", w.LPARAM),
        ("time", w.DWORD),
        ("pt", w.POINT),
    ]


class RECT(ctypes.Structure):
    _fields_ = [
        ("left", w.LONG),
        ("top", w.LONG),
        ("right", w.LONG),
        ("bottom", w.LONG),
    ]


WNDPROC = ctypes.WINFUNCTYPE(
    ctypes.c_ssize_t, w.HWND, w.UINT, w.WPARAM, w.LPARAM
)

user32.RegisterClassW.restype = w.ATOM
user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
user32.UnregisterClassW.restype = w.BOOL
user32.UnregisterClassW.argtypes = [w.LPCWSTR, w.HINSTANCE]
user32.CreateWindowExW.restype = w.HWND
user32.CreateWindowExW.argtypes = [
    w.DWORD, w.LPCWSTR, w.LPCWSTR, w.DWORD, ctypes.c_int, ctypes.c_int,
    ctypes.c_int, ctypes.c_int, w.HWND, w.HMENU, w.HINSTANCE, ctypes.c_void_p,
]
user32.DefWindowProcW.restype = ctypes.c_ssize_t
user32.DefWindowProcW.argtypes = [w.HWND, w.UINT, w.WPARAM, w.LPARAM]
user32.DestroyWindow.argtypes = [w.HWND]
user32.PostQuitMessage.argtypes = [ctypes.c_int]
user32.PostMessageW.restype = w.BOOL
user32.PostMessageW.argtypes = [w.HWND, w.UINT, w.WPARAM, w.LPARAM]
user32.SendMessageW.restype = ctypes.c_ssize_t
user32.SendMessageW.argtypes = [w.HWND, w.UINT, w.WPARAM, w.LPARAM]
user32.GetMessageW.restype = w.BOOL
user32.GetMessageW.argtypes = [ctypes.POINTER(MSG), w.HWND, w.UINT, w.UINT]
user32.TranslateMessage.argtypes = [ctypes.POINTER(MSG)]
user32.DispatchMessageW.restype = ctypes.c_ssize_t
user32.DispatchMessageW.argtypes = [ctypes.POINTER(MSG)]
user32.SetTimer.restype = ctypes.c_size_t
user32.SetTimer.argtypes = [w.HWND, ctypes.c_size_t, w.UINT, ctypes.c_void_p]
user32.KillTimer.restype = w.BOOL
user32.KillTimer.argtypes = [w.HWND, ctypes.c_size_t]
user32.RegisterWindowMessageW.restype = w.UINT
user32.RegisterWindowMessageW.argtypes = [w.LPCWSTR]
user32.FindWindowW.restype = w.HWND
user32.FindWindowW.argtypes = [w.LPCWSTR, w.LPCWSTR]
user32.IsWindow.argtypes = [w.HWND]
user32.CreatePopupMenu.restype = w.HMENU
user32.AppendMenuW.argtypes = [w.HMENU, w.UINT, ctypes.c_size_t, w.LPCWSTR]
user32.SetForegroundWindow.argtypes = [w.HWND]
user32.TrackPopupMenu.argtypes = [
    w.HMENU, w.UINT, ctypes.c_int, ctypes.c_int, ctypes.c_int, w.HWND,
    ctypes.c_void_p,
]
user32.DestroyMenu.argtypes = [w.HMENU]
user32.GetCursorPos.argtypes = [ctypes.POINTER(w.POINT)]

shell32.Shell_NotifyIconW.restype = w.BOOL
shell32.Shell_NotifyIconW.argtypes = [w.DWORD, ctypes.POINTER(NOTIFYICONDATAW)]
shell32.Shell_NotifyIconGetRect.restype = ctypes.c_long
shell32.Shell_NotifyIconGetRect.argtypes = [
    ctypes.POINTER(NOTIFYICONIDENTIFIER), ctypes.POINTER(RECT)
]

kernel32.GetModuleHandleW.restype = w.HMODULE
kernel32.GetModuleHandleW.argtypes = [w.LPCWSTR]


@dataclass
class TraySlot:
    uid: int
    title: str
    tip: str
    icon: int
    percent: Optional[int]


@dataclass
class TrayDisplayItem:
    name: str
    percent: Optional[int]
    status: str
    timestamp: datetime


def snapshot_to_items(snapshot: Optional[Snapshot]) -> list[TrayDisplayItem]:
    if not snapshot or not snapshot.readings:
        return [TrayDisplayItem(
            name="无线外设电量", percent=None, status="offline",
            timestamp=snapshot.finished if snapshot else datetime.now(),
        )]
    return [
        TrayDisplayItem(
            name=reading.target.name,
            percent=reading.percent if reading.status == "ok" else None,
            status=reading.status,
            timestamp=reading.updated or snapshot.finished,
        )
        for reading in snapshot.readings
    ]


def _make_nid(hwnd: int, uid: int) -> NOTIFYICONDATAW:
    data = NOTIFYICONDATAW()
    data.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
    data.hWnd = hwnd
    data.uID = uid
    data.uCallbackMessage = WM_TRAY_CALLBACK
    return data


def find_tray_window() -> int:
    return int(user32.FindWindowW(TRAY_WINDOW_CLASS, None) or 0)


def post_show_to_running_instance() -> bool:
    hwnd = find_tray_window()
    if not hwnd:
        return False
    return bool(user32.PostMessageW(hwnd, WM_APP_SHOW, 0, 0))


def request_quit_running_instance() -> bool:
    hwnd = find_tray_window()
    if not hwnd:
        return False
    return bool(user32.PostMessageW(hwnd, WM_CLOSE, 0, 0))


def tray_icon_rect(uid: int) -> Optional[tuple[int, int, int, int]]:
    hwnd = find_tray_window()
    if not hwnd:
        return None
    identifier = NOTIFYICONIDENTIFIER()
    identifier.cbSize = ctypes.sizeof(NOTIFYICONIDENTIFIER)
    identifier.hWnd = hwnd
    identifier.uID = uid
    rect = RECT()
    result = shell32.Shell_NotifyIconGetRect(
        ctypes.byref(identifier), ctypes.byref(rect)
    )
    if result != 0:
        return None
    return (rect.left, rect.top, rect.right, rect.bottom)


class TrayThread(threading.Thread):
    def __init__(
        self,
        controller,
        debug: bool = False,
        debug_log: Optional[Callable[[str], None]] = None,
    ):
        super().__init__(name="tray-window", daemon=True)
        self.controller = controller
        self.debug = debug
        self._debug_log = debug_log
        self.hwnd = 0
        self.ready = threading.Event()
        self.startup_error: Optional[BaseException] = None
        self._wndproc_ref = None
        self._icons = TrayIconFactory()
        self._slots: dict[int, TraySlot] = {}
        self._interval = controller.config.refresh_seconds
        self._class_atom = 0
        self._hinstance = kernel32.GetModuleHandleW(None)
        self._taskbar_created = user32.RegisterWindowMessageW("TaskbarCreated")
        self._lock = threading.Lock()
        self._stopping = False

    def _log(self, message: str) -> None:
        if self.debug and self._debug_log:
            self._debug_log(message)

    # -- lifecycle ---------------------------------------------------------
    def run(self) -> None:
        try:
            self._create_window()
            self.ready.set()
            with self._lock:
                if self._stopping:
                    user32.PostMessageW(self.hwnd, WM_CLOSE, 0, 0)
            self._message_loop()
        except BaseException as exc:
            self.startup_error = exc
            self.ready.set()
            self._log("tray thread failed: " + traceback.format_exc(limit=4))
        finally:
            self._remove_all_icons()
            self._icons.clear()
            if self.hwnd and user32.IsWindow(self.hwnd):
                user32.DestroyWindow(self.hwnd)
            if self._class_atom:
                try:
                    user32.UnregisterClassW(TRAY_WINDOW_CLASS, self._hinstance)
                except Exception:
                    pass
            self.hwnd = 0

    def _create_window(self) -> None:
        self._wndproc_ref = WNDPROC(self._wndproc)
        window_class = WNDCLASSW()
        window_class.lpfnWndProc = ctypes.cast(
            self._wndproc_ref, ctypes.c_void_p
        ).value
        window_class.hInstance = self._hinstance
        window_class.lpszClassName = TRAY_WINDOW_CLASS
        self._class_atom = user32.RegisterClassW(ctypes.byref(window_class))
        if not self._class_atom:
            error = ctypes.get_last_error()
            if error not in (0, 1410):
                raise ctypes.WinError(error)
        self.hwnd = user32.CreateWindowExW(
            0, TRAY_WINDOW_CLASS, TRAY_WINDOW_TITLE,
            0, 0, 0, 0, 0, None, None, self._hinstance, None,
        )
        if not self.hwnd:
            raise ctypes.WinError(ctypes.get_last_error())
        self._log(f"tray window created hwnd=0x{self.hwnd:X}")
        user32.SetTimer(self.hwnd, TIMER_ID, self._interval * 1000, None)
        self._add_or_update_icons()

    def _message_loop(self) -> None:
        message = MSG()
        while True:
            result = user32.GetMessageW(ctypes.byref(message), None, 0, 0)
            if result == 0:  # WM_QUIT
                break
            if result == -1:
                break
            user32.TranslateMessage(ctypes.byref(message))
            user32.DispatchMessageW(ctypes.byref(message))
        self._log("tray message loop exited")

    def stop(self) -> None:
        with self._lock:
            self._stopping = True
        hwnd = self.hwnd
        if hwnd:
            user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)

    def set_interval(self, seconds: int) -> None:
        hwnd = self.hwnd
        if hwnd:
            user32.PostMessageW(hwnd, WM_APP_SET_INTERVAL, int(seconds), 0)

    # -- window procedure --------------------------------------------------
    def _wndproc(self, hwnd, message, wparam, lparam):
        try:
            if message == WM_TIMER and wparam == TIMER_ID:
                self.controller.request_refresh("timer")
                return 0
            if message == WM_TRAY_CALLBACK:
                event = int(lparam) & 0xFFFF
                if event in (WM_CONTEXTMENU, WM_RBUTTONUP, NIN_KEYSELECT):
                    self.controller.show_panel_from_tray()
                elif event in (NIN_SELECT, WM_LBUTTONUP):
                    self.controller.request_refresh("tray-left-click")
                return 0
            if message == WM_APP_UPDATE:
                self._add_or_update_icons()
                return 0
            if message == WM_APP_SHOW:
                self.controller.show_panel_from_tray()
                return 0
            if message == WM_APP_SET_INTERVAL:
                self._interval = max(30, min(1800, int(wparam)))
                user32.KillTimer(hwnd, TIMER_ID)
                user32.SetTimer(hwnd, TIMER_ID, self._interval * 1000, None)
                return 0
            if message == WM_APP_QUIT:
                user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
                return 0
            if message == WM_CLOSE:
                if not self._stopping:
                    # An external WM_CLOSE must ask the Tk controller to
                    # perform the full shutdown.  Destroying only this hidden
                    # window would leave a visible Tk panel (or a window-less
                    # process) behind.
                    self.controller.request_quit_from_tray()
                    return 0
                self._remove_all_icons()
                user32.KillTimer(hwnd, TIMER_ID)
                user32.DestroyWindow(hwnd)
                return 0
            if message == WM_DESTROY:
                # Mandatory: without this the process stays alive as a
                # window-less zombie and the single-instance mutex is held.
                user32.PostQuitMessage(0)
                return 0
            if message == WM_QUERYENDSESSION:
                return 1
            if message == WM_ENDSESSION:
                if wparam:
                    user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
                return 0
            if self._taskbar_created and message == self._taskbar_created:
                # Explorer restarted: deleted icons cannot be modified back;
                # every slot must be NIM_ADD'ed again.
                self._log("TaskbarCreated received; re-adding tray icons")
                self._slots.clear()
                self._icons.clear()
                self._add_or_update_icons()
                return 0
        except Exception:
            self._log("WndProc error: " + traceback.format_exc(limit=4))
            return 0
        return user32.DefWindowProcW(hwnd, message, wparam, lparam)

    # -- icons -------------------------------------------------------------
    def _add_or_update_icons(self) -> None:
        if not self.hwnd:
            return
        snapshot = self.controller.latest_snapshot
        items = snapshot_to_items(snapshot)
        desired = min(len(items), 8)
        for index in range(desired):
            item = items[index]
            uid = index + 1
            icon = self._icons.get(item.percent, item.status)
            percent_text = f"{item.percent}%" if item.percent is not None else "--"
            timestamp = item.timestamp.strftime("%H:%M:%S") if item.timestamp else "--:--:--"
            tip = f"{item.name}：{percent_text}（{timestamp}）"
            existing = self._slots.get(uid)
            if existing is None:
                data = _make_nid(self.hwnd, uid)
                data.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP | NIF_SHOWTIP
                data.hIcon = icon
                data.szTip = tip[:127]
                if not shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(data)):
                    self._log(f"NIM_ADD failed for uid={uid}")
                    continue
                data.uVersion = NOTIFYICON_VERSION_4
                shell32.Shell_NotifyIconW(NIM_SETVERSION, ctypes.byref(data))
                self._slots[uid] = TraySlot(
                    uid=uid, title=item.name, tip=tip, icon=icon,
                    percent=item.percent,
                )
                self._log(f"tray icon added uid={uid} tip={tip}")
            else:
                data = _make_nid(self.hwnd, uid)
                data.uFlags = NIF_ICON | NIF_TIP | NIF_SHOWTIP
                data.hIcon = icon
                data.szTip = tip[:127]
                if not shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(data)):
                    self._log(f"NIM_MODIFY failed for uid={uid}; re-adding")
                    # The icon was deleted behind our back (e.g. Explorer
                    # restart).  NIM_MODIFY cannot revive it; NIM_ADD is the
                    # only correct recovery.
                    self._slots.pop(uid, None)
                    data.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP | NIF_SHOWTIP
                    if shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(data)):
                        data.uVersion = NOTIFYICON_VERSION_4
                        shell32.Shell_NotifyIconW(NIM_SETVERSION, ctypes.byref(data))
                        self._slots[uid] = TraySlot(
                            uid=uid, title=item.name, tip=tip, icon=icon,
                            percent=item.percent,
                        )
                else:
                    existing.title = item.name
                    existing.tip = tip
                    existing.icon = icon
                    existing.percent = item.percent
        for uid in list(self._slots):
            if uid > desired:
                self._delete_icon(uid)

    def _delete_icon(self, uid: int) -> None:
        data = _make_nid(self.hwnd, uid)
        shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(data))
        self._slots.pop(uid, None)
        self._log(f"tray icon deleted uid={uid}")

    def _remove_all_icons(self) -> None:
        for uid in list(self._slots):
            self._delete_icon(uid)
        self._slots.clear()

    @property
    def icon_count(self) -> int:
        with self._lock:
            return len(self._slots)


def wait_for_tray_window(timeout: float = 3.0) -> int:
    """Used by the second instance / --quit path."""
    import time as _time
    deadline = _time.monotonic() + timeout
    while _time.monotonic() < deadline:
        hwnd = find_tray_window()
        if hwnd:
            return hwnd
        _time.sleep(0.05)
    return 0
