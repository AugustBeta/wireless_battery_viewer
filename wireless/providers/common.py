"""各品牌提供者共用的辅助函数。"""

from __future__ import annotations

from typing import Iterable, Optional

from ..models import DeviceReading, DeviceTarget
from ..winhid import HidDeviceInfo


def debug_log(debug, message: str) -> None:
    """在启用调试回调时输出一条消息。"""
    if debug:
        debug(message)


def first_device(
    devices: Iterable[HidDeviceInfo],
    predicate,
) -> Optional[HidDeviceInfo]:
    """返回第一个满足条件的 HID 接口。"""
    for info in devices:
        try:
            if predicate(info):
                return info
        except Exception:
            continue
    return None


def offline(target: DeviceTarget, message: str, source: str = "") -> DeviceReading:
    """构造“未连接”状态的读取结果。"""
    return DeviceReading(
        target=target,
        percent=None,
        source=source or target.source_hint,
        status="offline",
        message=message,
    )


def error(target: DeviceTarget, message: str, source: str = "") -> DeviceReading:
    """构造“读取失败”状态的读取结果。"""
    return DeviceReading(
        target=target,
        percent=None,
        source=source or target.source_hint,
        status="error",
        message=message,
    )


def validate_percent(value: float) -> Optional[int]:
    """把任意数值转换为 0–100 的整数百分比。"""
    try:
        number = int(round(float(value)))
    except (TypeError, ValueError):
        return None
    return max(0, min(100, number))
