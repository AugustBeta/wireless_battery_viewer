"""手动标定提供者。

用户可以在完整调试窗口中把 Report ID、命令、电量字节下标和倍率保存到 JSON，
用于没有命中标准 HID 或品牌协议的接口。手动标定始终是最后优先级。
"""

from __future__ import annotations

from typing import Optional

from ..models import DeviceReading, DeviceTarget, ManualCalibration
from ..winhid import HidDeviceInfo, HidTransport
from .common import error, first_device, offline, validate_percent


PROVIDER_KEY = "manual"
SOURCE = "手动标定协议"


def discover(
    devices: list[HidDeviceInfo],
    calibrations: list[ManualCalibration],
    used_interfaces: set[str],
    debug=None,
) -> list[DeviceTarget]:
    """为匹配标定规则的剩余接口创建目标。"""
    targets: list[DeviceTarget] = []
    local_used = set(used_interfaces)
    for index, calibration in enumerate(calibrations):
        info = first_device(
            devices,
            lambda item, current=calibration: current.matches(item)
            and item.interface_key not in local_used,
        )
        if not info:
            continue
        targets.append(DeviceTarget(
            key=f"manual-{index}-{info.interface_key}",
            name=calibration.name,
            kind=calibration.kind,
            info=info,
            provider=PROVIDER_KEY,
            manual=calibration,
            paired=True,
            source_hint=SOURCE,
        ))
        local_used.add(info.interface_key)
    return targets


def read(
    target: DeviceTarget,
    debug=None,
    timeout_ms: int = 800,
) -> Optional[DeviceReading]:
    """按手动标定规则发送命令并解析电量。"""
    calibration = target.manual
    if calibration is None:
        return error(target, "缺少手动标定数据")
    if target.paired is False:
        return offline(target, "设备未连接")

    info = target.info
    packet_size = int(info.output_len or calibration.packet_size or 64)
    packet = bytes([calibration.report_id & 0xFF]) + bytes(calibration.command)
    if len(packet) > packet_size:
        return error(target, "标定命令超过接口数据包长度")
    expected = packet[:min(3, len(packet))]
    try:
        with HidTransport(info, packet_size=packet_size, debug=debug) as transport:
            result = transport.send(
                packet,
                timeout_ms=timeout_ms,
                expected_prefix=expected if len(expected) >= 2 else None,
            )
            if not result.matched:
                if result.nak:
                    return offline(target, "设备返回 NAK")
                return offline(target, "设备无响应")
            response = result.matched
    except Exception as exc:
        return error(target, f"通信失败：{exc}")

    index = calibration.battery_offset
    if index < 0 or index >= len(response):
        return error(target, "电量下标超出响应长度")
    percent = validate_percent(response[index] * calibration.scale)
    if percent is None:
        return error(target, "无法解析标定响应")
    charging = None
    if calibration.charging_offset is not None and calibration.charging_offset < len(response):
        charging = response[calibration.charging_offset] == calibration.charging_value
    return DeviceReading(
        target=target,
        percent=percent,
        charging=charging,
        source=SOURCE,
        status="ok",
        detail=f"report 0x{calibration.report_id:02X}, byte {index}, ×{calibration.scale:g}",
    )
