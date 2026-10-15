# AI Tool Usage Disclosure

## Tools Used
- **Google Antigravity IDE** (Google DeepMind)
- **Assisting Models**: Gemini 3.8 Flash & Claude 3.7 Sonnet (via Antigravity interactive chat and subagents)

## What AI Was Used For
- Interactive pair programming and code drafting (ingest reader, SQLite store, uploader, mode controller, CLI)
- Writing unit tests (`test_processing.py`, `test_store.py`, `test_crc.py`)
- Generating automated failure test simulation scripts (`scripts/failure_tests/*.sh`)
- Drafting technical documentation, architecture diagrams, and HTML/PDF dossier styling
- Debugging pipeline timing and SQLite concurrency issues

## What AI Was NOT Used For
- Core architectural decisions (using SQLite WAL, index-based monotone cursor accounting, atomic batch transactions)
- Reviewing mathematics against the assessment requirements
- Evaluating test results and validating zero-loss criteria

## Human Verification
- Validated mathematical formulas in `processing.py` (rolling window peak detection, half-up rounding, magnitude calculations).
- Verified that all 38 pytest unit tests pass cleanly.
- Inspected export output from deterministic ramp tests (`compare_export.py`) confirming zero discrepancies.
- Recorded and verified the live 4-panel terminal demonstration video.

Date: October 2026
Candidate: Aditya Sharma
