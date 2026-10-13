"""
reference.py — Independent reference implementation of §4 (§19 step 3).

This is a SECOND, INDEPENDENT implementation of the window/peak/HR logic.
It is NOT used in production. Its only job is to validate processing.py.

Deliberately written with different internal structure to catch systematic bugs.
"""

from __future__ import annotations

import math
from typing import Optional

SAMPLES_PER_WINDOW = 400


def _half_up_round(x: float, decimals: int = 1) -> float:
    """Half-up rounding (A5)."""
    factor = 10 ** decimals
    return math.floor(x * factor + 0.5) / factor


def ramp_s1(index: int, channel: int) -> int:
    """§5.1 ramp formula sensor 1. channel in {0,1,2}."""
    return (3 * index + channel) % 8_388_608


def ramp_s2(index: int, channel: int) -> int:
    """§5.1 ramp formula sensor 2. channel in {0,1}."""
    return (2 * index + channel) % 8_388_608


class ReferenceProcessor:
    """
    Reference processor: given a dict of available samples, computes one window.
    Keeps state for peak detection across the whole session.
    """

    def __init__(self) -> None:
        # Index → sensor1 ch2 value (value[1])
        self._s1ch2: dict[int, int] = {}
        # Confirmed peaks (in order)
        self._peaks: list[int] = []
        self._prev_peak: Optional[int] = None

    def feed_samples(
        self,
        s1_samples: dict[int, list[int]],   # index → [ch1, ch2, ch3]
        s2_samples: dict[int, list[int]],   # index → [ch1, ch2]
        window: int,
        mode: str,
        session_id: str = "test",
        result_time_ms: int = 0,
    ) -> dict:
        """Feed all samples for a window, return the result dict."""
        w_start = SAMPLES_PER_WINDOW * window
        w_end   = SAMPLES_PER_WINDOW * (window + 1) - 1

        # Feed sensor 1 ch2 into peak buffer
        for idx in range(w_start, w_end + 1):
            if idx in s1_samples:
                self._s1ch2[idx] = s1_samples[idx][1]
            # If not available, don't add to _s1ch2

        # Try to confirm peak at each index in window
        for idx in range(w_start, w_end + 1):
            self._try_peak(idx)

        # Stats
        missing_1 = SAMPLES_PER_WINDOW - len({k for k in s1_samples if w_start <= k <= w_end})
        missing_2 = SAMPLES_PER_WINDOW - len({k for k in s2_samples if w_start <= k <= w_end})

        sensor1 = self._channel_stats({k: v for k, v in s1_samples.items() if w_start <= k <= w_end}, 3)
        sensor2 = self._channel_stats({k: v for k, v in s2_samples.items() if w_start <= k <= w_end}, 2)

        magnitude = None
        w_s2 = {k: v for k, v in s2_samples.items() if w_start <= k <= w_end}
        if w_s2:
            mags = [math.sqrt(v[0]**2 + v[1]**2) for v in w_s2.values()]
            magnitude = _half_up_round(sum(mags) / len(mags))

        heart_rate = self._compute_hr(window, s1_samples)

        return {
            "session_id": session_id,
            "window": window,
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

    def _channel_stats(self, avail: dict, n_ch: int) -> Optional[dict]:
        if not avail:
            return None
        result = {}
        for ch in range(n_ch):
            key = f"ch{ch+1}"
            vals = [v[ch] for v in avail.values()]
            result[key] = {
                "mean": _half_up_round(sum(vals) / len(vals)),
                "min": min(vals),
                "max": max(vals),
            }
        return result

    def _try_peak(self, i: int) -> None:
        if i not in self._s1ch2:
            return
        # Need i+1 available
        if (i + 1) not in self._s1ch2:
            return
        # Need 800 prior samples all available
        start = i - 800
        if start < 0:
            return
        if any(j not in self._s1ch2 for j in range(start, i)):
            return
        vi = self._s1ch2[i]
        vi_prev = self._s1ch2.get(i - 1, vi)
        vi_next = self._s1ch2[i + 1]
        if not (vi > vi_prev and vi > vi_next):
            return
        history = [self._s1ch2[j] for j in range(start, i)]
        if vi <= 0.5 * max(history):
            return
        if self._prev_peak is not None and (i - self._prev_peak) < 100:
            return
        self._peaks.append(i)
        self._prev_peak = i

    def _compute_hr(self, window: int, s1_samples: dict) -> Optional[float]:
        if window < 4:
            return None
        hr_start = SAMPLES_PER_WINDOW * (window - 4)
        hr_end   = SAMPLES_PER_WINDOW * (window + 1) - 1
        n_expected = hr_end - hr_start + 1
        n_available = sum(1 for k in s1_samples if hr_start <= k <= hr_end)
        if n_available != n_expected:
            return None
        peaks_in_range = [p for p in self._peaks if hr_start <= p <= hr_end]
        if len(peaks_in_range) < 2:
            return None
        gaps = [peaks_in_range[k+1] - peaks_in_range[k] for k in range(len(peaks_in_range)-1)]
        mean_gap = sum(gaps) / len(gaps)
        return _half_up_round(24000.0 / mean_gap)
