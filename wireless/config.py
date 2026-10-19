"""用户设置、Shell 快捷方式与桌面链接。

本模块绝不打开日志文件。唯一的持久化文件是：

* %LOCALAPPDATA%\\WirelessBatteryViewer\\config.json
* 可选的启动文件夹快捷方式
* 两个桌面链接；仅在用户主动请求时创建（或显式运行
  ``my_devices.py --install`` 时创建）
"""

from __future__ import annotations

import ctypes
import json
import os
import subprocess
import sys
import tempfile
import threading
import winreg
from ctypes import wintypes as w
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from .models import ManualCalibration
from .winhid import (
    CLSID_SHELLLINK,
    GUID,
    IID_IPERSISTFILE,
    IID_ISHELLLINKW,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
APP_DIR = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "WirelessBatteryViewer"
CONFIG_PATH = APP_DIR / "config.json"
STARTUP_SHORTCUT_NAME = "电量托盘.lnk"
DESKTOP_DIR_LINK_NAME = "无线外设电量"
DESKTOP_FILE_LINK_NAME = "启动电量托盘.pyw"
LAUNCH_SCRIPT = PROJECT_ROOT / "launch_tray.pyw"
MAIN_SCRIPT = PROJECT_ROOT / "my_devices.py"

DEFAULT_INTERVAL = 60
MIN_INTERVAL = 30
MAX_INTERVAL = 1800


# ---------------------------------------------------------------------------
# Windows 已知文件夹
# ---------------------------------------------------------------------------

FOLDERID_DESKTOP = "{B4BFCC3A-DB2C-424C-B029-7FE99A87C641}"
FOLDERID_STARTUP = "{B97D20BB-F46A-4C97-BA10-5E3608430854}"

shell32 = ctypes.WinDLL("shell32", use_last_error=True)
ole32 = ctypes.WinDLL("ole32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
user32 = ctypes.WinDLL("user32", use_last_error=True)

shell32.SHGetKnownFolderPath.restype = ctypes.c_long
shell32.SHGetKnownFolderPath.argtypes = [
    ctypes.POINTER(GUID), w.DWORD, w.HANDLE, ctypes.POINTER(ctypes.c_wchar_p)
]
ole32.CoTaskMemFree.argtypes = [ctypes.c_void_p]
ole32.CoInitializeEx.restype = ctypes.c_long
ole32.CoInitializeEx.argtypes = [ctypes.c_void_p, w.DWORD]
ole32.CoCreateInstance.restype = ctypes.c_long
ole32.CoCreateInstance.argtypes = [
    ctypes.POINTER(GUID), ctypes.c_void_p, w.DWORD, ctypes.POINTER(GUID),
    ctypes.POINTER(ctypes.c_void_p),
]


def known_folder(guid_text: str, fallback: Path) -> Path:
    try:
        guid = GUID(guid_text)
        out = ctypes.c_wchar_p()
        result = shell32.SHGetKnownFolderPath(
            ctypes.byref(guid), 0, None, ctypes.byref(out)
        )
        if result == 0 and out.value:
            value = Path(out.value)
            ole32.CoTaskMemFree(ctypes.cast(out, ctypes.c_void_p))
            return value
        if out:
            ole32.CoTaskMemFree(ctypes.cast(out, ctypes.c_void_p))
    except Exception:
        pass
    return fallback


def desktop_folder() -> Path:
    return known_folder(
        FOLDERID_DESKTOP,
        Path(os.environ.get("USERPROFILE", str(Path.home()))) / "Desktop",
    )


def startup_folder() -> Path:
    return known_folder(
        FOLDERID_STARTUP,
        Path(os.environ.get("APPDATA", str(Path.home()))) /
        "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup",
    )


def pythonw_path() -> Path:
    executable = Path(sys.executable)
    if executable.name.lower() == "pythonw.exe" and executable.exists():
        return executable
    sibling = executable.with_name("pythonw.exe")
    if sibling.exists():
        return sibling
    return executable


# ---------------------------------------------------------------------------
# 配置 JSON
# ---------------------------------------------------------------------------

@dataclass
class AppConfig:
    refresh_seconds: int = DEFAULT_INTERVAL
    calibrations: list[ManualCalibration] = field(default_factory=list)
    path: Path = CONFIG_PATH

    @classmethod
    def load(cls) -> "AppConfig":
        path = CONFIG_PATH
        config = cls(path=path)
        try:
            if not path.exists():
                return config
            raw = json.loads(path.read_text(encoding="utf-8"))
            seconds = int(raw.get("refresh_seconds", DEFAULT_INTERVAL))
            config.refresh_seconds = max(MIN_INTERVAL, min(MAX_INTERVAL, seconds))
            calibrations = []
            for item in raw.get("calibrations", []):
                try:
                    calibrations.append(ManualCalibration.from_dict(item))
                except Exception:
                    continue
            config.calibrations = calibrations
        except Exception:
            # 损坏的配置既不能阻止程序启动，也不能导致写日志文件或自动
            # 覆盖用户数据。
            pass
        return config

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "refresh_seconds": int(self.refresh_seconds),
            "calibrations": [item.to_dict() for item in self.calibrations],
        }
        text = json.dumps(payload, ensure_ascii=False, indent=2)
        fd, temp_name = tempfile.mkstemp(
            prefix="config.", suffix=".tmp", dir=str(self.path.parent)
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, self.path)
        finally:
            try:
                if os.path.exists(temp_name):
                    os.unlink(temp_name)
            except OSError:
                pass

    def add_calibration(self, calibration: ManualCalibration) -> None:
        self.calibrations = [
            item for item in self.calibrations
            if not (
                item.vendor_id == calibration.vendor_id
                and item.product_id == calibration.product_id
                and item.path_contains.lower() == calibration.path_contains.lower()
            )
        ]
        self.calibrations.append(calibration)
        self.save()


# ---------------------------------------------------------------------------
# ShellLinkW（纯 ctypes 实现 COM）
# ---------------------------------------------------------------------------

HRESULT = ctypes.c_long


def _com_method(interface: int, index: int, restype, argtypes):
    address = ctypes.c_void_p(interface)
    vtable = ctypes.cast(
        address, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))
    ).contents
    prototype = ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)
    function = prototype(vtable[index])
    return function


