#!/usr/bin/env bash
# Daily Oura + Garmin -> Fitness Data sheet sync (moved from the PC's run_daily_pull.bat).
#   bash deploy/run_fitness_sync.sh          daily pull: Garmin, then Oura
#   bash deploy/run_fitness_sync.sh golf     Garmin golf rounds/holes/shots
# Scripts and credentials live in sync/ (credentials are git-ignored, mode 600).
# Log: data/fitness_sync.log. A failed run sends an ntfy alert to Eric's phone.
set -u
APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
SYNC="$APP_DIR/sync"
PY="$SYNC/.venv/bin/python"
LOG="$APP_DIR/data/fitness_sync.log"
mkdir -p "$APP_DIR/data"

# The .env on OneDrive points at a Windows path; use the Pi's copy of the Google key instead.
# (python-dotenv does not override variables that are already set.)
export GOOGLE_SERVICE_ACCOUNT_FILE="$(ls "$SYNC"/garmin-data-*.json | head -n 1)"

cd "$SYNC" || exit 1
if [ "${1:-daily}" = "golf" ]; then
  steps=("garmin_golf_to_sheets.py")
else
  steps=("garmin_to_sheets.py" "oura_to_sheets.py")
fi

failed=()
{
  echo "=============================================="
  echo "Run started: $(date '+%Y-%m-%d %H:%M:%S') (${1:-daily})"
} >> "$LOG"
for s in "${steps[@]}"; do
  echo "--- $s ---" >> "$LOG"
  if ! "$PY" "$s" >> "$LOG" 2>&1; then
    failed+=("$s")
  fi
done
echo "Run finished: $(date '+%Y-%m-%d %H:%M:%S') failed=${failed[*]:-none}" >> "$LOG"

if [ ${#failed[@]} -gt 0 ]; then
  topic="$("$APP_DIR/.venv/bin/python" -c 'import json,sys; print(json.load(open(sys.argv[1]))["ntfy_topic"])' "$APP_DIR/config.json")"
  curl -s -H "Title: Fitness sync failed" -H "Tags: warning" \
    -d "Failed: ${failed[*]}. Ask Claude to check data/fitness_sync.log." "https://ntfy.sh/$topic" > /dev/null
  exit 1
fi
