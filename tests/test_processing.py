"""
test_processing.py — Unit tests for processing.py (§4.5).

Tests:
  1. Ramp values: every sample matches formula exactly.
  2. Peak rule: verify specific known cases from spec.
  3. Heart-rate rule: sanity with signal-mode schedule.
  4. Rounding: half-up, not banker's.
  5. Cross-check with reference.py for 50 ramp windows.
"""

from __future__ import annotations

import math
import sys
import os
import pytest

# Make src importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from pipeline.processing import (
    PeakDetector,
    compute_window,
    _round1,
    SAMPLES_PER_WINDOW,
    SampleStore,
)
from tests.reference import ReferenceProcessor, ramp_s1, ramp_s2


# ---------------------------------------------------------------------------
# Minimal in-memory store for tests
# ---------------------------------------------------------------------------

class MemoryStore(SampleStore):
    def __init__(self):
        self._s: dict[tuple, list[int]] = {}  # (session, sensor, idx) → vals
        self._losses: list[tuple] = []

    def add(self, session_id, sensor, idx, vals):
        self._s[(session_id, sensor, idx)] = vals

    def add_loss(self, session_id, sensor, from_idx, to_idx, reason):
        self._losses.append((session_id, sensor, from_idx, to_idx, reason))

    def get_samples(self, session_id, sensor, from_index, to_index):
        result = []
        for idx in range(from_index, to_index + 1):
            key = (session_id, sensor, idx)
            if key in self._s:
                result.append((idx, self._s[key]))
        return result

    def get_losses(self, session_id, sensor, from_index, to_index):
        result = []
        for (sid, sen, fi, ti, r) in self._losses:
            if sid == session_id and sen == sensor:
                if fi <= to_index and (ti is None or ti >= from_index):
                    result.append((fi, ti, r))
        return result


# ---------------------------------------------------------------------------
# 1. Rounding
# ---------------------------------------------------------------------------

class TestRounding:
    def test_half_up(self):
        # 0.25 → 0.3 (half-up), not 0.2 (banker's)
        assert _round1(0.25) == 0.3

    def test_normal(self):
        assert _round1(1.23) == 1.2
        assert _round1(1.25) == 1.3
        assert _round1(1.35) == 1.4
        assert _round1(1.45) == 1.5

    def test_negative(self):
        assert _round1(-1.25) == -1.2  # half-up toward +inf


# ---------------------------------------------------------------------------
# 2. Ramp values
# ---------------------------------------------------------------------------

class TestRampValues:
    def test_sensor1_formula(self):
        for i in range(0, 1000, 100):
            for c in range(3):
                expected = (3 * i + c) % 8_388_608
                assert ramp_s1(i, c) == expected

    def test_sensor2_formula(self):
        for i in range(0, 1000, 100):
            for c in range(2):
                expected = (2 * i + c) % 8_388_608
                assert ramp_s2(i, c) == expected


# ---------------------------------------------------------------------------
# 3. Window stats on ramp data
# ---------------------------------------------------------------------------

class TestWindowStats:
    def _make_ramp_store(self, n_windows: int = 5) -> MemoryStore:
        store = MemoryStore()
        total = n_windows * SAMPLES_PER_WINDOW
        for idx in range(total):
            store.add("s1", 1, idx, [ramp_s1(idx, c) for c in range(3)])
            store.add("s1", 2, idx, [ramp_s2(idx, c) for c in range(2)])
        return store

    def test_window0_complete(self):
        store = self._make_ramp_store(2)
        pd = PeakDetector()
        result = compute_window("s1", 0, "offline", store, pd, 0)
        assert result["missing_1"] == 0
        assert result["missing_2"] == 0
        assert result["complete"] is True

    def test_window0_sensor1_stats(self):
        store = self._make_ramp_store(2)
        pd = PeakDetector()
        result = compute_window("s1", 0, "offline", store, pd, 0)
        s1 = result["sensor1"]
        # ch1 for window 0: values ramp_s1(i, 0) for i in [0,399]
        ch1_vals = [ramp_s1(i, 0) for i in range(400)]
        expected_mean = _round1(sum(ch1_vals) / len(ch1_vals))
        assert s1["ch1"]["mean"] == expected_mean
        assert s1["ch1"]["min"] == min(ch1_vals)
        assert s1["ch1"]["max"] == max(ch1_vals)

    def test_magnitude_ramp_window0(self):
        store = self._make_ramp_store(2)
        pd = PeakDetector()
        result = compute_window("s1", 0, "offline", store, pd, 0)
        mags = [math.sqrt(ramp_s2(i, 0)**2 + ramp_s2(i, 1)**2) for i in range(400)]
        expected = _round1(sum(mags) / len(mags))
        assert result["magnitude"] == expected

    def test_missing_counts(self):
        store = MemoryStore()
        # Only add 300 of 400 sensor-1 samples in window 0
        for idx in range(300):
            store.add("s1", 1, idx, [ramp_s1(idx, c) for c in range(3)])
        for idx in range(400):
            store.add("s1", 2, idx, [ramp_s2(idx, c) for c in range(2)])
        pd = PeakDetector()
        result = compute_window("s1", 0, "offline", store, pd, 0)
        assert result["missing_1"] == 100
        assert result["missing_2"] == 0
        assert result["complete"] is False

    def test_null_magnitude_when_no_sensor2(self):
        store = MemoryStore()
        for idx in range(500):
            store.add("s1", 1, idx, [ramp_s1(idx, c) for c in range(3)])
        pd = PeakDetector()
        result = compute_window("s1", 0, "offline", store, pd, 0)
        assert result["magnitude"] is None