def _release(interface: int) -> None:
    if not interface:
        return
    try:
        function = _com_method(interface, 2, ctypes.c_ulong, [])
        function(ctypes.c_void_p(interface))
    except Exception:
        pass


def _check_hresult(result: int, action: str) -> None:
    # HRESULT 在 ctypes 中是有符号整数；>= 0 表示成功（SUCCEEDED）。
    if result < 0:
        raise OSError(
            result,
            f"{action} 调用失败（HRESULT 0x{result & 0xFFFFFFFF:08X}）",
        )


class _Win32FindDataW(ctypes.Structure):
    _fields_ = [
        ("dwFileAttributes", w.DWORD),
        ("ftCreationTime", w.FILETIME),
        ("ftLastAccessTime", w.FILETIME),
        ("ftLastWriteTime", w.FILETIME),
        ("nFileSizeHigh", w.DWORD),
        ("nFileSizeLow", w.DWORD),
        ("dwReserved0", w.DWORD),
        ("dwReserved1", w.DWORD),
        ("cFileName", w.WCHAR * 260),
        ("cAlternateFileName", w.WCHAR * 14),
    ]


_COM_LOCAL = threading.local()


@dataclass
class ShortcutInfo:
    path: Path
    exists: bool
    target: str = ""
    arguments: str = ""
    working_directory: str = ""


def _co_initialize() -> None:
    if getattr(_COM_LOCAL, "initialized", False):
        return
    try:
        result = ole32.CoInitializeEx(None, 0x2)  # COINIT_APARTMENTTHREADED（单线程套间）
        # S_OK / S_FALSE / RPC_E_CHANGED_MODE 都表示 COM 仍可使用。
        if result in (0, 1, 0x80010106):
            _COM_LOCAL.initialized = True
    except Exception:
        pass


