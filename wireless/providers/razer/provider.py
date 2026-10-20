"""Razer 品牌提供者。

雷蛇无线鼠标通过 90/91 字节的 Feature Report 查询电量。协议参考
FerrisPulse 的 ``RazerDeviceHelper``：请求带事务号、命令类和 CRC，
接收器在同一个 Feature Report 上返回状态、原始电量和充电标志。

这里只负责发现带厂商 Feature Report 的雷蛇设备，并把原始值换算成
面板使用的百分比；标准 HID 电量和手动标定仍由通用调度处理。
"""

from __future__ import annotations

from typing import Optional

from ...models import DeviceReading, DeviceTarget
from ...winhid import HidDeviceInfo, exchange_feature_report
from ..common import debug_log, error, offline

PROVIDER_KEY = "razer"

RAZER_VENDOR_ID = 0x1532
RAZER_BATTERY_CLASS = 0x07
RAZER_BATTERY_COMMAND = 0x80
RAZER_CHARGING_COMMAND = 0x84
RAZER_TRANSACTION_IDS = (0x1F, 0x3F, 0xFF)
RAZER_REPORT_SIZE = 90
RAZER_BATTERY_SOURCE = "Razer 厂商协议"

# 雷蛇接收器和有线版的 PID 不同，但它们是同一款设备。归一化后用于
# 去重，避免同时插入接收器和数据线时出现两张卡片。
RAZER_PID_ALIASES = {
    0x00BF: 0x00BE,  # DeathAdder V4 Pro 接收器 -> 有线
    0x00E6: 0x00E5,  # Viper V4 Pro 接收器 -> 有线
    0x00C1: 0x00C0,  # Viper V3 Pro 接收器 -> 有线
    0x00C2: 0x00C0,  # Viper V3 Pro Sentinels 有线
    0x00C3: 0x00C0,  # Viper V3 Pro Sentinels 接收器
    0x00DF: 0x00DE,  # Viper V3 Pro SE 接收器 -> 有线
    0x00AD: 0x00AC,  # Viper V3 HyperSpeed 接收器 -> 有线
    0x00B7: 0x00B6,  # DeathAdder V3 Pro 接收器 -> 有线
    0x00B3: 0x00B2,  # DeathAdder V3 HyperSpeed 接收器 -> 有线
    0x00B5: 0x00B4,  # Viper V2 Pro 接收器 -> 有线
    0x00A6: 0x00A5,  # Cobra Pro 接收器 -> 有线
    0x009B: 0x009A,  # Basilisk V3 Pro 接收器 -> 有线
    0x00C5: 0x00C4,  # Basilisk V3 35K 接收器 -> 有线
    0x00A8: 0x00A7,  # Viper Mini Signature 接收器 -> 有线
    0x0090: 0x008F,  # Naga V2 Pro 接收器 -> 有线
    0x0092: 0x0091,  # Naga Pro 接收器 -> 有线
    0x00A3: 0x00A2,  # DeathAdder V2 Pro 接收器 -> 有线
    0x0088: 0x0084,  # Viper Ultimate 接收器 -> 有线
    0x007B: 0x007A,  # Basilisk Ultimate 接收器 -> 有线
    0x007D: 0x007C,  # DeathAdder V2 Mini 接收器 -> 有线
}

_KEYBOARD_WORDS = (
    "keyboard", "blackwidow", "huntsman", "cynosa", "ornata",
    "deathstalker", "tartarus", "orbweaver", "anansi", "nostromo",
)
_HEADSET_WORDS = (
    "headset", "kraken", "blackshark", "nari", "hammerhead",
    "barracuda", "nommo", "leviathan", "seiren", "speaker",
    "soundbar", "audio",
)


def normalize_pid(product_id: int) -> int:
    """把接收器 PID 归一到同一款设备的有线版 PID。"""
    return RAZER_PID_ALIASES.get(product_id, product_id)


def _kind_for(product_id: int, name: str) -> str:
    lower = name.lower()
    if any(word in lower for word in _KEYBOARD_WORDS):
        return "keyboard"
    if any(word in lower for word in _HEADSET_WORDS):
        return "headset"
    if 0x0100 <= product_id <= 0x02FF:
        return "keyboard"
    if 0x0500 <= product_id <= 0x05FF:
        return "headset"
    return "mouse"


def _candidate_score(info: HidDeviceInfo) -> int:
    """同一款设备的多个接口中，优先选带完整 Feature Report 的接口。"""
    score = 0
    if info.feature_len == 91:
        score += 8
    elif info.feature_len == 90:
        score += 6
    if info.interface_number == 0:
        score += 2
    if info.usage_page == 0x0001:
        score += 1
    return score


