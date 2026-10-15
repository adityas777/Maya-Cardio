#!/usr/bin/env bash
set -e
source /home/pglap/.venv/bin/activate
cd /home/pglap/pipeline

echo "=== PREPARING TEST 03: NETWORK CUT ==="
killall -9 python python3 pipeline uvicorn 2>/dev/null || true
rm -rf data/*
mkdir -p data evidence/03_network_cut

WSL_IP=$(ip -4 addr show eth0 | awk '/inet /{print $2}' | cut -d/ -f1)
echo "WSL IP for Cloud: $WSL_IP"

# Clear any lingering iptables DROP rules
sudo iptables -D OUTPUT -d "$WSL_IP" -j DROP 2>/dev/null || true

# Config with cloud enabled
cat > config/pipeline.toml << EOF
[mock]
host = "127.0.0.1"
port = 7100

[store]
db_path = "data/pipeline.db"

[cloud]
base_url = "http://${WSL_IP}:8000"
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

# Start Cloud API
CLOUD_DB_PATH=data/cloud.db python -m uvicorn pipeline.cloud_api:app --host 0.0.0.0 --port 8000 &
CLOUD_PID=$!
sleep 2

# Start fake mock in ramp mode
python -m tests.fake_mock --mode ramp --seed 3 --duration 600 --port 7100 &
MOCK_PID=$!
sleep 2

# Start pipeline
pipeline --config config/pipeline.toml run &
PIPE_PID=$!
sleep 6

SESSION_ID=$(pipeline status | python3 -c "import sys,json; print(json.load(sys.stdin)['session_id'])")
echo "Session ID: $SESSION_ID"

# Run 03_network_cut.sh
bash scripts/failure_tests/03_network_cut.sh "$SESSION_ID" "$WSL_IP"

# Clean up
killall -9 python python3 pipeline uvicorn 2>/dev/null || true
sudo iptables -D OUTPUT -d "$WSL_IP" -j DROP 2>/dev/null || true

cp -r evidence/03_network_cut /mnt/c/Users/pglap/OneDrive/Desktop/MAYACARDIO/pipeline/evidence/ 2>/dev/null || true
echo "=== TEST 03 COMPLETE ==="
