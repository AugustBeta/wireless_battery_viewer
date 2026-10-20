"""电量提供者包。

目录约定：
- ``asus/``       ASUS OMNI/独立无线设备的发现与读取
- ``logitech/``   Logitech HID++ 2.0 适配器
- ``standard.py`` 标准 HID 电量字段
- ``manual.py``   手动标定
- ``registry.py`` 品牌注册表
- ``discovery.py`` 统一发现与读取调度

以后新增品牌时，新建独立目录并只在 ``registry.py`` 中注册即可。
"""

from .discovery import discover_targets, read_target
from .registry import BRAND_PROVIDERS, get_brand

__all__ = [
    "BRAND_PROVIDERS",
    "discover_targets",
    "get_brand",
    "read_target",
]