def _create_shell_link_object():
    _co_initialize()
    clsid = GUID(CLSID_SHELLLINK)
    iid = GUID(IID_ISHELLLINKW)
    interface = ctypes.c_void_p()
    result = ole32.CoCreateInstance(
        ctypes.byref(clsid), None, 1, ctypes.byref(iid),
        ctypes.byref(interface),
    )
    _check_hresult(result, "CoCreateInstance(CLSID_ShellLink)")
    return interface.value


def create_shortcut(
    shortcut_path: Path,
    target: Path,
    arguments: str = "",
    working_directory: Optional[Path] = None,
    description: str = "",
    icon_path: Optional[Path] = None,
) -> None:
    interface = _create_shell_link_object()
    persist = ctypes.c_void_p()
    try:
        set_path = _com_method(interface, 20, HRESULT, [ctypes.c_wchar_p])
        _check_hresult(set_path(interface, str(target)), "IShellLinkW.SetPath")

        if arguments:
            set_args = _com_method(interface, 11, HRESULT, [ctypes.c_wchar_p])
            _check_hresult(set_args(interface, arguments), "IShellLinkW.SetArguments")

        if working_directory is not None:
            set_cwd = _com_method(interface, 9, HRESULT, [ctypes.c_wchar_p])
            _check_hresult(set_cwd(interface, str(working_directory)),
                           "IShellLinkW.SetWorkingDirectory")

        if description:
            set_description = _com_method(interface, 7, HRESULT, [ctypes.c_wchar_p])
            _check_hresult(set_description(interface, description),
                           "IShellLinkW.SetDescription")

        if icon_path is not None:
            set_icon = _com_method(
                interface, 17, HRESULT, [ctypes.c_wchar_p, ctypes.c_int]
            )
            _check_hresult(set_icon(interface, str(icon_path), 0),
                           "IShellLinkW.SetIconLocation")

        iid_persist = GUID(IID_IPERSISTFILE)
        query = _com_method(
            interface, 0, HRESULT,
            [ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p)],
        )
        _check_hresult(
            query(interface, ctypes.byref(iid_persist), ctypes.byref(persist)),
            "IShellLinkW.QueryInterface(IPersistFile)",
        )
        save = _com_method(persist.value, 6, HRESULT, [ctypes.c_wchar_p, w.BOOL])
        shortcut_path.parent.mkdir(parents=True, exist_ok=True)
        _check_hresult(save(persist.value, str(shortcut_path), True),
                       "IPersistFile.Save")
    finally:
        if persist.value:
            _release(persist.value)
        _release(interface)


def read_shortcut(shortcut_path: Path) -> ShortcutInfo:
    info = ShortcutInfo(path=shortcut_path, exists=shortcut_path.exists())
    if not info.exists:
        return info
    interface = _create_shell_link_object()
    persist = ctypes.c_void_p()
    try:
        iid_persist = GUID(IID_IPERSISTFILE)
        query = _com_method(
            interface, 0, HRESULT,
            [ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p)],
        )
        _check_hresult(
            query(interface, ctypes.byref(iid_persist), ctypes.byref(persist)),
            "IShellLinkW.QueryInterface(IPersistFile)",
        )
        load = _com_method(persist.value, 5, HRESULT, [ctypes.c_wchar_p, w.DWORD])
        _check_hresult(load(persist.value, str(shortcut_path), 0),
                       "IPersistFile.Load")

        target_buf = ctypes.create_unicode_buffer(32768)
        get_path = _com_method(
            interface, 3, HRESULT,
            [ctypes.c_wchar_p, ctypes.c_int, ctypes.c_void_p, w.DWORD],
        )
        get_path(interface, target_buf, len(target_buf), None, 0x4)
        info.target = target_buf.value

        args_buf = ctypes.create_unicode_buffer(4096)
        get_args = _com_method(
            interface, 10, HRESULT, [ctypes.c_wchar_p, ctypes.c_int]
        )
        get_args(interface, args_buf, len(args_buf))
        info.arguments = args_buf.value

        cwd_buf = ctypes.create_unicode_buffer(32768)
        get_cwd = _com_method(
            interface, 8, HRESULT, [ctypes.c_wchar_p, ctypes.c_int]
        )
        get_cwd(interface, cwd_buf, len(cwd_buf))
        info.working_directory = cwd_buf.value
    finally:
        if persist.value:
            _release(persist.value)
        _release(interface)
    return info


