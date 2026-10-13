# MayaCardio Biosignal Pipeline — "Never Lose a Heartbeat"

> **Assessment submission.** Built to the MayaCardio Software Systems Engineering
> Candidate Assessment (Oct 2026). All requirements are from `AGENT_BUILD_SPEC.md`.

---

## Quick Start

### Requirements
- Python 3.10+
- ARM64 or x86-64 Linux (tested on Raspberry Pi 4, Ubuntu 22.04)
- SQLite 3.x (bundled with Python)

### Fresh Pi install

```bash
git clone https://github.com/YOUR_USERNAME/mayacardio-pipeline.git
cd mayacardio-pipeline/pipeline
sudo bash setup_pi.sh
```

Then edit `/opt/pipeline/config/pipeline.toml` and set your cloud URL and token.

### Manual install (development)

```bash
cd pipeline
python3 -m venv .venv
source .venv/bin/activate          # Linux/Mac
pip install -e .
```

### Run (with fake_mock test double)

```bash
# Terminal 1 — start mock (signal mode, seed 7)
python -m tests.fake_mock --mode signal --seed 7 --duration 3600 --port 7100

# Terminal 2 — start cloud API
PIPELINE_CLOUD_TOKEN=dev-token uvicorn pipeline.cloud_api:app --port 8000

# Terminal 3 — start pipeline
PIPELINE_CLOUD_TOKEN=dev-token pipeline run --config config/pipeline.example.toml
```

### CLI commands

```bash
pipeline status                                    # one JSON line: mode, samples, backlog
pipeline export --session SESSION_ID --from device # JSON lines to stdout
pipeline export --session SESSION_ID --from cloud  # fetch from cloud API
pipeline results --session SESSION_ID              # one JSON line per window
pipeline selftest                                  # quick sanity check
```

---

## Repository Layout

```
pipeline/
├── README.md                   ← this file
├── pyproject.toml              ← dependencies (pinned)
├── setup_pi.sh                 ← fresh Pi install
├── config/
│   └── pipeline.example.toml  ← all configuration knobs
├── src/pipeline/
│   ├── cli.py                  ← CLI entry point (status/export/results/run)
│   ├── reader.py               ← TCP client, CRC, gap detection, dedup
│   ├── store.py                ← SQLite WAL store, PK dedup, cursors
│   ├── losses.py               ← loss record helpers and reason constants
│   ├── processing.py           ← SHARED window + peak + heart-rate logic
│   ├── offline.py              ← device-side offline window processor
│   ├── uploader.py             ← batching, idempotent POSTs, catch-up
│   ├── mode.py                 ← network probe, hysteresis, switching
│   ├── cloud_api.py            ← FastAPI cloud API (runs on free-tier VM)
│   └── logging_utils.py
├── cloud/
│   └── deploy.md               ← Oracle Cloud Always Free deployment steps
├── tests/
│   ├── fake_mock.py            ← test double of biosignal-mock (signal+ramp)
│   ├── fault_proxy.py          ← TCP proxy: corrupt/duplicate/drop/delay
│   ├── reference.py            ← independent reference implementation of §4
│   ├── test_processing.py      ← unit tests: ramp, peaks, HR, rounding
│   ├── test_crc.py             ← CRC extraction edge cases
│   ├── test_store.py           ← PK dedup, cursor atomicity, concurrency
│   └── compare_export.py       ← validate export vs ramp formula or ground truth
├── systemd/
│   ├── pipeline.service
│   └── biosignal-mock.service
├── scripts/
│   ├── failure_tests/          ← 01–08 failure test scripts
│   └── resources/
│       └── measure_1h.sh       ← 1-hour resource measurement
└── evidence/                   ← test logs and results (see §12)
```

---

## Architecture

```
biosignal-mock (TCP 7100)  <--TCP JSON-->  [reader×2]  --> [durable store] --> [uploader] --> cloud
                                                |                |                   ^
                                                |                +---> [offline proc] |
                                                v                                 [mode ctrl]
                                         [loss recorder]
```

Components:

