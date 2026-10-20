"""Logitech 品牌提供者。

G PRO 系列无线耳机不走 HID++ 2.0 电量特性，而是在 ``0xFF43/0x0202``
接口上用 20 字节长报告查询电池电压。这里负责发现这类接收器并把电压换算
成百分比；HID++ 2.0 适配器保留给以后支持罗技鼠标和键盘。
"""

from __future__ import annotations

from typing import Optional

from ...models import DeviceReading, DeviceTarget
from ...winhid import HidTransport
from ..common import debug_log, error, offline, validate_percent


PROVIDER_KEY = "logitech"

# 这些接收器使用同一套 G PRO 电压查询；0x0AF7（PRO X 2）的帧格式不同，
# 不能直接套用。
GPRO_PRODUCT_IDS = frozenset((0x0AA7, 0x0AAA, 0x0ABA))
GPRO_USAGE_PAGE = 0xFF43
GPRO_USAGE = 0x0202
GPRO_BATTERY_COMMAND = bytes((0x11, 0xFF, 0x06, 0x0D))
GPRO_BATTERY_SOURCE = "Logitech G PRO 厂商协议"

# HeadsetControl 对 G PRO 系列的电压拟合系数（从常数项到五次项）。
GPRO_BATTERY_TERMS = (
    -1.7790085824253613e006,
    2.3692153307344888e003,
    -1.2580905041506840,
    3.3295201187388395e-004,
    -4.3913981733597497e-008,
    2.3093121045539907e-012,
)

HIDPP_FEATURE_BATTERY_VOLTAGE = 0x1000
HIDPP_FEATURE_BATTERY_LEVEL = 0x1001
HIDPP_FEATURE_UNIFIED_BATTERY = 0x1004


def discover(devices, debug=None) -> list[DeviceTarget]:
    """发现 G PRO 系列接收器的 HID++ 长报告接口。"""
    targets: list[DeviceTarget] = []
    for info in devices:
        if info.vendor_id != 0x046D or info.product_id not in GPRO_PRODUCT_IDS:
            continue
        if info.usage_page != GPRO_USAGE_PAGE or info.usage != GPRO_USAGE:
            continue
        name = info.product or "Logitech G PRO 无线耳机"
        targets.append(DeviceTarget(
            key=f"logitech-gpro-{info.product_id:04x}",
            name=name,
            kind="headset",
            info=info,
            provider=PROVIDER_KEY,
            source_hint=GPRO_BATTERY_SOURCE,
        ))
    return targets


def _voltage_percent(millivolts: int) -> Optional[int]:
    """按 G PRO 系列的放电曲线把毫伏换算成百分比。"""
    if not 2500 <= millivolts <= 5000:
        return None
    value = sum(
        term * (millivolts ** power)
        for power, term in enumerate(GPRO_BATTERY_TERMS)
    )
    return validate_percent(value)


def _read_gpro(
    target: DeviceTarget,
    debug=None,
    timeout_ms: int = 800,
) -> Optional[DeviceReading]:
    """读取 G PRO 耳机的电池电压帧。"""
    info = target.info
    packet_size = max(7, int(info.output_len or 20))
    try:
        with HidTransport(info, packet_size=packet_size, debug=debug) as transport:
            result = transport.send(
                GPRO_BATTERY_COMMAND,
                timeout_ms=timeout_ms,
                expected_prefix=GPRO_BATTERY_COMMAND,
            )
            response = result.matched
            if not response:
                debug_log(debug, f"G PRO battery query returned no frame: {target.name}")
                return offline(
                    target,
                    "耳机未连接或已休眠",
                    GPRO_BATTERY_SOURCE,
                )
            if len(response) < 7:
                debug_log(debug, f"G PRO battery frame too short: {response.hex(' ')}")
                return error(target, "电量响应长度异常", GPRO_BATTERY_SOURCE)
            millivolts = (response[4] << 8) | response[5]
            percent = _voltage_percent(millivolts)
            if percent is None:
                debug_log(debug, f"G PRO battery voltage out of range: {millivolts} mV")
                return error(target, f"电压超出范围：{millivolts} mV", GPRO_BATTERY_SOURCE)
            return DeviceReading(
                target=target,
                percent=percent,
                charging=response[6] == 0x03,
                source=GPRO_BATTERY_SOURCE,
                status="ok",
                detail=f"{millivolts} mV",
            )
    except Exception as exc:
        debug_log(debug, f"G PRO battery query failed for {target.name}: {exc}")
        return None


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
    # getFeature 的返回帧是 [11, 设备, 0x00, 功能, 特性索引, ...]；
    # 特性索引在第 5 字节，而不是回显的特性索引 0x00。
    if not response or len(response) < 5 or response[2] == 0xFF:
        return None
    return int(response[4]) or None


def read(
    target: DeviceTarget,
    debug=None,
    timeout_ms: int = 800,
) -> Optional[DeviceReading]:
    """优先读取 G PRO 耳机电压；其它罗技设备走 HID++ 2.0。"""
    if (
        target.info.product_id in GPRO_PRODUCT_IDS
        and target.info.usage_page == GPRO_USAGE_PAGE
    ):
        return _read_gpro(target, debug=debug, timeout_ms=timeout_ms)

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
