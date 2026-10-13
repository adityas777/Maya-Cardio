#!/usr/bin/env bash
# setup_pi.sh — Fresh-board install on a Raspberry Pi 4 (Raspberry Pi OS 64-bit / Debian Bookworm).
# Run as root or with sudo from the cloned repo directory.

set -euo pipefail

INSTALL_DIR="/opt/pipeline"
SERVICE_USER="pi"
PYTHON_MIN="3.10"

echo "=== MayaCardio Pipeline — Pi Setup ==="
echo "Installing to $INSTALL_DIR as user $SERVICE_USER"

# ── System packages ──────────────────────────────────────────────────────────
apt-get update -qq
apt-get install -y --no-install-recommends \
  python3 python3-venv python3-pip \
  sqlite3 \
  iptables \
  bc \
  git \
  curl

# ── Python version check ────────────────────────────────────────────────────
PYTHON_VER=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
echo "Python $PYTHON_VER"
python3 -c "import sys; assert sys.version_info >= (3,10), 'Python 3.10+ required'" \
  || { echo "ERROR: Python 3.10+ required"; exit 1; }

# ── Create install directory ─────────────────────────────────────────────────
mkdir -p "$INSTALL_DIR"
cp -r . "$INSTALL_DIR/"
chown -R "$SERVICE_USER:$SERVICE_USER" "$INSTALL_DIR"

# ── Virtual environment ──────────────────────────────────────────────────────
sudo -u "$SERVICE_USER" python3 -m venv "$INSTALL_DIR/.venv"
sudo -u "$SERVICE_USER" "$INSTALL_DIR/.venv/bin/pip" install --upgrade pip wheel
sudo -u "$SERVICE_USER" "$INSTALL_DIR/.venv/bin/pip" install -e "$INSTALL_DIR"

# ── Data directory ───────────────────────────────────────────────────────────
mkdir -p "$INSTALL_DIR/data"
chown "$SERVICE_USER:$SERVICE_USER" "$INSTALL_DIR/data"

# ── Config file ──────────────────────────────────────────────────────────────
if [ ! -f "$INSTALL_DIR/config/pipeline.toml" ]; then
  cp "$INSTALL_DIR/config/pipeline.example.toml" "$INSTALL_DIR/config/pipeline.toml"
  echo ""
  echo "IMPORTANT: Edit $INSTALL_DIR/config/pipeline.toml and set:"
  echo "  [cloud] base_url = \"https://your-cloud-api.example.com\""
  echo "  Set env var PIPELINE_CLOUD_TOKEN in /etc/systemd/system/pipeline.service"
fi

# ── Systemd units ────────────────────────────────────────────────────────────
cp "$INSTALL_DIR/systemd/pipeline.service"       /etc/systemd/system/
cp "$INSTALL_DIR/systemd/biosignal-mock.service" /etc/systemd/system/

# Update WorkingDirectory in service files
sed -i "s|/opt/pipeline|$INSTALL_DIR|g" /etc/systemd/system/pipeline.service
sed -i "s|/opt/pipeline|$INSTALL_DIR|g" /etc/systemd/system/biosignal-mock.service

systemctl daemon-reload
systemctl enable biosignal-mock.service
systemctl enable pipeline.service

echo ""
echo "=== Setup complete ==="
echo ""
echo "Next steps:"
echo "  1. Edit $INSTALL_DIR/config/pipeline.toml"
echo "  2. Set PIPELINE_CLOUD_TOKEN in /etc/systemd/system/pipeline.service"
echo "  3. systemctl start biosignal-mock"
echo "  4. systemctl start pipeline"
echo "  5. pipeline status"
echo ""
echo "Run tests: cd $INSTALL_DIR && .venv/bin/pytest tests/ -v"
