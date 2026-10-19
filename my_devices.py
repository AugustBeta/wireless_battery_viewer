#!/usr/bin/env python3
"""Windows 10/11 无线外设电量查看工具。

示例：
    python my_devices.py --once
    python my_devices.py                 # 简洁面板 + 托盘
    python my_devices.py --tray          # 只启动托盘（启动快捷方式使用）
    python my_devices.py --debug-ui      # 简洁面板 + 完整调试窗口
    python my_devices.py --install       # 启动快捷方式 + 桌面链接
    python my_devices.py --quit          # 让正在运行的托盘退出
"""

from __future__ import annotations

import argparse
import ctypes
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import Optional

BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from wireless import __version__
from wireless.config import (
    AppConfig,
    create_desktop_links,
    create_startup_shortcut,
    read_autostart_state,
)
from wireless.repository import BatteryRepository
from wireless.tray import (
    find_tray_window,
    post_show_to_running_instance,
    request_quit_running_instance,
    wait_for_tray_window,
)
from wireless.winhid import enumerate_hid_devices

DEBUG = False
MUTEX_NAME = "Local\\WirelessBatteryViewer.SingleInstance.v1"
ERROR_ALREADY_EXISTS = 183


def debug_log(message: str) -> None:
    if not DEBUG:
        return
    stamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
    try:
        print(f"[{stamp}] {message}", flush=True)
    except Exception:
        pass


def ensure_debug_console() -> None:
    """只有 --debug 才产生控制台输出；绝不打开日志文件。"""
    if not DEBUG:
        return
    try:
        kernel32 = ctypes.windll.kernel32
        # pythonw 完全没有标准输入输出。只在这种情况下分配控制台；
        # 如果 stdout 已被重定向，则保留重定向。
        if sys.stdout is None or sys.stderr is None:
            if not kernel32.AllocConsole():
                return
            kernel32.SetConsoleOutputCP(65001)
            kernel32.SetConsoleCP(65001)
            sys.stdout = open("CONOUT$", "w", encoding="utf-8", buffering=1)
            sys.stderr = open("CONOUT$", "w", encoding="utf-8", buffering=1)
        else:
            try:
                sys.stdout.reconfigure(encoding="utf-8", errors="replace")
                sys.stderr.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass
        try:
            kernel32.SetConsoleTitleW("无线外设电量 · debug")
        except Exception:
            pass
    except Exception:
        pass


def set_dpi_awareness() -> None:
    """在创建 Tk/图标前声明 DPI 感知，保证托盘数字清晰。"""
    user32 = ctypes.windll.user32
    try:
        # PER_MONITOR_AWARE_V2 = -4；在 Windows 10/11 上这是最佳模式。
        if user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
            return
    except Exception:
        pass
    try:
        user32.SetProcessDPIAware()
    except Exception:
        pass


def set_app_user_model_id() -> None:
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            "WirelessBatteryViewer"
        )
    except Exception:
        pass


MUTEX_HANDLE = None
KERNEL32 = ctypes.WinDLL("kernel32", use_last_error=True)


def acquire_single_instance() -> bool:
    global MUTEX_HANDLE
    if instance_running():
        return False
    KERNEL32.CreateMutexW.restype = ctypes.c_void_p
    KERNEL32.CreateMutexW.argtypes = [
        ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p
    ]
    ctypes.set_last_error(0)
    handle = KERNEL32.CreateMutexW(None, False, MUTEX_NAME)
    if not handle:
        return True
    if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
        KERNEL32.CloseHandle(handle)
        return False
    MUTEX_HANDLE = handle
    return True


def release_single_instance() -> None:
    global MUTEX_HANDLE
    if MUTEX_HANDLE:
        try:
            KERNEL32.CloseHandle(MUTEX_HANDLE)
        except Exception:
            pass
        MUTEX_HANDLE = None


def instance_running() -> bool:
    """检查命名互斥体是否存在，不干扰正在持有它的进程。"""
    KERNEL32.OpenMutexW.restype = ctypes.c_void_p
    KERNEL32.OpenMutexW.argtypes = [ctypes.c_uint, ctypes.c_int, ctypes.c_wchar_p]
    handle = KERNEL32.OpenMutexW(0x00100000, False, MUTEX_NAME)  # SYNCHRONIZE（同步访问）
    if not handle:
        return False
    KERNEL32.CloseHandle(handle)
    return True


def _format_reading(reading) -> str:
    name = reading.target.name
    if reading.status == "ok":
        charging = ""
        if reading.charging:
            charging = "，充电中"
        return f"{name}：{reading.percent}%（{reading.source}{charging}）"
    message = reading.message or reading.status_text
    source = reading.source or reading.target.source_hint
    return f"{name}：--（{message}，来源：{source}）"


def command_once() -> int:
    config = AppConfig.load()
    repo = BatteryRepository(
        config,
        on_snapshot=lambda snapshot: None,
        debug=DEBUG,
        debug_log=debug_log,
    )
    snapshot = repo.refresh_sync()
    for reading in snapshot.readings:
        print(_format_reading(reading), flush=True)
    if not snapshot.readings:
        print("未发现支持的无线外设。", flush=True)
        return 1
    return 0 if snapshot.ok_count else 1