# ---------------------------------------------------------------------------
# 4. Peak rule
# ---------------------------------------------------------------------------

class TestPeakRule:
    def _feed_flat(self, pd: PeakDetector, start: int, count: int, value: int = 0):
        for i in range(start, start + count):
            pd.feed(i, value)

    def test_single_peak_above_threshold(self):
        pd = PeakDetector()
        # Feed 801 samples: first 800 = 100, then a peak at 800, then 801 = 50
        for i in range(800):
            pd.feed(i, 100)
        pd.feed(800, 9000)  # peak candidate (> 0.5*100=50; > neighbours)
        pd.feed(801, 50)    # confirms peak at 800
        peaks = pd.peaks_in_range(0, 900)
        assert 800 in peaks

    def test_peak_below_50pct_max_rejected(self):
        pd = PeakDetector()
        for i in range(800):
            pd.feed(i, 1000)
        pd.feed(800, 400)   # 400 <= 0.5*1000 = 500 → rejected
        pd.feed(801, 0)
        peaks = pd.peaks_in_range(0, 900)
        assert 800 not in peaks

    def test_100_sample_gap_rule(self):
        pd = PeakDetector()
        # Create two peaks 50 samples apart — second should be rejected
        for i in range(800):
            pd.feed(i, 100)
        pd.feed(800, 9000)
        pd.feed(801, 50)    # confirms peak at 800
        for i in range(802, 850):
            pd.feed(i, 100)
        pd.feed(850, 9000)
        pd.feed(851, 50)    # gap = 50 < 100 → rejected
        peaks = pd.peaks_in_range(0, 900)
        assert 850 not in peaks
        assert 800 in peaks


# ---------------------------------------------------------------------------
# 5. Cross-check with reference.py (ramp, 5 windows)
# ---------------------------------------------------------------------------

class TestCrossCheck:
    def test_ramp_5_windows_match_reference(self):
        N = 5
        total = (N + 1) * SAMPLES_PER_WINDOW + 10  # extra for peak confirmation

        s1 = {i: [ramp_s1(i, c) for c in range(3)] for i in range(total)}
        s2 = {i: [ramp_s2(i, c) for c in range(2)] for i in range(total)}

        store = MemoryStore()
        for i in range(total):
            store.add("sess", 1, i, s1[i])
            store.add("sess", 2, i, s2[i])

        ref = ReferenceProcessor()
        pd  = PeakDetector()

        for w in range(N):
            result = compute_window("sess", w, "offline", store, pd, 0)
            # Reference processor is called window-by-window with full history
            # (it is stateful — builds its own peak detector across calls)
            ref_out = ref.feed_samples(s1, s2, w, "offline", "sess", 0)

            assert result["missing_1"] == ref_out["missing_1"], f"w={w} missing_1"
            assert result["missing_2"] == ref_out["missing_2"], f"w={w} missing_2"
            assert result["complete"]  == ref_out["complete"],   f"w={w} complete"
            assert result["magnitude"] == ref_out["magnitude"],  f"w={w} magnitude"
            assert result["heart_rate"] == ref_out["heart_rate"], f"w={w} heart_rate"


# ---------------------------------------------------------------------------
# 6. Heart-rate null below window 4
# ---------------------------------------------------------------------------

class TestHeartRate:
    def test_null_for_early_windows(self):
        store = MemoryStore()
        total = 10 * SAMPLES_PER_WINDOW
        for idx in range(total):
            store.add("s", 1, idx, [100, idx % 1000, 100])
            store.add("s", 2, idx, [100, 100])
        pd = PeakDetector()
        for w in range(4):
            result = compute_window("s", w, "offline", store, pd, 0)
            assert result["heart_rate"] is None, f"w={w} should be null"


# ---------------------------------------------------------------------------
# 7. Regression: boundary peak at last sample of window (peak_boundary_fix)
# ---------------------------------------------------------------------------

