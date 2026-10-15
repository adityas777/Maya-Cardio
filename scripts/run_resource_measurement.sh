#!/usr/bin/env bash
# run_resource_measurement.sh — Automated 1-hour (or custom duration) resource measurement
set -euo pipefail

DURATION="${1:-3600}"   # Default 3600s (1 hour), or pass e.g. 180 for a 3-minute test
INTERVAL=5

echo "=== STARTING RESOURCE MEASUREMENT (${DURATION}s) ==="
source /home/pglap/.venv/bin/activate
cd /home/pglap/pipeline

# 1. Clean up any previous processes
killall -9 python python3 pipeline uvicorn 2>/dev/null || true
rm -rf data/*
mkdir -p data evidence

# 2. Get local interface IP
WSL_IP=$(ip -4 addr show eth0 2>/dev/null | awk '/inet /{print $2}' | cut -d/ -f1 || echo "127.0.0.1")

# 3. Configure pipeline for online cloud upload
cat > config/pipeline.toml << EOF
[mock]
host = "127.0.0.1"
port = 7100

[store]
db_path = "data/pipeline.db"

[cloud]
base_url = "http://127.0.0.1:8000"
bearer_token_env = "PIPELINE_CLOUD_TOKEN"
upload_batch_samples = 400
catchup_batch_samples = 24000
health_timeout_s = 1.0

[mode]
probe_interval_s = 1.0
offline_after_failures = 2
online_after_successes = 2

[processing]
grace_period_s = 3.0
EOF

export PIPELINE_CLOUD_TOKEN="dev-token"

# 4. Start Cloud API in background
CLOUD_DB_PATH=data/cloud.db python -m uvicorn pipeline.cloud_api:app --host 127.0.0.1 --port 8000 > /dev/null 2>&1 &
CLOUD_PID=$!
sleep 2

# 5. Start Fake Mock in Signal mode (ECG signal mode with seed 7 as specified in AGENT_BUILD_SPEC)
python -m tests.fake_mock --mode signal --seed 7 --duration $((DURATION + 300)) --port 7100 > /dev/null 2>&1 &
MOCK_PID=$!
sleep 2

# 6. Start Pipeline in online mode
pipeline --config config/pipeline.toml run > /dev/null 2>&1 &
PIPE_PID=$!
sleep 4

echo "Pipeline running with PID=$PIPE_PID"
echo "Sampling resources for ${DURATION}s..."

# 7. Run resource measurement
# Override DURATION in measure_1h
sed -i "s/^DURATION=.*/DURATION=$DURATION/" scripts/resources/measure_1h.sh
bash scripts/resources/measure_1h.sh "$PIPE_PID" "lo"

# 8. Clean up
kill -9 "$PIPE_PID" "$MOCK_PID" "$CLOUD_PID" 2>/dev/null || true
killall -9 python python3 pipeline uvicorn 2>/dev/null || true

# 9. Sync evidence to Windows
cp -r evidence/* /mnt/c/Users/pglap/OneDrive/Desktop/MAYACARDIO/pipeline/evidence/ 2>/dev/null || true

echo "=== RESOURCE MEASUREMENT COMPLETE ==="
