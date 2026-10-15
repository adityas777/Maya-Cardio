#!/usr/bin/env bash
# 01_process_kill.sh — Test: kill -9 pipeline, restart, verify no duplicate, loss listed.
# Usage: bash scripts/failure_tests/01_process_kill.sh SESSION_ID DB_PATH

set -euo pipefail
SESSION_ID="${1:-}"
DB_PATH="${2:-data/pipeline.db}"
EVIDENCE_DIR="evidence/01_process_kill"
mkdir -p "$EVIDENCE_DIR"

echo "=== Test 01: Process Kill ===" | tee "$EVIDENCE_DIR/run.log"
echo "Date: $(date)" | tee -a "$EVIDENCE_DIR/run.log"
echo "Session: $SESSION_ID" | tee -a "$EVIDENCE_DIR/run.log"

# Status before kill
echo "--- pipeline status BEFORE kill ---" | tee -a "$EVIDENCE_DIR/run.log"
pipeline status | tee "$EVIDENCE_DIR/status_before.json"

# Find and kill the pipeline process
PID=$(pgrep -f "bin/pipeline" | head -1 || true)
if [ -z "$PID" ]; then
  echo "ERROR: pipeline run process not found" | tee -a "$EVIDENCE_DIR/run.log"
  exit 1
fi
echo "Killing PID $PID with SIGKILL at $(date)" | tee -a "$EVIDENCE_DIR/run.log"
KILL_TIME=$(date +%s%N)
kill -9 "$PID"
sleep 2

# Restart
echo "Restarting pipeline..." | tee -a "$EVIDENCE_DIR/run.log"
pipeline --config config/pipeline.toml run &
NEW_PID=$!
echo "New PID: $NEW_PID" | tee -a "$EVIDENCE_DIR/run.log"
sleep 5

# Status after restart
echo "--- pipeline status AFTER restart ---" | tee -a "$EVIDENCE_DIR/run.log"
pipeline status | tee "$EVIDENCE_DIR/status_after.json"

# Export and compare
echo "--- Export and compare ---" | tee -a "$EVIDENCE_DIR/run.log"
pipeline export --session "$SESSION_ID" --from device > "$EVIDENCE_DIR/export_device.jsonl"
python tests/compare_export.py --export "$EVIDENCE_DIR/export_device.jsonl" --mode ramp \
  | tee "$EVIDENCE_DIR/compare_result.txt"

# Check for duplicates: every index should appear at most once
python3 -c "
import json, sys, collections
counts = collections.Counter()
with open('$EVIDENCE_DIR/export_device.jsonl') as f:
    for line in f:
        line=line.strip()
        if not line: continue
        obj=json.loads(line)
        if 'index' in obj:
            counts[(obj['sensor'],obj['index'])] += 1
dups = {k:v for k,v in counts.items() if v>1}
if dups:
    print('FAIL: duplicates found:', dups); sys.exit(1)
else:
    print('PASS: no duplicates')
" | tee -a "$EVIDENCE_DIR/run.log"

echo "RESULT: see compare_result.txt and run.log" | tee -a "$EVIDENCE_DIR/run.log"
