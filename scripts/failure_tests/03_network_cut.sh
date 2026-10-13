#!/usr/bin/env bash
# 03_network_cut.sh — Test: cut network to cloud, verify offline switch ≤10s,
#                     restore, verify online switch ≤10s, no loss/dup.

set -euo pipefail
SESSION_ID="${1:-}"
CLOUD_IP="${2:-}"   # e.g. 1.2.3.4
EVIDENCE_DIR="evidence/03_network_cut"
mkdir -p "$EVIDENCE_DIR"

echo "=== Test 03: Network Cut ===" | tee "$EVIDENCE_DIR/run.log"
echo "Date: $(date)" | tee -a "$EVIDENCE_DIR/run.log"
echo "Session: $SESSION_ID  Cloud IP: $CLOUD_IP" | tee -a "$EVIDENCE_DIR/run.log"

# Status before cut
pipeline status | tee "$EVIDENCE_DIR/status_before.json"

# Record monotonic time of cut
CUT_MONO=$(python3 -c "import time; print(time.monotonic())")
echo "Network cut at mono=$CUT_MONO wall=$(date)" | tee -a "$EVIDENCE_DIR/run.log"

# Drop traffic to cloud IP
sudo iptables -A OUTPUT -d "$CLOUD_IP" -j DROP

# Poll pipeline status until mode=offline (max 15s)
for i in $(seq 1 15); do
  sleep 1
  MODE=$(pipeline status | python3 -c "import sys,json; print(json.load(sys.stdin)['mode'])")
  echo "  t=${i}s mode=$MODE" | tee -a "$EVIDENCE_DIR/run.log"
  if [ "$MODE" = "offline" ]; then
    SWITCH_OFFLINE_S=$i
    echo "OFFLINE in ${i}s" | tee -a "$EVIDENCE_DIR/run.log"
    break
  fi
done

if [ "${SWITCH_OFFLINE_S:-99}" -gt 10 ]; then
  echo "FAIL: did not switch offline within 10s" | tee -a "$EVIDENCE_DIR/run.log"
fi

# Keep network down for 60s
echo "Keeping network down for 60s..." | tee -a "$EVIDENCE_DIR/run.log"
sleep "$((60 - SWITCH_OFFLINE_S))"

# Restore network
sudo iptables -D OUTPUT -d "$CLOUD_IP" -j DROP
echo "Network restored at $(date)" | tee -a "$EVIDENCE_DIR/run.log"

# Poll for online
for i in $(seq 1 15); do
  sleep 1
  MODE=$(pipeline status | python3 -c "import sys,json; print(json.load(sys.stdin)['mode'])")
  echo "  t=${i}s mode=$MODE" | tee -a "$EVIDENCE_DIR/run.log"
  if [ "$MODE" = "online" ]; then
    echo "ONLINE in ${i}s" | tee -a "$EVIDENCE_DIR/run.log"
    break
  fi
done

# Wait for catch-up
echo "Waiting for catch-up (max 15 min)..." | tee -a "$EVIDENCE_DIR/run.log"
for i in $(seq 1 90); do
  sleep 10
  STATUS=$(pipeline status)
  BACKLOG=$(echo "$STATUS" | python3 -c "import sys,json; print(json.load(sys.stdin)['backlog'])")
  echo "  t=$((i*10))s backlog=$BACKLOG" | tee -a "$EVIDENCE_DIR/run.log"
  if [ "$BACKLOG" = "0" ]; then
    echo "CATCH-UP COMPLETE in $((i*10))s" | tee -a "$EVIDENCE_DIR/run.log"
    break
  fi
done

# Export and compare
pipeline export --session "$SESSION_ID" --from device > "$EVIDENCE_DIR/export_device.jsonl"
pipeline export --session "$SESSION_ID" --from cloud  > "$EVIDENCE_DIR/export_cloud.jsonl"

python tests/compare_export.py --export "$EVIDENCE_DIR/export_device.jsonl" --mode ramp \
  | tee "$EVIDENCE_DIR/compare_device.txt"

diff "$EVIDENCE_DIR/export_device.jsonl" "$EVIDENCE_DIR/export_cloud.jsonl" \
  && echo "PASS: device==cloud export" | tee -a "$EVIDENCE_DIR/run.log" \
  || echo "DIFF: exports differ" | tee -a "$EVIDENCE_DIR/run.log"

echo "=== DONE ===" | tee -a "$EVIDENCE_DIR/run.log"
