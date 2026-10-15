#!/usr/bin/env bash
set -e
source /home/pglap/.venv/bin/activate
cd /home/pglap/pipeline

echo "=== PREPARING TEST 07: CORRUPT AND DUPLICATE ==="
killall -9 python python3 pipeline 2>/dev/null || true
rm -rf data/*
mkdir -p data evidence/07_corrupt_and_duplicate

# Create proxy config
cat > config/pipeline_proxy.toml << 'EOF'
[mock]
host = "127.0.0.1"
port = 7200

[store]
db_path = "data/pipeline.db"

[cloud]
base_url = "http://localhost:9999"
token = "dummy"

[mode]
probe_interval_s = 2
offline_after_failures = 2
online_after_successes = 2
EOF

# Start mock on port 7100
python -m tests.fake_mock --mode ramp --seed 7 --duration 600 --port 7100 &
sleep 2

# Start pipeline pointing to proxy port 7200
pipeline --config config/pipeline_proxy.toml run &
sleep 1

# Start fault proxy (30% corrupt, 30% duplicate) on 7200
python tests/fault_proxy.py \
  --listen-port 7200 \
  --upstream-port 7100 \
  --corrupt-rate 0.3 \
  --duplicate-rate 0.3 \
  --seed 12345 &
PROXY_PID=$!
sleep 20

SESSION_ID=$(pipeline --config config/pipeline_proxy.toml status | python3 -c "import sys,json; print(json.load(sys.stdin)['session_id'])")
echo "Session ID: $SESSION_ID"

pipeline --config config/pipeline_proxy.toml status | tee evidence/07_corrupt_and_duplicate/status.json

# Export and check duplicates
pipeline --config config/pipeline_proxy.toml export --session "$SESSION_ID" --from device > evidence/07_corrupt_and_duplicate/export.jsonl

python tests/compare_export.py --export evidence/07_corrupt_and_duplicate/export.jsonl --mode ramp \
  | tee evidence/07_corrupt_and_duplicate/compare.txt

python3 -c "
import json, collections, sys
counts = collections.Counter()
with open('evidence/07_corrupt_and_duplicate/export.jsonl') as f:
    for line in f:
        line = line.strip()
        if not line: continue
        obj = json.loads(line)
        if 'index' in obj:
            counts[(obj['sensor'], obj['index'])] += 1
dups = {k: v for k, v in counts.items() if v > 1}
if dups:
    print('FAIL: duplicates found:', list(dups.items())[:5])
    sys.exit(1)
else:
    print('PASS: no duplicates despite proxy duplication')
" | tee -a evidence/07_corrupt_and_duplicate/run.log

killall -9 python python3 pipeline 2>/dev/null || true
rm -f config/pipeline_proxy.toml

cp -r evidence/07_corrupt_and_duplicate /mnt/c/Users/pglap/OneDrive/Desktop/MAYACARDIO/pipeline/evidence/ 2>/dev/null || true
echo "=== TEST 07 COMPLETE ==="
