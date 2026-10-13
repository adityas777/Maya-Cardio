"""
offline.py — On-device offline window processor (§7.3).

Runs as a background thread.
Uses the same processing.py module as the cloud.
Produces results with mode="offline".
"""

from __future__ import annotations

import json
import logging
import threading
import time
from typing import Optional

from .store import Store
from .mode import ModeController, MODE_OFFLINE
from .processing import PeakDetector, compute_window, SAMPLES_PER_WINDOW

logger = logging.getLogger(__name__)


class OfflineProcessor(threading.Thread):
    """
    Watches the store for new committed windows and processes them offline.
    """

    def __init__(
        self,
        store: Store,
        mode_controller: ModeController,
        grace_period_s: float = 3.0,
        poll_interval_s: float = 0.5,
    ) -> None:
        super().__init__(daemon=True, name="offline-processor")
        self.store = store
        self.mode_controller = mode_controller
        self.grace_period_s = grace_period_s
        self.poll_interval_s = poll_interval_s
        self._stop_event = threading.Event()

    def stop(self) -> None:
        self._stop_event.set()

    def run(self) -> None:
        # Per-session state
        _session: Optional[str] = None
        _next_window: int = 0
        _peak_detector: Optional[PeakDetector] = None

        while not self._stop_event.is_set():
            if self.mode_controller.mode != MODE_OFFLINE:
                self._stop_event.wait(timeout=self.poll_interval_s)
                continue

            session_id = self.store.get_open_session()
            if not session_id:
                self._stop_event.wait(timeout=self.poll_interval_s)
                continue

            # Reset per-session state on new session
            if session_id != _session:
                _session = session_id
                _next_window = 0
                _peak_detector = PeakDetector()
                logger.info("OfflineProcessor: started for session %s", session_id)

            # Find the latest committed next_index for sensor 1 (limiting factor)
            next_idx_s1, _ = self.store.get_cursor(session_id, 1)
            next_idx_s2, _ = self.store.get_cursor(session_id, 2)
            # Highest committed sample index: next_index - 1
            committed_up_to = min(next_idx_s1, next_idx_s2) - 1

            # Determine which windows are ready
            # Window w is ready when committed_up_to >= 400*(w+1)-1 + grace
            # i.e. at least one sample past window end (for peak rule) + grace
            while True:
                w_end = SAMPLES_PER_WINDOW * (_next_window + 1) - 1
                # Need samples up to w_end + 1 for peak confirmation
                required = w_end + 1
                if committed_up_to < required:
                    # Check grace: if grace has expired since window end's expected time
                    # Use index-based timing: 400 samples = 1 second
                    samples_past = committed_up_to - w_end
                    seconds_past = samples_past / 400.0
                    if seconds_past < self.grace_period_s:
                        break  # not ready yet

                result_time_ms = int(time.time() * 1000)
                try:
                    result = compute_window(
                        session_id=session_id,
                        window=_next_window,
                        mode="offline",
                        store=self.store,
                        peak_detector=_peak_detector,
                        result_time_ms=result_time_ms,
                    )
                    self.store.write_result(
                        session_id=session_id,
                        window=_next_window,
                        mode="offline",
                        result_json=json.dumps(result),
                        result_time_ms=result_time_ms,
                    )
                    logger.debug(
                        "OfflineProcessor: window %d processed HR=%s",
                        _next_window, result.get("heart_rate"),
                    )
                except Exception as exc:
                    logger.error("OfflineProcessor: window %d error: %s", _next_window, exc)

                _next_window += 1

            self._stop_event.wait(timeout=self.poll_interval_s)
