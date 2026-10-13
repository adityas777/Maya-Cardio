"""
processing.py — SHARED window + peak + heart-rate logic (§4).

Used by BOTH the on-device offline processor AND the cloud API.
NEVER import device-only or cloud-only modules here.

Rounding: 1 decimal place, half-up (assumption A5).
    Python's built-in round() uses banker's rounding (half-to-even).
    We use _round1() which always rounds 0.5 up, matching A5.
"""

from __future__ import annotations

import math
from typing import Optional


# ---------------------------------------------------------------------------
# Rounding helper (A5: half-up)
# ---------------------------------------------------------------------------

def _round1(x: float) -> float:
    """Round to 1 decimal place using half-up (not banker's rounding)."""
    return math.floor(x * 10 + 0.5) / 10


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

class SampleStore:
    """
    Abstract interface the processor needs from the store.
    Both on-device (SQLite) and cloud stores implement this duck-typed interface.
    """

    def get_samples(
        self,
        session_id: str,
        sensor: int,
        from_index: int,
        to_index: int,
    ) -> list[tuple[int, list[int]]]:
        """
        Return list of (index, values) for *available* (not-lost) samples
        with from_index <= index <= to_index, ordered by index.
        """
        raise NotImplementedError

    def get_losses(
        self,
        session_id: str,
        sensor: int,
        from_index: int,
        to_index: int,
    ) -> list[tuple[int, Optional[int], str]]:
        """
        Return list of (from_index, to_index_or_None, reason) for loss records
        that overlap [from_index, to_index].
        """
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Peak detector (§4.3)
# ---------------------------------------------------------------------------

class PeakDetector:
    """
    Stateful, left-to-right peak detector for sensor-1 channel-2 (value[1]).

    Rules (§4.3):
    1. The 800 samples before i AND sample i+1 must be available.
    2. v[i] > v[i-1] AND v[i] > v[i+1]  (strict)
    3. v[i] > 0.5 × max(v[i-800 .. i-1])
    4. i − previous_peak ≥ 100  (if a previous peak exists)

    A candidate that fails rule 4 is simply skipped; the earlier peak stays.
    """

    def __init__(self) -> None:
        # rolling buffer: index → value (only sensor 1 ch2)
        self._buf: dict[int, int] = {}
        self._prev_peak: Optional[int] = None
        # confirmed peaks: sorted list of indexes
        self._peaks: list[int] = []
        # set of candidate indexes already checked, to avoid re-checking
        self._checked: set[int] = set()

    def feed(self, index: int, value: int) -> None:
        """Feed one new available sample. Call in ascending index order."""
        self._buf[index] = value
        # Try to confirm the candidate at index-1 now that index (i+1) is known
        self._try_confirm(index - 1)
        # Prune buffer: keep only the most recent 1200 samples
        if len(self._buf) > 1200:
            oldest = min(self._buf)
            del self._buf[oldest]

    def feed_unavailable(self, index: int) -> None:
        """Signal that this index is lost/missing (so it cannot be a peak)."""
        # Remove from buffer so rule-1 check fails if it was there
        self._buf.pop(index, None)

    def _try_confirm(self, i: int) -> None:
        """Check whether index i satisfies all peak rules."""
        if i in self._checked:
            return  # already evaluated; do not re-check
        self._checked.add(i)
        if i not in self._buf:
            return

        vi = self._buf[i]

        # Rule 1a: i+1 must be available
        if (i + 1) not in self._buf:
            return

        # Rule 1b: the 800 samples before i must ALL be available
        start = i - 800
        if start < 0:
            return  # not enough history
        for j in range(start, i):
            if j not in self._buf:
                return

        # Rule 2: strict local maximum
        if not (vi > self._buf.get(i - 1, vi) and vi > self._buf[i + 1]):
            return

        # Rule 3: > 0.5 × max of previous 800
        history = [self._buf[j] for j in range(start, i)]
        if not history:
            return
        if vi <= 0.5 * max(history):
            return

        # Rule 4: minimum gap from previous peak
        if self._prev_peak is not None and (i - self._prev_peak) < 100:
            return  # fails gap rule; previous peak stays

        # All rules pass → it's a peak
        self._peaks.append(i)
        self._prev_peak = i

    def peaks_in_range(self, from_idx: int, to_idx: int) -> list[int]:
        """Return confirmed peak indexes in [from_idx, to_idx]."""
        return [p for p in self._peaks if from_idx <= p <= to_idx]

    @property
    def previous_peak(self) -> Optional[int]:
        return self._prev_peak


# ---------------------------------------------------------------------------
# Per-window statistics
# ---------------------------------------------------------------------------

def _channel_stats(
    values: list[int],
) -> Optional[dict]:
    """Return {mean, min, max} or None if values is empty."""
    if not values:
        return None
    return {
        "mean": _round1(sum(values) / len(values)),
        "min": min(values),
        "max": max(values),
    }


