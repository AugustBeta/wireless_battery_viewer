"""Notification-area icons with the battery percentage rendered as digits.

We build a 32-bit top-down DIB and an AND mask, then turn it into an HICON via
CreateIconIndirect.  This keeps the dependency list at ctypes only and makes
the number sharp at the current DPI (SetProcessDPIAware is called by main).
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes as w
from dataclasses import dataclass

gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
user32 = ctypes.WinDLL("user32", use_last_error=True)

DIB_RGB_COLORS = 0
BI_RGB = 0
SM_CXSMICON = 49
SM_CYSMICON = 50
DEFAULT_ICON_SIZE = 32

# 3x5 pixel font.  Digits are deliberately blocky so the number remains
# readable at 16-32 physical pixels.
FONT = {
    "0": ("111", "101", "101", "101", "111"),
    "1": ("010", "110", "010", "010", "111"),
    "2": ("111", "001", "111", "100", "111"),
    "3": ("111", "001", "111", "001", "111"),
    "4": ("101", "101", "111", "001", "001"),
    "5": ("111", "100", "111", "001", "111"),
    "6": ("111", "100", "111", "101", "111"),
    "7": ("111", "001", "010", "010", "010"),
    "8": ("111", "101", "111", "101", "111"),
    "9": ("111", "101", "111", "001", "111"),
    "-": ("000", "000", "111", "000", "000"),
    "?": ("111", "001", "011", "000", "010"),
    "+": ("000", "010", "111", "010", "000"),
}


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", w.DWORD),
        ("biWidth", w.LONG),
        ("biHeight", w.LONG),
        ("biPlanes", w.WORD),
        ("biBitCount", w.WORD),
        ("biCompression", w.DWORD),
        ("biSizeImage", w.DWORD),
        ("biXPelsPerMeter", w.LONG),
        ("biYPelsPerMeter", w.LONG),
        ("biClrUsed", w.DWORD),
        ("biClrImportant", w.DWORD),
    ]


class RGBQUAD(ctypes.Structure):
    _fields_ = [
        ("rgbBlue", ctypes.c_ubyte),
        ("rgbGreen", ctypes.c_ubyte),
        ("rgbRed", ctypes.c_ubyte),
        ("rgbReserved", ctypes.c_ubyte),
    ]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", RGBQUAD * 1)]


class ICONINFO(ctypes.Structure):
    _fields_ = [
        ("fIcon", w.BOOL),
        ("xHotspot", w.DWORD),
        ("yHotspot", w.DWORD),
        ("hbmMask", w.HANDLE),
        ("hbmColor", w.HANDLE),
    ]


gdi32.CreateCompatibleDC.restype = w.HDC
gdi32.CreateCompatibleDC.argtypes = [w.HDC]
gdi32.DeleteDC.argtypes = [w.HDC]
gdi32.CreateDIBSection.restype = w.HANDLE
gdi32.CreateDIBSection.argtypes = [
    w.HDC, ctypes.POINTER(BITMAPINFO), w.UINT, ctypes.POINTER(ctypes.c_void_p),
    w.HANDLE, w.DWORD,
]
gdi32.CreateBitmap.restype = w.HANDLE
gdi32.CreateBitmap.argtypes = [
    ctypes.c_int, ctypes.c_int, w.UINT, w.UINT, ctypes.c_void_p
]
gdi32.DeleteObject.argtypes = [w.HANDLE]
user32.CreateIconIndirect.restype = w.HANDLE
user32.CreateIconIndirect.argtypes = [ctypes.POINTER(ICONINFO)]
user32.DestroyIcon.argtypes = [w.HANDLE]
user32.GetSystemMetrics.argtypes = [ctypes.c_int]


@dataclass
class IconKey:
    text: str
    color: tuple[int, int, int]
    size: int


LEVEL_GREEN = (22, 163, 74)
LEVEL_YELLOW = (217, 154, 0)
LEVEL_RED = (220, 38, 38)
LEVEL_GRAY = (120, 128, 140)


def level_color(percent: int | None, status: str = "ok") -> tuple[int, int, int]:
    if percent is None or status != "ok":
        return LEVEL_GRAY
    if percent >= 60:
        return LEVEL_GREEN
    if percent >= 30:
        return LEVEL_YELLOW
    return LEVEL_RED


def _alpha_round_rect(px: bytearray, size: int, radius: int, color: tuple[int, int, int]) -> None:
    red, green, blue = color
    radius = max(1, radius)
    for y in range(size):
        for x in range(size):
            # Rounded rectangle coverage with a tiny 1px transparent border.
            left = radius
            right = size - radius - 1
            top = radius
            bottom = size - radius - 1
            inside = True
            if x < left and y < top:
                inside = (left - x) ** 2 + (top - y) ** 2 <= radius ** 2
            elif x > right and y < top:
                inside = (x - right) ** 2 + (top - y) ** 2 <= radius ** 2
            elif x < left and y > bottom:
                inside = (left - x) ** 2 + (y - bottom) ** 2 <= radius ** 2
            elif x > right and y > bottom:
                inside = (x - right) ** 2 + (y - bottom) ** 2 <= radius ** 2
            if not inside:
                continue
            offset = (y * size + x) * 4
            # BGRA, top-down.
            px[offset] = blue
            px[offset + 1] = green
            px[offset + 2] = red
            px[offset + 3] = 255


def _clear_pixel(px: bytearray, size: int, x: int, y: int) -> None:
    if 0 <= x < size and 0 <= y < size:
        offset = (y * size + x) * 4
        px[offset] = 0
        px[offset + 1] = 0
        px[offset + 2] = 0
        px[offset + 3] = 0


def _draw_text(px: bytearray, size: int, text: str) -> None:
    glyphs = [FONT[ch] for ch in text if ch in FONT]
    if not glyphs:
        return
    count = len(glyphs)
    scale = 1
    while True:
        width = count * 3 * scale + (count - 1) * scale
        height = 5 * scale
        if width > size - 2 or height > size - 4:
            break
        scale += 1
    scale = max(1, scale - 1)
    width = count * 3 * scale + (count - 1) * scale
    height = 5 * scale
    start_x = (size - width) // 2
    start_y = (size - height) // 2
    # A 1-pixel dark shadow keeps white digits readable on the yellow chip.
    shadow = (start_x + max(1, scale // 2), start_y + max(1, scale // 2))
    for pass_index, (ox, oy, red, green, blue) in enumerate((
        (shadow[0], shadow[1], 0, 0, 0),
        (start_x, start_y, 255, 255, 255),
    )):
        for glyph_index, glyph in enumerate(glyphs):
            base_x = ox + glyph_index * (3 * scale + scale)
            for row, line in enumerate(glyph):
                for col, bit in enumerate(line):
                    if bit != "1":
                        continue
                    for dy in range(scale):
                        for dx in range(scale):
                            x = base_x + col * scale + dx
                            y = oy + row * scale + dy
                            if not (0 <= x < size and 0 <= y < size):
                                continue
                            offset = (y * size + x) * 4
                            if pass_index == 1:
                                if px[offset + 3] == 0:
                                    continue
                                px[offset] = blue
                                px[offset + 1] = green
                                px[offset + 2] = red
                                px[offset + 3] = 255
                            else:
                                # Shadow only on colored background and only
                                # where a digit would go.
                                if px[offset + 3] == 0:
                                    continue
                                px[offset] = 0
                                px[offset + 1] = 0
                                px[offset + 2] = 0


def render_pixels(text: str, color: tuple[int, int, int], size: int = 32) -> tuple[bytearray, int]:
    size = max(16, min(64, int(size)))
    px = bytearray(size * size * 4)
    _alpha_round_rect(px, size, max(2, size // 5), color)
    _draw_text(px, size, text)
    return px, size


def _make_hicon(text: str, color: tuple[int, int, int], size: int) -> int:
    px, size = render_pixels(text, color, size)

    bmi = BITMAPINFO()
    bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bmi.bmiHeader.biWidth = size
    bmi.bmiHeader.biHeight = -size  # top-down
    bmi.bmiHeader.biPlanes = 1
    bmi.bmiHeader.biBitCount = 32
    bmi.bmiHeader.biCompression = BI_RGB
    bits = ctypes.c_void_p()
    dc = gdi32.CreateCompatibleDC(None)
    if not dc:
        raise ctypes.WinError(ctypes.get_last_error())
    colour = mask = None
    try:
        colour = gdi32.CreateDIBSection(
            dc, ctypes.byref(bmi), DIB_RGB_COLORS,
            ctypes.byref(bits), None, 0,
        )
        if not colour or not bits:
            raise ctypes.WinError(ctypes.get_last_error())
        ctypes.memmove(bits, bytes(px), len(px))

        # AND mask: 1 = transparent.  Rows are padded to 16 bits.
        stride = ((size + 15) // 16) * 2
        mask_data = bytearray(stride * size)
        for y in range(size):
            for x in range(size):
                alpha = px[(y * size + x) * 4 + 3]
                if alpha == 0:
                    byte_index = y * stride + (x // 8)
                    mask_data[byte_index] |= 0x80 >> (x % 8)
        mask_buffer = (ctypes.c_ubyte * len(mask_data)).from_buffer_copy(mask_data)
        mask = gdi32.CreateBitmap(size, size, 1, 1, mask_buffer)
        if not mask:
            raise ctypes.WinError(ctypes.get_last_error())

        info = ICONINFO()
        info.fIcon = True
        info.hbmMask = mask
        info.hbmColor = colour
        icon = user32.CreateIconIndirect(ctypes.byref(info))
        if not icon:
            raise ctypes.WinError(ctypes.get_last_error())
        return int(icon)
    finally:
        if mask:
            gdi32.DeleteObject(mask)
        if colour:
            gdi32.DeleteObject(colour)
        if dc:
            gdi32.DeleteDC(dc)


class TrayIconFactory:
    def __init__(self) -> None:
        self._cache: dict[tuple[str, tuple[int, int, int], int], int] = {}

    @staticmethod
    def current_size() -> int:
        size = int(user32.GetSystemMetrics(SM_CXSMICON) or 0)
        if size < 16:
            size = DEFAULT_ICON_SIZE
        return max(16, min(64, size))

    def get(self, percent: int | None, status: str = "ok") -> int:
        text = "--" if percent is None or status != "ok" else str(int(percent))
        color = level_color(percent, status)
        size = self.current_size()
        key = (text, color, size)
        icon = self._cache.get(key)
        if icon:
            return icon
        icon = _make_hicon(text, color, size)
        self._cache[key] = icon
        return icon

    def clear(self) -> None:
        for icon in self._cache.values():
            try:
                user32.DestroyIcon(icon)
            except Exception:
                pass
        self._cache.clear()
