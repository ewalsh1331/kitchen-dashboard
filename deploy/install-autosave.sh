#!/usr/bin/env bash
# Sets up GitHub backup automation. Run from ~/kitchen-dashboard:
#   bash deploy/install-autosave.sh
# 1. Every commit is pushed right away (git post-commit hook, in background).
# 2. A nightly 2 AM timer commits anything left uncommitted and pushes.
set -euo pipefail
cd "$(dirname "$0")/.."
APP_DIR="$(pwd)"
chmod +x deploy/autosave.sh

cat > .git/hooks/post-commit <<'EOF'
#!/usr/bin/env bash
# Push each new commit to GitHub in the background (see deploy/install-autosave.sh).
( git push -q origin HEAD >/dev/null 2>&1 & )
EOF
chmod +x .git/hooks/post-commit

mkdir -p ~/.config/systemd/user
cat > ~/.config/systemd/user/kitchen-autosave.service <<EOF
[Unit]
Description=Kitchen Pi nightly GitHub backup

[Service]
Type=oneshot
WorkingDirectory=$APP_DIR
ExecStart=/usr/bin/bash $APP_DIR/deploy/autosave.sh
EOF
cat > ~/.config/systemd/user/kitchen-autosave.timer <<EOF
[Unit]
Description=Kitchen Pi nightly GitHub backup at 2 AM

[Timer]
OnCalendar=*-*-* 02:00:00
Persistent=true

[Install]
WantedBy=timers.target
EOF
systemctl --user daemon-reload
systemctl --user enable --now kitchen-autosave.timer
systemctl --user list-timers kitchen-autosave.timer --no-pager || true
