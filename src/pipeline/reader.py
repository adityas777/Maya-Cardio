"""
reader.py — TCP client for biosignal-mock (§5.2, §6.1).

One Reader instance per sensor runs in its own thread.
Validates CRC, detects gaps, deduplicates, writes to Store.

CRC implementation (§5.2 warning):
  - Extract the raw bytes of the data array SUBSTRING from the reply line.
  - Do NOT re-serialize parsed JSON to get CRC input.
  - zlib.crc32(raw_bytes) & 0xFFFFFFFF compared to crc32 field (8 hex digits).
"""

from __future__ import annotations

import json
import socket
import threading
import time
import zlib
import logging
from typing import Optional

from .store import Store
from .losses import (
    REASON_BUFFER_OVERFLOW,
    REASON_CORRUPT_REPLY,
    REASON_NOT_DELIVERED,
)

logger = logging.getLogger(__name__)

MAX_CRC_RETRIES = 3
BATCH_SAMPLES = 400   # commit every 400 samples (1 second of data) per §6.2


def _extract_crc(line: bytes) -> Optional[bytes]:
    """
    Extract the raw bytes of the data array from a reply line.

    The reply is newline-delimited JSON.  We find the 'data': key and then
    extract from the first '[' that opens the data array to its matching ']'.
    This avoids any JSON re-serialisation.

    Returns the raw bytes, or None on failure.
    """
    try:
        # Find '"data":' or '"data" :' etc.
        marker = b'"data"'
        pos = line.find(marker)
        if pos == -1:
            return None
        # Skip past the marker and optional whitespace/colon
        pos += len(marker)
        while pos < len(line) and line[pos:pos+1] in (b' ', b'\t', b':'):
            pos += 1
        if pos >= len(line) or line[pos:pos+1] != b'[':
            return None
        # Walk to matching ']' counting bracket depth
        depth = 0
        start = pos
        for i in range(pos, len(line)):
            ch = line[i:i+1]
            if ch == b'[':
                depth += 1
            elif ch == b']':
                depth -= 1
                if depth == 0:
                    return line[start:i+1]
        return None
    except Exception:
        return None


def _compute_crc(raw: bytes) -> str:
    return format(zlib.crc32(raw) & 0xFFFFFFFF, '08x')


