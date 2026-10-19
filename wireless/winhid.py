"""Windows HID layer built only on ctypes.

Everything here uses the ordinary Windows HID API (setupapi/hid/kernel32).
No third-party modules are involved.

Two rules are deliberately enforced in this module:

* A HID handle is never used for a blocking ReadFile at the same time as a
  WriteFile/IOCTL on that same handle.  Vendor exchanges use a separate
  FILE_FLAG_OVERLAPPED read handle and a separate write handle.
* System-exclusive top-level collections are always opened with
  dwDesiredAccess=0 for metadata and Feature Report access.
"""

from __future__ import annotations

import ctypes
import os
import re
import sys
import time
from ctypes import wintypes as w
from dataclasses import dataclass, field
from typing import Iterable, Optional, Sequence

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
setupapi = ctypes.WinDLL("setupapi", use_last_error=True)
hid = ctypes.WinDLL("hid", use_last_error=True)

INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
FILE_SHARE_READ = 0x00000001
FILE_SHARE_WRITE = 0x00000002
OPEN_EXISTING = 3
FILE_FLAG_OVERLAPPED = 0x40000000
ERROR_IO_PENDING = 997
ERROR_NO_MORE_ITEMS = 259
WAIT_OBJECT_0 = 0
WAIT_TIMEOUT = 258
ERROR_SEM_TIMEOUT = 121

DIGCF_PRESENT = 0x00000002
DIGCF_DEVICEINTERFACE = 0x00000010

HIDP_STATUS_SUCCESS = 0x00110000
HIDP_STATUS_BUFFER_TOO_SMALL = 0xC0110007
HIDP_REPORT_TYPE_INPUT = 0
HIDP_REPORT_TYPE_OUTPUT = 1
HIDP_REPORT_TYPE_FEATURE = 2


class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", w.DWORD),
        ("Data2", w.WORD),
        ("Data3", w.WORD),
        ("Data4", ctypes.c_ubyte * 8),
    ]

    def __init__(self, text: Optional[str] = None):
        super().__init__()
        if text:
            # IMPORTANT: split only three times.  The last section itself
            # still contains a dash (e.g. C000-000000000046).
            parts = text.strip().strip("{}").split("-", 3)
            if len(parts) != 4:
                raise ValueError("bad GUID: " + text)
            self.Data1 = int(parts[0], 16)
            self.Data2 = int(parts[1], 16)
            self.Data3 = int(parts[2], 16)
            tail = bytes.fromhex(parts[3].replace("-", ""))
            if len(tail) != 8:
                raise ValueError("bad GUID tail: " + text)
            for index, value in enumerate(tail):
                self.Data4[index] = value


GUID_DEVINTERFACE_HID = "{4D1E55B2-F16F-11CF-88CB-001111000030}"
CLSID_SHELLLINK = "{00021401-0000-0000-C000-000000000046}"
IID_ISHELLLINKW = "{000214F9-0000-0000-C000-000000000046}"
IID_IPERSISTFILE = "{0000010B-0000-0000-C000-000000000046}"


class SP_DEVICE_INTERFACE_DATA(ctypes.Structure):
    _fields_ = [
        ("cbSize", w.DWORD),
        ("InterfaceClassGuid", GUID),
        ("Flags", w.DWORD),
        ("Reserved", ctypes.c_size_t),
    ]


class HIDD_ATTRIBUTES(ctypes.Structure):
    _fields_ = [
        ("Size", w.ULONG),
        ("VendorID", w.USHORT),
        ("ProductID", w.USHORT),
        ("VersionNumber", w.USHORT),
    ]


class HIDP_CAPS(ctypes.Structure):
    _fields_ = [
        ("Usage", w.USHORT),
        ("UsagePage", w.USHORT),
        ("InputReportByteLength", w.USHORT),
        ("OutputReportByteLength", w.USHORT),
        ("FeatureReportByteLength", w.USHORT),
        ("Reserved", w.USHORT * 17),
        ("NumberLinkCollectionNodes", w.USHORT),
        ("NumberInputButtonCaps", w.USHORT),
        ("NumberInputValueCaps", w.USHORT),
        ("NumberInputDataIndices", w.USHORT),
        ("NumberOutputButtonCaps", w.USHORT),
        ("NumberOutputValueCaps", w.USHORT),
        ("NumberOutputDataIndices", w.USHORT),
        ("NumberFeatureButtonCaps", w.USHORT),
        ("NumberFeatureValueCaps", w.USHORT),
        ("NumberFeatureDataIndices", w.USHORT),
    ]


