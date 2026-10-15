#!/usr/bin/env bash
set -e
source /home/pglap/.venv/bin/activate
cd /home/pglap/pipeline

echo "=== PREPARING TEST 01: PROCESS KILL ==="
killall -9 python python3 pipeline 2>/dev/null || true
rm -rf data/*
mkdir -p data evidence/01_process_kill

# Start fake mock in ramp mode
python -m tests.fake_mock --mode ramp --seed 42 --duration 600 --port 7100 &
MOCK_PID=$!
sleep 2

# Start pipeline
pipeline --config config/pipeline.toml run &
PIPE_PID=$!
sleep 5

SESSION_ID=$(pipeline status | python3 -c "import sys,json; print(json.load(sys.stdin)['session_id'])")
echo "Session ID: $SESSION_ID"

# Run 01_process_kill.sh
bash scripts/failure_tests/01_process_kill.sh "$SESSION_ID" data/pipeline.db

# Stop processes
killall -9 python python3 pipeline 2>/dev/null || true

# Copy evidence to Windows workspace
cp -r evidence/01_process_kill /mnt/c/Users/pglap/OneDrive/Desktop/MAYACARDIO/pipeline/evidence/ 2>/dev/null || true

echo "=== TEST 01 COMPLETE ==="
