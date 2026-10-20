"""标准 HID 电量字段提供者。

对应 Generic Device Controls usage page 0x0006 / usage 0x0020。
这是所有目标的第一优先级读取方式。
"""

from __future__ import annotations

from typing import Optional

from ..models import DeviceReading, DeviceTarget
from ..winhid import HidDeviceInfo, find_battery_fields, read_standard_battery


PROVIDER_KEY = "standard"
SOURCE = "标准 HID 电量字段"


def discover(
    devices: list[HidDeviceInfo],
    used_interfaces: set[str],
    debug=None,
) -> list[DeviceTarget]:
    """为尚未被品牌提供者占用的接口创建标准 HID 电量目标。"""
    targets: list[DeviceTarget] = []
    for info in devices:
        if info.interface_key in used_interfaces:
            continue
        if not info.accessible:
            continue
        try:
            fields = find_battery_fields(info)
        except Exception:
            fields = []
        if not fields:
            continue

        kind = "other"
        if info.usage_page == 0x0001 and info.usage in (0x0002, 0x0006):
            kind = "mouse" if info.usage == 0x0002 else "keyboard"
        targets.append(DeviceTarget(
            key="std-" + info.interface_key,
            name=info.product or f"HID 设备 {info.vid_pid}",
            kind=kind,
            info=info,
            provider=PROVIDER_KEY,
            paired=True,
            source_hint=SOURCE,
        ))
    return targets


def try_read(target: DeviceTarget, debug=None) -> Optional[DeviceReading]:
    """尝试读取标准 HID 电量字段；没有该字段时返回 None。"""
    info = target.info
    try:
        if not find_battery_fields(info):
            return None
        percent = read_standard_battery(info, input_timeout_ms=900)
    except Exception:
        return None
    if percent is None:
        return None
    return DeviceReading(
        target=target,
        percent=percent,
        charging=None,
        source=SOURCE,
        status="ok",
        detail="Generic Device Controls 0x0006 / 0x0020",
    )