# ---------------------------------------------------------------------------
# Main window processor (§4.1, §4.2, §4.4)
# ---------------------------------------------------------------------------

SAMPLES_PER_WINDOW = 400  # 1 second at 400 Hz


def compute_window(
    session_id: str,
    window: int,
    mode: str,          # "online" or "offline"
    store: SampleStore,
    peak_detector: PeakDetector,
    result_time_ms: int,
) -> dict:
    """
    Compute result for window w.  Modifies peak_detector in place.

    Returns a dict matching the §10 JSON schema.
    """
    w = window
    w_start = SAMPLES_PER_WINDOW * w          # inclusive
    w_end   = SAMPLES_PER_WINDOW * (w + 1) - 1  # inclusive

    # ── Sensor 1 samples in [w_start, w_end] ──────────────────────────────
    s1_rows = store.get_samples(session_id, 1, w_start, w_end)
    s1_avail = {idx: vals for idx, vals in s1_rows}
    missing_1 = SAMPLES_PER_WINDOW - len(s1_avail)

    # ── Sensor 2 samples ───────────────────────────────────────────────────
    s2_rows = store.get_samples(session_id, 2, w_start, w_end)
    s2_avail = {idx: vals for idx, vals in s2_rows}
    missing_2 = SAMPLES_PER_WINDOW - len(s2_avail)

    # ── Feed peak detector (sensor 1, ch2 = value[1]) ─────────────────────
    # Feed every index in window in order, marking unavailable ones.
    # Also feed w_end+1 so a peak exactly on w_end is confirmed inside
    # this window (peak confirmation requires the next sample to be known).
    s1_next_row = store.get_samples(session_id, 1, w_end + 1, w_end + 1)
    s1_next = {idx: vals for idx, vals in s1_next_row}
    for idx in range(w_start, w_end + 2):
        if idx in s1_avail:
            peak_detector.feed(idx, s1_avail[idx][1])
        elif idx == w_end + 1 and idx in s1_next:
            peak_detector.feed(idx, s1_next[idx][1])
        else:
            peak_detector.feed_unavailable(idx)

    # ── Channel stats ──────────────────────────────────────────────────────
    def _sensor_stats(avail: dict, n_ch: int) -> Optional[dict]:
        if not avail:
            return None
        result = {}
        for ch_idx in range(n_ch):
            key = f"ch{ch_idx + 1}"
            ch_vals = [vals[ch_idx] for vals in avail.values()]
            result[key] = _channel_stats(ch_vals)
        return result

    sensor1 = _sensor_stats(s1_avail, n_ch=3)
    sensor2 = _sensor_stats(s2_avail, n_ch=2)

    # ── Magnitude (sensor 2, §4.2) ─────────────────────────────────────────
    magnitude: Optional[float] = None
    if s2_avail:
        mags = [math.sqrt(vals[0] ** 2 + vals[1] ** 2) for vals in s2_avail.values()]
        magnitude = _round1(sum(mags) / len(mags))

    # ── Heart rate (§4.4) ─────────────────────────────────────────────────
    heart_rate = _compute_heart_rate(
        session_id=session_id,
        window=w,
        store=store,
        peak_detector=peak_detector,
    )

    return {
        "session_id": session_id,
        "window": w,
        "mode": mode,
        "missing_1": missing_1,
        "missing_2": missing_2,
        "complete": (missing_1 == 0 and missing_2 == 0),
        "sensor1": sensor1,
        "sensor2": sensor2,
        "magnitude": magnitude,
        "heart_rate": heart_rate,
        "result_time_ms": result_time_ms,
    }


def _compute_heart_rate(
    session_id: str,
    window: int,
    store: SampleStore,
    peak_detector: PeakDetector,
) -> Optional[float]:
    """
    §4.4 heart-rate rule.

    - null if window < 4
    - Range: samples 400(w-4) .. 400(w+1)-1  (last 5 seconds)
    - null if ANY sensor-1 sample in that range is missing
    - Need ≥ 2 peaks in that range
    - HR = 24000 / mean(gap_samples), rounded to 1 decimal
    """
    w = window
    if w < 4:
        return None

    hr_start = SAMPLES_PER_WINDOW * (w - 4)
    hr_end   = SAMPLES_PER_WINDOW * (w + 1) - 1

    # Check ALL sensor-1 samples available in [hr_start, hr_end]
    s1_rows = store.get_samples(session_id, 1, hr_start, hr_end)
    n_expected = hr_end - hr_start + 1
    if len(s1_rows) != n_expected:
        return None

    # Get peaks in range
    peaks = peak_detector.peaks_in_range(hr_start, hr_end)
    if len(peaks) < 2:
        return None

    gaps = [peaks[k + 1] - peaks[k] for k in range(len(peaks) - 1)]
    mean_gap = sum(gaps) / len(gaps)
    return _round1(24000.0 / mean_gap)
