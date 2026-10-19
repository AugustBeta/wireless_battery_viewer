"""后台读取服务。

界面绝不在 Tk 线程中读取 HID。短生命周期的后台工作线程负责刷新所有目标，
并通过线程安全的回调发布不可变快照。
托盘的 SetTimer 是唯一的空闲定时器；这里没有轮询或 sleep 循环。
"""

from __future__ import annotations

import threading
import traceback
from datetime import datetime
from typing import Callable, Optional

from .config import AppConfig
from .models import DeviceReading, Snapshot
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

    @property
    def last_snapshot(self) -> Optional[Snapshot]:
        with self._lock:
            return self._last_snapshot

    def _log(self, message: str) -> None:
        if self.debug and self._debug_log:
            self._debug_log(message)

    def request_refresh(self, reason: str = "timer") -> bool:
        """安排一次刷新；服务已停止时返回 False。"""
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
                # 界面回调绝不能让读取线程退出。
                self._log("snapshot callback failed: " + traceback.format_exc(limit=2))
            if not more:
                return
            with self._lock:
                if self._stopped or self._busy:
                    # 快照回调执行期间，新请求可能已经启动了自己的工作线程；
                    # 让那个线程负责下一轮刷新。
                    return
                self._busy = True

    def _refresh_once(self) -> Snapshot:
        started = datetime.now()
        errors: list[str] = []
        readings: list[DeviceReading] = []
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
        except Exception as exc:
            errors.append(f"枚举设备失败：{exc}")
            self._log("enumeration failed: " + traceback.format_exc(limit=3))
            targets = []

        for target in targets:
            if self._stopped:
                break
            # 已知未配对的接收器通道不是设备，不能生成卡片或托盘图标。
            if target.paired is False:
                self._log(f"hidden unpaired channel: {target.name}")
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
            # 当配对列表不可用时，我们探测两个通道。没有响应且没有已知
            # 配对信息的通道同样不显示。
            if reading.status != "ok" and target.paired is not True:
                self._log(f"hidden silent channel: {target.name} ({reading.status})")
                continue
            readings.append(reading)
            self._log(
                f"{target.name}: status={reading.status} "
                f"percent={reading.percent} source={reading.source}"
            )

        if not readings:
            errors.append("未发现已连接的无线外设")
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
