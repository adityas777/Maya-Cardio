#!/usr/bin/env bash
set -e
source /home/pglap/.venv/bin/activate
cd /home/pglap/pipeline

echo "=== PREPARING TEST 04: MOCK RESTART ==="
killall -9 python python3 pipeline 2>/dev/null || true
rm -rf data/*
mkdir -p data evidence/04_mock_restart

# Start initial mock
python -m tests.fake_mock --mode ramp --seed 1 --duration 600 --port 7100 &
sleep 2

# Start pipeline
pipeline --config config/pipeline.toml run &
sleep 6

# Run 04_mock_restart.sh
bash scripts/failure_tests/04_mock_restart.sh

# Clean up
killall -9 python python3 pipeline 2>/dev/null || true

cp -r evidence/04_mock_restart /mnt/c/Users/pglap/OneDrive/Desktop/MAYACARDIO/pipeline/evidence/ 2>/dev/null || true
echo "=== TEST 04 COMPLETE ==="
