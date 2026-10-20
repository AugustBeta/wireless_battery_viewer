"""ASUS 设备型号表与协议参数定义。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from ...winhid import HidDeviceInfo


ASUS_VID = 0x0B05

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


@dataclass(frozen=True)
class AsusSpec:
    """单个 ASUS 设备/通道的读数参数。"""

    key: str
    pid: int
    name: str
    kind: str
    report_id: int
    command: tuple[int, int]
    battery_offset: int
    charging_offset: Optional[int] = None
    charging_value: int = 1
    path_contains: str = ""
    usage_page: Optional[int] = None
    usage: Optional[int] = None
    packet_size: int = 64
    charging_command: Optional[tuple[int, int]] = None
    source: str = "ASUS 厂商协议"

    def matches(self, info: HidDeviceInfo) -> bool:
        if info.vendor_id != ASUS_VID or info.product_id != self.pid:
            return False
        if self.path_contains and self.path_contains.lower() not in info.path.lower():
            return False
        if self.usage_page is not None and info.usage_page != self.usage_page:
            return False
        if self.usage is not None and info.usage != self.usage:
            return False
        return info.accessible or not self.path_contains


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
