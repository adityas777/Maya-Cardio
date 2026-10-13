# Design Note: MayaCardio Biosignal Pipeline

**"Never Lose a Heartbeat"** · MayaCardio Software Systems Engineering Assessment · Oct 2026

---

## 1. Architecture

The pipeline consists of six components running on the device:

```
biosignal-mock (TCP 7100)
        |
        v (newline-delimited JSON, ≤1 s of data per reply)
   [Reader × 2]           one thread per sensor
        |     \
        |      \--> [Loss Recorder]   (writes loss rows in same DB transaction)
        v
  [Durable Store]         SQLite WAL, synchronous=FULL
        |           \
        |            \--> [Offline Processor]  (same processing.py)
        v
   [Uploader]             batches POSTs to cloud API
        ^
        |
  [Mode Controller]       probes /v1/health every 2 s; hysteresis
```

**Why SQLite?** Crash-safe with WAL+FULL sync. The primary key `(session_id, sensor, idx)` makes deduplication a single `INSERT OR IGNORE`. The export query is just a `SELECT`. Simpler and more reliable than a hand-rolled append log.

**Why index-based, not clock-based?** Wall clocks can jump (NTP, power cut, DST). Sample indexes are monotone by protocol. All ordering, gap detection, and window boundaries use indexes. `time.monotonic()` is used only for timeouts (reconnect backoff, health probes).

**Data flow:**
1. Reader sends `{"cmd":"read","sensor":N}` → gets back ≤400 samples + CRC.
2. CRC verified on raw data bytes (not re-serialised JSON). If mismatch: retry up to 3×; then record loss `corrupt_reply`.
3. Gap detected (`first_index > expected_next`): record loss `[expected_next, first_index-1]`.
4. Samples + losses + cursor committed atomically every 400 samples or 1 second.
5. Mode controller switches online ↔ offline based on consecutive health probe results.
6. Uploader (when online) POSTs to cloud; advances `cloud_ack` only after HTTP 200.
7. On network return, uploader enters catch-up: 60-second batches instead of 1-second batches.
8. Cloud API processes windows as data arrives; results written with `mode="online"`.
9. Offline processor watches store; processes windows with `mode="offline"` when offline.

---

## 2. What is Guaranteed

| Property | Mechanism |
|---|---|
| Every received sample stored exactly once | `INSERT OR IGNORE` + PK `(session_id, sensor, idx)` |
| Every un-stored sample listed as loss | Gap detection, mock-reported `lost`, CRC failure, disk-full all write loss records |
| Switching causes no loss or duplicate | Reader never stops; uploader pauses/resumes from `cloud_ack`; cloud writes are idempotent |
| Cloud copy idempotent | Same `INSERT OR IGNORE` on PK; re-sending after switch cannot duplicate |
| Results follow §4 exactly | Tested against independent `tests/reference.py` on ramp data |

---

## 3. What is NOT Guaranteed

- **Samples dropped by the mock before the reader read them**: these appear in the mock's `lost` field and are recorded honestly. They are not recoverable.
- **Power-cut loss beyond the stated bound** if the SD card lies about fsync completion. This is a hardware property; the software does everything correctly (`synchronous=FULL`).
- **Cloud availability beyond the Oracle Always Free tier**: no SLA. The device continues collecting offline if the cloud is down.
- **Online deadline when the network is degraded but not fully down**: if health probes pass but upload latency is high, the 5-second deadline may not be met. The grace period (A7) applies.

---

## 4. Maximum Data Loss on Power Cut

**Stated bound (A8): 400 samples per sensor (1 second of data).**

Reason: samples are committed to SQLite every 400 samples (or 1 second timer), whichever
comes first, in one transaction with `synchronous=FULL`. On a power cut, the uncommitted
in-memory batch is lost. The reader reconnects after reboot and records the gap as
`crash_or_power_loss` (§6.4).

**Measured value**: recorded in `evidence/05_power_cut/run.log` after the power-cut test.
The measured loss must be ≤ 400 samples per sensor.

---

## 5. Resource Figures Summary

Measured over 3600 s on-device in online mode. Full data in `resources_report.md`.

| Metric | Value |
|---|---|
| Avg CPU (total) | TBD (run `scripts/resources/measure_1h.sh`) |
| Avg CPU (pipeline process) | TBD |
| Peak RSS | TBD |
| Peak VmHWM | TBD |
| Disk written | TBD |
| Network sent (cloud) | TBD |
| Board | Raspberry Pi 4 Model B, 4 cores, 2 GB RAM |
| OS | Raspberry Pi OS 64-bit (Debian Bookworm) |
| Python | 3.11.x |
| Cloud | Oracle Cloud Always Free, Ampere A1, region TBD |

---

## 6. Assumptions and Open Points

**Assumptions A1–A9** are listed in `README.md` and reproduced here:

| # | Assumption |
|---|---|
| A1 | Available = in the store and not in a loss record |
| A2 | `magnitude` is `null` if no sensor-2 samples available |
| A3 | Heart-rate "missing" check is sensor-1 samples |
| A4 | Gap-rule failure: skip candidate; earlier peak stays |
| A5 | Rounding: 1 decimal, half-up |
| A6 | Both offline and online results printed |
| A7 | Grace period = 3 s |
| A8 | Power-cut bound = 400 samples/sensor |
| A9 | Session = unit of export; identified by mock `session_id` |

**Open items (§16):**
1. Official mock binary URL (ARM64 + x86-64) — using `fake_mock.py` test double.
2. `check_ground_truth` tool — using ramp formula in `compare_export.py`.
3. Submission form accepted file types — TBD.
4. AI tool disclosure — see `AI_DISCLOSURE.md`.