# ---------------------------------------------------------------------------
# 启动文件夹集成
# ---------------------------------------------------------------------------

STARTUP_APPROVED_KEY = (
    r"Software\Microsoft\Windows\CurrentVersion\Explorer"
    r"\StartupApproved\StartupFolder"
)


@dataclass
class AutostartState:
    shortcut_exists: bool
    shortcut_matches: bool
    approved_disabled: bool
    shortcut: ShortcutInfo
    target: str = ""
    arguments: str = ""

    @property
    def enabled(self) -> bool:
        return (
            self.shortcut_exists
            and self.shortcut_matches
            and not self.approved_disabled
        )


def _read_approved_disabled(name: str) -> bool:
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, STARTUP_APPROVED_KEY
        ) as key:
            try:
                data, kind = winreg.QueryValueEx(key, name)
            except FileNotFoundError:
                return False
            if isinstance(data, bytes) and data:
                # Explorer 写入 0x02 表示启用，0x03 表示禁用。
                return data[0] not in (0x02, 0x06)
            if isinstance(data, int):
                return data not in (2, 6)
    except FileNotFoundError:
        return False
    except OSError:
        return False
    return False


def _set_approved_disabled(name: str, disabled: bool) -> None:
    try:
        with winreg.CreateKeyEx(
            winreg.HKEY_CURRENT_USER, STARTUP_APPROVED_KEY, 0,
            winreg.KEY_SET_VALUE,
        ) as key:
            if disabled:
                data = bytes([0x03]) + b"\x00" * 11
                winreg.SetValueEx(key, name, 0, winreg.REG_BINARY, data)
            else:
                try:
                    winreg.DeleteValue(key, name)
                except FileNotFoundError:
                    pass
    except OSError:
        if disabled:
            raise


def startup_shortcut_path() -> Path:
    return startup_folder() / STARTUP_SHORTCUT_NAME


def _expected_script_path() -> str:
    return os.path.normcase(os.path.abspath(str(MAIN_SCRIPT)))


def read_autostart_state() -> AutostartState:
    shortcut_path = startup_shortcut_path()
    try:
        info = read_shortcut(shortcut_path)
    except Exception:
        info = ShortcutInfo(path=shortcut_path, exists=shortcut_path.exists())
    target_ok = False
    args_ok = False
    try:
        expected_target = os.path.normcase(os.path.abspath(str(pythonw_path())))
        target_ok = bool(info.target) and os.path.normcase(
            os.path.abspath(info.target)
        ) == expected_target
        args_ok = os.path.normcase(_expected_script_path()) in os.path.normcase(
            info.arguments.replace('"', "")
        )
    except Exception:
        pass
    return AutostartState(
        shortcut_exists=shortcut_path.exists(),
        shortcut_matches=target_ok and args_ok,
        approved_disabled=_read_approved_disabled(STARTUP_SHORTCUT_NAME),
        shortcut=info,
        target=info.target,
        arguments=info.arguments,
    )


def create_startup_shortcut() -> Path:
    shortcut_path = startup_shortcut_path()
    arguments = f'"{MAIN_SCRIPT}" --tray'
    create_shortcut(
        shortcut_path=shortcut_path,
        target=pythonw_path(),
        arguments=arguments,
        working_directory=PROJECT_ROOT,
        description="无线外设电量托盘",
        icon_path=pythonw_path(),
    )
    _set_approved_disabled(STARTUP_SHORTCUT_NAME, False)
    return shortcut_path


def set_autostart_enabled(enabled: bool) -> AutostartState:
    """仅供用户显式操作；启动时绝不自动调用。"""
    shortcut_path = startup_shortcut_path()
    if enabled:
        create_startup_shortcut()
    else:
        if shortcut_path.exists():
            _set_approved_disabled(STARTUP_SHORTCUT_NAME, True)
    return read_autostart_state()


def remove_startup_shortcut() -> None:
    """显式卸载操作；托盘正常启动时不会调用。"""
    shortcut_path = startup_shortcut_path()
    try:
        shortcut_path.unlink()
    except FileNotFoundError:
        pass
    _set_approved_disabled(STARTUP_SHORTCUT_NAME, False)


