#!/usr/bin/env bash
# 06_clock_change.sh — Jump wall clock ±1h mid-run; verify pipeline unaffected.
# Pipeline uses time.monotonic() for all ordering/timeouts; only log timestamps change.

set -euo pipefail
SESSION_ID="${1:-}"
EVIDENCE_DIR="evidence/06_clock_change"
mkdir -p "$EVIDENCE_DIR"

echo "=== Test 06: Clock Change ===" | tee "$EVIDENCE_DIR/run.log"
date | tee -a "$EVIDENCE_DIR/run.log"

pipeline status | tee "$EVIDENCE_DIR/status_before.json"
SAMPLES_BEFORE=$(pipeline status | python3 -c "import sys,json;print(json.load(sys.stdin)['samples_saved'])")

echo "Jumping clock forward 1 hour..." | tee -a "$EVIDENCE_DIR/run.log"
# Temporarily disable NTP to allow the jump
timedatectl set-ntp false 2>/dev/null || true
sudo date --set="$(date -d '+1 hour' '+%Y-%m-%d %H:%M:%S')"

sleep 10

pipeline status | tee "$EVIDENCE_DIR/status_during.json"
SAMPLES_DURING=$(pipeline status | python3 -c "import sys,json;print(json.load(sys.stdin)['samples_saved'])")

# Restore clock
sudo date --set="$(date -d '-1 hour' '+%Y-%m-%d %H:%M:%S')"
timedatectl set-ntp true 2>/dev/null || true

sleep 5

pipeline status | tee "$EVIDENCE_DIR/status_after.json"
SAMPLES_AFTER=$(pipeline status | python3 -c "import sys,json;print(json.load(sys.stdin)['samples_saved'])")

echo "Samples: before=$SAMPLES_BEFORE during=$SAMPLES_DURING after=$SAMPLES_AFTER" \
  | tee -a "$EVIDENCE_DIR/run.log"

# Pipeline should keep running and accumulating samples regardless of clock
[ "$SAMPLES_AFTER" -gt "$SAMPLES_BEFORE" ] \
  && echo "PASS: samples accumulating despite clock change" | tee -a "$EVIDENCE_DIR/run.log" \
  || echo "FAIL: pipeline may have stalled" | tee -a "$EVIDENCE_DIR/run.log"

echo "=== DONE ===" | tee -a "$EVIDENCE_DIR/run.log"
