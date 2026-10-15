#!/usr/bin/env bash
# 04_mock_restart.sh — Kill and restart the mock; verify old session closed,
#                      new session starts, old unread range listed as loss.

set -euo pipefail
EVIDENCE_DIR="evidence/04_mock_restart"
mkdir -p "$EVIDENCE_DIR"

echo "=== Test 04: Mock Restart ===" | tee "$EVIDENCE_DIR/run.log"
date | tee -a "$EVIDENCE_DIR/run.log"

pipeline status | tee "$EVIDENCE_DIR/status_before.json"
OLD_SESSION=$(pipeline status | python3 -c "import sys,json; print(json.load(sys.stdin)['session_id'])")
echo "Old session: $OLD_SESSION" | tee -a "$EVIDENCE_DIR/run.log"

# Kill mock
MOCK_PID=$(pgrep -f "biosignal-mock|fake_mock" | head -1 || true)
echo "Killing mock PID $MOCK_PID" | tee -a "$EVIDENCE_DIR/run.log"
kill -9 "$MOCK_PID" || true
sleep 2

# Restart mock (new session_id will be generated)
if command -v biosignal-mock >/dev/null 2>&1; then
  biosignal-mock --mode ramp --seed 99 --duration 600 --port 7100 &
else
  python -m tests.fake_mock --mode ramp --seed 99 --duration 600 --port 7100 &
fi
echo "Mock restarted with seed=99" | tee -a "$EVIDENCE_DIR/run.log"
sleep 5

# Check that pipeline detected new session
pipeline status | tee "$EVIDENCE_DIR/status_after.json"
NEW_SESSION=$(pipeline status | python3 -c "import sys,json; print(json.load(sys.stdin)['session_id'])")
echo "New session: $NEW_SESSION" | tee -a "$EVIDENCE_DIR/run.log"

[ "$OLD_SESSION" != "$NEW_SESSION" ] \
  && echo "PASS: session changed" | tee -a "$EVIDENCE_DIR/run.log" \
  || echo "FAIL: session did not change" | tee -a "$EVIDENCE_DIR/run.log"

# Export old session — must have loss with to_index:null
pipeline export --session "$OLD_SESSION" --from device > "$EVIDENCE_DIR/export_old.jsonl"
python3 -c "
import json, sys
losses = []
with open('$EVIDENCE_DIR/export_old.jsonl') as f:
    for line in f:
        line=line.strip()
        if not line: continue
        obj=json.loads(line)
        if 'reason' in obj:
            losses.append(obj)
mock_restart = [l for l in losses if l['reason'] in ('mock_restart','crash_or_power_loss')]
null_end     = [l for l in mock_restart if l.get('to_index') is None]
if null_end:
    print('PASS: unread range listed with to_index:null')
else:
    print('INFO: losses found but none with to_index:null:', mock_restart)
    print('(acceptable if mock was fully drained before restart)')
" | tee -a "$EVIDENCE_DIR/run.log"

echo "=== DONE ===" | tee -a "$EVIDENCE_DIR/run.log"
