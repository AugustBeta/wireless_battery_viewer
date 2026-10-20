"""Logitech HID++ 2.0 品牌提供者。

当前版本不自动发现罗技设备，只提供读取适配器；这样可以避免把接收器里的
每个 HID 集合都误报成独立设备。以后需要正式支持罗技时，在 ``discover``
中根据产品线和接口筛选目标即可。
"""

from __future__ import annotations

from typing import Optional

from ...models import DeviceReading, DeviceTarget
from ...winhid import HidTransport
from ..common import validate_percent


PROVIDER_KEY = "logitech"

HIDPP_FEATURE_BATTERY_VOLTAGE = 0x1000
HIDPP_FEATURE_BATTERY_LEVEL = 0x1001
HIDPP_FEATURE_UNIFIED_BATTERY = 0x1004


def discover(devices, debug=None) -> list[DeviceTarget]:
    """保留的罗技发现入口；当前不自动创建设备。"""
    return []


def _hidpp_request(
    transport: HidTransport,
    device_index: int,
    feature_index: int,
    function: int,
    params: bytes = b"",
    timeout_ms: int = 700,
) -> Optional[bytes]:
    payload = bytes([
        0x11,
        device_index & 0xFF,
        feature_index & 0xFF,
        ((function & 0x0F) << 4) & 0xFF,
    ]) + params
    expected = bytes([0x11, device_index & 0xFF, feature_index & 0xFF])
    result = transport.send(payload, timeout_ms=timeout_ms, expected_prefix=expected)
    return result.matched


def _hidpp_get_feature(
    transport: HidTransport,
    device_index: int,
    feature_id: int,
) -> Optional[int]:
    response = _hidpp_request(
        transport,
        device_index,
        0x00,
        0x00,
        bytes([(feature_id >> 8) & 0xFF, feature_id & 0xFF]),
    )
    if not response or len(response) < 3 or response[2] == 0:
        return None
    return int(response[2])


def read(
    target: DeviceTarget,
    debug=None,
    timeout_ms: int = 800,
) -> Optional[DeviceReading]:
    """尝试通过 HID++ 2.0 读取罗技设备电量。"""
    info = target.info
    packet_size = max(7, int(info.output_len or 20))
    if packet_size < 7:
        return None
    try:
        with HidTransport(info, packet_size=packet_size, debug=debug) as transport:
            # 0xFF 用于寻址接收器；0x01..0x06 用于寻址已配对设备。
            for device_index in (0xFF, 0x01, 0x02, 0x03, 0x04, 0x05, 0x06):
                feature = _hidpp_get_feature(
                    transport, device_index, HIDPP_FEATURE_BATTERY_LEVEL
                )
                if feature:
                    response = _hidpp_request(
                        transport, device_index, feature, 0x00
                    )
                    if response and len(response) >= 5:
                        percent = validate_percent(response[4])
                        if percent is not None:
                            charging = None
                            if len(response) >= 7:
                                charging = response[6] in (1, 2)
                            return DeviceReading(
                                target=target,
                                percent=percent,
                                charging=charging,
                                source="Logitech HID++ 2.0",
                                status="ok",
                                detail=f"feature 0x1001, device 0x{device_index:02X}",
                            )

                feature = _hidpp_get_feature(
                    transport, device_index, HIDPP_FEATURE_UNIFIED_BATTERY
                )
                if feature:
                    response = _hidpp_request(
                        transport, device_index, feature, 0x01
                    )
                    if response and len(response) >= 5:
                        percent = validate_percent(response[4])
                        if percent is not None:
                            return DeviceReading(
                                target=target,
                                percent=percent,
                                source="Logitech HID++ 2.0",
                                status="ok",
                                detail=f"feature 0x1004, device 0x{device_index:02X}",
                            )

                feature = _hidpp_get_feature(
                    transport, device_index, HIDPP_FEATURE_BATTERY_VOLTAGE
                )
                if feature:
                    response = _hidpp_request(
                        transport, device_index, feature, 0x00
                    )
                    if response and len(response) >= 6:
                        millivolts = response[4] | (response[5] << 8)
                        if 2500 <= millivolts <= 5000:
                            percent = validate_percent(
                                (millivolts - 3200) / (4200 - 3200) * 100
                            )
                            if percent is not None:
                                return DeviceReading(
                                    target=target,
                                    percent=percent,
                                    charging=None,
                                    source="Logitech HID++ 2.0（电压估算）",
                                    status="ok",
                                    detail=f"{millivolts} mV, feature 0x1000",
                                )
    except Exception as exc:
        if debug:
            debug(f"HID++ failed for {target.name}: {exc}")
    return None
