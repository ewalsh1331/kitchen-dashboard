#!/usr/bin/env bash
# One-time setup on the Pi. Run from inside the kitchen-dashboard folder:
#   bash deploy/setup.sh
set -euo pipefail

APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
USER_NAME="$(whoami)"

echo "==> Installing system packages"
sudo apt-get update -y
sudo apt-get install -y python3-venv curl

echo "==> Creating Python environment"
python3 -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/pip" install --upgrade pip
"$APP_DIR/.venv/bin/pip" install -r "$APP_DIR/requirements.txt"

echo "==> Installing the dashboard service (starts on boot)"
sudo tee /etc/systemd/system/kitchen-dashboard.service >/dev/null <<EOF
[Unit]
Description=Kitchen dashboard server
After=network-online.target
Wants=network-online.target

[Service]
User=$USER_NAME
WorkingDirectory=$APP_DIR
ExecStart=$APP_DIR/.venv/bin/python $APP_DIR/app.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
sudo systemctl daemon-reload
sudo systemctl enable --now kitchen-dashboard.service

echo "==> Opening the dashboard full-screen at login"
chmod +x "$APP_DIR/deploy/kiosk.sh"
mkdir -p "$HOME/.config/autostart"
cat > "$HOME/.config/autostart/kitchen-dashboard.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Kitchen Dashboard
Exec=$APP_DIR/deploy/kiosk.sh
X-GNOME-Autostart-enabled=true
EOF

echo "==> Turning off screen blanking"
sudo raspi-config nonint do_blanking 1 || echo "   (set manually: raspi-config > Display Options > Screen Blanking > No)"

echo
echo "Done. Next: run auth.py (see the guide), then reboot with: sudo reboot"
