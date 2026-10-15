#!/usr/bin/env bash
set -e
source /home/pglap/.venv/bin/activate
cd /home/pglap/pipeline

echo "=== Cleaning old database ==="
rm -rf data/*
mkdir -p data evidence

echo "=== Starting FakeMock ramp mode ==="
python -m tests.fake_mock --mode ramp --seed 0 --duration 600 --port 7100 &
MOCK_PID=$!
sleep 2

echo "=== Starting Pipeline ==="
pipeline --config config/pipeline.toml run &
PIPE_PID=$!

echo "=== Collecting for 30 seconds ==="
sleep 30

echo "=== Checking status ==="
pipeline status | tee evidence/status_ramp.json

echo "=== Stopping Pipeline ==="
kill -SIGINT $PIPE_PID 2>/dev/null || true
wait $PIPE_PID 2>/dev/null || true
kill -9 $MOCK_PID 2>/dev/null || true

SESSION_ID=$(python3 -c "import json; data=json.load(open('evidence/status_ramp.json')); print(data['session_id'])")
echo "Session ID: $SESSION_ID"

echo "=== Exporting Device Data ==="
pipeline export --session "$SESSION_ID" --from device > evidence/ramp_export.jsonl

echo "=== Running Ramp Integrity Check ==="
python tests/compare_export.py --export evidence/ramp_export.jsonl --mode ramp | tee evidence/ramp_compare_result.txt

echo "=== Copying evidence back to Windows ==="
cp -r evidence/* /mnt/c/Users/pglap/OneDrive/Desktop/MAYACARDIO/pipeline/evidence/ 2>/dev/null || true

echo "=== DONE ==="
