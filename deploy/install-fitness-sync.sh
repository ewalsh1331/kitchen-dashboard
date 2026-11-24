#!/usr/bin/env bash
# Schedule the Oura/Garmin -> Fitness Data sheet sync on the Pi (user timers; lingering is on).
#   daily: every day 8:00 AM (Garmin + Oura)      golf: Mon + Fri 8:30 AM
# Persistent=true: a run missed while the Pi was off happens at next boot.
set -eu
APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
U=~/.config/systemd/user
mkdir -p "$U"

for kind in daily golf; do
  cat > "$U/fitness-sync-$kind.service" <<EOF
[Unit]
Description=Fitness sheet sync ($kind)
After=network-online.target

[Service]
Type=oneshot
ExecStart=/usr/bin/bash $APP_DIR/deploy/run_fitness_sync.sh $kind
EOF
done

cat > "$U/fitness-sync-daily.timer" <<EOF
[Unit]
Description=Fitness sheet sync, daily 8 AM

[Timer]
OnCalendar=*-*-* 08:00:00
Persistent=true
RandomizedDelaySec=120

[Install]
WantedBy=timers.target
EOF

cat > "$U/fitness-sync-golf.timer" <<EOF
[Unit]
Description=Fitness sheet golf sync, Mon + Fri 8:30 AM

[Timer]
OnCalendar=Mon,Fri *-*-* 08:30:00
Persistent=true

[Install]
WantedBy=timers.target
EOF

systemctl --user daemon-reload
systemctl --user enable --now fitness-sync-daily.timer fitness-sync-golf.timer
systemctl --user list-timers 'fitness-sync-*' --no-pager
