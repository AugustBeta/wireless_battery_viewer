"""Background reader service.

The UI never reads HID on the Tk thread.  A short-lived worker thread refreshes
all targets and publishes an immutable snapshot through a thread-safe callback.
The tray's SetTimer is the only idle timer; there is no polling/sleep loop.
"""

from __future__ import annotations

import threading
import traceback
from datetime import datetime
from typing import Callable, Optional

from .config import AppConfig
from .models import DeviceReading, DeviceTarget, Snapshot
from .providers import discover_targets, read_target
from .winhid import enumerate_hid_devices


class BatteryRepository:
    def __init__(
        self,
        config: AppConfig,
        on_snapshot: Callable[[Snapshot], None],
        debug: bool = False,
        debug_log: Optional[Callable[[str], None]] = None,
    ):
        self.config = config
        self.on_snapshot = on_snapshot
        self.debug = debug
        self._debug_log = debug_log
        self._lock = threading.Lock()
        self._busy = False
        self._pending = False
        self._stopped = False
        self._last_snapshot: Optional[Snapshot] = None
        self._last_targets: list[DeviceTarget] = []

    @property
    def last_snapshot(self) -> Optional[Snapshot]:
        with self._lock:
            return self._last_snapshot

    def _log(self, message: str) -> None:
        if self.debug and self._debug_log:
            self._debug_log(message)

    def request_refresh(self, reason: str = "timer") -> bool:
        """Schedule one refresh.  Returns False if the service is stopped."""
        with self._lock:
            if self._stopped:
                return False
            if self._busy:
                self._pending = True
                self._log(f"refresh already running; queue one more ({reason})")
                return True
            self._busy = True
        self._log(f"refresh requested: {reason}")
        thread = threading.Thread(
            target=self._worker,
            name="battery-refresh",
            daemon=True,
        )
        thread.start()
        return True

    def refresh_sync(self) -> Snapshot:
        snapshot = self._refresh_once()
        with self._lock:
            self._last_snapshot = snapshot
        return snapshot

    def _worker(self) -> None:
        while True:
            snapshot = self._refresh_once()
            with self._lock:
                self._last_snapshot = snapshot
                self._busy = False
                more = self._pending and not self._stopped
                self._pending = False
            try:
                self.on_snapshot(snapshot)
            except Exception:
                # A UI callback must never kill the reader thread.
                self._log("snapshot callback failed: " + traceback.format_exc(limit=2))
            if not more:
                return
            with self._lock:
                if self._stopped or self._busy:
                    # A new request may have started its own worker while the
                    # snapshot callback ran; let that one own the next pass.
                    return
                self._busy = True

    def _refresh_once(self) -> Snapshot:
        started = datetime.now()
        errors: list[str] = []
        readings: list[DeviceReading] = []
        reused_offline = False
        try:
            devices = enumerate_hid_devices()
            self._log(f"enumerated {len(devices)} HID interfaces")
            targets = discover_targets(
                devices,
                calibrations=self.config.calibrations,
                debug=self._debug_log if self.debug else None,
            )
            self._log(
                "targets: " + ", ".join(
                    f"{item.role}/{item.name}" for item in targets
                )
            )
            with self._lock:
                if targets:
                    self._last_targets = targets
                elif self._last_targets:
                    # Keep the cards/icons stable when a receiver is
                    # unplugged, so the tray does not jump between one and
                    # two icons.
                    targets = list(self._last_targets)
                    reused_offline = True
        except Exception as exc:
            errors.append(f"枚举设备失败：{exc}")
            self._log("enumeration failed: " + traceback.format_exc(limit=3))
            targets = []

        for target in targets:
            if self._stopped:
                break
            if reused_offline:
                readings.append(DeviceReading(
                    target=target,
                    percent=None,
                    source=target.source_hint,
                    status="offline",
                    message="未检测到设备",
                    updated=datetime.now(),
                ))
                continue
            try:
                reading = read_target(
                    target,
                    debug=self._debug_log if self.debug else None,
                )
            except Exception as exc:
                reading = DeviceReading(
                    target=target,
                    percent=None,
                    source=target.source_hint,
                    status="error",
                    message=f"读取异常：{exc}",
                )
                self._log(
                    f"read failed for {target.name}: "
                    + traceback.format_exc(limit=3)
                )
            reading.updated = datetime.now()
            readings.append(reading)
            self._log(
                f"{target.name}: status={reading.status} "
                f"percent={reading.percent} source={reading.source}"
            )

        if not targets:
            errors.append("未发现支持的无线外设")
        snapshot = Snapshot(
            readings=readings,
            started=started,
            finished=datetime.now(),
            errors=errors,
        )
        return snapshot

    def stop(self) -> None:
        with self._lock:
            self._stopped = True
            self._pending = False
