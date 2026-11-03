#!/usr/bin/env bash
# Adds the month view + rotation to the existing dashboard. Safe to run twice.
set -euo pipefail
cd "$(dirname "$0")/.."

git add -A && git commit -qm "Before month view" || true

# 1. Hook month_api into app.py (one line, before the run block)
if ! grep -q "month_api" app.py; then
  sed -i 's/^if __name__ == "__main__":/import month_api  # month view + rotation\nmonth_api.register(app)\n\n\nif __name__ == "__main__":/' app.py
  echo "app.py: month view registered"
fi

# 2. Rotation settings in config.json (keeps everything else)
.venv/bin/python - <<'EOF'
import json, pathlib
p = pathlib.Path("config.json")
c = json.loads(p.read_text())
c.setdefault("rotation", [{"page": "/", "seconds": 45}, {"page": "/month", "seconds": 30}])
p.write_text(json.dumps(c, indent=2) + "\n")
print("config.json: rotation =", c["rotation"])
EOF

# 3. Point the kiosk browser at the rotating page
sed -i 's#^URL="http://127.0.0.1:8080"$#URL="http://127.0.0.1:8080/rotate"#' deploy/kiosk.sh
sed -i 's#curl -fs "$URL/api/status"#curl -fs "http://127.0.0.1:8080/api/status"#' deploy/kiosk.sh
grep -n "^URL=" deploy/kiosk.sh

chmod +x deploy/refresh-screen.sh
bash deploy/refresh-screen.sh
git add -A && git commit -qm "Month view + rotation" && echo "Saved." || echo "Already up to date."
