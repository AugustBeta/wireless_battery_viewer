"""桌面链接使用的双击启动器。

该文件以符号链接（失败时使用硬链接）的方式放到桌面，不会被复制过去。
通过 .pyw 文件关联由 pythonw.exe 运行，因此不会出现控制台窗口。
"""

from __future__ import annotations

import os
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from my_devices import main

if __name__ == "__main__":
    raise SystemExit(main(["--panel"]))
