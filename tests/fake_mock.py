"""
fake_mock.py — Test double of biosignal-mock (§0, §5.2).

Implements the exact same newline-delimited JSON protocol as the real mock,
including:
  - signal mode (ECG-like ch2 with 60/90/72 bpm cycle)
  - ramp mode  (deterministic formula for integrity checking)
  - session_id, first_index, count, lost, session_end, crc32
  - CRC computed on the raw data bytes as they appear in the reply

Usage:
    python -m tests.fake_mock --mode signal --seed 7 --duration 3600 --port 7100
    python -m tests.fake_mock --mode ramp   --seed 0 --duration 600  --port 7100

WARNING: This is NOT the official biosignal-mock. It is used for development
and testing only when the official binary is unavailable. The submission notes
this clearly.
"""

from __future__ import annotations

import argparse
import json
import math
import socket
import threading
import time
import zlib
import random
import logging
from typing import Optional

logger = logging.getLogger(__name__)

SAMPLE_RATE = 400         # samples per second
MAX_24BIT   = 8388608     # 2^23; ramp modulus

# ── Heart-rate schedule for signal mode ──────────────────────────────────────
# Minutes 0-2: 60 bpm, 2-4: 90 bpm, 4-6: 72 bpm, repeating
_HR_SCHEDULE = [
    (  0 * 60 * SAMPLE_RATE,  2 * 60 * SAMPLE_RATE, 60),
    (  2 * 60 * SAMPLE_RATE,  4 * 60 * SAMPLE_RATE, 90),
    (  4 * 60 * SAMPLE_RATE,  6 * 60 * SAMPLE_RATE, 72),
]
_HR_PERIOD = 6 * 60 * SAMPLE_RATE


def _hr_at(index: int) -> int:
    """Return the target heart rate (bpm) for a given sample index."""
    pos = index % _HR_PERIOD
    for start, end, bpm in _HR_SCHEDULE:
        if start <= pos < end:
            return bpm
    return 60  # fallback


def _ecg_pulse(phase: float, amplitude: int = 1_000_000) -> float:
    """
    Simple ECG-like pulse: a narrow Gaussian peak at phase=0.
    phase in [0, 1).
    """
    return amplitude * math.exp(-((phase - 0.0) ** 2) / (2 * (0.02 ** 2)))


def _generate_signal_sample(index: int, sensor: int, rng: random.Random) -> list[int]:
    """Generate one sample in signal mode."""
    if sensor == 1:
        hr = _hr_at(index)
        samples_per_beat = SAMPLE_RATE * 60 // hr
        phase = (index % samples_per_beat) / samples_per_beat
        ch2 = int(_ecg_pulse(phase))
        ch1 = int(ch2 * 0.6)
        ch3 = int(ch2 * 1.3)
        # Add mild noise
        noise = lambda: rng.randint(-10, 10)
        return [ch1 + noise(), ch2 + noise(), ch3 + noise()]
    else:
        # Sensor 2: slowly varying around 400000, 150000
        ch1 = 400_000 + int(5000 * math.sin(index / (SAMPLE_RATE * 10)))
        ch2 = 150_000 + int(2000 * math.cos(index / (SAMPLE_RATE * 7)))
        return [ch1, ch2]


def _generate_ramp_sample(index: int, sensor: int) -> list[int]:
    """§5.1 ramp formula."""
    if sensor == 1:
        return [(3 * index + c) % MAX_24BIT for c in range(3)]
    else:
        return [(2 * index + c) % MAX_24BIT for c in range(2)]


def _make_reply(
    session_id: str,
    sensor: int,
    first_index: int,
    data: list[list[int]],
    lost: list[dict],
    session_end: bool,
) -> bytes:
    """Serialize reply with CRC computed on raw data bytes."""
    # Serialize data exactly as it will appear in the JSON
    data_str = json.dumps(data, separators=(',', ':'))
    data_bytes = data_str.encode()
    crc = format(zlib.crc32(data_bytes) & 0xFFFFFFFF, '08x')

    reply = {
        "session_id": session_id,
        "sensor": sensor,
        "first_index": first_index,
        "count": len(data),
        "lost": lost,
        "session_end": session_end,
        "crc32": crc,
        "data": data,  # will be serialized by json.dumps below
    }
    # We need to ensure the data field in the final JSON matches data_bytes exactly.
    # Build the JSON manually to guarantee this:
    body = (
        '{"session_id":' + json.dumps(session_id) +
        ',"sensor":' + str(sensor) +
        ',"first_index":' + str(first_index) +
        ',"count":' + str(len(data)) +
        ',"lost":' + json.dumps(lost, separators=(',', ':')) +
        ',"session_end":' + ('true' if session_end else 'false') +
        ',"crc32":"' + crc + '"' +
        ',"data":' + data_str +
        '}'
    )
    return (body + '\n').encode()