| Component | File | Description |
|-----------|------|-------------|
| **reader** | `reader.py` | One thread per sensor. Validates CRC, detects gaps, deduplicates. |
| **durable store** | `store.py` | SQLite WAL + FULL sync. INSERT OR IGNORE for dedup. |
| **uploader** | `uploader.py` | Batching, idempotent POSTs. Advances cloud_ack cursor. |
| **offline processor** | `offline.py` | Identical `processing.py` logic, mode=offline. |
| **mode controller** | `mode.py` | Probes `/v1/health` every 2s. Hysteresis: 2 fails→offline, 2 pass→online. |
| **loss recorder** | `losses.py` | Reason constants; store writes losses in same transaction as samples. |
| **cloud API** | `cloud_api.py` | FastAPI. Idempotent sample ingest. Triggers window processing. |

---

## Processing Logic (§4)

All logic in **`processing.py`** — used by both device and cloud.

- **Window w**: samples `400w..400w+399` (1 second at 400 Hz)
- **Peak** (sensor 1, ch2): strict local max; > 0.5×max of prior 800; gap ≥ 100 from prev peak
- **Heart rate**: `24000 / mean(peak gaps)` over 5-second range; null if w < 4 or any sample missing
- **Magnitude**: `mean(sqrt(ch1²+ch2²))` over available sensor-2 samples in window
- **Rounding**: half-up to 1 decimal (A5 — see `_round1()`)

---

## Assumptions Register (A1–A9)

| # | Assumption | Where used |
|---|---|---|
| A1 | Available = in the store and not in a loss record | §4.1 |
| A2 | `magnitude` is `null` if no sensor-2 samples available | §4.2 |
| A3 | Heart-rate "missing" check refers to sensor-1 samples | §4.4 |
| A4 | Peak gap rule: candidate failing 100-sample gap is skipped; earlier peak stays | §4.3 |
| A5 | Rounding: 1 decimal, **half-up** (`_round1()` in `processing.py`) | §4.2 |
| A6 | Both offline and online results printed; mode field distinguishes them | §10 |
| A7 | Grace period = 3 s before emitting window with missing data | §7.2 |
| A8 | Power-cut bound = 400 samples/sensor (1 s commit interval) | §3.2, §6.4 |
| A9 | Single session = unit of export; identified by mock `session_id` | §5.2 |

---

## Running Tests

```bash
cd pipeline
pip install pytest
pytest tests/ -v
```

Expected output: all tests PASS.

### Key tests

| File | What it covers |
|------|----------------|
| `test_processing.py` | Ramp values, peak rule, HR rule, half-up rounding, cross-check vs `reference.py` |
| `test_crc.py` | CRC extraction on compact/spaced JSON, corruption detection |
| `test_store.py` | PK dedup, cursor atomicity, loss records, concurrent sensors |

---

## Data Safety

- Every commit is inside one SQLite transaction with `synchronous=FULL` before being
  considered saved.
- **Maximum data loss on power cut**: the in-memory batch not yet committed = **400 samples
  per sensor** (1 second of data). Stated as A8; verified in failure test 05.
- Every gap is recorded as a loss with a reason. Silent loss = 0 by design.

---

## Open Items (§16)

1. **Official mock binary URL** not yet received. Using `tests/fake_mock.py` test double.
2. **`check_ground_truth` tool** not yet received. Using `tests/compare_export.py` with ramp formula.
3. Submission form file types and link policy: TBD — confirm with contact.
4. AI tool disclosure: fill in `AI_DISCLOSURE.md` truthfully.

---

## Evidence Index

| Test | Evidence directory | Status |
|------|--------------------|--------|
| Normal run 10 min | `evidence/01_normal_run/` | TBD |
| Ramp run 10 min | `evidence/02_ramp_run/` | TBD |
| Network cut 60s | `evidence/03_network_cut/` | TBD |
| Process kill | `evidence/01_process_kill/` | TBD |
| Power cut | `evidence/05_power_cut/` | TBD |
| Mock restart | `evidence/04_mock_restart/` | TBD |
| Corrupt + duplicate | `evidence/07_corrupt_duplicate/` | TBD |
| Sensor silent | `evidence/08_sensor_silent/` | TBD |
| Full disk | `evidence/05_disk_full/` | TBD |
| Clock change | `evidence/06_clock_change/` | TBD |
| Long outage (2h) | `evidence/11_long_outage/` | TBD |

---

## License

For assessment purposes only. Not for redistribution.
