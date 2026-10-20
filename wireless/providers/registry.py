"""品牌提供者注册表。

新增品牌时：
1. 新建 ``wireless/providers/<brand>/`` 目录；
2. 在品牌目录的 ``provider.py`` 中实现
   ``discover(devices, debug=None) -> list[DeviceTarget]`` 和
   ``read(target, debug=None, timeout_ms=800) -> Optional[DeviceReading]``；
3. 把品牌模块加入 ``BRAND_PROVIDERS``。

品牌发现函数只负责返回自己的目标；接口去重、标准 HID 兜底、手动标定和
读取优先级由 ``discovery.py`` 统一处理。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from ..models import DeviceReading, DeviceTarget
from ..winhid import HidDeviceInfo
from . import asus, logitech, razer


@dataclass(frozen=True)
class BrandProvider:
    """一个品牌提供者的注册信息。"""

    key: str
    label: str
    module: Any

    def discover(self, devices: list[HidDeviceInfo], debug=None) -> list[DeviceTarget]:
        return self.module.discover(devices, debug=debug)

    def read(
        self,
        target: DeviceTarget,
        debug=None,
        timeout_ms: int = 800,
    ) -> Optional[DeviceReading]:
        return self.module.read(target, debug=debug, timeout_ms=timeout_ms)


BRAND_PROVIDERS: tuple[BrandProvider, ...] = (
    BrandProvider("asus", "ASUS", asus),
    BrandProvider("logitech", "Logitech", logitech),
    BrandProvider("razer", "Razer", razer),
)

_BY_KEY = {brand.key: brand for brand in BRAND_PROVIDERS}


def get_brand(key: str) -> Optional[BrandProvider]:
    """按内部提供者标识查找品牌。"""
    return _BY_KEY.get(key)
