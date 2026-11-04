#!/usr/bin/env bash
# Nightly backup: commit any uncommitted changes and push to GitHub.
# Secrets are excluded by .gitignore. Installed by deploy/install-autosave.sh.
set -uo pipefail
cd "$(dirname "$0")/.."
LOG=data/autosave.log
mkdir -p data
GIT="git -c user.name=Eric -c user.email=e.walsh1331@gmail.com"

if [ -n "$(git status --porcelain)" ]; then
  git add -A
  $GIT commit -qm "Auto-save $(date '+%Y-%m-%d %H:%M')" && echo "$(date) committed changes" >> "$LOG"
fi

if git push -q origin HEAD 2>>"$LOG"; then
  echo "$(date) pushed $(git rev-parse --short HEAD)" >> "$LOG"
else
  echo "$(date) push FAILED (offline?) - will retry next run" >> "$LOG"
  exit 1
fi
