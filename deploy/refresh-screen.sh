#!/usr/bin/env bash
# Restart the dashboard service and reopen the full-screen browser,
# so edits to app.py / static/ show up immediately. Safe to run over SSH.
set -u
APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"

sudo -n systemctl restart kitchen-dashboard || {
  echo "Could not restart the service without a password (see install step for sudoers rule)."; exit 1; }

pkill -f "chromium.*--kiosk" 2>/dev/null
sleep 2

export XDG_RUNTIME_DIR="/run/user/$(id -u)"
export WAYLAND_DISPLAY="${WAYLAND_DISPLAY:-wayland-0}"
export DISPLAY="${DISPLAY:-:0}"
setsid nohup "$APP_DIR/deploy/kiosk.sh" >/dev/null 2>&1 < /dev/null &
echo "Dashboard service restarted and browser relaunched."
