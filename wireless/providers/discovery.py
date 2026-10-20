"""通用设备发现与读取调度。

品牌目录只负责发现和解析自己的设备；这里统一处理：
- 品牌优先级与接口去重；
- 标准 HID 电量字段；
- Input Report 兜底；
- 手动标定；
- 读取优先级和离线结果。
"""

from __future__ import annotations

import traceback
from typing import Optional

from ..models import DeviceReading, DeviceTarget, ManualCalibration
from ..winhid import (
    HidDeviceInfo,
    enumerate_hid_devices,
    listen_input,
)
from . import manual, standard
from .common import offline
from .registry import BRAND_PROVIDERS, get_brand


def discover_targets(
    devices: Optional[list[HidDeviceInfo]] = None,
    calibrations: Optional[list[ManualCalibration]] = None,
    debug=None,
) -> list[DeviceTarget]:
    """发现所有已连接设备，返回按品牌、标准 HID、手动标定排序的目标。"""
    devices = devices if devices is not None else enumerate_hid_devices()
    calibrations = calibrations or []
    targets: list[DeviceTarget] = []
    used_interfaces: set[str] = set()

    # 1. 品牌提供者。当前注册 ASUS、Logitech 和 Razer。
    for brand in BRAND_PROVIDERS:
        try:
            found = brand.discover(devices, debug=debug)
        except Exception:
            if debug:
                debug(f"{brand.label} discovery failed: " + traceback.format_exc(limit=2))
            continue
        for target in found:
            if target.info.interface_key in used_interfaces:
                continue
            used_interfaces.add(target.info.interface_key)
            targets.append(target)

    # 2. 标准 HID Battery Strength。
    for target in standard.discover(devices, used_interfaces, debug=debug):
        if target.info.interface_key in used_interfaces:
            continue
        used_interfaces.add(target.info.interface_key)
        targets.append(target)

    # 3. 手动标定（最低优先级）。
    for target in manual.discover(
        devices, calibrations, used_interfaces, debug=debug
    ):
        if target.info.interface_key in used_interfaces:
            continue
        used_interfaces.add(target.info.interface_key)
        targets.append(target)

    targets.sort(key=lambda item: (
        {"keyboard": 0, "mouse": 1, "headset": 2, "other": 3}.get(item.kind, 9),
        item.name.lower(),
    ))
    return targets


def read_target(
    target: DeviceTarget,
    debug=None,
    timeout_ms: int = 800,
) -> DeviceReading:
    """对单个目标依次执行提供者优先级链。"""
    info = target.info

    # 1. 标准 HID 电量字段。
    reading = standard.try_read(target, debug=debug)
    if reading is not None:
        return reading

    # 2. 品牌协议。目标由哪个品牌发现，就交给哪个品牌读取。
    brand = get_brand(target.provider)
    if brand is not None:
        result = brand.read(target, debug=debug, timeout_ms=timeout_ms)
        if result is not None:
            return result

    # 3. Input Report 兜底。标准电量字段已在上方尝试过；对于未知协议，
    #    这里短暂监听，并允许手动标定页面把发现的报文模式保存为提供者。
    if target.provider == "input" and info.input_len:
        try:
            reports = listen_input(info, seconds=1.4, max_reports=64)
            if debug and reports:
                debug(f"Input reports from {target.name}: {len(reports)}")
        except Exception:
            pass

    # 4. 手动标定。
    if target.manual is not None:
        result = manual.read(target, debug=debug, timeout_ms=timeout_ms)
        if result is not None:
            return result

    return offline(target, "没有可用的电量协议")
