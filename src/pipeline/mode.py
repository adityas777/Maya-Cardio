"""
mode.py — Network probe, hysteresis, and mode switching (§7).

Runs as a background daemon thread.
Calls /v1/health every probe_interval_s.
Goes offline after offline_after_failures consecutive failures.
Goes online after online_after_successes consecutive successes.
"""

from __future__ import annotations

import threading
import time
import logging
from typing import Callable, Optional

import urllib.request
import urllib.error

logger = logging.getLogger(__name__)

MODE_ONLINE  = "online"
MODE_OFFLINE = "offline"


class ModeController(threading.Thread):
    """
    Background thread that probes the cloud and manages mode transitions.

    Callbacks:
        on_online(prev_mode):  called when transitioning online
        on_offline(prev_mode): called when transitioning offline
    """

    def __init__(
        self,
        health_url: str,
        probe_interval_s: float = 2.0,
        offline_after_failures: int = 2,
        online_after_successes: int = 2,
        health_timeout_s: float = 1.0,
        on_online:  Optional[Callable[[str], None]] = None,
        on_offline: Optional[Callable[[str], None]] = None,
    ) -> None:
        super().__init__(daemon=True, name="mode-controller")
        self.health_url = health_url
        self.probe_interval_s = probe_interval_s
        self.offline_after_failures = offline_after_failures
        self.online_after_successes = online_after_successes
        self.health_timeout_s = health_timeout_s
        self.on_online  = on_online
        self.on_offline = on_offline

        self._mode = MODE_OFFLINE    # start offline; go online only after probes pass
        self._mode_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._consecutive_failures = 0
        self._consecutive_successes = 0

        # Monotonic timestamp of last mode switch
        self._last_switch_time = time.monotonic()

    # ── Public API ───────────────────────────────────────────────────────

    @property
    def mode(self) -> str:
        with self._mode_lock:
            return self._mode

    def stop(self) -> None:
        self._stop_event.set()

    # ── Thread ───────────────────────────────────────────────────────────

    def run(self) -> None:
        while not self._stop_event.is_set():
            probe_start = time.monotonic()
            success = self._probe()
            elapsed = time.monotonic() - probe_start

            with self._mode_lock:
                current_mode = self._mode
                if success:
                    self._consecutive_failures = 0
                    self._consecutive_successes += 1
                    if (current_mode == MODE_OFFLINE and
                            self._consecutive_successes >= self.online_after_successes):
                        self._switch_to(MODE_ONLINE)
                else:
                    self._consecutive_successes = 0
                    self._consecutive_failures += 1
                    if (current_mode == MODE_ONLINE and
                            self._consecutive_failures >= self.offline_after_failures):
                        self._switch_to(MODE_OFFLINE)

            sleep_for = max(0.0, self.probe_interval_s - elapsed)
            self._stop_event.wait(timeout=sleep_for)

    def _probe(self) -> bool:
        try:
            req = urllib.request.Request(self.health_url, method="GET")
            with urllib.request.urlopen(req, timeout=self.health_timeout_s) as resp:
                return resp.status == 200
        except Exception:
            return False

    def _switch_to(self, new_mode: str) -> None:
        """Must be called with _mode_lock held."""
        old_mode = self._mode
        self._mode = new_mode
        latency = time.monotonic() - self._last_switch_time
        self._last_switch_time = time.monotonic()
        self._consecutive_failures = 0
        self._consecutive_successes = 0
        logger.info(
            "Mode switch: %s → %s  (latency since last switch: %.2fs)",
            old_mode, new_mode, latency,
        )
        if new_mode == MODE_ONLINE and self.on_online:
            threading.Thread(target=self.on_online, args=(old_mode,), daemon=True).start()
        elif new_mode == MODE_OFFLINE and self.on_offline:
            threading.Thread(target=self.on_offline, args=(old_mode,), daemon=True).start()
