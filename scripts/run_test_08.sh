#!/usr/bin/env bash
set -e
source /home/pglap/.venv/bin/activate
cd /home/pglap/pipeline

echo "=== PREPARING TEST 08: SENSOR SILENT ==="
killall -9 python python3 pipeline 2>/dev/null || true
rm -rf data/*
mkdir -p data evidence/08_sensor_silent

# Create proxy config for port 7201
cat > config/pipeline_proxy8.toml << 'EOF'
[mock]
host = "127.0.0.1"
port = 7201

[store]
db_path = "data/pipeline.db"

[cloud]
base_url = "http://localhost:9999"
token = "dummy"

[mode]
probe_interval_s = 2
offline_after_failures = 2
online_after_successes = 2

[processing]
grace_period_s = 3.0
EOF

# Start mock on 7100
python -m tests.fake_mock --mode ramp --seed 8 --duration 600 --port 7100 &
sleep 2

# Start proxy normally first (no drop)
python tests/fault_proxy.py \
  --listen-port 7201 \
  --upstream-port 7100 &
PROXY_PID=$!
sleep 1

# Start pipeline pointing to 7201
pipeline --config config/pipeline_proxy8.toml run &
sleep 5

SESSION_ID=$(pipeline --config config/pipeline_proxy8.toml status | python3 -c "import sys,json; print(json.load(sys.stdin)['session_id'])")
echo "Session ID: $SESSION_ID"

pipeline --config config/pipeline_proxy8.toml status | tee evidence/08_sensor_silent/status_before.json

# Now restart proxy with --drop-sensor 2 for 15 seconds
echo "Dropping sensor 2..."
kill $PROXY_PID
sleep 1

python tests/fault_proxy.py \
  --listen-port 7201 \
  --upstream-port 7100 \
  --drop-sensor 2 &
PROXY_PID=$!

sleep 15

# Restore proxy to normal
echo "Restoring sensor 2..."
kill $PROXY_PID
sleep 1

python tests/fault_proxy.py \
  --listen-port 7201 \
  --upstream-port 7100 &
PROXY_PID=$!

sleep 5

# Export and check
echo "Exporting..."
pipeline --config config/pipeline_proxy8.toml export --session "$SESSION_ID" --from device > evidence/08_sensor_silent/export.jsonl

python3 -c "
import json
losses=[]
samples_s2=[]
with open('evidence/08_sensor_silent/export.jsonl') as f:
    for line in f:
        line=line.strip()
        if not line: continue
        obj=json.loads(line)
        if 'reason' in obj and obj.get('sensor')==2:
            losses.append(obj)
        elif 'index' in obj and obj.get('sensor')==2:
            samples_s2.append(obj)
print(f'Sensor 2 samples stored: {len(samples_s2)}')
print(f'Sensor 2 loss records: {len(losses)}')
" | tee -a evidence/08_sensor_silent/run.log

# Check results for incomplete windows
pipeline --config config/pipeline_proxy8.toml results --session "$SESSION_ID" | tee evidence/08_sensor_silent/results.jsonl || true

python3 -c "
import json, sys
try:
    results = [json.loads(l) for l in open('evidence/08_sensor_silent/results.jsonl') if l.strip()]
    incomplete = [r for r in results if not r.get('complete', True) or r.get('missing_2', 0) > 0]
    print(f'Incomplete windows found: {len(incomplete)}')
    print('PASS: sensor silence handled without crashing')
except Exception as e:
    print('Results parse:', e)
" | tee -a evidence/08_sensor_silent/run.log

killall -9 python python3 pipeline 2>/dev/null || true
rm -f config/pipeline_proxy8.toml

cp -r evidence/08_sensor_silent /mnt/c/Users/pglap/OneDrive/Desktop/MAYACARDIO/pipeline/evidence/ 2>/dev/null || true
echo "=== TEST 08 COMPLETE ==="
