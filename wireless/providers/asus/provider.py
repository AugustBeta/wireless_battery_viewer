"""ASUS 品牌提供者。

集中存放所有 ASUS 2.4GHz/OMNI 设备的配对发现、命令收发和电量解析逻辑。
新增 ASUS 型号时，优先在 ``specs.py`` 的型号表中增加条目。
"""

from __future__ import annotations

import time
from typing import Optional

from ...models import DeviceReading, DeviceTarget
from ...winhid import HidDeviceInfo, HidTransport
from ..common import (
    debug_log,
    error,
    first_device,
    offline,
    validate_percent,
)
from .specs import (
    ASUS_STANDALONE_SPECS,
    ASUS_VID,
    OMNI_KEYBOARDS,
    OMNI_KEYBOARD_COMMAND,
    OMNI_MICE,
    OMNI_MOUSE_COMMAND,
    AsusSpec,
)


PROVIDER_KEY = "asus"


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
                    debug_log(debug, "OMNI pair list: " + response[:24].hex(" ").upper())
                    for offset in range(5, min(len(response), 5 + 4 * 12), 4):
                        pid = _entry_pid(response, offset)
                        if pid:
                            pids.append(pid)
                    if pids:
                        return pids
        except Exception as exc:
            debug_log(debug, f"OMNI pairing query failed: {exc}")
        if attempt == 0:
            time.sleep(0.12)
    return pids


def discover(devices: list[HidDeviceInfo], debug=None) -> list[DeviceTarget]:
    """发现 ASUS OMNI 接收器通道和独立 ASUS 无线设备。"""
    targets: list[DeviceTarget] = []
    used_interfaces: set[str] = set()

    # --- ASUS OMNI 接收器（1ACE） -----------------------------------------
    omni = [
        info for info in devices
        if info.vendor_id == ASUS_VID and info.product_id == 0x1ACE
    ]
    if omni:
        col01 = first_device(omni, lambda item: "mi_02&col01" in item.path.lower())
        col02 = first_device(omni, lambda item: "mi_02&col02" in item.path.lower())
        col03 = first_device(omni, lambda item: "mi_02&col03" in item.path.lower())
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
                provider=PROVIDER_KEY,
                provider_data=spec,
                paired=(
                    True if keyboard_pid is not None
                    else (None if not pairings_known or unknown_pairings else False)
                ),
                source_hint=spec.source,
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
                provider=PROVIDER_KEY,
                provider_data=spec,
                paired=(
                    True if mouse_pid is not None
                    else (None if not pairings_known or unknown_pairings else False)
                ),
                source_hint=spec.source,
            ))
            used_interfaces.add(col03.interface_key)

    # --- 独立 ASUS 无线设备 -----------------------------------------------
    for spec in ASUS_STANDALONE_SPECS:
        info = first_device(
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
                provider=PROVIDER_KEY,
                provider_data=spec,
                paired=True,
                source_hint=spec.source,
            ))
            used_interfaces.add(info.interface_key)

    return targets


def _asus_query(
    target: DeviceTarget,
    command: tuple[int, int],
    debug=None,
    timeout_ms: int = 800,
) -> tuple[Optional[bytes], str]:
    spec = target.provider_data
    if not isinstance(spec, AsusSpec):
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


def read(
    target: DeviceTarget,
    debug=None,
    timeout_ms: int = 800,
) -> DeviceReading:
    """读取一个 ASUS 目标的电量。"""
    spec = target.provider_data
    if not isinstance(spec, AsusSpec):
        return error(target, "缺少 ASUS 协议定义")
    if target.paired is False:
        return offline(target, "接收器未配对")

    response, query_error = _asus_query(
        target, spec.command, debug=debug, timeout_ms=timeout_ms
    )
    if response is None and "NAK" in query_error and target.paired is not False:
        # OMNI 接收器刚查询完另一个通道时，偶尔会对第一条命令返回 NAK。
        # 安静地重试一次成本很低，通常即可恢复。
        time.sleep(0.18)
        response, query_error = _asus_query(
            target, spec.command, debug=debug, timeout_ms=timeout_ms
        )
    if response is None:
        return offline(target, query_error, spec.source)

    offset = spec.battery_offset
    if offset < 0 or offset >= len(response):
        return error(target, "电池字节下标超出响应长度", spec.source)

    if spec.key == "cetra-speednova" and len(response) >= 9:
        # Cetra SpeedNova 会分别报告左耳、右耳和充电盒电量。
        values = [response[6], response[7], response[8]]
        percent = validate_percent(min(values))
        detail_extra = f"L={values[0]}% R={values[1]}% Case={values[2]}%"
    else:
        percent = validate_percent(response[offset])
        detail_extra = ""
    if percent is None:
        return error(target, "无法解析电量字节", spec.source)

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


__all__ = ["PROVIDER_KEY", "discover", "read"]