class FakeMockSession:
    """Manages one session of the mock."""

    def __init__(self, mode: str, seed: int, duration: int, rng: random.Random) -> None:
        self.mode = mode
        self.duration = duration
        self.rng = rng
        self.session_id = format(rng.randint(0, 0xFFFFFFFF), '08x')
        # Per-sensor state
        self._next: dict[int, int] = {1: 0, 2: 0}
        self._total_samples = duration * SAMPLE_RATE
        # Buffer: last 10 seconds per sensor
        self._buffer_size = 10 * SAMPLE_RATE

    def read(self, sensor: int, from_index: Optional[int] = None) -> bytes:
        cur = self._next[sensor]
        requested = from_index if from_index is not None else cur
        lost = []

        # Check if requested index is within buffer
        oldest_available = max(0, cur - self._buffer_size)
        if requested < oldest_available:
            lost_end = oldest_available - 1
            lost.append({
                "from_index": requested,
                "to_index": lost_end,
                "reason": "buffer_overflow",
            })
            requested = oldest_available

        # Generate up to 1 second of data
        max_samples = SAMPLE_RATE
        end = min(requested + max_samples, self._total_samples)
        data = []
        for i in range(requested, end):
            if self.mode == "ramp":
                sample = _generate_ramp_sample(i, sensor)
            else:
                sample = _generate_signal_sample(i, sensor, self.rng)
            data.append(sample)

        session_end = (end >= self._total_samples)
        first_index = requested

        # Advance cursor
        if data:
            self._next[sensor] = requested + len(data)

        return _make_reply(
            session_id=self.session_id,
            sensor=sensor,
            first_index=first_index,
            data=data,
            lost=lost,
            session_end=session_end,
        )


class FakeMockServer:
    """
    TCP server implementing the biosignal-mock protocol.
    Only one client at a time (per spec).
    """

    def __init__(self, host: str, port: int, mode: str, seed: int, duration: int) -> None:
        self.host = host
        self.port = port
        self.mode = mode
        self.seed = seed
        self.duration = duration
        self._rng = random.Random(seed)
        self._session = FakeMockSession(mode, seed, duration, self._rng)
        self._lock = threading.Lock()

    def serve_forever(self) -> None:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as srv:
            srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            srv.bind((self.host, self.port))
            srv.listen(1)
            logger.info(
                "FakeMock listening on %s:%d mode=%s seed=%d duration=%d",
                self.host, self.port, self.mode, self.seed, self.duration,
            )
            while True:
                conn, addr = srv.accept()
                logger.info("FakeMock: connection from %s", addr)
                threading.Thread(
                    target=self._handle,
                    args=(conn,),
                    daemon=True,
                ).start()

    def _handle(self, conn: socket.socket) -> None:
        try:
            buf = b""
            while True:
                chunk = conn.recv(4096)
                if not chunk:
                    break
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    try:
                        req = json.loads(line.decode())
                    except json.JSONDecodeError:
                        continue
                    if req.get("cmd") != "read":
                        continue
                    sensor = req.get("sensor")
                    from_index = req.get("from_index")
                    with self._lock:
                        reply = self._session.read(sensor, from_index)
                    conn.sendall(reply)
        except Exception as exc:
            logger.debug("FakeMock: client error: %s", exc)
        finally:
            conn.close()


# ── CLI entry ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(__file__).split("tests")[0])
    logging.basicConfig(level=logging.INFO)

    parser = argparse.ArgumentParser(description="FakeMock test double")
    parser.add_argument("--mode", default="signal", choices=["signal", "ramp"])
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--duration", type=int, default=3600)
    parser.add_argument("--port", type=int, default=7100)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()

    server = FakeMockServer(args.host, args.port, args.mode, args.seed, args.duration)
    server.serve_forever()
