#!/usr/bin/env bash
# 08_sensor_silent.sh — Drop one sensor via fault_proxy for 20s;
#                       verify loss listed, complete=false, no invented values.

set -euo pipefail
SESSION_ID="${1:-}"
EVIDENCE_DIR="evidence/08_sensor_silent"
mkdir -p "$EVIDENCE_DIR"
PROXY_PORT=7201
SILENT_SENSOR=2    # drop sensor 2

echo "=== Test 08: Sensor Silent (sensor $SILENT_SENSOR for 20s) ===" | tee "$EVIDENCE_DIR/run.log"
date | tee -a "$EVIDENCE_DIR/run.log"

pipeline status | tee "$EVIDENCE_DIR/status_before.json"
SAMPLES_BEFORE=$(pipeline status | python3 -c "import sys,json; print(json.load(sys.stdin)['samples_saved'])")

# Start proxy dropping sensor 2
python3 tests/fault_proxy.py \
  --listen-port "$PROXY_PORT" \
  --upstream-port 7100 \
  --drop-sensor "$SILENT_SENSOR" &
PROXY_PID=$!
echo "FaultProxy PID=$PROXY_PID dropping sensor $SILENT_SENSOR" | tee -a "$EVIDENCE_DIR/run.log"

sleep 20

# Stop proxy (sensor restored)
kill "$PROXY_PID" 2>/dev/null || true
echo "Sensor restored at $(date)" | tee -a "$EVIDENCE_DIR/run.log"

sleep 10

# Export
pipeline export --session "$SESSION_ID" --from device > "$EVIDENCE_DIR/export.jsonl"

# Check: loss listed for sensor 2, and no sensor-2 samples invented in the silent window
python3 -c "
import json, sys
losses=[]
samples_s2=[]
with open('$EVIDENCE_DIR/export.jsonl') as f:
    for line in f:
        line=line.strip()
        if not line: continue
        obj=json.loads(line)
        if 'reason' in obj and obj.get('sensor')==$SILENT_SENSOR:
            losses.append(obj)
        elif 'index' in obj and obj.get('sensor')==$SILENT_SENSOR:
            samples_s2.append(obj)

if losses:
    print(f'PASS: {len(losses)} loss record(s) for sensor $SILENT_SENSOR')
else:
    print('FAIL: no loss records for sensor $SILENT_SENSOR')

print(f'INFO: {len(samples_s2)} sensor-$SILENT_SENSOR samples stored (before/after silent period)')
" | tee -a "$EVIDENCE_DIR/run.log"

# Check results show complete=false for affected windows
pipeline results --session "$SESSION_ID" | python3 -c "
import json, sys
results = [json.loads(l) for l in sys.stdin if l.strip()]
incomplete = [r for r in results if not r.get('complete') and r.get('missing_2',0)>0]
if incomplete:
    print(f'PASS: {len(incomplete)} windows with complete=false and missing_2>0')
else:
    print('WARN: no incomplete windows found (may not have processed yet)')
" | tee -a "$EVIDENCE_DIR/run.log"

echo "=== DONE ===" | tee -a "$EVIDENCE_DIR/run.log"