class TestPeakBoundaryFix:
    """
    Regression test for peak_boundary_fix.patch.

    Signal: sensor-1 ch2 = 100 everywhere, except:
      - sample 1000: ch2 = 1000  (peak in window 2, last sample = 1199... wait,
        window 2 = samples 800-1199. sample 1000 is index 1000, inside window 2.
        But to hit the boundary we place peaks at 1000 and 1999.)
      - sample 1999: ch2 = 1000  (last sample of window 4 = samples 1600-1999)

    With the fix, the peak at 1999 is confirmed when w_end+1 = 2000 is fed.
    HR window 4 looks back over samples 0-1999 (windows 0-4).
    Peaks at 1000 and 1999 → gap = 999 → HR = 24000/999 ≈ 24.0.
    """

    def _make_store(self) -> MemoryStore:
        store = MemoryStore()
        total = 6 * SAMPLES_PER_WINDOW  # windows 0-5
        for idx in range(total):
            # sensor 1: ch2 is the ECG channel
            ch2 = 1000 if idx in (1000, 1999) else 100
            store.add("fix", 1, idx, [50, ch2, 150])
            # sensor 2: minimal valid data
            store.add("fix", 2, idx, [1, 1])
        return store

    def test_window4_heart_rate_24(self):
        store = self._make_store()
        pd = PeakDetector()
        result = None
        for w in range(5):
            result = compute_window("fix", w, "offline", store, pd, 0)
        # window 4: peaks at 1000 and 1999, gap = 999
        # HR = round1(24000 / 999) = round1(24.024...) = 24.0
        assert result["heart_rate"] == 24.0, (
            f"Expected 24.0, got {result['heart_rate']}"
        )

    def test_no_duplicate_peaks(self):
        store = self._make_store()
        pd = PeakDetector()
        for w in range(5):
            compute_window("fix", w, "offline", store, pd, 0)
        # Each boundary index must appear at most once in the confirmed peaks
        peaks = pd._peaks
        assert len(peaks) == len(set(peaks)), f"Duplicate peaks: {peaks}"

    def test_peak_buffer_size_bounded(self):
        """Buffer must never exceed 1200 entries during a long run."""
        store = MemoryStore()
        total = 13 * SAMPLES_PER_WINDOW
        for idx in range(total):
            store.add("buf", 1, idx, [50, 100, 150])
            store.add("buf", 2, idx, [1, 1])
        pd = PeakDetector()
        for w in range(13):
            compute_window("buf", w, "offline", store, pd, 0)
            assert len(pd._buf) <= 1200, (
                f"Buffer size {len(pd._buf)} > 1200 after window {w}"
            )


# ---------------------------------------------------------------------------
# 8. Regression: steady 75 bpm signal gives correct HR for windows 10-12
# ---------------------------------------------------------------------------

class TestSteady75Bpm:
    """
    75 bpm → samples_per_beat = 400*60/75 = 320 samples.
    Peaks every 320 samples starting at index 800:
      800, 1120, 1440, 1760, 2080, ...

    We need 60 seconds = 24000 samples (windows 0-59).
    HR is first computed at window 4 (samples 0-1999).
    We assert HR == 75.0 for windows 10 through 12.
    """

    PEAK_START = 800
    PEAK_INTERVAL = 320  # samples per beat at 75 bpm
    TOTAL_WINDOWS = 13
    BASELINE = 100
    PEAK_VAL = 9000

    def _make_store(self) -> MemoryStore:
        store = MemoryStore()
        total = self.TOTAL_WINDOWS * SAMPLES_PER_WINDOW
        peak_set = set(
            range(self.PEAK_START, total, self.PEAK_INTERVAL)
        )
        for idx in range(total):
            ch2 = self.PEAK_VAL if idx in peak_set else self.BASELINE
            store.add("bpm75", 1, idx, [50, ch2, 150])
            store.add("bpm75", 2, idx, [1, 1])
        return store

    def test_heart_rate_75_for_windows_10_to_12(self):
        store = self._make_store()
        pd = PeakDetector()
        results = {}
        for w in range(self.TOTAL_WINDOWS):
            results[w] = compute_window("bpm75", w, "offline", store, pd, 0)
        for w in range(10, 13):
            hr = results[w]["heart_rate"]
            assert hr == 75.0, f"window {w}: expected 75.0, got {hr}"

    def test_no_duplicate_peaks_75bpm(self):
        store = self._make_store()
        pd = PeakDetector()
        for w in range(self.TOTAL_WINDOWS):
            compute_window("bpm75", w, "offline", store, pd, 0)
        peaks = pd._peaks
        assert len(peaks) == len(set(peaks)), f"Duplicate peaks: {peaks}"

    def test_peak_buffer_at_most_1200_75bpm(self):
        store = self._make_store()
        pd = PeakDetector()
        for w in range(self.TOTAL_WINDOWS):
            compute_window("bpm75", w, "offline", store, pd, 0)
            assert len(pd._buf) <= 1200, (
                f"Buffer {len(pd._buf)} > 1200 after window {w}"
            )

