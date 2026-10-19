"""设备发现与电量协议提供者。

每个目标的读取优先级如下：

    标准 HID Battery Strength（0x0006/0x0020）
    厂商协议表（当前为 ASUS）
    Logitech HID++ 2.0（预留扩展适配器）
    Input Report 兜底
    手动标定 JSON

当前版本有意只自动发现 ASUS 设备，以及对外暴露标准 HID 电量字段的设备。
其他品牌可通过提供者注册表或调试窗口中的手动标定页面接入。
"""

from __future__ import annotations

import time
from typing import Iterable, Optional

from .models import (
    AsusSpec,
    DeviceReading,
    DeviceTarget,
    ManualCalibration,
)
from .winhid import (
    HidDeviceInfo,
    HidTransport,
    find_battery_fields,
    read_standard_battery,
    listen_input,
)


ASUS_VID = 0x0B05
LOGITECH_VID = 0x046D

# OMNI 配对列表中的接收器 PID。该表与 G-Helper 使用的表一致，
# 用于判断接收器实际配对了哪些设备。
OMNI_KEYBOARDS = {
    0x1A85: "ROG Azoth (OMNI)",
    0x1B42: "ROG Azoth Extreme (OMNI)",
    0x1CF1: "ROG Azoth Extreme SE (OMNI)",
    0x1AB0: "ROG Strix Scope II 96 (OMNI)",
    0x1B7A: "ROG Strix Scope II 96 RX (OMNI)",
    0x1B06: "ROG Falchion RX Low Profile (OMNI)",
}

OMNI_MICE = {
    0x1B65: "ROG Harpe Ace Mini (OMNI)",
    0x1C0E: "ROG Keris II Origin (OMNI)",
    0x1D4E: "ROG Keris II Origin KJP (OMNI)",
    0x1A94: "ROG Harpe Ace Aim Lab Edition (OMNI)",
    0x1AD7: "ROG Strix Impact III Wireless (OMNI)",
    0x1A72: "ROG Gladius III Aimpoint (OMNI)",
    0x1A68: "ROG Keris Wireless Aimpoint (OMNI)",
    0x1A6A: "ROG Keris Wireless Aimpoint (OMNI)",
    0x1B1A: "ROG Keris Ace II (OMNI)",
    0x1B18: "ROG Keris Ace II (OMNI)",
    0x1B68: "ROG Harpe Ace Extreme (OMNI)",
    0x1B69: "ROG Harpe Ace Extreme (OMNI)",
}

OMNI_KEYBOARD_COMMAND = (0x12, 0x01)
OMNI_MOUSE_COMMAND = (0x12, 0x07)

# 独立 ASUS 无线产品。只有匹配的 HID 接口实际存在时，才会使用对应定义。
ASUS_STANDALONE_SPECS: list[AsusSpec] = [
    AsusSpec(
        key="scope96rx-wireless",
        pid=0x1B78,
        name="ROG Strix Scope II 96 RX (Wireless)",
        kind="keyboard",
        report_id=0x00,
        command=(0x12, 0x01),
        battery_offset=6,
        charging_offset=9,
        path_contains="mi_01",
        packet_size=65,
        source="ASUS 键盘厂商协议",
    ),
    AsusSpec(
        key="scope96-wireless",
        pid=0x1AAE,
        name="ROG Strix Scope II 96 (Wireless)",
        kind="keyboard",
        report_id=0x00,
        command=(0x12, 0x01),
        battery_offset=6,
        charging_offset=9,
        path_contains="mi_01",
        packet_size=65,
        source="ASUS 键盘厂商协议",
    ),
    AsusSpec(
        key="delta2",
        pid=0x1AFA,
        name="ROG Delta II",
        kind="headset",
        report_id=0xCC,
        command=(0x12, 0x07),
        battery_offset=6,
        charging_offset=5,
        charging_command=(0x12, 0x08),
        path_contains="",
        usage_page=0xFF00,
        source="ASUS 耳机厂商协议",
    ),
    AsusSpec(
        key="pelta",
        pid=0x1B84,
        name="ROG Pelta",
        kind="headset",
        report_id=0xCC,
        command=(0x12, 0x07),
        battery_offset=6,
        charging_offset=5,
        charging_command=(0x12, 0x08),
        usage_page=0xFF00,
        source="ASUS 耳机厂商协议",
    ),
    AsusSpec(
        key="cetra-speednova",
        pid=0x1AD3,
        name="ROG Cetra SpeedNova",
        kind="headset",
        report_id=0xCC,
        command=(0x12, 0x07),
        battery_offset=6,
        charging_offset=5,
        charging_command=(0x12, 0x08),
        usage_page=0xFF00,
        source="ASUS 耳机厂商协议",
    ),
]

