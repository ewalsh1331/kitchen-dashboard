#!/usr/bin/env bash
# Installs the OneDrive workout-log tools. Run from ~/kitchen-dashboard: bash deploy/install-workout.sh
set -euo pipefail
cd "$(dirname "$0")/.."
git add -A && git commit -qm "Before workout log" || true

echo "==> Installing rclone and openpyxl"
if ! command -v rclone >/dev/null; then
  curl -fsSL https://rclone.org/install.sh | sudo bash
fi
.venv/bin/pip install -q openpyxl

echo "==> Keeping downloaded copies out of git"
grep -qx "data/" .gitignore || echo "data/" >> .gitignore

echo "==> Updating CLAUDE.md and permissions"
.venv/bin/python - <<'EOF'
import json, pathlib, re
p = pathlib.Path("CLAUDE.md"); t = p.read_text()
workouts = """## Workouts

- Goal: general fitness, strength, look good. Program: Jeff Nippard Pure Bodybuilding PPL. Good apartment gym, no injuries.
- Log file: Fitness_Program_Log.xlsx in OneDrive (tabs: Workout Templates, Workout Log, plus warm-up/weak-point reference).
- **Always `tools/workout.py pull` first** (fresh download), then read with `tools/workout.py show TAB` or openpyxl on `data/Fitness_Program_Log.xlsx`.
- To log: edit `data/Fitness_Program_Log.xlsx` with openpyxl. Load and save the existing workbook; never rebuild it. Append rows matching the existing columns and formatting. Then `tools/workout.py push`.
- Before pushing, show Eric the exact rows you'll add and wait for "yes".
- If push says the file changed in OneDrive, he edited it in Excel: pull again, re-apply, push. Never force it.
- When asked about a workout: check Workout Templates for that day's exercises, sets/reps and weights, plus what he actually did last time from Workout Log. Include Sub 1/Sub 2 options.
- If he says he completed a workout, check whether it's already logged before adding it.
- If a round or long practice is coming up, avoid stacking a heavy leg day right before or after.
- If he skipped a session, ask whether to reschedule rather than dropping it.
"""
t, n = re.subn(r"## Workouts\n.*?(?=\n## )", workouts.rstrip() + "\n", t, flags=re.S)
if not n:
    t += "\n" + workouts
t = t.replace("| Workout done | Not on the Pi yet. Say so, and tell him to log it in Fitness_Program_Log.xlsx |",
              "| Workout done | Workout Log tab via `tools/workout.py` (pull → add rows → confirm → push) |")
t = t.replace("| Fitness Data sheet | `tools/fitness.py tabs` / `tools/fitness.py read TAB --last 14` (read-only) |",
              "| Fitness Data sheet | `tools/fitness.py tabs` / `tools/fitness.py read TAB --last 14` (read-only) |\n"
              "| Workout log (OneDrive xlsx) | `tools/workout.py pull` / `show TAB [--last N] [--find TEXT]` / `push` |")
p.write_text(t)

s = pathlib.Path(".claude/settings.json"); cfg = json.loads(s.read_text())
allow = cfg.setdefault("permissions", {}).setdefault("allow", [])
for rule in ["Bash(.venv/bin/python tools/workout.py pull:*)",
             "Bash(.venv/bin/python tools/workout.py show:*)",
             "Bash(.venv/bin/python tools/workout.py find:*)"]:
    if rule not in allow:
        allow.append(rule)
s.write_text(json.dumps(cfg, indent=2) + "\n")

c = pathlib.Path("config.json"); conf = json.loads(c.read_text())
conf.setdefault("workout_log_remote", "onedrive:Fitness_Program_Log.xlsx")
c.write_text(json.dumps(conf, indent=2) + "\n")
print("CLAUDE.md, settings.json, config.json updated")
EOF

git add -A && git commit -qm "Workout log tools" && echo "Saved." || echo "Already up to date."
echo
echo "Next: connect OneDrive with 'rclone config' (see the steps from Claude)."
