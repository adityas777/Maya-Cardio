#!/usr/bin/env bash
set -e
source /home/pglap/.venv/bin/activate
cd /home/pglap/pipeline

echo "=== TEST 05: DISK FULL SIMULATION ==="
killall -9 python python3 pipeline 2>/dev/null || true
mkdir -p evidence/05_disk_full

# Create a small 6MB loop filesystem
echo "Setting up small 6MB virtual disk..."
sudo umount /tmp/small_disk 2>/dev/null || true
dd if=/dev/zero of=/tmp/small_disk.img bs=1M count=6 status=none
mkfs.ext4 -F /tmp/small_disk.img >/dev/null 2>&1
sudo mkdir -p /tmp/small_disk
sudo mount -o loop /tmp/small_disk.img /tmp/small_disk
sudo chown -R pglap:pglap /tmp/small_disk

# Create temporary pipeline config pointing to small disk
cat > config/pipeline_small_disk.toml << 'EOF'
[mock]
host = "127.0.0.1"
port = 7100

[store]
db_path = "/tmp/small_disk/pipeline.db"

[cloud]
base_url = "http://localhost:9999"
token = "dummy"

[mode]
probe_interval_s = 2
offline_after_failures = 2
online_after_successes = 2

[uploader]
batch_size = 400
EOF

# Start fake mock in ramp mode
python -m tests.fake_mock --mode ramp --seed 5 --duration 600 --port 7100 &
sleep 2

# Start pipeline pointing to small disk
pipeline --config config/pipeline_small_disk.toml run &
PIPE_PID=$!
sleep 2

pipeline --config config/pipeline_small_disk.toml status | tee evidence/05_disk_full/status_before.json
SESSION_ID=$(pipeline --config config/pipeline_small_disk.toml status | python3 -c "import sys,json; print(json.load(sys.stdin)['session_id'])")
echo "Session ID: $SESSION_ID"

# Let it write until the 6MB disk is full
echo "Waiting for disk to fill..."
sleep 15

pipeline --config config/pipeline_small_disk.toml status | tee evidence/05_disk_full/status_disk_full.json

# Check if pipeline survived
if kill -0 $PIPE_PID 2>/dev/null; then
  echo "PASS: pipeline survived disk full condition" | tee -a evidence/05_disk_full/run.log
else
  echo "FAIL: pipeline exited" | tee -a evidence/05_disk_full/run.log
fi

# Clean up
killall -9 python python3 pipeline 2>/dev/null || true
sudo umount /tmp/small_disk 2>/dev/null || true
rm -f /tmp/small_disk.img config/pipeline_small_disk.toml

cp -r evidence/05_disk_full /mnt/c/Users/pglap/OneDrive/Desktop/MAYACARDIO/pipeline/evidence/ 2>/dev/null || true
echo "=== TEST 05 COMPLETE ==="
