# Cloud Deployment Guide

## Recommended: Oracle Cloud Always Free ARM64 VM

Oracle Cloud Always Free provides an ARM64 Ampere A1 VM (4 OCPUs, 24 GB RAM) at no cost — no
credit card charges, no trial conversion. This keeps "identical logic" trivial since the same
Python runs on both device and cloud.

**Verify no charges before using any cloud service.**

---

## Steps

### 1. Create Oracle Cloud Always Free account
- Go to <https://www.oracle.com/cloud/free/>
- Select **Always Free** resources only (Ampere A1 shape `VM.Standard.A1.Flex`)
- Region: any; pick one close to you

### 2. Create VM
- Shape: `VM.Standard.A1.Flex`  1 OCPU, 6 GB RAM (free tier allows up to 4 OCPUs)
- Image: Ubuntu 22.04 (ARM64) or Oracle Linux 9
- Open port 8000 (or 443 with TLS) in the Security List

### 3. SSH into VM and set up

```bash
# Install Python and dependencies
sudo apt-get update
sudo apt-get install -y python3.11 python3.11-venv python3-pip git

# Clone your repo (or scp the pipeline directory)
git clone https://github.com/YOUR_USERNAME/mayacardio-pipeline.git /opt/pipeline

# Create venv and install
cd /opt/pipeline
python3.11 -m venv .venv
.venv/bin/pip install -e .

# Create data directory
sudo mkdir -p /data
sudo chown ubuntu:ubuntu /data

# Set env vars
export PIPELINE_CLOUD_TOKEN="$(openssl rand -hex 32)"
export CLOUD_DB_PATH="/data/cloud.db"
echo "Your token: $PIPELINE_CLOUD_TOKEN"
# Save this token — put it in pipeline.toml bearer_token_env on the Pi
```

### 4. Run the cloud API

```bash
# Development / quick start:
PIPELINE_CLOUD_TOKEN=your_token CLOUD_DB_PATH=/data/cloud.db \
  .venv/bin/uvicorn pipeline.cloud_api:app --host 0.0.0.0 --port 8000

# Production (systemd):
cat > /etc/systemd/system/cloud-api.service << 'EOF'
[Unit]
Description=MayaCardio Cloud API
After=network.target

[Service]
User=ubuntu
WorkingDirectory=/opt/pipeline
Environment="PIPELINE_CLOUD_TOKEN=YOUR_TOKEN_HERE"
Environment="CLOUD_DB_PATH=/data/cloud.db"
ExecStart=/opt/pipeline/.venv/bin/uvicorn pipeline.cloud_api:app \
    --host 0.0.0.0 --port 8000 --workers 1
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable --now cloud-api
```

### 5. (Optional) TLS with Let's Encrypt

```bash
sudo apt-get install -y nginx certbot python3-certbot-nginx
# Set up nginx reverse proxy on 443 → 8000
# certbot --nginx -d your-domain.example.com
```

### 6. Verify the health endpoint

```bash
curl http://YOUR_VM_IP:8000/v1/health
# {"status":"ok","timestamp_ms":...}
```

### 7. Update pipeline.toml on the Pi

```toml
[cloud]
base_url = "http://YOUR_VM_IP:8000"   # or https:// if TLS configured
bearer_token_env = "PIPELINE_CLOUD_TOKEN"
```

Set `PIPELINE_CLOUD_TOKEN` in the Pi's systemd unit:
```
Environment="PIPELINE_CLOUD_TOKEN=your_token_here"
```

---

## Checking free-tier limits

- Oracle Always Free: 10 GB storage, 10 Mbps outbound — more than enough for this workload
  (2h catch-up ≈ 5.76M samples × ~50 bytes/sample ≈ 288 MB JSON)
- **Do NOT enable paid tiers.** If prompted for credit card charges, stop immediately.

---

## Alternative: Cloudflare Workers (free plan)

Not recommended for this submission because:
- Workers have 10ms CPU per request limit (too low for window processing)
- Durable Objects have limited free-tier storage

Use Oracle Cloud Always Free instead.
