"""
uploader.py — Batching, idempotent POSTs to cloud, and catch-up (§7.2, §8).

Runs as a background daemon thread.
Only sends when mode == "online".
Advances cloud_ack cursor only after the cloud confirms receipt.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from typing import Optional

import urllib.request
import urllib.error

from .store import Store
from .mode import ModeController, MODE_ONLINE

logger = logging.getLogger(__name__)

SENSORS = (1, 2)


class Uploader(threading.Thread):
    """
    Background uploader thread.

    During normal operation: sends up to upload_batch_samples per POST per sensor.
    During catch-up: uses catchup_batch_samples for bulk uploads.
    """

    def __init__(
        self,
        store: Store,
        mode_controller: ModeController,
        cloud_base_url: str,
        bearer_token: str,
        upload_batch_samples: int = 400,
        catchup_batch_samples: int = 24000,
        poll_interval_s: float = 0.5,
    ) -> None:
        super().__init__(daemon=True, name="uploader")
        self.store = store
        self.mode_controller = mode_controller
        self.cloud_base_url = cloud_base_url.rstrip("/")
        self.bearer_token = bearer_token
        self.upload_batch_samples = upload_batch_samples
        self.catchup_batch_samples = catchup_batch_samples
        self.poll_interval_s = poll_interval_s

        self._stop_event = threading.Event()
        self._in_catchup = False

    def stop(self) -> None:
        self._stop_event.set()

    def run(self) -> None:
        while not self._stop_event.is_set():
            if self.mode_controller.mode != MODE_ONLINE:
                self._stop_event.wait(timeout=self.poll_interval_s)
                continue

            session_id = self.store.get_open_session() or (self.store.list_sessions() or [None])[-1]
            if not session_id:
                self._stop_event.wait(timeout=self.poll_interval_s)
                continue

            has_more = False
            try:
                has_more = self._upload_cycle(session_id)
            except Exception as exc:
                logger.warning("Uploader: upload cycle error: %s", exc)

            if not has_more:
                self._stop_event.wait(timeout=self.poll_interval_s)

    # ── Upload one cycle ──────────────────────────────────────────────────

    def _upload_cycle(self, session_id: str) -> bool:
        backlog = self.store.backlog_size(session_id)
        if backlog > self.upload_batch_samples:
            if not self._in_catchup:
                self.trigger_catchup()
        elif self._in_catchup:
            self.finish_catchup()

        any_sent = False
        for sensor in SENSORS:
            next_idx, cloud_ack = self.store.get_cursor(session_id, sensor)
            batch_size = (
                self.catchup_batch_samples
                if self._in_catchup
                else self.upload_batch_samples
            )

            samples = self.store.get_unuploaded_samples(session_id, sensor, batch_size)
            losses  = self.store.get_unuploaded_losses(session_id, sensor)

            if not samples and not losses:
                continue

            payload = {
                "session_id": session_id,
                "sensor": sensor,
                "samples": [{"idx": idx, "values": vals} for idx, vals in samples],
                "losses": [
                    {"from_index": f, "to_index": t, "reason": r}
                    for f, t, r in losses
                ],
            }

            try:
                new_ack = self._post_samples(payload)
                if new_ack is not None:
                    self.store.advance_cloud_ack(session_id, sensor, new_ack)
                    any_sent = True
                    logger.debug(
                        "Uploader s%d: cloud_ack advanced to %d", sensor, new_ack
                    )
            except Exception as exc:
                logger.warning("Uploader s%d: POST failed: %s", sensor, exc)

        # Return True if there is still backlog to upload immediately
        return any_sent and (self.store.backlog_size(session_id) > 0)

    # ── HTTP helpers ─────────────────────────────────────────────────────

    def _post_samples(self, payload: dict) -> Optional[int]:
        """
        POST /v1/samples.  Returns the new highest contiguous index, or None.
        """
        url = f"{self.cloud_base_url}/v1/samples"
        body = json.dumps(payload).encode()
        req = urllib.request.Request(
            url,
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.bearer_token}",
            },
        )
        with urllib.request.urlopen(req, timeout=10.0) as resp:
            result = json.loads(resp.read())
            return result.get("highest_contiguous_index")

    def trigger_catchup(self) -> None:
        """Called by mode controller when network returns."""
        logger.info("Uploader: catch-up triggered")
        self._in_catchup = True

    def finish_catchup(self) -> None:
        self._in_catchup = False
        logger.info("Uploader: catch-up finished")