def command_list_hid() -> int:
    devices = enumerate_hid_devices()
    print(
        f"{'VID:PID':>9}  {'IN/OUT/FEAT':>12}  {'USAGE':>12}  "
        f"{'LOCATION':<16}  PRODUCT"
    )
    for info in devices:
        product = info.product or info.manufacturer or "(none)"
        reports = f"{info.input_len}/{info.output_len}/{info.feature_len}"
        usage = f"{info.usage_page:04X}/{info.usage:04X}"
        print(
            f"{info.vid_pid:>9}  {reports:>12}  {usage:>12}  "
            f"{info.short_location:<16}  {product}"
        )
    print(f"\n共 {len(devices)} 个 HID 接口。")
    return 0


def command_quit() -> int:
    if not request_quit_running_instance():
        # 短暂等待刚启动、托盘窗口尚未创建的实例。
        hwnd = wait_for_tray_window(2.0)
        if hwnd:
            request_quit_running_instance()
        else:
            print("电量托盘未在运行。", flush=True)
            return 0
    deadline = time.monotonic() + 4.0
    while time.monotonic() < deadline:
        if not instance_running() and not find_tray_window():
            print("已发送退出请求，托盘进程已结束。", flush=True)
            return 0
        time.sleep(0.05)
    print("已发送退出请求，但进程尚未退出。", flush=True)
    return 1


def command_install(links_only: bool = False, autostart_only: bool = False) -> int:
    if not autostart_only:
        try:
            result = create_desktop_links()
            print(f"桌面目录：{result['desktop']}")
            print(f"目录链接：{result['directory']} -> {result['directory_link']}")
            print(f"文件链接：{result['file']} -> {result['file_link']}")
        except Exception as exc:
            print(f"桌面链接失败：{exc}", flush=True)
            return 1
    if not links_only:
        try:
            shortcut = create_startup_shortcut()
            state = read_autostart_state()
            print(f"启动快捷方式：{shortcut}")
            print(f"目标：pythonw.exe + {BASE_DIR / 'my_devices.py'} --tray")
            print(f"状态：{'已启用' if state.enabled else '未启用'}")
        except Exception as exc:
            print(f"启动快捷方式失败：{exc}", flush=True)
            return 1
    return 0


def command_gui(show_panel: bool, open_debug: bool) -> int:
    set_dpi_awareness()
    set_app_user_model_id()
    if not acquire_single_instance():
        debug_log("another instance holds the mutex; asking it to show")
        if post_show_to_running_instance():
            return 0
        if wait_for_tray_window(2.0):
            post_show_to_running_instance()
        return 0
    config = AppConfig.load()
    try:
        from wireless.app import Application, show_fatal_error

        app = Application(
            config=config,
            start_hidden=not show_panel,
            open_debug=open_debug,
            debug=DEBUG,
            debug_log=debug_log,
        )
        try:
            app.root.report_callback_exception = _tk_exception
        except Exception:
            pass
        return app.run()
    except Exception as exc:
        if DEBUG:
            traceback.print_exc()
        else:
            try:
                show_fatal_error(f"启动失败：\n{exc}")
            except Exception:
                pass
        return 1
    finally:
        release_single_instance()


def _tk_exception(exc_type, exc_value, exc_tb) -> None:
    if DEBUG:
        traceback.print_exception(exc_type, exc_value, exc_tb)
    else:
        try:
            ctypes.windll.user32.MessageBoxW(
                None, f"界面错误：\n{exc_value}", "无线外设电量", 0x10
            )
        except Exception:
            pass


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Windows 10/11 无线外设电量工具（仅标准库 + ctypes）"
    )
    parser.add_argument("--once", action="store_true", help="读取一次并打印，然后退出")
    parser.add_argument("--list-hid", action="store_true", help="列出所有 HID 接口后退出")
    parser.add_argument("--tray", action="store_true", help="启动托盘并隐藏面板")
    parser.add_argument("--panel", action="store_true", help="启动托盘并显示简洁面板")
    parser.add_argument("--debug-ui", action="store_true", help="启动并打开完整调试窗口")
    parser.add_argument("--debug", action="store_true", help="只在控制台输出调试信息")
    parser.add_argument("--install", action="store_true", help="创建开机启动快捷方式和桌面链接")
    parser.add_argument("--links-only", action="store_true", help="只创建桌面链接")
    parser.add_argument("--autostart-only", action="store_true", help="只创建启动文件夹快捷方式")
    parser.add_argument("--quit", action="store_true", help="让正在运行的托盘退出")
    parser.add_argument("--version", action="store_true", help="显示版本")
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    global DEBUG
    parser = build_parser()
    args = parser.parse_args(argv)
    DEBUG = bool(args.debug)
    ensure_debug_console()
    if args.version:
        print(f"WirelessBatteryViewer {__version__}")
        return 0
    if args.quit:
        return command_quit()
    if args.list_hid:
        return command_list_hid()
    if args.once:
        return command_once()
    if args.install or args.links_only or args.autostart_only:
        return command_install(
            links_only=bool(args.links_only),
            autostart_only=bool(args.autostart_only),
        )
    show_panel = bool(args.panel) or not args.tray
    return command_gui(show_panel=show_panel, open_debug=bool(args.debug_ui))


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130)
    except SystemExit:
        raise
    except Exception as exc:
        if DEBUG:
            traceback.print_exc()
        else:
            try:
                ctypes.windll.user32.MessageBoxW(
                    None, f"未处理错误：\n{exc}", "无线外设电量", 0x10
                )
            except Exception:
                pass
        raise SystemExit(1)
