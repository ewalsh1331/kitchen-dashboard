#!/usr/bin/env bash
# Sets up the 8 PM daily check-in. Run from ~/kitchen-dashboard: bash deploy/install-checkin.sh
set -euo pipefail
cd "$(dirname "$0")/.."
APP_DIR="$(pwd)"
mkdir -p data
git add -A && git commit -qm "Before check-in" || true

# 1. A private notification topic (the random name is what keeps it private)
TOPIC=$(.venv/bin/python - <<'EOF'
import json, pathlib, secrets
p = pathlib.Path("config.json"); c = json.loads(p.read_text())
c.setdefault("ntfy_topic", "kitchen-pi-" + secrets.token_hex(8))
p.write_text(json.dumps(c, indent=2) + "\n")
print(c["ntfy_topic"])
EOF
)
chmod +x deploy/checkin.sh

# 2. Daily timer at 8:00 PM (Pi clock is America/Chicago)
mkdir -p ~/.config/systemd/user
cat > ~/.config/systemd/user/kitchen-checkin.service <<EOF
[Unit]
Description=Kitchen Pi evening check-in

[Service]
Type=oneshot
WorkingDirectory=$APP_DIR
ExecStart=/usr/bin/bash $APP_DIR/deploy/checkin.sh
EOF
cat > ~/.config/systemd/user/kitchen-checkin.timer <<EOF
[Unit]
Description=Kitchen Pi evening check-in at 8 PM

[Timer]
OnCalendar=*-*-* 20:00:00
Persistent=false

[Install]
WantedBy=timers.target
EOF
systemctl --user daemon-reload
systemctl --user enable --now kitchen-checkin.timer

git add -A && git commit -qm "Evening check-in" && echo "Saved." || true
echo
echo "Pi time zone: $(timedatectl show -p Timezone --value)"
systemctl --user list-timers kitchen-checkin.timer --no-pager | head -3
echo
echo "================================================"
echo "  Subscribe to this topic in the ntfy app:"
echo "  $TOPIC"
echo "================================================"