# Endgame / VAXEE / Nordic 适配入口。部分设备会暴露标准 HID 电量字段，
# 因此标准提供者会自动优先执行。这里的条目只是未来厂商命令的发现钩子，
# 不会单独创建设备卡片。
VENDOR_PROTOCOL_NAMES = {
    0x3367: "Endgame Gear",
    0x3057: "VAXEE",
    0x1915: "Nordic Semiconductor",
}


def _debug_log(debug, message: str) -> None:
    if debug:
        debug(message)


def _entry_pid(response: bytes, offset: int) -> Optional[int]:
    if offset + 1 >= len(response):
        return None
    pid = response[offset] | (response[offset + 1] << 8)
    return pid or None


def read_omni_pairings(info: HidDeviceInfo, debug=None) -> list[int]:
    """从 mi_02&col01 通道读取接收器的配对列表。

    OMNI 接收器会响应 ``01 A0 00 00``，并从偏移 5 开始返回 4 字节记录。
    查询失败不会中断流程：调用方仍会探测两个通道，并隐藏没有响应的通道。
    """
    pids: list[int] = []
    packet_size = max(5, int(info.output_len or 64))
    for attempt in range(2):
        try:
            with HidTransport(info, packet_size=packet_size, debug=debug) as transport:
                packet = bytes([0x01, 0xA0, 0x00, 0x00])
                result = transport.send(
                    packet,
                    timeout_ms=700,
                    expected_prefix=b"\x01\xA0\x00",
                )
                if result.matched:
                    response = result.matched
                    _debug_log(debug, "OMNI pair list: " + response[:24].hex(" ").upper())
                    for offset in range(5, min(len(response), 5 + 4 * 12), 4):
                        pid = _entry_pid(response, offset)
                        if pid:
                            pids.append(pid)
                    if pids:
                        return pids
        except Exception as exc:
            _debug_log(debug, f"OMNI pairing query failed: {exc}")
        if attempt == 0:
            time.sleep(0.12)
    return pids


def _first(devices: Iterable[HidDeviceInfo], predicate) -> Optional[HidDeviceInfo]:
    for info in devices:
        try:
            if predicate(info):
                return info
        except Exception:
            continue
    return None