class Reader(threading.Thread):
    """
    Reads one sensor from the biosignal-mock in a background thread.

    Args:
        sensor:      1 or 2
        host/port:   mock TCP address
        store:       durable store
        session_cb:  callable(new_session_id) called when session changes
        read_timeout: socket timeout in seconds
        reconnect_initial / reconnect_max: backoff params
    """

    def __init__(
        self,
        sensor: int,
        host: str,
        port: int,
        store: Store,
        session_cb=None,
        read_timeout: float = 2.0,
        reconnect_initial: float = 1.0,
        reconnect_max: float = 30.0,
    ) -> None:
        super().__init__(daemon=True, name=f"reader-s{sensor}")
        self.sensor = sensor
        self.host = host
        self.port = port
        self.store = store
        self.session_cb = session_cb
        self.read_timeout = read_timeout
        self.reconnect_initial = reconnect_initial
        self.reconnect_max = reconnect_max

        self._stop_event = threading.Event()
        self._current_session: Optional[str] = None
        self._next_index: int = 0

        # Pending samples/losses buffered before commit
        self._pending_samples: list[tuple[int, list[int]]] = []
        self._pending_losses: list[tuple[int, Optional[int], str]] = []
        self._last_commit_time: float = time.monotonic()

        # Restore state from store if there's an open session
        self._load_cursor()

    # ── Startup recovery ─────────────────────────────────────────────────

    def _load_cursor(self) -> None:
        session_id = self.store.get_open_session()
        if session_id:
            self._current_session = session_id
            next_idx, _ = self.store.get_cursor(session_id, self.sensor)
            self._next_index = next_idx
            logger.info("Reader s%d: resuming session %s from index %d",
                        self.sensor, session_id, next_idx)

    # ── Thread entry ─────────────────────────────────────────────────────

    def stop(self) -> None:
        self._stop_event.set()

    def run(self) -> None:
        backoff = self.reconnect_initial
        while not self._stop_event.is_set():
            try:
                self._connect_and_read()
                backoff = self.reconnect_initial  # reset on clean exit
            except Exception as exc:
                logger.warning("Reader s%d: connection error: %s – reconnecting in %.1fs",
                               self.sensor, exc, backoff)
                time.sleep(backoff)
                backoff = min(backoff * 2, self.reconnect_max)

    # ── Connection + read loop ───────────────────────────────────────────

    def _connect_and_read(self) -> None:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(self.read_timeout)
            sock.connect((self.host, self.port))
            logger.info("Reader s%d: connected to %s:%d", self.sensor, self.host, self.port)

            buf = b""
            crc_retries = 0

            while not self._stop_event.is_set():
                # Build request
                req: dict = {"cmd": "read", "sensor": self.sensor}
                if self._next_index > 0:
                    req["from_index"] = self._next_index
                sock.sendall((json.dumps(req) + "\n").encode())

                # Read one newline-delimited reply
                deadline = time.monotonic() + self.read_timeout
                while b"\n" not in buf:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError(f"Sensor {self.sensor}: reply timeout")
                    sock.settimeout(remaining)
                    chunk = sock.recv(65536)
                    if not chunk:
                        raise ConnectionError("Mock closed connection")
                    buf += chunk

                line, buf = buf.split(b"\n", 1)

                # ── CRC verification ─────────────────────────────────────
                raw_data_bytes = _extract_crc(line)
                try:
                    reply = json.loads(line.decode())
                except json.JSONDecodeError as exc:
                    logger.error("Reader s%d: JSON parse error: %s", self.sensor, exc)
                    crc_retries += 1
                    if crc_retries >= MAX_CRC_RETRIES:
                        self._record_loss(self._next_index, None, REASON_CORRUPT_REPLY)
                        self._flush()
                        crc_retries = 0
                    continue

                if raw_data_bytes is None:
                    logger.error("Reader s%d: could not extract data bytes for CRC", self.sensor)
                    crc_retries += 1
                    if crc_retries >= MAX_CRC_RETRIES:
                        self._record_loss(self._next_index, None, REASON_CORRUPT_REPLY)
                        self._flush()
                        crc_retries = 0
                    continue

                actual_crc = _compute_crc(raw_data_bytes)
                expected_crc = reply.get("crc32", "")
                if actual_crc != expected_crc:
                    logger.warning(
                        "Reader s%d: CRC mismatch at expected=%s actual=%s; retrying from %d",
                        self.sensor, expected_crc, actual_crc, self._next_index,
                    )
                    crc_retries += 1
                    if crc_retries >= MAX_CRC_RETRIES:
                        self._record_loss(self._next_index, None, REASON_CORRUPT_REPLY)
                        self._flush()
                        crc_retries = 0
                    continue

                crc_retries = 0

                # ── Session check ────────────────────────────────────────
                reply_session = reply.get("session_id")
                if reply_session != self._current_session:
                    self._handle_new_session(reply_session)

                # ── Gap detection ────────────────────────────────────────
                first_index = reply.get("first_index", 0)
                count = reply.get("count", 0)
                data = reply.get("data", [])
                lost_ranges = reply.get("lost", [])
                session_end = reply.get("session_end", False)

                if first_index > self._next_index:
                    gap_reason = REASON_NOT_DELIVERED
                    if lost_ranges:
                        gap_reason = lost_ranges[0].get("reason", REASON_NOT_DELIVERED)
                    self._record_loss(self._next_index, first_index - 1, gap_reason)
                    logger.warning(
                        "Reader s%d: gap %d..%d reason=%s",
                        self.sensor, self._next_index, first_index - 1, gap_reason,
                    )

                # ── Apply mock-reported losses ───────────────────────────
                for lr in lost_ranges:
                    self._record_loss(
                        lr["from_index"],
                        lr.get("to_index"),
                        lr.get("reason", REASON_BUFFER_OVERFLOW),
                    )

                # ── Store new samples ────────────────────────────────────
                for i, sample_vals in enumerate(data):
                    idx = first_index + i
                    if idx < self._next_index:
                        continue  # duplicate; skip
                    self._pending_samples.append((idx, sample_vals))
                    self._next_index = idx + 1

                # ── Commit policy (§6.2) ─────────────────────────────────
                now = time.monotonic()
                if (len(self._pending_samples) >= BATCH_SAMPLES or
                        (now - self._last_commit_time) >= 1.0):
                    self._flush()

                if session_end:
                    logger.info("Reader s%d: session_end received", self.sensor)
                    self._flush()
                    if self._current_session:
                        self.store.close_session(self._current_session)
                    break

    # ── Session transition ───────────────────────────────────────────────

    def _handle_new_session(self, new_session_id: str) -> None:
        logger.info(
            "Reader s%d: session change %s → %s",
            self.sensor, self._current_session, new_session_id,
        )
        if self._current_session:
            # Record unread range as loss (to_index unknown)
            from .losses import REASON_MOCK_RESTART
            self._record_loss(self._next_index, None, REASON_MOCK_RESTART)
            self._flush()
            self.store.close_session(self._current_session)

        self._current_session = new_session_id
        self._next_index = 0
        self.store.open_session(new_session_id)
        if self.session_cb:
            self.session_cb(new_session_id)

    # ── Helpers ──────────────────────────────────────────────────────────

    def _record_loss(
        self,
        from_idx: int,
        to_idx: Optional[int],
        reason: str,
    ) -> None:
        self._pending_losses.append((from_idx, to_idx, reason))

    def _flush(self) -> None:
        if not self._current_session:
            self._pending_samples.clear()
            self._pending_losses.clear()
            return
        if not self._pending_samples and not self._pending_losses:
            self._last_commit_time = time.monotonic()
            return
        try:
            self.store.write_batch(
                session_id=self._current_session,
                sensor=self.sensor,
                samples=self._pending_samples,
                losses=self._pending_losses,
                new_next_index=self._next_index,
            )
        except Exception as exc:
            # Disk-full or other write error — record loss, keep running
            logger.error("Reader s%d: store write failed: %s", self.sensor, exc)
            from .losses import REASON_DISK_FULL
            if self._pending_samples:
                first = self._pending_samples[0][0]
                last = self._pending_samples[-1][0]
                # Can't write to store either, just log
                logger.error(
                    "Reader s%d: DISK FULL – losing samples %d..%d",
                    self.sensor, first, last,
                )
        finally:
            self._pending_samples.clear()
            self._pending_losses.clear()
            self._last_commit_time = time.monotonic()
