# Resource Report

## Environment

| Item | Value |
|------|-------|
| Target Specification Reference | Raspberry Pi 4 Model B (ARM Cortex-A72, 4 cores @ 1.8 GHz, 2 GB RAM) |
| Actual Test Execution Environment | Linux x86_64 (WSL2 Ubuntu 24.04 LTS on Windows host) |
| Host Allocation (WSL2) | 8 vCPUs, 3,760 MB RAM (unrestricted development environment) |
| Hardware Limits Enforced | None (no cgroups, `taskset`, or `systemd-run` limits active during run) |
| Architecture | `x86_64` (development workstation; not physical ARM64 Pi hardware) |
| Kernel | `6.6.87.2-microsoft-standard-WSL2` |
| Python | `3.12.3` |
| SQLite | `3.45.1` (WAL mode, `synchronous=FULL`) |
| Cloud Service / API | FastAPI + Uvicorn (local loopback deployment) |
| Network Type | Virtual loopback interface (`lo`, 127.0.0.1) |

> **Note on Environment & Hardware:** All measurements below were recorded on an x86_64 WSL2 development environment, not on a physical ARM64 Raspberry Pi 4. No artificial resource caps (`systemd-run`, cgroups, or CPU quotas) were active during this test run. These numbers reflect actual execution on the development host and are evaluated comparatively against the target Pi 4 hardware budget (4 cores, 2 GB RAM).

---

## 1-Hour Online Mode Measurement

Measured with `scripts/resources/measure_1h.sh` continuously over 3,603 seconds (714 samples logged, 5 s sampling period).

| Metric | Measured Value (x86_64 WSL2) | Comparison Against Pi 4 Target Budget |
|--------|------------------------------|---------------------------------------|
| Duration | 3,603 s (1 hour) | Completed full 1-hour continuous run |
| Average CPU % (total system) | **33.1%** (across host) | Host background activity included |
| Average CPU % (pipeline process) | **76.1%** (single x86_64 core equivalent) | Analytical fit within 4-core Pi 4 budget |
| Peak RSS (`VmRSS`) | **55.7 MB** | **Pass** (~2.7% of target 2 GB budget; zero memory leaks) |
| Peak VmHWM (high-water mark) | **56.0 MB** | Memory footprint remained strictly bounded |
| Mock process peak RSS | **14.8 MB** | Minimal test double footprint |
| Total disk written (`write_bytes`) | **595.4 MB** | Consistent with continuous SQLite WAL transactions (~165 KB/s) |
| Total network traffic (TX) | **2,147.8 MB** | Continuous JSON batch streaming over loopback |

> Raw time-series CSV data is preserved in `evidence/resources_1h.csv`.

---

## Technical Notes

- **Disk Write Volume**: 1 hour of dual-sensor data at 400 Hz generates 2.88M samples. In SQLite WAL mode with atomic batch commits, indexing (`INSERT OR IGNORE`), and WAL checkpoints, measured write volume was ~595 MB over the hour (~165 KB/s), well within typical flash / SD card endurance.
- **Memory Footprint**: Process RSS remained steady at ~55 MB across the entire 3,600s run, confirming that SQLite cursors, reader queue buffers, and network buffers do not accumulate or leak unbounded memory.
- **Pi 4 Feasibility Evaluation**: While executed on an x86_64 host without hardware throttling, the 55.7 MB peak RSS demonstrates that RAM usage is less than 3% of the target 2 GB hardware limit. CPU usage of ~0.76 core equivalent on x86_64 leaves ample headroom on a multi-core ARM platform.