def discover_targets(
    devices: Optional[list[HidDeviceInfo]] = None,
    calibrations: Optional[list[ManualCalibration]] = None,
    debug=None,
) -> list[DeviceTarget]:
    from .winhid import enumerate_hid_devices

    devices = devices if devices is not None else enumerate_hid_devices()
    calibrations = calibrations or []
    targets: list[DeviceTarget] = []
    used_interfaces: set[str] = set()

    # --- ASUS OMNI 接收器（1ACE） -----------------------------------------
    omni = [info for info in devices if info.vendor_id == ASUS_VID and info.product_id == 0x1ACE]
    if omni:
        col01 = _first(omni, lambda item: "mi_02&col01" in item.path.lower())
        col02 = _first(omni, lambda item: "mi_02&col02" in item.path.lower())
        col03 = _first(omni, lambda item: "mi_02&col03" in item.path.lower())
        pairings = read_omni_pairings(col01, debug=debug) if col01 else []
        if pairings:
            # 接收器从配对列表查询切回普通命令时，可能在极短时间内对下一条
            # 命令返回 NAK。
            time.sleep(0.12)
        keyboard_pid = next((pid for pid in pairings if pid in OMNI_KEYBOARDS), None)
        mouse_pid = next((pid for pid in pairings if pid in OMNI_MICE), None)
        pairings_known = bool(pairings)
        unknown_pairings = bool(pairings_known and any(
            pid not in OMNI_KEYBOARDS and pid not in OMNI_MICE
            for pid in pairings
        ))
        # 只暴露实际已配对的通道。如果接收器没有返回配对列表，则探测两个
        # 通道，再由读取器隐藏始终没有响应的那一个。
        keyboard_present = (
            not pairings_known or keyboard_pid is not None or unknown_pairings
        )
        mouse_present = (
            not pairings_known or mouse_pid is not None or unknown_pairings
        )

        if col02 and keyboard_present:
            name = OMNI_KEYBOARDS.get(keyboard_pid or 0, "ROG OMNI 键盘")
            spec = AsusSpec(
                key="omni-keyboard",
                pid=0x1ACE,
                name=name,
                kind="keyboard",
                report_id=0x02,
                command=OMNI_KEYBOARD_COMMAND,
                battery_offset=6,
                charging_offset=9,
                path_contains="mi_02&col02",
                packet_size=64,
                source="ASUS OMNI 厂商协议",
            )
            targets.append(DeviceTarget(
                key="omni-keyboard",
                name=name,
                kind="keyboard",
                info=col02,
                provider="asus",
                asus=spec,
                paired=(
                    True if keyboard_pid is not None
                    else (None if not pairings_known or unknown_pairings else False)
                ),
            ))
            used_interfaces.add(col02.interface_key)

        if col03 and mouse_present:
            name = OMNI_MICE.get(mouse_pid or 0, "ROG OMNI 鼠标")
            spec = AsusSpec(
                key="omni-mouse",
                pid=0x1ACE,
                name=name,
                kind="mouse",
                report_id=0x03,
                command=OMNI_MOUSE_COMMAND,
                battery_offset=5,
                charging_offset=10,
                charging_value=1,
                path_contains="mi_02&col03",
                packet_size=64,
                source="ASUS OMNI 厂商协议",
            )
            targets.append(DeviceTarget(
                key="omni-mouse",
                name=name,
                kind="mouse",
                info=col03,
                provider="asus",
                asus=spec,
                paired=(
                    True if mouse_pid is not None
                    else (None if not pairings_known or unknown_pairings else False)
                ),
            ))
            used_interfaces.add(col03.interface_key)

    # --- 独立 ASUS 无线设备 -----------------------------------------------
    for spec in ASUS_STANDALONE_SPECS:
        info = _first(
            devices,
            lambda item, current=spec: current.matches(item)
            and item.interface_key not in used_interfaces,
        )
        if info:
            targets.append(DeviceTarget(
                key=spec.key,
                name=spec.name,
                kind=spec.kind,
                info=info,
                provider="asus",
                asus=spec,
                paired=True,
            ))
            used_interfaces.add(info.interface_key)

    # --- 标准 HID Battery Strength ---------------------------------------
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
        name = info.product or f"HID 设备 {info.vid_pid}"
        targets.append(DeviceTarget(
            key="std-" + info.interface_key,
            name=name,
            kind=kind,
            info=info,
            provider="standard",
            paired=True,
        ))
        used_interfaces.add(info.interface_key)

    # --- 手动标定（最低优先级） -------------------------------------------
    for index, calibration in enumerate(calibrations):
        info = _first(
            devices,
            lambda item, current=calibration: current.matches(item)
            and item.interface_key not in used_interfaces,
        )
        if not info:
            continue
        targets.append(DeviceTarget(
            key=f"manual-{index}-{info.interface_key}",
            name=calibration.name,
            kind=calibration.kind,
            info=info,
            provider="manual",
            manual=calibration,
            paired=True,
        ))
        used_interfaces.add(info.interface_key)

    targets.sort(key=lambda item: (
        {"keyboard": 0, "mouse": 1, "headset": 2, "other": 3}.get(item.kind, 9),
        item.name.lower(),
    ))
    return targets


# ---------------------------------------------------------------------------
# 读取器
# ---------------------------------------------------------------------------

def _offline(target: DeviceTarget, message: str, source: str = "") -> DeviceReading:
    return DeviceReading(
        target=target,
        percent=None,
        source=source or target.source_hint,
        status="offline",
        message=message,
    )


def _error(target: DeviceTarget, message: str, source: str = "") -> DeviceReading:
    return DeviceReading(
        target=target,
        percent=None,
        source=source or target.source_hint,
        status="error",
        message=message,
    )


def _validate_percent(value: float) -> Optional[int]:
    try:
        number = int(round(float(value)))
    except (TypeError, ValueError):
        return None
    return max(0, min(100, number))


def _asus_query(
    target: DeviceTarget,
    command: tuple[int, int],
    debug=None,
    timeout_ms: int = 800,
) -> tuple[Optional[bytes], str]:
    spec = target.asus
    if spec is None:
        return None, "缺少 ASUS 协议定义"
    info = target.info
    packet_size = int(info.output_len or spec.packet_size or 64)
    packet = bytes([spec.report_id & 0xFF, command[0] & 0xFF, command[1] & 0xFF])
    if len(packet) > packet_size:
        return None, "接口数据包长度不足"
    expected = packet[:3]
    try:
        with HidTransport(info, packet_size=packet_size, debug=debug) as transport:
            result = transport.send(
                packet,
                timeout_ms=timeout_ms,
                expected_prefix=expected,
            )
            if result.matched:
                return result.matched, ""
            if result.nak:
                return None, "设备休眠或未连接（接收器返回 NAK）"
            return None, "设备无响应"
    except PermissionError:
        return None, "接口被系统独占，无法发送厂商命令"
    except OSError as exc:
        message = str(exc)
        if "拒绝访问" in message or "Access is denied" in message or "5" == message[:1]:
            return None, "接口被系统独占，无法发送厂商命令"
        return None, f"通信失败：{message}"
    except Exception as exc:
        return None, f"通信失败：{exc}"


