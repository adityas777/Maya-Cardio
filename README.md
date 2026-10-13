# MayaCardio Biosignal Pipeline

> **"Never Lose a Heartbeat"** — MayaCardio Software Systems Engineering Assessment, Oct 2026

A two-sensor biosignal pipeline that collects ECG-like data from a mock TCP source, stores it durably on-device (SQLite WAL), uploads to a cloud API, and computes heart-rate windows on both device and cloud — with seamless offline/online mode switching and guaranteed lossless accounting.

---

## Table of Contents

1. [Fresh-Pi Install](#1-fresh-pi-install)
2. [Running the Pipeline](#2-running-the-pipeline)
3. [Testing](#3-testing)
4. [Cloud Deployment](#4-cloud-deployment)
5. [File Index](#5-file-index)
6. [Assumptions A1–A9](#6-assumptions-a1a9)
7. [Test Double Note](#7-test-double-note)

---

## 1. Fresh-Pi Install

Requires a Raspberry Pi 4 running **Raspberry Pi OS 64-bit (Debian Bookworm)**, Python 3.10+, connected to the internet.

```bash
# Clone the repo
git clone https://github.com/adityas777/Maya-Cardio.git /opt/pipeline-src
cd /opt/pipeline-src

# Run the setup script (installs to /opt/pipeline, enables systemd units)
sudo bash setup_pi.sh
```

`setup_pi.sh` does the following automatically:
- Installs system packages (`python3-venv`, `sqlite3`, `iptables`, `bc`, etc.)
- Copies the repo to `/opt/pipeline`
- Creates a virtualenv and runs `pip install -e .`
- Copies `config/pipeline.example.toml` → `config/pipeline.toml` if not present
- Installs and enables `biosignal-mock.service` and `pipeline.service` via systemd

### After setup: set the cloud token

The service file reads credentials from `/etc/pipeline.env`. Create it and lock it down:

```bash
sudo bash -c 'echo "PIPELINE_CLOUD_TOKEN=your_token_here" > /etc/pipeline.env'
sudo chmod 600 /etc/pipeline.env
sudo chown root:root /etc/pipeline.env
```

Then edit the cloud URL in `/opt/pipeline/config/pipeline.toml`:

```toml
[cloud]
base_url = "https://your-cloud-api.example.com"
```

Finally start the services:

```bash
sudo systemctl start biosignal-mock
sudo systemctl start pipeline
pipeline status
```

---

## 2. Running the Pipeline

All commands use the `pipeline` CLI installed into the virtualenv.
Pass `--config` before the subcommand if the config is not in the default location (`config/pipeline.toml`).

### Start collecting, uploading, and processing

```bash
pipeline run
# or with explicit config:
pipeline --config /opt/pipeline/config/pipeline.toml run
```

Press `Ctrl+C` or send `SIGTERM` to stop cleanly.

### Check live status

```bash
pipeline status
```

Prints a single JSON line:

```json
{
  "mode": "online",
  "session_id": "269e0d37",
  "samples_saved": 12000,
  "samples_in_cloud": 8000,
  "backlog": 4000,
  "timestamp_ms": 1791912943496
}
```

### Export session data

```bash
# Export from the on-device SQLite store
pipeline export --session SESSION_ID --from device

# Export from the cloud API
pipeline export --session SESSION_ID --from cloud
```

Streams newline-delimited JSON (samples + loss records) to stdout. Redirect to a file:

```bash
pipeline export --session SESSION_ID --from device > evidence/ramp_export.jsonl
```

### View processing results (heart-rate windows)

```bash
# Results computed on-device (mode=offline)
pipeline results --session SESSION_ID

# Results computed on the cloud (mode=online)
pipeline results --session SESSION_ID --from cloud
```

Each line is a JSON window result with `heart_rate`, `magnitude`, per-channel stats, and `missing_1`/`missing_2` counts.

### Quick smoke test

```bash
pipeline selftest
```

---

## 3. Testing

### Unit tests (pytest)

```bash
cd /opt/pipeline
.venv/bin/pytest tests/ -q
```

Runs 38 tests covering:
- CRC extraction and verification (`test_crc.py`)
- Processing logic: rounding, ramp formulas, window stats, peak detection, heart-rate, cross-check against `reference.py` (`test_processing.py`)
- SQLite store: deduplication, cursor atomicity, loss records, concurrency (`test_store.py`)

### Ramp-data export integrity check

After running the pipeline in **ramp mode** and exporting:

```bash
# Start mock in ramp mode
python -m tests.fake_mock --mode ramp --seed 0 --duration 600 --port 7100

# Export device data to a file
pipeline export --session SESSION_ID --from device > evidence/ramp_export.jsonl

# Verify every sample matches the ramp formula
python3 tests/compare_export.py \
    --export evidence/ramp_export.jsonl \
    --mode ramp
```

To compare against a ground-truth JSONL file produced by the official tool:

```bash
python3 tests/compare_export.py \
    --export evidence/ramp_export.jsonl \
    --mode ground_truth \
    --truth /path/to/ground_truth.jsonl
```

Full argument reference:

```
usage: compare_export.py [-h] --export EXPORT --mode {ramp,ground_truth}
                         [--truth TRUTH]

  --export EXPORT       Export JSONL file (from `pipeline export`)
  --mode {ramp,ground_truth}
  --truth TRUTH         Ground truth JSONL (required for ground_truth mode)
```

---

## 4. Cloud Deployment

See **[cloud/deploy.md](cloud/deploy.md)** for full instructions to set up an Oracle Cloud Always Free ARM64 VM (Ampere A1, no-charge tier), deploy the FastAPI cloud API, and wire it to the pipeline on the Pi.

---

## 5. File Index

```
pipeline/
├── src/pipeline/
│   ├── cli.py              Entry point: run, status, export, results, selftest
│   ├── reader.py           TCP reader thread; one per sensor
│   ├── store.py            SQLite WAL store; INSERT OR IGNORE deduplication
│   ├── uploader.py         Batching uploader; advances cloud_ack on HTTP 200
│   ├── mode.py             Online/offline mode controller; hysteresis probes
│   ├── processing.py       Shared window + peak + heart-rate logic (§4)
│   ├── offline.py          On-device offline processor
│   ├── cloud_api.py        FastAPI cloud API (deploy on Oracle A1)
│   ├── losses.py           Loss record helpers
│   └── logging_utils.py    Structured log setup
│
├── tests/
│   ├── fake_mock.py        Test double for biosignal-mock (see §7)
│   ├── fault_proxy.py      TCP proxy that injects corruption / drops
│   ├── reference.py        Independent reference implementation of §4
│   ├── compare_export.py   Ramp / ground-truth export verifier
│   ├── test_processing.py  38 unit tests for processing logic
│   ├── test_store.py       Unit tests for SQLite store
│   └── test_crc.py         Unit tests for CRC extraction
│
├── config/
│   └── pipeline.example.toml   Copy to pipeline.toml; fill in cloud URL
│
├── systemd/
│   ├── pipeline.service        systemd unit for the pipeline
│   └── biosignal-mock.service  systemd unit for the test-double mock
│
├── scripts/
│   ├── resources/
│   │   └── measure_1h.sh       1-hour CPU/RAM/disk/net measurement
│   └── failure_tests/
│       ├── 01_process_kill.sh
│       ├── 02_power_cut_sim.md  (manual — requires VM console access)
│       ├── 03_network_cut.sh
│       ├── 04_mock_restart.sh
│       ├── 05_disk_full.sh
│       ├── 06_clock_change.sh
│       ├── 07_corrupt_and_duplicate.sh
│       └── 08_sensor_silent.sh
│
├── cloud/
│   └── deploy.md           Cloud VM setup guide
│
├── evidence/               Generated evidence (not committed; see .gitignore)
│   ├── README.md           Lists expected files and how to force-add for submission
│   ├── resources_1h.csv    Output of measure_1h.sh (fill before submission)
│   ├── ramp_export.jsonl   Ramp-mode export for integrity check
│   └── 01_process_kill/ … 08_sensor_silent/   One folder per failure test
│
├── pyproject.toml          Build config; registers `pipeline` CLI entry point
├── setup_pi.sh             Fresh-board install script
├── DESIGN_NOTE.md          Architecture, guarantees, assumptions, resource figures
├── resources_report.md     Measured resource table (fill TBD values on Pi)
├── AI_DISCLOSURE.md        AI tool usage disclosure (fill before submission)
└── README.md               This file
```

---

## 6. Assumptions A1–A9

These assumptions were made where the spec was silent or ambiguous. They are reproduced from `DESIGN_NOTE.md`:

| # | Assumption |
|---|---|
| A1 | "Available" means stored in the device DB and **not** in a loss record. |
| A2 | `magnitude` is `null` if **no** sensor-2 samples are available in the window. |
| A3 | The heart-rate "missing" check uses **sensor-1** samples only. |
| A4 | If a peak candidate fails the 100-sample gap rule, it is **skipped**; the earlier peak stays. |
| A5 | Rounding is to **1 decimal place, half-up** (not banker's rounding). |
| A6 | **Both** offline and online results are stored and printed. |
| A7 | Grace period (max wait for missing data before emitting a window) = **3 s**. |
| A8 | Power-cut data-loss bound = **400 samples per sensor** (1 second of data). |
| A9 | Session is the unit of export; identified by the mock's `session_id` field. |

---

## 7. Test Double Note

The official `biosignal-mock` binary (ARM64 + x86-64) has not been provided.
In its place, **`tests/fake_mock.py`** implements the identical newline-delimited JSON protocol, including `session_id`, `first_index`, `count`, `lost`, `session_end`, and `crc32` fields. It supports both `signal` mode (ECG-like with a 60/90/72 bpm schedule) and `ramp` mode (deterministic formula for integrity checking).

All development, local testing, and the results in this submission were produced using `fake_mock.py`. The pipeline code is agnostic to whether it talks to the official binary or the test double — they use the same protocol.

Once an official mock binary is available, replace the `ExecStart` line in `systemd/biosignal-mock.service` with the path to that binary.