def discover(devices, debug=None) -> list[DeviceTarget]:
    """发现带雷蛇厂商 Feature Report 的无线设备。"""
    candidates: dict[int, HidDeviceInfo] = {}
    for info in devices:
        if info.vendor_id != RAZER_VENDOR_ID:
            continue
        if info.feature_len < RAZER_REPORT_SIZE or not info.accessible:
            continue
        normalized = normalize_pid(info.product_id)
        current = candidates.get(normalized)
        if current is None or _candidate_score(info) > _candidate_score(current):
            candidates[normalized] = info

    targets: list[DeviceTarget] = []
    for normalized, info in candidates.items():
        name = info.product or f"Razer 外设 ({info.product_id:04X})"
        targets.append(DeviceTarget(
            key=f"razer-{normalized:04x}",
            name=name,
            kind=_kind_for(info.product_id, name),
            info=info,
            provider=PROVIDER_KEY,
            source_hint=RAZER_BATTERY_SOURCE,
        ))
        debug_log(
            debug,
            f"Razer target: {name} ({info.vid_pid}, feature={info.feature_len})",
        )
    return targets


def _build_report(info: HidDeviceInfo, transaction_id: int, command: int) -> bytes:
    """按雷蛇 Feature Report 布局生成一条带 CRC 的查询。"""
    size = int(info.feature_len)
    if size < RAZER_REPORT_SIZE:
        raise ValueError(f"Feature Report 长度不足：{size}")
    # 91 字节报告在最前面多了 Windows 的 Report ID 字节，协议整体后移。
    offset = 1 if size == 91 else 0
    report = bytearray(size)
    report[offset + 1] = transaction_id & 0xFF
    report[offset + 5] = 0x02  # 后续两个参数字节
    report[offset + 6] = RAZER_BATTERY_CLASS
    report[offset + 7] = command & 0xFF
    crc = 0
    for value in report[offset + 2:offset + 88]:
        crc ^= value
    report[offset + 88] = crc
    report[offset + 89] = 0
    return bytes(report)


def _parse_response(response: Optional[bytes], command: int) -> Optional[int]:
    """校验响应并返回第一个参数字节。

    接收器可能回显上一次的事务号，因此不校验 [1]；只要状态、命令类和
    命令匹配，数据就是本次查询的有效响应。
    """
    if not response or len(response) < RAZER_REPORT_SIZE:
        return None
    offset = 1 if len(response) == 91 else 0
    if response[offset] != 0x02:
        return None
    if response[offset + 6] != RAZER_BATTERY_CLASS:
        return None
    if response[offset + 7] != (command & 0xFF):
        return None
    return int(response[offset + 9])


def _query(info: HidDeviceInfo, command: int, debug=None) -> Optional[int]:
    """用几个常见事务号重试一条厂商查询。"""
    for transaction_id in RAZER_TRANSACTION_IDS:
        try:
            request = _build_report(info, transaction_id, command)
            response = exchange_feature_report(info, request, delay_ms=20)
        except Exception as exc:
            debug_log(debug, f"Razer command 0x{command:02X} failed: {exc}")
            return None
        value = _parse_response(response, command)
        if value is not None:
            return value
        if response:
            debug_log(
                debug,
                f"Razer command 0x{command:02X} invalid response: "
                f"{response[:16].hex(' ')}",
            )
    return None


def read(
    target: DeviceTarget,
    debug=None,
    timeout_ms: int = 800,
) -> Optional[DeviceReading]:
    """读取雷蛇设备的电量；充电状态查询失败不影响电量结果。"""
    info = target.info
    if info.feature_len < RAZER_REPORT_SIZE:
        return error(target, "该接口没有可用的厂商 Feature Report", RAZER_BATTERY_SOURCE)

    raw = _query(info, RAZER_BATTERY_COMMAND, debug=debug)
    if raw is None or raw <= 0:
        debug_log(debug, f"Razer battery query returned no data: {target.name}")
        return offline(target, "设备未响应电量查询（可能已休眠）", RAZER_BATTERY_SOURCE)

    # 雷蛇固件把 0 当作异常值，最低显示 1%。
    percent = max(1, min(100, round(raw / 255 * 100)))
    charging_raw = _query(info, RAZER_CHARGING_COMMAND, debug=debug)
    charging = None if charging_raw is None else charging_raw == 1
    return DeviceReading(
        target=target,
        percent=percent,
        charging=charging,
        source=RAZER_BATTERY_SOURCE,
        status="ok",
        detail=f"raw {raw}/255",
    )
