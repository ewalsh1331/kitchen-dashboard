#!/usr/bin/env bash
# Install the Kitchen connector (connector/server.py) as a user service that starts at boot
# (user lingering is on), and expose it through Tailscale Funnel.
# Funnel only proxies 127.0.0.1:8090; the dashboard (8080) stays private.
set -eu
APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
mkdir -p ~/.config/systemd/user

cat > ~/.config/systemd/user/kitchen-connector.service <<EOF
[Unit]
Description=Kitchen connector (MCP tools for the Claude app)
After=network-online.target

[Service]
WorkingDirectory=$APP_DIR
ExecStart=$APP_DIR/.venv/bin/python $APP_DIR/connector/server.py
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
EOF

systemctl --user daemon-reload
systemctl --user enable --now kitchen-connector.service
tailscale funnel --bg 8090
systemctl --user --no-pager status kitchen-connector.service | head -n 5
