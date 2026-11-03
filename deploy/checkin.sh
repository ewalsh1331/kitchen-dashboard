#!/usr/bin/env bash
# Evening check-in: Claude gathers today's/tomorrow's info (read-only) and
# pushes a short summary to Eric's phone via ntfy.
#   bash deploy/checkin.sh          # real run
#   bash deploy/checkin.sh --test   # sends a test notification only
set -uo pipefail
cd "$(dirname "$0")/.."
CLAUDE="$HOME/.local/bin/claude"
TOPIC=$(.venv/bin/python -c "import json;print(json.load(open('config.json'))['ntfy_topic'])")
CLICK="https://claude.ai/code"

push() {  # $1 = title, $2 = body
  curl -fsS -H "Title: $1" -H "Click: $CLICK" -H "Tags: house" \
       -d "$2" "https://ntfy.sh/$TOPIC" >/dev/null
}

if [ "${1:-}" = "--test" ]; then
  push "Kitchen Pi" "Test notification. Check-ins will arrive at 8 PM."
  echo "Test sent to topic $TOPIC"; exit 0
fi

TOMORROW=$(date -d tomorrow +%F)
PROMPT=$(sed "s/<tomorrow>/$TOMORROW/g" deploy/checkin-prompt.md)

# Only read-only tools are allowed; anything else is refused automatically.
OUT=$(timeout 600 "$CLAUDE" -p "$PROMPT" --max-turns 20 --output-format text \
  --allowedTools \
    "Bash(.venv/bin/python tools/gcal.py list:*)" \
    "Bash(.venv/bin/python tools/fitness.py:*)" \
    "Bash(.venv/bin/python tools/workout.py pull:*)" \
    "Bash(.venv/bin/python tools/workout.py show:*)" \
  2>>data/checkin.log)
STATUS=$?

mkdir -p data
echo "=== $(date) status=$STATUS" >> data/checkin.log
echo "$OUT" >> data/checkin.log

if [ $STATUS -ne 0 ] || [ -z "$OUT" ]; then
  push "Kitchen Pi check-in failed" "The 8 PM check-in didn't run (status $STATUS). Details: data/checkin.log on the Pi."
  exit 1
fi
push "Evening check-in" "$OUT"
echo "Sent."