class _ValueCapsRange(ctypes.Structure):
    _fields_ = [
        ("UsageMin", w.USHORT),
        ("UsageMax", w.USHORT),
        ("StringMin", w.USHORT),
        ("StringMax", w.USHORT),
        ("DesignatorMin", w.USHORT),
        ("DesignatorMax", w.USHORT),
        ("DataIndexMin", w.USHORT),
        ("DataIndexMax", w.USHORT),
    ]


class _ValueCapsNotRange(ctypes.Structure):
    _fields_ = [
        ("Usage", w.USHORT),
        ("Reserved1", w.USHORT),
        ("StringIndex", w.USHORT),
        ("Reserved2", w.USHORT),
        ("DesignatorIndex", w.USHORT),
        ("Reserved3", w.USHORT),
        ("DataIndex", w.USHORT),
        ("Reserved4", w.USHORT),
    ]


class _ValueCapsUnion(ctypes.Union):
    _fields_ = [("Range", _ValueCapsRange), ("NotRange", _ValueCapsNotRange)]


class HIDP_VALUE_CAPS(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [
        ("UsagePage", w.USHORT),
        ("ReportID", ctypes.c_ubyte),
        ("IsAlias", ctypes.c_ubyte),
        ("BitField", w.USHORT),
        ("LinkCollection", w.USHORT),
        ("LinkUsage", w.USHORT),
        ("LinkUsagePage", w.USHORT),
        ("IsRange", ctypes.c_ubyte),
        ("IsStringRange", ctypes.c_ubyte),
        ("IsDesignatorRange", ctypes.c_ubyte),
        ("IsAbsolute", ctypes.c_ubyte),
        ("HasNull", ctypes.c_ubyte),
        ("Reserved", ctypes.c_ubyte),
        ("BitSize", w.USHORT),
        ("ReportCount", w.USHORT),
        ("Reserved2", w.USHORT * 5),
        ("UnitsExp", w.ULONG),
        ("Units", w.ULONG),
        ("LogicalMin", w.LONG),
        ("LogicalMax", w.LONG),
        ("PhysicalMin", w.LONG),
        ("PhysicalMax", w.LONG),
        ("u", _ValueCapsUnion),
    ]


class OVERLAPPED(ctypes.Structure):
    _fields_ = [
        ("Internal", ctypes.c_size_t),
        ("InternalHigh", ctypes.c_size_t),
        ("Offset", w.DWORD),
        ("OffsetHigh", w.DWORD),
        ("hEvent", w.HANDLE),
    ]


# ---------------------------------------------------------------------------
# Function prototypes
# ---------------------------------------------------------------------------

setupapi.SetupDiGetClassDevsW.restype = w.HANDLE
setupapi.SetupDiGetClassDevsW.argtypes = [
    ctypes.POINTER(GUID), w.LPCWSTR, w.HWND, w.DWORD
]
setupapi.SetupDiEnumDeviceInterfaces.restype = w.BOOL
setupapi.SetupDiEnumDeviceInterfaces.argtypes = [
    w.HANDLE, ctypes.c_void_p, ctypes.POINTER(GUID), w.DWORD,
    ctypes.POINTER(SP_DEVICE_INTERFACE_DATA),
]
setupapi.SetupDiGetDeviceInterfaceDetailW.restype = w.BOOL
setupapi.SetupDiGetDeviceInterfaceDetailW.argtypes = [
    w.HANDLE, ctypes.POINTER(SP_DEVICE_INTERFACE_DATA), ctypes.c_void_p,
    w.DWORD, ctypes.POINTER(w.DWORD), ctypes.c_void_p,
]
setupapi.SetupDiDestroyDeviceInfoList.argtypes = [w.HANDLE]

hid.HidD_GetAttributes.restype = w.BOOL
hid.HidD_GetAttributes.argtypes = [w.HANDLE, ctypes.POINTER(HIDD_ATTRIBUTES)]
hid.HidD_GetProductString.restype = w.BOOL
hid.HidD_GetProductString.argtypes = [w.HANDLE, ctypes.c_void_p, w.ULONG]
hid.HidD_GetManufacturerString.restype = w.BOOL
hid.HidD_GetManufacturerString.argtypes = [w.HANDLE, ctypes.c_void_p, w.ULONG]
hid.HidD_GetSerialNumberString.restype = w.BOOL
hid.HidD_GetSerialNumberString.argtypes = [w.HANDLE, ctypes.c_void_p, w.ULONG]
hid.HidD_GetPreparsedData.restype = w.BOOL
hid.HidD_GetPreparsedData.argtypes = [
    w.HANDLE, ctypes.POINTER(ctypes.c_void_p)
]
hid.HidD_FreePreparsedData.restype = w.BOOL
hid.HidD_FreePreparsedData.argtypes = [ctypes.c_void_p]
hid.HidP_GetCaps.restype = ctypes.c_long
hid.HidP_GetCaps.argtypes = [ctypes.c_void_p, ctypes.POINTER(HIDP_CAPS)]
hid.HidP_GetValueCaps.restype = ctypes.c_long
hid.HidP_GetValueCaps.argtypes = [
    ctypes.c_int, ctypes.POINTER(HIDP_VALUE_CAPS), ctypes.POINTER(w.USHORT),
    ctypes.c_void_p,
]
hid.HidP_GetUsageValue.restype = ctypes.c_long
hid.HidP_GetUsageValue.argtypes = [
    ctypes.c_int, w.USHORT, w.USHORT, w.USHORT, ctypes.POINTER(w.ULONG),
    ctypes.c_void_p, ctypes.c_void_p, w.ULONG,
]
hid.HidD_GetFeature.restype = w.BOOL
hid.HidD_GetFeature.argtypes = [w.HANDLE, ctypes.c_void_p, w.ULONG]
hid.HidD_SetFeature.restype = w.BOOL
hid.HidD_SetFeature.argtypes = [w.HANDLE, ctypes.c_void_p, w.ULONG]

kernel32.CreateFileW.restype = w.HANDLE
kernel32.CreateFileW.argtypes = [
    w.LPCWSTR, w.DWORD, w.DWORD, ctypes.c_void_p, w.DWORD, w.DWORD, w.HANDLE
]
kernel32.CloseHandle.restype = w.BOOL
kernel32.CloseHandle.argtypes = [w.HANDLE]
kernel32.ReadFile.restype = w.BOOL
kernel32.ReadFile.argtypes = [
    w.HANDLE, ctypes.c_void_p, w.DWORD, ctypes.POINTER(w.DWORD),
    ctypes.POINTER(OVERLAPPED),
]
kernel32.WriteFile.restype = w.BOOL
kernel32.WriteFile.argtypes = [
    w.HANDLE, ctypes.c_void_p, w.DWORD, ctypes.POINTER(w.DWORD),
    ctypes.POINTER(OVERLAPPED),
]
kernel32.CreateEventW.restype = w.HANDLE
kernel32.CreateEventW.argtypes = [
    ctypes.c_void_p, w.BOOL, w.BOOL, w.LPCWSTR
]
kernel32.ResetEvent.restype = w.BOOL
kernel32.ResetEvent.argtypes = [w.HANDLE]
kernel32.WaitForSingleObject.restype = w.DWORD
kernel32.WaitForSingleObject.argtypes = [w.HANDLE, w.DWORD]
kernel32.GetOverlappedResult.restype = w.BOOL
kernel32.GetOverlappedResult.argtypes = [
    w.HANDLE, ctypes.POINTER(OVERLAPPED), ctypes.POINTER(w.DWORD), w.BOOL
]
kernel32.CancelIoEx.restype = w.BOOL
kernel32.CancelIoEx.argtypes = [w.HANDLE, ctypes.POINTER(OVERLAPPED)]


# ---------------------------------------------------------------------------
# Basic HID types
# ---------------------------------------------------------------------------

_PATH_VID = re.compile(r"vid_([0-9a-f]{4})", re.IGNORECASE)
_PATH_PID = re.compile(r"pid_([0-9a-f]{4})", re.IGNORECASE)
_PATH_MI = re.compile(r"mi_([0-9a-f]{2})", re.IGNORECASE)
_PATH_COL = re.compile(r"&col([0-9a-f]{2})", re.IGNORECASE)


@dataclass(frozen=True)
class HidDeviceInfo:
    path: str
    vendor_id: int
    product_id: int
    version: int = 0
    manufacturer: str = ""
    product: str = ""
    serial: str = ""
    usage_page: int = 0
    usage: int = 0
    input_len: int = 0
    output_len: int = 0
    feature_len: int = 0
    interface_number: Optional[int] = None
    collection_number: Optional[int] = None
    accessible: bool = False
    meta_error: str = ""

    @property
    def vid_pid(self) -> str:
        return f"{self.vendor_id:04X}:{self.product_id:04X}"

    @property
    def interface_key(self) -> str:
        """Stable identity for a HID collection inside one USB device."""
        mi = f"mi_{self.interface_number:02X}" if self.interface_number is not None else "mi_00"
        col = f"col{self.collection_number:02X}" if self.collection_number is not None else "col00"
        return f"{self.vendor_id:04x}:{self.product_id:04x}:{mi}:{col}"

    @property
    def display_name(self) -> str:
        if self.product:
            return self.product
        return f"HID {self.vid_pid}"

    @property
    def short_location(self) -> str:
        mi = f"MI_{self.interface_number:02d}" if self.interface_number is not None else "MI_?"
        col = f"COL_{self.collection_number:02d}" if self.collection_number is not None else "COL_?"
        return f"{mi} / {col}"

    def matches(self, text: str) -> bool:
        return text.lower() in self.path.lower()


def _parse_path(path: str) -> tuple[Optional[int], Optional[int], int, int]:
    low = path.lower()
    m = _PATH_MI.search(low)
    mi = int(m.group(1), 16) if m else None
    m = _PATH_COL.search(low)
    col = int(m.group(1), 16) if m else None
    m = _PATH_VID.search(low)
    vid = int(m.group(1), 16) if m else 0
    m = _PATH_PID.search(low)
    pid = int(m.group(1), 16) if m else 0
    return mi, col, vid, pid


def _unicode_string(handle: int, function) -> str:
    buf = ctypes.create_unicode_buffer(256)
    if function(handle, buf, ctypes.sizeof(buf)):
        return buf.value
    return ""


def _get_feature_length(path: str) -> int:
    try:
        with open_hid(path, 0) as handle:
            pdata = ctypes.c_void_p()
            if not hid.HidD_GetPreparsedData(handle, ctypes.byref(pdata)):
                return 0
            try:
                caps = HIDP_CAPS()
                if hid.HidP_GetCaps(pdata, ctypes.byref(caps)) != HIDP_STATUS_SUCCESS:
                    return 0
                return int(caps.FeatureReportByteLength)
            finally:
                hid.HidD_FreePreparsedData(pdata)
    except OSError:
        return 0


def _read_metadata(path: str) -> HidDeviceInfo:
    mi, col, path_vid, path_pid = _parse_path(path)
    attrs = HIDD_ATTRIBUTES()
    attrs.Size = ctypes.sizeof(attrs)
    manufacturer = product = serial = ""
    version = 0
    usage_page = usage = 0
    input_len = output_len = feature_len = 0
    accessible = False
    meta_error = ""
    handle = INVALID_HANDLE_VALUE
    try:
        handle = _open_handle(path, 0, False)
        accessible = True
        hid.HidD_GetAttributes(handle, ctypes.byref(attrs))
        manufacturer = _unicode_string(handle, hid.HidD_GetManufacturerString)
        product = _unicode_string(handle, hid.HidD_GetProductString)
        serial = _unicode_string(handle, hid.HidD_GetSerialNumberString)
        pdata = ctypes.c_void_p()
        if hid.HidD_GetPreparsedData(handle, ctypes.byref(pdata)):
            try:
                caps = HIDP_CAPS()
                if hid.HidP_GetCaps(pdata, ctypes.byref(caps)) == HIDP_STATUS_SUCCESS:
                    usage_page = int(caps.UsagePage)
                    usage = int(caps.Usage)
                    input_len = int(caps.InputReportByteLength)
                    output_len = int(caps.OutputReportByteLength)
                    feature_len = int(caps.FeatureReportByteLength)
            finally:
                hid.HidD_FreePreparsedData(pdata)
    except OSError as exc:
        meta_error = str(exc)
    finally:
        if handle not in (None, INVALID_HANDLE_VALUE):
            kernel32.CloseHandle(handle)

    return HidDeviceInfo(
        path=path,
        vendor_id=int(attrs.VendorID) or path_vid,
        product_id=int(attrs.ProductID) or path_pid,
        version=int(attrs.VersionNumber),
        manufacturer=manufacturer,
        product=product,
        serial=serial,
        usage_page=usage_page,
        usage=usage,
        input_len=input_len,
        output_len=output_len,
        feature_len=feature_len,
        interface_number=mi,
        collection_number=col,
        accessible=accessible,
        meta_error=meta_error,
    )


def enumerate_hid_devices() -> list[HidDeviceInfo]:
    """Enumerate every present HID interface, including inaccessible ones."""
    guid = GUID(GUID_DEVINTERFACE_HID)
    flags = DIGCF_PRESENT | DIGCF_DEVICEINTERFACE
    info_set = setupapi.SetupDiGetClassDevsW(
        ctypes.byref(guid), None, None, flags
    )
    if info_set in (None, INVALID_HANDLE_VALUE):
        raise ctypes.WinError(ctypes.get_last_error())

    paths: list[str] = []
    try:
        index = 0
        while True:
            did = SP_DEVICE_INTERFACE_DATA()
            did.cbSize = ctypes.sizeof(did)
            if not setupapi.SetupDiEnumDeviceInterfaces(
                info_set, None, ctypes.byref(guid), index, ctypes.byref(did)
            ):
                error = ctypes.get_last_error()
                if error == ERROR_NO_MORE_ITEMS:
                    break
                raise ctypes.WinError(error)
            index += 1

            required = w.DWORD(0)
            setupapi.SetupDiGetDeviceInterfaceDetailW(
                info_set, ctypes.byref(did), None, 0,
                ctypes.byref(required), None,
            )
            if required.value <= 0:
                continue
            detail = ctypes.create_string_buffer(required.value)
            # SP_DEVICE_INTERFACE_DETAIL_DATA_W.cbSize is 8 on 64-bit, 6 on x86.
            ctypes.cast(detail, ctypes.POINTER(w.DWORD))[0] = (
                8 if ctypes.sizeof(ctypes.c_void_p) == 8 else 6
            )
            if not setupapi.SetupDiGetDeviceInterfaceDetailW(
                info_set, ctypes.byref(did), detail, required.value,
                ctypes.byref(required), None,
            ):
                continue
            path = ctypes.wstring_at(ctypes.addressof(detail) + ctypes.sizeof(w.DWORD))
            if path:
                paths.append(path)
    finally:
        setupapi.SetupDiDestroyDeviceInfoList(info_set)

    devices = [_read_metadata(path) for path in paths]
    devices.sort(
        key=lambda item: (
            item.vendor_id,
            item.product_id,
            item.interface_number if item.interface_number is not None else -1,
            item.collection_number if item.collection_number is not None else -1,
            item.path.lower(),
        )
    )
    return devices


# ---------------------------------------------------------------------------
# Handles and overlapped reader
# ---------------------------------------------------------------------------

def _open_handle(path: str, access: int, overlapped: bool) -> int:
    flags = FILE_FLAG_OVERLAPPED if overlapped else 0
    handle = kernel32.CreateFileW(
        path,
        access,
        FILE_SHARE_READ | FILE_SHARE_WRITE,
        None,
        OPEN_EXISTING,
        flags,
        None,
    )
    if handle in (None, INVALID_HANDLE_VALUE):
        error = ctypes.get_last_error()
        raise OSError(error, ctypes.FormatError(error))
    return handle


class open_hid:
    """Context manager for an access-0 metadata/Feature handle."""

    def __init__(self, path: str, access: int = 0):
        self.path = path
        self.access = access
        self.handle = INVALID_HANDLE_VALUE

    def __enter__(self) -> int:
        self.handle = _open_handle(self.path, self.access, False)
        return self.handle

    def __exit__(self, exc_type, exc, tb) -> None:
        if self.handle not in (None, INVALID_HANDLE_VALUE):
            kernel32.CloseHandle(self.handle)
            self.handle = INVALID_HANDLE_VALUE


class OverlappedHidReader:
    """Dedicated overlapped HID read handle.

    It owns the only read handle for a vendor exchange.  No write ever happens
    on this handle, which is the safety rule that avoids the known Windows HID
    deadlock/crash.
    """

    def __init__(self, path: str, report_len: int = 64):
        self.path = path
        self.report_len = max(1, int(report_len or 64))
        self.handle = _open_handle(
            path, GENERIC_READ, True
        )
        self.event = kernel32.CreateEventW(None, True, False, None)
        if not self.event:
            kernel32.CloseHandle(self.handle)
            raise ctypes.WinError(ctypes.get_last_error())
        self._closed = False

    def read(self, timeout_ms: int) -> Optional[bytes]:
        if self._closed:
            return None
        timeout_ms = max(0, int(timeout_ms))
        buf = (ctypes.c_ubyte * self.report_len)()
        count = w.DWORD(0)
        ov = OVERLAPPED()
        ov.hEvent = self.event
        kernel32.ResetEvent(self.event)
        ok = kernel32.ReadFile(
            self.handle, buf, self.report_len, ctypes.byref(count), ctypes.byref(ov)
        )
        if not ok:
            error = ctypes.get_last_error()
            if error != ERROR_IO_PENDING:
                raise ctypes.WinError(error)
            wait = kernel32.WaitForSingleObject(self.event, timeout_ms)
            if wait != WAIT_OBJECT_0:
                kernel32.CancelIoEx(self.handle, ctypes.byref(ov))
                # Wait for cancellation completion before letting ov go out of
                # scope.  A timed-out overlapped read must never outlive its
                # OVERLAPPED structure.
                kernel32.WaitForSingleObject(self.event, 1000)
                return None
            if not kernel32.GetOverlappedResult(
                self.handle, ctypes.byref(ov), ctypes.byref(count), False
            ):
                error = ctypes.get_last_error()
                if error in (ERROR_SEM_TIMEOUT, 995):
                    return None
                raise ctypes.WinError(error)
        size = int(count.value)
        if size <= 0:
            return None
        return bytes(buf[:size])

    def drain(self, max_reports: int = 32, timeout_ms: int = 1) -> int:
        drained = 0
        for _ in range(max_reports):
            if self.read(timeout_ms) is None:
                break
            drained += 1
        return drained

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self.handle not in (None, INVALID_HANDLE_VALUE):
            kernel32.CancelIoEx(self.handle, None)
            kernel32.CloseHandle(self.handle)
            self.handle = INVALID_HANDLE_VALUE
        if self.event:
            kernel32.CloseHandle(self.event)
            self.event = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()


@dataclass
class ExchangeResult:
    reports: list[bytes] = field(default_factory=list)
    matched: Optional[bytes] = None
    nak: bool = False
    timed_out: bool = False
    written: bytes = b""

    @property
    def ok(self) -> bool:
        return self.matched is not None


class HidTransport:
    """Full-duplex vendor HID transport with isolated read/write handles."""

    def __init__(self, info: HidDeviceInfo, packet_size: Optional[int] = None,
                 debug=None):
        self.info = info
        self.packet_size = int(packet_size or info.output_len or 64)
        if self.packet_size < 3:
            self.packet_size = 64
        self.debug = debug or (lambda message: None)
        self.write_handle = INVALID_HANDLE_VALUE
        self.reader: Optional[OverlappedHidReader] = None
        try:
            # The write handle is intentionally never used for ReadFile.
            self.write_handle = _open_handle(info.path, GENERIC_WRITE, False)
            self.reader = OverlappedHidReader(
                info.path, max(info.input_len or self.packet_size, 1)
            )
        except Exception:
            self.close()
            raise

    def _write(self, packet: bytes) -> None:
        if not packet:
            raise ValueError("empty HID packet")
        buf = (ctypes.c_ubyte * len(packet)).from_buffer_copy(packet)
        count = w.DWORD(0)
        if not kernel32.WriteFile(
            self.write_handle, buf, len(packet), ctypes.byref(count), None
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        if count.value != len(packet):
            raise OSError(0, f"short HID write ({count.value}/{len(packet)})")

    def send(
        self,
        payload: Sequence[int],
        timeout_ms: int = 800,
        expected_prefix: Optional[bytes] = None,
        drain: bool = True,
        collect_after_match_ms: int = 0,
    ) -> ExchangeResult:
        packet = bytes(payload)
        if len(packet) < self.packet_size:
            packet = packet + b"\x00" * (self.packet_size - len(packet))
        elif len(packet) > self.packet_size:
            raise ValueError(
                f"packet is {len(packet)} bytes, interface max is {self.packet_size}"
            )
        result = ExchangeResult(written=packet)
        if drain and self.reader:
            self.reader.drain()
        self.debug(
            f"HID OUT {self.info.vid_pid} {self.info.short_location}: "
            f"{packet[:24].hex(' ')}"
        )
        self._write(packet)
        if not self.reader:
            return result
        deadline = time.monotonic() + max(1, timeout_ms) / 1000.0
        matched_at: Optional[float] = None
        while True:
            now = time.monotonic()
            if matched_at is not None:
                if now - matched_at >= collect_after_match_ms / 1000.0:
                    break
            if matched_at is None and now >= deadline:
                result.timed_out = True
                break
            remaining = deadline - now
            wait_ms = max(1, int(remaining * 1000))
            if matched_at is not None:
                wait_ms = max(1, collect_after_match_ms)
            report = self.reader.read(wait_ms)
            if report is None:
                if matched_at is None:
                    result.timed_out = True
                break
            result.reports.append(report)
            self.debug(
                f"HID IN  {self.info.vid_pid} {self.info.short_location}: "
                f"{report[:24].hex(' ')}"
            )
            if len(report) >= 3 and report[1] == 0xFF and report[2] == 0xAA:
                result.nak = True
                if expected_prefix is None or report[:len(expected_prefix)] != expected_prefix:
                    if matched_at is None:
                        break
            if expected_prefix and report.startswith(expected_prefix):
                result.matched = report
                if collect_after_match_ms <= 0:
                    break
                matched_at = time.monotonic()
            if expected_prefix is None:
                # Raw debug send: collect whatever arrives until timeout.
                continue
        return result

    def close(self) -> None:
        if self.reader:
            try:
                self.reader.close()
            except Exception:
                pass
            self.reader = None
        if self.write_handle not in (None, INVALID_HANDLE_VALUE):
            kernel32.CloseHandle(self.write_handle)
            self.write_handle = INVALID_HANDLE_VALUE

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()


# ---------------------------------------------------------------------------
# Feature Reports
# ---------------------------------------------------------------------------

def get_feature_report(info: HidDeviceInfo, report_id: int,
                       length: Optional[int] = None,
                       access: int = 0) -> Optional[bytes]:
    if not info.feature_len and not length:
        return None
    size = int(length or info.feature_len)
    if size <= 0:
        return None
    with open_hid(info.path, access) as handle:
        buf = (ctypes.c_ubyte * size)()
        buf[0] = report_id & 0xFF
        if not hid.HidD_GetFeature(handle, buf, size):
            error = ctypes.get_last_error()
            if error:
                raise OSError(error, ctypes.FormatError(error))
            return None
        return bytes(buf)


def probe_feature_reports(info: HidDeviceInfo, report_ids: Iterable[int] = range(256)) -> dict[int, bytes]:
    if not info.feature_len:
        return {}
    found: dict[int, bytes] = {}
    with open_hid(info.path, 0) as handle:
        size = int(info.feature_len)
        for report_id in report_ids:
            if report_id < 0 or report_id > 255:
                continue
            buf = (ctypes.c_ubyte * size)()
            buf[0] = report_id
            if hid.HidD_GetFeature(handle, buf, size):
                found[report_id] = bytes(buf)
    return found


# ---------------------------------------------------------------------------
# Standard HID battery (Generic Device Controls 0x0006 / 0x0020)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ValueField:
    report_type: int
    report_id: int
    link_collection: int
    bit_size: int
    logical_min: int
    logical_max: int
    usage_page: int
    usage: int


def _value_caps(preparsed: ctypes.c_void_p, report_type: int) -> list[HIDP_VALUE_CAPS]:
    length = w.USHORT(0)
    hid.HidP_GetValueCaps(
        report_type, ctypes.POINTER(HIDP_VALUE_CAPS)(), ctypes.byref(length),
        preparsed,
    )
    if length.value == 0:
        return []
    caps = (HIDP_VALUE_CAPS * length.value)()
    status = hid.HidP_GetValueCaps(
        report_type, caps, ctypes.byref(length), preparsed
    )
    if status not in (0, HIDP_STATUS_SUCCESS, HIDP_STATUS_BUFFER_TOO_SMALL):
        return []
    return list(caps[:length.value])


def _caps_has_battery(cap: HIDP_VALUE_CAPS) -> bool:
    if int(cap.UsagePage) != 0x0006:
        return False
    if cap.IsRange:
        return int(cap.UsageMin) <= 0x0020 <= int(cap.UsageMax)
    return int(cap.Usage) == 0x0020


def find_battery_fields(info: HidDeviceInfo) -> list[ValueField]:
    fields: list[ValueField] = []
    try:
        with open_hid(info.path, 0) as handle:
            pdata = ctypes.c_void_p()
            if not hid.HidD_GetPreparsedData(handle, ctypes.byref(pdata)):
                return fields
            try:
                for report_type in (HIDP_REPORT_TYPE_FEATURE, HIDP_REPORT_TYPE_INPUT):
                    for cap in _value_caps(pdata, report_type):
                        if not _caps_has_battery(cap):
                            continue
                        fields.append(ValueField(
                            report_type=report_type,
                            report_id=int(cap.ReportID),
                            link_collection=int(cap.LinkCollection),
                            bit_size=int(cap.BitSize),
                            logical_min=int(cap.LogicalMin),
                            logical_max=int(cap.LogicalMax),
                            usage_page=int(cap.UsagePage),
                            usage=0x0020,
                        ))
            finally:
                hid.HidD_FreePreparsedData(pdata)
    except OSError:
        pass
    return fields


def _usage_value(preparsed, field: ValueField, report: bytes) -> Optional[int]:
    value = w.ULONG(0)
    buf = (ctypes.c_ubyte * len(report)).from_buffer_copy(report)
    status = hid.HidP_GetUsageValue(
        field.report_type,
        field.usage_page,
        field.link_collection,
        field.usage,
        ctypes.byref(value),
        preparsed,
        buf,
        len(report),
    )
    if status not in (0, HIDP_STATUS_SUCCESS):
        return None
    return int(value.value)


def read_standard_battery(info: HidDeviceInfo, input_timeout_ms: int = 1200) -> Optional[int]:
    """Try the standard HID battery usage in Feature, then Input reports."""
    fields = find_battery_fields(info)
    if not fields:
        return None
    feature_fields = [field for field in fields if field.report_type == HIDP_REPORT_TYPE_FEATURE]
    input_fields = [field for field in fields if field.report_type == HIDP_REPORT_TYPE_INPUT]

    try:
        with open_hid(info.path, 0) as handle:
            pdata = ctypes.c_void_p()
            if not hid.HidD_GetPreparsedData(handle, ctypes.byref(pdata)):
                return None
            try:
                for field in feature_fields:
                    if not info.feature_len:
                        continue
                    buf = (ctypes.c_ubyte * info.feature_len)()
                    buf[0] = field.report_id
                    if not hid.HidD_GetFeature(handle, buf, info.feature_len):
                        continue
                    value = _usage_value(pdata, field, bytes(buf))
                    if value is not None and 0 <= value <= 100:
                        return value
                if input_fields:
                    # A separate overlapped handle is used even here.  The
                    # metadata handle above is never used for ReadFile.
                    with OverlappedHidReader(
                        info.path, max(1, info.input_len or 64)
                    ) as reader:
                        deadline = time.monotonic() + input_timeout_ms / 1000.0
                        while time.monotonic() < deadline:
                            report = reader.read(
                                max(1, int((deadline - time.monotonic()) * 1000))
                            )
                            if not report:
                                break
                            for field in input_fields:
                                if field.report_id and (not report or report[0] != field.report_id):
                                    continue
                                value = _usage_value(pdata, field, report)
                                if value is not None and 0 <= value <= 100:
                                    return value
            finally:
                hid.HidD_FreePreparsedData(pdata)
    except OSError:
        return None
    return None


def listen_input(info: HidDeviceInfo, seconds: float = 2.0,
                 max_reports: int = 200) -> list[bytes]:
    """Listen on an independent overlapped read handle for a few seconds."""
    reports: list[bytes] = []
    deadline = time.monotonic() + max(0.1, seconds)
    with OverlappedHidReader(
        info.path, max(1, info.input_len or 64)
    ) as reader:
        while len(reports) < max_reports and time.monotonic() < deadline:
            report = reader.read(
                max(1, int((deadline - time.monotonic()) * 1000))
            )
            if not report:
                break
            reports.append(report)
    return reports


def send_raw_report(info: HidDeviceInfo, report_id: int, payload: bytes,
                    timeout_ms: int = 1000) -> ExchangeResult:
    packet_size = max(3, int(info.output_len or 64))
    packet = bytes([report_id & 0xFF]) + bytes(payload)
    if len(packet) > packet_size:
        raise ValueError(f"命令过长：{len(packet)} > {packet_size}")
    with HidTransport(info, packet_size=packet_size) as transport:
        return transport.send(
            packet, timeout_ms=timeout_ms, expected_prefix=None,
            collect_after_match_ms=0,
        )


def hex_bytes(data: bytes, limit: int = 0) -> str:
    if limit and len(data) > limit:
        data = data[:limit]
    return data.hex(" ").upper()


def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False