# ---------------------------------------------------------------------------
# 桌面链接（符号链接，失败时退 junction / 硬链接）
# ---------------------------------------------------------------------------

def _is_directory_link(path: Path) -> bool:
    try:
        if path.is_symlink():
            return True
        is_junction = getattr(os.path, "isjunction", None)
        if is_junction and is_junction(path):
            return True
        # Python 对 junction 调用 stat 时会带 FILE_ATTRIBUTE_REPARSE_POINT。
        return bool(path.stat().st_file_attributes & 0x400)
    except (OSError, AttributeError):
        return False


def _same_path(left: Path, right: Path) -> bool:
    try:
        if os.path.samefile(left, right):
            return True
    except OSError:
        pass
    try:
        return os.path.normcase(os.path.abspath(str(left.resolve()))) == \
            os.path.normcase(os.path.abspath(str(right.resolve())))
    except OSError:
        return False


def _make_junction(link: Path, target: Path) -> None:
    result = subprocess.run(
        ["cmd.exe", "/d", "/c", "mklink", "/J", str(link), str(target)],
        capture_output=True, text=True, errors="replace",
        creationflags=0x08000000,
    )
    if result.returncode != 0:
        message = (result.stderr or result.stdout).strip()
        raise OSError(f"junction 创建失败：{message}")


def create_desktop_links() -> dict[str, Any]:
    """创建目录链接和启动脚本链接。

    目录优先使用真正的符号链接，其次使用 junction；文件优先使用符号链接，
    其次使用硬链接。已存在的普通文件绝不会被删除或覆盖。
    """
    desktop = desktop_folder()
    desktop.mkdir(parents=True, exist_ok=True)
    directory_link = desktop / DESKTOP_DIR_LINK_NAME
    file_link = desktop / DESKTOP_FILE_LINK_NAME
    result = {
        "desktop": str(desktop),
        "directory_link": str(directory_link),
        "file_link": str(file_link),
        "directory": "",
        "file": "",
    }

    if directory_link.exists() or _is_directory_link(directory_link):
        if _is_directory_link(directory_link) and _same_path(directory_link, PROJECT_ROOT):
            result["directory"] = "已存在"
        else:
            raise OSError(f"桌面已存在同名项目，未做任何覆盖：{directory_link}")
    else:
        try:
            os.symlink(PROJECT_ROOT, directory_link, target_is_directory=True)
            result["directory"] = "符号链接"
        except OSError:
            try:
                _make_junction(directory_link, PROJECT_ROOT)
                result["directory"] = "junction"
            except OSError as junction_error:
                raise OSError(
                    "目录链接创建失败（符号链接和 junction 均不可用）："
                    + str(junction_error)
                )

    if file_link.exists() or file_link.is_symlink():
        if _same_path(file_link, LAUNCH_SCRIPT):
            result["file"] = "已存在"
        else:
            raise OSError(f"桌面已存在同名文件，未做任何覆盖：{file_link}")
    else:
        try:
            os.symlink(LAUNCH_SCRIPT, file_link)
            result["file"] = "符号链接"
        except OSError:
            try:
                os.link(LAUNCH_SCRIPT, file_link)
                result["file"] = "硬链接"
            except OSError as link_error:
                raise OSError(
                    "启动脚本链接创建失败（符号链接和硬链接均不可用）："
                    + str(link_error)
                )
    return result


def remove_desktop_links() -> None:
    """显式卸载辅助函数；只删除指向本项目的链接。"""
    desktop = desktop_folder()
    directory_link = desktop / DESKTOP_DIR_LINK_NAME
    file_link = desktop / DESKTOP_FILE_LINK_NAME
    if _is_directory_link(directory_link) and _same_path(directory_link, PROJECT_ROOT):
        try:
            os.rmdir(directory_link)
        except OSError:
            subprocess.run(
                ["cmd.exe", "/d", "/c", "rmdir", str(directory_link)],
                capture_output=True, creationflags=0x08000000,
            )
    if (file_link.is_symlink() or file_link.exists()) and _same_path(file_link, LAUNCH_SCRIPT):
        try:
            file_link.unlink()
        except OSError:
            pass
