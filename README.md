# MayaCardio Biosignal Pipeline

> **"Never Lose a Heartbeat"** — MayaCardio Software Systems Engineering Assessment  
> Target Platform: **Raspberry Pi 4 Model B (4 cores, 2 GB RAM)**  
> Author: **Aditya Sharma**

[![Test Suite](https://img.shields.io/badge/pytest-38%20passed-success)](tests/)
[![Pi 4 RAM](https://img.shields.io/badge/Peak%20RSS-55.7%20MB%20%2F%202%20GB-brightgreen)](resources_report.md)
[![Pi 4 CPU](https://img.shields.io/badge/CPU%20Usage-19%25%20of%20Pi%204-brightgreen)](resources_report.md)
[![Zero Loss](https://img.shields.io/badge/Zero%20Silent%20Loss-100%25%20Accounted-blue)](evidence/)

---

## 📌 Submission Deliverables & Links

* 🎥 **Demonstration Video (YouTube)**: [https://www.youtube.com/watch?v=fFcPxcLqXg0](https://www.youtube.com/watch?v=fFcPxcLqXg0)  
  *Demonstrates real-time 400 Hz dual-sensor streaming, network cut & restoration (<10s detection, 40s backlog catch-up), process crash recovery (`kill -9`), export deduplication, and window heart-rate calculations.*
* 🐙 **GitHub Repository**: [https://github.com/adityas777/Maya-Cardio.git](https://github.com/adityas777/Maya-Cardio.git)
* 📑 **Master Submission Dossier (PDF)**: [`MayaCardio_Submission_Dossier.pdf`](MayaCardio_Submission_Dossier.pdf)  
  *Comprehensive 6-page technical dossier with executive summary, architecture diagram, 1-hour Pi 4 benchmarks, failure test evidence, and verification logs.*
* 📄 **Two-Page Design Note (PDF)**: [`DESIGN_NOTE.pdf`](DESIGN_NOTE.pdf)  
  *Strictly 2 pages as mandated by §14: architecture diagram, core guarantees, non-guarantees, assumptions A1–A9, and benchmark table.*

---

## Table of Contents

1. [Evaluator Quickstart (Run in 3 Minutes)](#1-evaluator-quickstart-run-in-3-minutes)
2. [1-Hour Pi 4 Resource Benchmark (Measured Results)](#2-1-hour-pi-4-resource-benchmark-measured-results)
3. [Failure Mode Verification (All 8 Tests Passed)](#3-failure-mode-verification-all-8-tests-passed)
4. [CLI Reference & Commands](#4-cli-reference--commands)
5. [System Architecture & Guarantees](#5-system-architecture--guarantees)
6. [Fresh-Pi Deployment via systemd](#6-fresh-pi-deployment-via-systemd)
7. [Cloud Deployment](#7-cloud-deployment)
8. [Assumptions A1–A9 & Test Double Note](#8-assumptions-a1a9--test-double-note)
9. [Repository File Index](#9-repository-file-index)

---

## 1. Evaluator Quickstart (Run in 3 Minutes)

Works on any Linux, WSL2 (Ubuntu), or Raspberry Pi 4 environment with Python 3.10+.

### Step 1: Clone & Setup Environment

```bash
git clone https://github.com/adityas777/Maya-Cardio.git
cd Maya-Cardio

# Create virtualenv and install package in editable mode with test dependencies
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -e ".[dev]"
```

### Step 2: Run Unit Tests (38 Tests)

```bash
pytest tests/ -q
# Output: ...................................... [100%]
# 38 passed in ~0.5s
```

Covers:
* CRC verification on raw frame bytes (`test_crc.py`)
* Peak detection, half-up rounding, window magnitude, and heart rate math (§4) (`test_processing.py`)
* SQLite WAL concurrency, composite PK deduplication, and atomic cursor advances (`test_store.py`)

### Step 3: Run Ramp Verification (Mathematical Zero-Loss Proof)

```bash
# 1. Start mock in deterministic ramp mode in background
python -m tests.fake_mock --mode ramp --seed 0 --duration 15 --port 7100 > /dev/null 2>&1 &
MOCK_PID=$!

# 2. Run pipeline for 10 seconds to collect samples
mkdir -p data
pipeline --config config/pipeline.example.toml run &
PIPE_PID=$!
sleep 10
kill -INT $PIPE_PID $MOCK_PID 2>/dev/null || true
wait $PIPE_PID 2>/dev/null || true

# 3. Export device data and mathematically verify every sample against ramp formula
SESSION_ID=$(sqlite3 data/pipeline.db "SELECT session_id FROM sessions LIMIT 1;")
pipeline export --session "$SESSION_ID" --from device > ramp_export.jsonl
python tests/compare_export.py --export ramp_export.jsonl --mode ramp
# Output: PASS: All samples matched expected ramp formula. Discrepancies: 0
```

### Step 4: Run 4-Terminal Live Demo (Simulation)

To reproduce the exact demonstration shown in the video:

* **Terminal 1 (Cloud API)**:
  ```bash
  CLOUD_DB_PATH=data/cloud.db python -m uvicorn pipeline.cloud_api:app --host 0.0.0.0 --port 8000
  ```
* **Terminal 2 (Sensor Mock)**:
  ```bash
  python -m tests.fake_mock --mode signal --seed 7 --duration 600 --port 7100
  ```
* **Terminal 3 (Pipeline Daemon)**:
  ```bash
  pipeline --config config/pipeline.example.toml run
  ```
* **Terminal 4 (Observer CLI)**:
  ```bash
  # Check live status (online/offline, saved samples, backlog)
  pipeline status

  # Export samples from device and cloud
  S=$(pipeline status | python3 -c "import sys,json; print(json.load(sys.stdin)['session_id'])")
  pipeline export --session "$S" --from device 2>/dev/null | head -n 5
  pipeline export --session "$S" --from cloud 2>/dev/null | head -n 5

  # Inspect window processing results
  pipeline results --session "$S" | tail -n 5
  ```

---

## 2. 1-Hour Pi 4 Resource Benchmark (Measured Results)

Continuously benchmarked for **3,603 seconds (714 samples, 5s interval)** under enforced Raspberry Pi 4 bounds (`CPUQuota=400%`, `MemoryMax=2G`).

| Metric | Measured Value | Pi 4 Limit / Bound | Status | Assessment Verdict |
|---|---|---|---|---|
| **Test Duration** | **3,603 s** (1 hour) | &ge; 3,600 s | Complete | **PASS** (714 samples logged) |
| **Peak RAM (RSS)** | **55.7 MB** | 2,048 MB (2 GB) | **Pass** | **Only 2.7% of Pi 4 limit** (zero leak) |
| **Peak VmHWM** | **56.0 MB** | 2,048 MB (2 GB) | **Pass** | Rock-solid memory stability |
| **Pipeline Process CPU** | **76.1%** (single core) | 400% (4 cores) | **Pass** | **19.0% of total Pi 4 capacity** |
| **Total System CPU** | **33.1%** | 400% (4 cores) | **Pass** | Leaves &gt;80% CPU headroom for OS |
| **Total Disk Written** | **595.4 MB** | Wear-leveling safe | **Pass** | ~165 KB/s continuous WAL commits |
| **Total Network TX** | **2,147.8 MB** | Local / Cloud link | **Pass** | Continuous 400-sample JSON streaming |

*Full raw time-series metrics CSV: [`evidence/resources_1h.csv`](evidence/resources_1h.csv).*

---

## 3. Failure Mode Verification (All 8 Tests Passed)

All 8 automated failure scenarios required by §11 were simulated, executed, and verified:

| # | Test Scenario | Automated Script | Injected Failure Condition | Verified Pass Criteria | Evidence |
|---|---|---|---|---|---|
| **01** | Process Kill | `scripts/run_test_01.sh` | `kill -9` pipeline under load | Zero duplicates on restart (`INSERT OR IGNORE`), cursor preserved | [`evidence/01_process_kill`](evidence/01_process_kill/) |
| **02** | Power Cut | `scripts/run_test_02.sh` | Simulated ungraceful power loss | Loss bounded &le; 400 samples/sensor (1s sync), DB passes integrity check | [`evidence/02_power_cut`](evidence/02_power_cut/) |
| **03** | Network Outage | `scripts/run_test_03.sh` | Cloud API killed for 30s | Switches to offline in &le;10s; catches up in &le;40s upon return; device & cloud export identical | [`evidence/03_network_cut`](evidence/03_network_cut/) |
| **04** | Mock Restart | `scripts/run_test_04.sh` | Mock killed and restarted (new session) | Detects session change, rotates DB session cleanly, starts new ingest from index 0 | [`evidence/04_mock_restart`](evidence/04_mock_restart/) |
| **05** | Disk Full | `scripts/run_test_05.sh` | Read-only partition / disk full | Safely transitions to degraded mode; records disk loss; self-heals when space freed | [`evidence/05_disk_full`](evidence/05_disk_full/) |
| **06** | Clock Jump | `scripts/run_test_06.sh` | System clock jumped &plusmn;1 hour (NTP) | Zero corrupted windows (pipeline strictly relies on sample index, monotonic timers) | [`evidence/06_clock_change`](evidence/06_clock_change/) |
| **07** | Corrupt & Dups | `scripts/run_test_07.sh` | Bitflips, CRC corruption, duplicate TCP chunks | CRC failures retried and recorded as loss; duplicates discarded via composite PK | [`evidence/07_corrupt_and_duplicate`](evidence/07_corrupt_and_duplicate/) |
| **08** | Silent Sensor | `scripts/run_test_08.sh` | Sensor 2 halted; Sensor 1 active | Heart rate continues computing on Sensor 1; Sensor 2 marked `magnitude: null` | [`evidence/08_sensor_silent`](evidence/08_sensor_silent/) |

---

## 4. CLI Reference & Commands

```
pipeline [--config PATH] <command> [options]
```

| Subcommand | Description | Example Usage |
|---|---|---|
| `run` | Starts ingestion, storage, upload, and processing. | `pipeline run` |
| `status` | Emits single JSON status line (mode, session, samples, cloud ack, backlog). | `pipeline status` |
| `export` | Streams newline-delimited JSON samples and losses. | `pipeline export --session <id> --from device` |
| `results` | Prints window calculation results (heart rate, magnitude, missing). | `pipeline results --session <id>` |
| `selftest` | Executes end-to-end component diagnostics. | `pipeline selftest` |

---

## 5. System Architecture & Guarantees

```
┌─────────────────────────────────┐
│   biosignal-mock (TCP 7100)     │ Dual-sensor signed 24-bit samples @ 400 Hz
└────────────────┬────────────────┘
                 │
                 ▼
┌─────────────────────────────────┐       ┌──────────────────────────────┐
│       Reader (Thread × 2)       ├──────>│        Loss Recorder         │
└────────────────┬────────────────┘       └──────────────┬───────────────┘
                 │ (Atomic batches)                      │ (Gap, CRC, Disk losses)
                 ▼                                       ▼
┌────────────────────────────────────────────────────────────────────────┐
│                    Durable Store (SQLite WAL Mode)                     │
│           Composite Primary Key: (session_id, sensor, idx)             │
└────────────────┬───────────────────────────────────────┬───────────────┘
                 │                                       │
        [When Online]                           [When Offline]
                 ▼                                       ▼
┌─────────────────────────────────┐       ┌──────────────────────────────┐
│       Batching Uploader         │       │      Offline Processor       │
│  (Advances ack on HTTP 200 OK)  │       │     (Emits mode=offline)     │
└────────────────┬────────────────┘       └──────────────────────────────┘
                 │ (HTTP POST /v1/samples)
                 ▼
┌─────────────────────────────────┐
│     Cloud API (FastAPI)         │ Stores in cloud.db; computes online windows
└─────────────────────────────────┘
```

### Guarantees
1. **Zero Silent Loss**: Every sample received is committed to SQLite or explicitly cataloged with reason (`gap`, `crc_mismatch`, `disk_full`).
2. **Exactly-Once Storage**: Composite primary key `(session_id, sensor, idx)` ensures idempotent deduplication (`INSERT OR IGNORE`).
3. **Resilient Network Switching**: Hysteresis probe logic switches between Online and Offline modes in &le;10s. Upon reconnection, high-throughput catch-up batches drain backlogs with zero duplicate uploads.
4. **Independent Window Processing**: Shared algorithm (`processing.py`) computes mathematical identical results on both edge and cloud.

---

## 6. Fresh-Pi Deployment via systemd

On a fresh Raspberry Pi 4 running Raspberry Pi OS 64-bit (Debian Bookworm):

```bash
# 1. Clone repository
git clone https://github.com/adityas777/Maya-Cardio.git /opt/pipeline-src
cd /opt/pipeline-src

# 2. Run automated setup script
sudo bash setup_pi.sh

# 3. Configure cloud token
sudo bash -c 'echo "PIPELINE_CLOUD_TOKEN=production-token" > /etc/pipeline.env'
sudo chmod 600 /etc/pipeline.env

# 4. Start systemd services
sudo systemctl start biosignal-mock
sudo systemctl start pipeline

# 5. Check live status
pipeline status
```

---

## 7. Cloud Deployment

See **[`cloud/deploy.md`](cloud/deploy.md)** for instructions on deploying the FastAPI cloud API on Oracle Cloud Always Free ARM64 (Ampere A1) or any Linux VM.

---

## 8. Assumptions A1–A9 & Test Double Note

### Assumptions A1–A9 (from `DESIGN_NOTE.md`):
* **A1**: "Available" means stored in the device database and not listed in a loss record.
* **A2**: `magnitude` is `null` if no sensor-2 samples are available in the window.
* **A3**: The heart-rate "missing" check uses sensor-1 samples only.
* **A4**: If a peak candidate violates the 100-sample refractory rule, it is skipped; the earlier peak is retained.
* **A5**: Rounding is to 1 decimal place, half-up (standard scientific rounding).
* **A6**: Both offline and online results are computed and stored with corresponding mode tags.
* **A7**: Grace period for out-of-order window completion is 3.0 seconds.
* **A8**: Power-cut data loss is strictly bounded to &le; 400 samples/sensor (1 second of data).
* **A9**: Session is the primary export unit, identified by the mock's `session_id`.

### Test Double Note
`tests/fake_mock.py` implements the full biosignal protocol over TCP (port 7100), including CRC32 checksums, configurable packet delays, ramp mode, and error injection. The pipeline communicates with any compliant mock implementation.

---

## 9. Repository File Index

```
Maya-Cardio/
├── src/pipeline/
│   ├── cli.py                  CLI commands (run, status, export, results, selftest)
│   ├── reader.py               TCP reader thread per sensor
│   ├── store.py                SQLite WAL storage engine (composite PK)
│   ├── uploader.py             Batch HTTP uploader with catch-up logic
│   ├── mode.py                 Online/offline mode controller with hysteresis
│   ├── processing.py           Shared windowing and heart-rate peak math (§4)
│   ├── offline.py              On-device offline window processor
│   ├── cloud_api.py            FastAPI cloud backend API
│   ├── losses.py               Loss accounting utilities
│   └── logging_utils.py        Structured logging
├── tests/
│   ├── fake_mock.py            Biosignal TCP mock source (signal & ramp modes)
│   ├── fault_proxy.py          TCP network and corruption injection proxy
│   ├── reference.py            Ground-truth mathematical reference implementation
│   ├── compare_export.py       Export verification tool (ramp & ground truth)
│   ├── test_processing.py      38 unit tests for windowing and peak detection
│   ├── test_store.py           SQLite store concurrency and deduplication tests
│   └── test_crc.py             CRC extraction and validation tests
├── config/
│   ├── pipeline.example.toml   Example pipeline configuration
│   └── pipeline.toml           Default local configuration
├── systemd/
│   ├── pipeline.service        systemd unit for pipeline daemon
│   └── biosignal-mock.service  systemd unit for mock sensor
├── scripts/
│   ├── resources/
│   │   ├── measure_1h.sh       1-hour Linux resource benchmark script
│   │   └── measure_windows.ps1 Windows resource measurement script
│   └── failure_tests/
│       ├── 01_process_kill.sh ... 08_sensor_silent.sh  (Automated failure tests)
├── evidence/
│   ├── resources_1h.csv        Raw 1-hour time-series benchmark measurements
│   └── 01_process_kill/ ...    Individual failure test logs and artifacts
├── DESIGN_NOTE.pdf             Mandatory 2-page design PDF
├── MayaCardio_Submission_Dossier.pdf  Master 6-page comprehensive dossier
├── resources_report.md         Full benchmark analysis and compliance report
├── AI_DISCLOSURE.md            AI tooling and methodology disclosure
├── setup_pi.sh                 Raspberry Pi fresh-board installation script
├── pyproject.toml              Python build and dependency specification
└── README.md                   Project overview and evaluator manual
```