def _read_asus(target: DeviceTarget, debug=None, timeout_ms: int = 800) -> DeviceReading:
    spec = target.asus
    if spec is None:
        return _error(target, "缺少 ASUS 协议定义")
    if target.paired is False:
        return _offline(target, "接收器未配对")

    response, error = _asus_query(target, spec.command, debug=debug, timeout_ms=timeout_ms)
    if response is None and "NAK" in error and target.paired is not False:
        # OMNI 接收器刚查询完另一个通道时，偶尔会对第一条命令返回 NAK。
        # 安静地重试一次成本很低，通常即可恢复。
        time.sleep(0.18)
        response, error = _asus_query(
            target, spec.command, debug=debug, timeout_ms=timeout_ms
        )
    if response is None:
        return _offline(target, error, spec.source)

    offset = spec.battery_offset
    if offset < 0 or offset >= len(response):
        return _error(target, "电池字节下标超出响应长度", spec.source)

    if spec.key == "cetra-speednova" and len(response) >= 9:
        # Cetra SpeedNova 会分别报告左耳、右耳和充电盒电量。
        values = [response[6], response[7], response[8]]
        percent = _validate_percent(min(values))
        detail_extra = f"L={values[0]}% R={values[1]}% Case={values[2]}%"
    else:
        percent = _validate_percent(response[offset])
        detail_extra = ""
    if percent is None:
        return _error(target, "无法解析电量字节", spec.source)

    charging: Optional[bool] = None
    if spec.charging_command is None and spec.charging_offset is not None \
            and spec.charging_offset < len(response):
        charging = response[spec.charging_offset] == spec.charging_value

    if spec.charging_command:
        charge_response, _ = _asus_query(
            target, spec.charging_command, debug=debug, timeout_ms=timeout_ms
        )
        if charge_response is not None:
            if spec.key == "cetra-speednova":
                if len(charge_response) >= 6:
                    charging = (charge_response[5] & 0x03) != 0
            else:
                index = spec.charging_offset if spec.charging_offset is not None else 5
                if index < len(charge_response):
                    charging = charge_response[index] == spec.charging_value

    detail = (
        f"{target.info.path.split('#')[1] if '#' in target.info.path else target.info.path}"
        f" | report 0x{spec.report_id:02X}"
    )
    if detail_extra:
        detail += " | " + detail_extra
    return DeviceReading(
        target=target,
        percent=percent,
        charging=charging,
        source=spec.source,
        status="ok",
        message="",
        detail=detail,
    )


def _read_manual(target: DeviceTarget, debug=None, timeout_ms: int = 800) -> DeviceReading:
    calibration = target.manual
    if calibration is None:
        return _error(target, "缺少手动标定数据")
    if target.paired is False:
        return _offline(target, "设备未连接")
    info = target.info
    packet_size = int(info.output_len or calibration.packet_size or 64)
    packet = bytes([calibration.report_id & 0xFF]) + bytes(calibration.command)
    if len(packet) > packet_size:
        return _error(target, "标定命令超过接口数据包长度")
    expected = packet[:min(3, len(packet))]
    try:
        with HidTransport(info, packet_size=packet_size, debug=debug) as transport:
            result = transport.send(
                packet, timeout_ms=timeout_ms,
                expected_prefix=expected if len(expected) >= 2 else None,
            )
            if not result.matched:
                if result.nak:
                    return _offline(target, "设备返回 NAK")
                return _offline(target, "设备无响应")
            response = result.matched
    except Exception as exc:
        return _error(target, f"通信失败：{exc}")

    index = calibration.battery_offset
    if index < 0 or index >= len(response):
        return _error(target, "电量下标超出响应长度")
    percent = _validate_percent(response[index] * calibration.scale)
    if percent is None:
        return _error(target, "无法解析标定响应")
    charging = None
    if calibration.charging_offset is not None and calibration.charging_offset < len(response):
        charging = response[calibration.charging_offset] == calibration.charging_value
    return DeviceReading(
        target=target,
        percent=percent,
        charging=charging,
        source="手动标定协议",
        status="ok",
        detail=f"report 0x{calibration.report_id:02X}, byte {index}, ×{calibration.scale:g}",
    )


