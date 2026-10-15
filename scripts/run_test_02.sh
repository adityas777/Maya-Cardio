#!/usr/bin/env bash
set -e
source /home/pglap/.venv/bin/activate
cd /home/pglap/pipeline

echo "=== TEST 02: POWER CUT SIMULATION ==="
killall -9 python python3 pipeline 2>/dev/null || true
rm -rf data/*
mkdir -p data evidence/02_power_cut

# Start fake mock in ramp mode
python -m tests.fake_mock --mode ramp --seed 123 --duration 600 --port 7100 &
MOCK_PID=$!
sleep 2

# Start pipeline
pipeline --config config/pipeline.toml run &
PIPE_PID=$!
sleep 10

# Capture status before power cut
pipeline status | tee evidence/02_power_cut/status_before.json
SESSION_ID=$(pipeline status | python3 -c "import sys,json; print(json.load(sys.stdin)['session_id'])")

# Simulate power cut: kill all immediately with SIGKILL (no clean shutdown)
echo "Simulating power cut (SIGKILL on all processes)..."
kill -9 $PIPE_PID $MOCK_PID 2>/dev/null || true
sleep 3

# Simulate reboot: restart mock and pipeline
echo "Simulating reboot and restart..."
python -m tests.fake_mock --mode ramp --seed 123 --duration 600 --port 7100 &
MOCK_PID=$!
sleep 2

pipeline --config config/pipeline.toml run &
PIPE_PID=$!
sleep 8

# Status after reboot
pipeline status | tee evidence/02_power_cut/status_after.json

# Export and check loss records
echo "Exporting and extracting losses..."
pipeline export --session "$SESSION_ID" --from device > evidence/02_power_cut/export.jsonl
grep '"reason"' evidence/02_power_cut/export.jsonl > evidence/02_power_cut/losses.jsonl || touch evidence/02_power_cut/losses.jsonl

python3 -c "
import json
with open('evidence/02_power_cut/losses.jsonl') as f:
    losses = [json.loads(line) for line in f if line.strip()]
print(f'Total loss records found: {len(losses)}')
for l in losses:
    print('Loss record:', l)
" | tee evidence/02_power_cut/summary.txt

killall -9 python python3 pipeline 2>/dev/null || true

cp -r evidence/02_power_cut /mnt/c/Users/pglap/OneDrive/Desktop/MAYACARDIO/pipeline/evidence/ 2>/dev/null || true
echo "=== TEST 02 COMPLETE ==="
