#!/usr/bin/env bash
set -e
source /home/pglap/.venv/bin/activate
cd /home/pglap/pipeline

echo "=== PREPARING TEST 06: CLOCK CHANGE ==="
killall -9 python python3 pipeline 2>/dev/null || true
rm -rf data/*
mkdir -p data evidence/06_clock_change

# Start mock
python -m tests.fake_mock --mode ramp --seed 6 --duration 600 --port 7100 &
sleep 2

# Start pipeline
pipeline --config config/pipeline.toml run &
sleep 5

SESSION_ID=$(pipeline status | python3 -c "import sys,json; print(json.load(sys.stdin)['session_id'])")
echo "Session ID: $SESSION_ID"

# Run 06_clock_change.sh
bash scripts/failure_tests/06_clock_change.sh "$SESSION_ID"

# Clean up
killall -9 python python3 pipeline 2>/dev/null || true

cp -r evidence/06_clock_change /mnt/c/Users/pglap/OneDrive/Desktop/MAYACARDIO/pipeline/evidence/ 2>/dev/null || true
echo "=== TEST 06 COMPLETE ==="
