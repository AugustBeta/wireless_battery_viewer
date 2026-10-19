"""Double-click launcher for the desktop link.

This file is linked (symlink, or hardlink as fallback) onto the Desktop; it is
not copied there.  It uses pythonw.exe via the .pyw association, so no console
window appears.
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
