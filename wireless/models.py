"""设备发现、读取器以及两个界面共用的数据模型。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

from .winhid import HidDeviceInfo


KIND_LABELS = {
    "keyboard": "键盘",
    "mouse": "鼠标",
    "headset": "耳机",
    "other": "外设",
}

KIND_ORDER = {"keyboard": 0, "mouse": 1, "headset": 2, "other": 3}


@dataclass(frozen=True)
class AsusSpec:
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
        if info.vendor_id != 0x0B05 or info.product_id != self.pid:
            return False
        if self.path_contains and self.path_contains.lower() not in info.path.lower():
            return False
        if self.usage_page is not None and info.usage_page != self.usage_page:
            return False
        if self.usage is not None and info.usage != self.usage:
            return False
        return info.accessible or not self.path_contains


@dataclass(frozen=True)
class ManualCalibration:
    name: str
    vendor_id: int
    product_id: int
    path_contains: str
    report_id: int
    battery_offset: int
    scale: float = 1.0
    command: tuple[int, ...] = (0x12, 0x01)
    charging_offset: Optional[int] = None
    charging_value: int = 1
    packet_size: int = 64
    kind: str = "other"

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "ManualCalibration":
        command = raw.get("command", [0x12, 0x01])
        if isinstance(command, str):
            command = [int(part, 16) for part in command.replace(",", " ").split()]
        return cls(
            name=str(raw.get("name") or "校准设备"),
            vendor_id=int(raw.get("vendor_id", 0)),
            product_id=int(raw.get("product_id", 0)),
            path_contains=str(raw.get("path_contains", "")),
            report_id=int(raw.get("report_id", 0)),
            battery_offset=int(raw.get("battery_offset", 0)),
            scale=float(raw.get("scale", 1.0)),
            command=tuple(int(value) & 0xFF for value in command),
            charging_offset=(
                int(raw["charging_offset"])
                if raw.get("charging_offset") not in (None, "")
                else None
            ),
            charging_value=int(raw.get("charging_value", 1)),
            packet_size=int(raw.get("packet_size", 64)),
            kind=str(raw.get("kind", "other")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "vendor_id": self.vendor_id,
            "product_id": self.product_id,
            "path_contains": self.path_contains,
            "report_id": self.report_id,
            "battery_offset": self.battery_offset,
            "scale": self.scale,
            "command": list(self.command),
            "charging_offset": self.charging_offset,
            "charging_value": self.charging_value,
            "packet_size": self.packet_size,
            "kind": self.kind,
        }

    def matches(self, info: HidDeviceInfo) -> bool:
        if self.vendor_id and info.vendor_id != self.vendor_id:
            return False
        if self.product_id and info.product_id != self.product_id:
            return False
        return self.path_contains.lower() in info.path.lower()


@dataclass(frozen=True)
class DeviceTarget:
    key: str
    name: str
    kind: str
    info: HidDeviceInfo
    provider: str
    asus: Optional[AsusSpec] = None
    manual: Optional[ManualCalibration] = None
    paired: Optional[bool] = None

    @property
    def role(self) -> str:
        return KIND_LABELS.get(self.kind, "外设")

    @property
    def source_hint(self) -> str:
        if self.asus:
            return self.asus.source
        if self.provider == "standard":
            return "标准 HID 电量字段"
        if self.provider == "logitech":
            return "Logitech HID++ 2.0"
        if self.provider == "manual":
            return "手动标定协议"
        return "Input Report"


@dataclass
class DeviceReading:
    target: DeviceTarget
    percent: Optional[int] = None
    charging: Optional[bool] = None
    source: str = ""
    status: str = "pending"  # 状态：pending | ok | offline | error
    message: str = ""
    updated: Optional[datetime] = None
    detail: str = ""

    @property
    def connected(self) -> bool:
        return self.status == "ok"

    @property
    def battery_text(self) -> str:
        if self.percent is None:
            return "—"
        return f"{self.percent}%"

    @property
    def status_text(self) -> str:
        if self.status == "ok":
            if self.charging:
                return "充电中"
            if self.charging is False:
                return "放电中"
            return "已连接"
        if self.status == "offline":
            return "未连接"
        if self.status == "error":
            return "读取失败"
        return "正在读取"

    @property
    def color(self) -> str:
        if self.percent is None or self.status != "ok":
            return "#9AA4B2"
        if self.percent >= 60:
            return "#16A34A"
        if self.percent >= 30:
            return "#D99A00"
        return "#DC2626"


@dataclass
class Snapshot:
    readings: list[DeviceReading] = field(default_factory=list)
    started: datetime = field(default_factory=datetime.now)
    finished: datetime = field(default_factory=datetime.now)
    errors: list[str] = field(default_factory=list)

    @property
    def ok_count(self) -> int:
        return sum(1 for item in self.readings if item.status == "ok")

    @property
    def display_count(self) -> int:
        return len(self.readings)