def read_target(target: DeviceTarget, debug=None, timeout_ms: int = 800) -> DeviceReading:
    """对单个目标依次执行提供者优先级链。"""
    info = target.info

    # 1. 标准 HID 电量字段。
    try:
        if find_battery_fields(info):
            percent = read_standard_battery(info, input_timeout_ms=900)
            if percent is not None:
                return DeviceReading(
                    target=target,
                    percent=percent,
                    charging=None,
                    source="标准 HID 电量字段",
                    status="ok",
                    detail="Generic Device Controls 0x0006 / 0x0020",
                )
    except Exception:
        pass

    # 2. 厂商协议表。ASUS 是当前完整实现的品牌。
    if target.asus is not None:
        return _read_asus(target, debug=debug, timeout_ms=timeout_ms)

    # 3. Logitech HID++ 2.0 是有意保留的扩展适配器，只对显式注册的
    #    Logitech 目标调用，不会遍历系统中的每个罗技接收器接口。
    if target.provider == "logitech":
        result = read_logitech_hidpp(target, debug=debug)
        if result is not None:
            return result

    # 4. Input Report 兜底。标准电量字段已在上方尝试过；对于未知协议，
    #    这里短暂监听，并允许手动标定页面把发现的报文模式保存为提供者。
    if target.provider == "input" and info.input_len:
        try:
            reports = listen_input(info, seconds=1.4, max_reports=64)
            if debug and reports:
                debug(f"Input reports from {target.name}: {len(reports)}")
        except Exception:
            pass

    # 5. 手动标定。
    if target.manual is not None:
        return _read_manual(target, debug=debug, timeout_ms=timeout_ms)

    return _offline(target, "没有可用的电量协议")


# ---------------------------------------------------------------------------
# Logitech HID++ 2.0 适配器（预留扩展点）
# ---------------------------------------------------------------------------

HIDPP_FEATURE_BATTERY_VOLTAGE = 0x1000
HIDPP_FEATURE_BATTERY_LEVEL = 0x1001
HIDPP_FEATURE_UNIFIED_BATTERY = 0x1004


def _hidpp_request(transport: HidTransport, device_index: int, feature_index: int,
                   function: int, params: bytes = b"",
                   timeout_ms: int = 700) -> Optional[bytes]:
    payload = bytes([
        0x11, device_index & 0xFF, feature_index & 0xFF,
        ((function & 0x0F) << 4) & 0xFF,
    ]) + params
    expected = bytes([0x11, device_index & 0xFF, feature_index & 0xFF])
    result = transport.send(payload, timeout_ms=timeout_ms, expected_prefix=expected)
    return result.matched


def _hidpp_get_feature(transport: HidTransport, device_index: int,
                       feature_id: int) -> Optional[int]:
    response = _hidpp_request(
        transport, device_index, 0x00, 0x00,
        bytes([(feature_id >> 8) & 0xFF, feature_id & 0xFF]),
    )
    if not response or len(response) < 3 or response[2] == 0:
        return None
    return int(response[2])


def read_logitech_hidpp(target: DeviceTarget, debug=None) -> Optional[DeviceReading]:
    info = target.info
    packet_size = max(7, int(info.output_len or 20))
    if packet_size < 7:
        return None
    try:
        with HidTransport(info, packet_size=packet_size, debug=debug) as transport:
            # 0xFF 用于寻址接收器；0x01..0x06 用于寻址已配对设备。
            for device_index in (0xFF, 0x01, 0x02, 0x03, 0x04, 0x05, 0x06):
                feature = _hidpp_get_feature(transport, device_index, HIDPP_FEATURE_BATTERY_LEVEL)
                if feature:
                    response = _hidpp_request(
                        transport, device_index, feature, 0x00
                    )
                    if response and len(response) >= 5:
                        percent = _validate_percent(response[4])
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
                feature = _hidpp_get_feature(transport, device_index, HIDPP_FEATURE_UNIFIED_BATTERY)
                if feature:
                    response = _hidpp_request(transport, device_index, feature, 0x01)
                    if response and len(response) >= 5:
                        percent = _validate_percent(response[4])
                        if percent is not None:
                            return DeviceReading(
                                target=target,
                                percent=percent,
                                source="Logitech HID++ 2.0",
                                status="ok",
                                detail=f"feature 0x1004, device 0x{device_index:02X}",
                            )
                feature = _hidpp_get_feature(transport, device_index, HIDPP_FEATURE_BATTERY_VOLTAGE)
                if feature:
                    response = _hidpp_request(transport, device_index, feature, 0x00)
                    if response and len(response) >= 6:
                        millivolts = response[4] | (response[5] << 8)
                        if 2500 <= millivolts <= 5000:
                            percent = _validate_percent(
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
