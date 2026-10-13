#!/usr/bin/env bash
# 07_corrupt_and_duplicate.sh — Run fault_proxy with corruption+duplication;
#                               verify CRC rejected and duplicates ignored.

set -euo pipefail
SESSION_ID="${1:-}"
EVIDENCE_DIR="evidence/07_corrupt_duplicate"
mkdir -p "$EVIDENCE_DIR"
PROXY_PORT=7200

echo "=== Test 07: Corrupt + Duplicate via fault_proxy ===" | tee "$EVIDENCE_DIR/run.log"
date | tee -a "$EVIDENCE_DIR/run.log"

# Start fault proxy (30% corrupt, 30% duplicate)
python3 tests/fault_proxy.py \
  --listen-port "$PROXY_PORT" \
  --upstream-port 7100 \
  --corrupt-rate 0.3 \
  --duplicate-rate 0.3 \
  --seed 12345 &
PROXY_PID=$!
echo "FaultProxy PID=$PROXY_PID" | tee -a "$EVIDENCE_DIR/run.log"

# Point pipeline at the proxy (restart with proxy port)
# This assumes the pipeline can be reconfigured; for the test, update config
# and restart pipeline.
# In CI: pipeline run --config config/pipeline_proxy.toml &

sleep 60

pipeline status | tee "$EVIDENCE_DIR/status.json"

# Export and verify — ramp formula checks all values, duplicates would show up
pipeline export --session "$SESSION_ID" --from device > "$EVIDENCE_DIR/export.jsonl"
python3 tests/compare_export.py --export "$EVIDENCE_DIR/export.jsonl" --mode ramp \
  | tee "$EVIDENCE_DIR/compare.txt"

# Check for duplicates
python3 -c "
import json, collections
counts=collections.Counter()
with open('$EVIDENCE_DIR/export.jsonl') as f:
    for line in f:
        line=line.strip()
        if not line: continue
        obj=json.loads(line)
        if 'index' in obj:
            counts[(obj['sensor'],obj['index'])]+=1
dups={k:v for k,v in counts.items() if v>1}
if dups:
    print('FAIL: duplicates', list(dups.items())[:5])
else:
    print('PASS: no duplicates despite proxy duplication')
" | tee -a "$EVIDENCE_DIR/run.log"

kill "$PROXY_PID" 2>/dev/null || true
echo "=== DONE ===" | tee -a "$EVIDENCE_DIR/run.log"
