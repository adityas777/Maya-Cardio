#!/usr/bin/env bash
# 05_disk_full.sh — Fill disk until ENOSPC; verify pipeline survives, losses recorded.

set -euo pipefail
SESSION_ID="${1:-}"
EVIDENCE_DIR="evidence/05_disk_full"
mkdir -p "$EVIDENCE_DIR"

echo "=== Test 05: Disk Full ===" | tee "$EVIDENCE_DIR/run.log"
date | tee -a "$EVIDENCE_DIR/run.log"

pipeline status | tee "$EVIDENCE_DIR/status_before.json"

# Fill disk (leave ~20MB for OS) — adjust /data path to your data partition
FREE_KB=$(df /data 2>/dev/null | awk 'NR==2{print $4}' || df . | awk 'NR==2{print $4}')
FILL_KB=$((FREE_KB - 20480))
if [ "$FILL_KB" -gt 0 ]; then
  echo "Filling ${FILL_KB}KB..." | tee -a "$EVIDENCE_DIR/run.log"
  dd if=/dev/zero of=/tmp/disk_fill bs=1024 count="$FILL_KB" 2>&1 | tee -a "$EVIDENCE_DIR/run.log" || true
fi

# Wait 10 seconds for disk-full losses to appear
sleep 10
pipeline status | tee "$EVIDENCE_DIR/status_disk_full.json"
echo "Pipeline still alive: PASS" | tee -a "$EVIDENCE_DIR/run.log"

# Check for disk_full loss records
pipeline export --session "$SESSION_ID" --from device | \
  python3 -c "
import json,sys
losses=[json.loads(l) for l in sys.stdin if l.strip() and 'reason' in json.loads(l)]
df=[l for l in losses if l.get('reason')=='disk_full']
print('disk_full losses:', len(df))
print('PASS' if df else 'FAIL: no disk_full losses recorded')
" | tee -a "$EVIDENCE_DIR/run.log"

# Free disk
rm -f /tmp/disk_fill
echo "Disk freed" | tee -a "$EVIDENCE_DIR/run.log"
sleep 5
pipeline status | tee "$EVIDENCE_DIR/status_after.json"
echo "Pipeline recovered: $(pipeline status | python3 -c 'import sys,json;print(json.load(sys.stdin)[\"mode\"])')" \
  | tee -a "$EVIDENCE_DIR/run.log"

echo "=== DONE ===" | tee -a "$EVIDENCE_DIR/run.log"
