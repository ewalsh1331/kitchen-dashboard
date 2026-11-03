"""Fitness_Program_Log.xlsx (OneDrive) for Claude Code on the Pi.

Workflow: pull -> read/edit data/Fitness_Program_Log.xlsx with openpyxl -> push.
push refuses to overwrite if the OneDrive copy changed since the last pull.

Examples (run from ~/kitchen-dashboard):
  .venv/bin/python tools/workout.py find                    # locate the file in OneDrive
  .venv/bin/python tools/workout.py pull                    # download fresh copy + summary
  .venv/bin/python tools/workout.py show "Workout Log" --last 15
  .venv/bin/python tools/workout.py show "Workout Templates" --find "Pull"
  .venv/bin/python tools/workout.py day "Pull #1"           # template + last logged session
  .venv/bin/python tools/workout.py push                    # upload edits (guarded)
"""
import argparse
import datetime as dt
import json
import shutil
import subprocess
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
CONFIG = json.loads((BASE / "config.json").read_text())
REMOTE = CONFIG.get("workout_log_remote", "onedrive:Fitness_Program_Log.xlsx")
DATA = BASE / "data"
LOCAL = DATA / "Fitness_Program_Log.xlsx"
STATE = DATA / ".workout_pull.json"
BACKUPS = DATA / "backups"
RCLONE = shutil.which("rclone") or "/usr/bin/rclone"


def rclone(*args, capture=True):
    r = subprocess.run([RCLONE, *args], capture_output=capture, text=True)
    if r.returncode != 0:
        sys.exit(f"rclone failed: {' '.join(args)}\n{r.stderr.strip()}")
    return r.stdout


def remote_info() -> dict:
    name = REMOTE.split(":", 1)[1].rsplit("/", 1)[-1]
    r = subprocess.run([RCLONE, "lsjson", "--hash", REMOTE], capture_output=True, text=True)
    match = [f for f in json.loads(r.stdout or "[]") if f.get("Name") == name] if r.returncode == 0 else []
    if not match:
        sys.exit(f"Not found in OneDrive: {REMOTE}. Run 'workout.py find' and set workout_log_remote in config.json.")
    f = match[0]
    return {"size": f.get("Size"), "modtime": f.get("ModTime"), "hashes": f.get("Hashes", {})}


def summary():
    from openpyxl import load_workbook
    wb = load_workbook(LOCAL, read_only=True, data_only=True)
    out = []
    for ws in wb.worksheets:
        header = next(ws.iter_rows(min_row=1, max_row=1, values_only=True), ())
        out.append({"tab": ws.title, "rows": ws.max_row, "columns": [h for h in header if h is not None]})
    return out


def cmd_find(_):
    out = rclone("lsf", "-R", "--files-only", "--exclude", "/Personal Vault/**", "--include", "*Fitness_Program_Log*", "onedrive:")
    print(json.dumps({"matches": [f"onedrive:{l}" for l in out.splitlines()], "configured": REMOTE}, indent=2))


def cmd_pull(_):
    DATA.mkdir(exist_ok=True)
    info = remote_info()
    rclone("copyto", REMOTE, str(LOCAL))
    STATE.write_text(json.dumps({**info, "pulled_at": dt.datetime.now().isoformat()}))
    print(json.dumps({"pulled": str(LOCAL), "onedrive_modified": info["modtime"], "tabs": summary()}, indent=2, default=str))


def cmd_push(_):
    if not LOCAL.exists() or not STATE.exists():
        sys.exit("Nothing to push. Run 'pull' first.")
    pulled = json.loads(STATE.read_text())
    now = remote_info()
    if (now["hashes"] or {"size": now["size"], "m": now["modtime"]}) != (pulled["hashes"] or {"size": pulled["size"], "m": pulled["modtime"]}):
        sys.exit("STOP: the OneDrive file changed since the last pull (probably edited in Excel). "
                 "Not uploading. Run 'pull' again, re-apply the edit, then push.")
    BACKUPS.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    rclone("copyto", REMOTE, str(BACKUPS / f"Fitness_Program_Log-{stamp}.xlsx"))  # pre-upload backup
    for old in sorted(BACKUPS.glob("*.xlsx"))[:-20]:
        old.unlink()
    rclone("copyto", str(LOCAL), REMOTE)
    STATE.write_text(json.dumps({**remote_info(), "pulled_at": dt.datetime.now().isoformat()}))
    print(json.dumps({"pushed": REMOTE, "backup": f"data/backups/Fitness_Program_Log-{stamp}.xlsx"}, indent=2))


def cmd_show(a):
    from openpyxl import load_workbook
    if not LOCAL.exists():
        sys.exit("Run 'pull' first.")
    wb = load_workbook(LOCAL, read_only=True, data_only=True)
    if a.tab not in wb.sheetnames:
        sys.exit(f"No tab '{a.tab}'. Tabs: {wb.sheetnames}")
    rows = list(wb[a.tab].iter_rows(values_only=True))
    header_idx = next((i for i, r in enumerate(rows) if sum(c is not None for c in r) >= 2), 0)
    header = [str(h) if h is not None else f"col{i+1}" for i, h in enumerate(rows[header_idx])]
    body = [(header_idx + 2 + n, r) for n, r in enumerate(rows[header_idx + 1:]) if any(c is not None for c in r)]
    if a.find:
        body = [(n, r) for n, r in body if any(a.find.lower() in str(c).lower() for c in r if c is not None)]
    elif not a.all:
        body = body[-a.last:]
    data = [{"_row": n, **{h: (c.isoformat() if hasattr(c, "isoformat") else c) for h, c in zip(header, r) if c is not None}}
            for n, r in body]
    print(json.dumps(data, indent=2, default=str))


def _clean(v):
    return v.isoformat()[:10] if hasattr(v, "isoformat") else v


def _table(ws):
    """Header + non-empty body rows of a log-style tab, as (row_number, dict)."""
    rows = list(ws.iter_rows(values_only=True))
    hi = next((i for i, r in enumerate(rows) if sum(c is not None for c in r) >= 2), 0)
    header = [str(h) if h is not None else f"col{i+1}" for i, h in enumerate(rows[hi])]
    return [(hi + 2 + n, {h: _clean(c) for h, c in zip(header, r) if c is not None})
            for n, r in enumerate(rows[hi + 1:]) if any(c is not None for c in r)]


def cmd_day(a):
    """One program day: template exercises (with subs) + the most recent logged session of that day."""
    from openpyxl import load_workbook
    if not LOCAL.exists():
        sys.exit("Run 'pull' first.")
    wb = load_workbook(LOCAL, read_only=True, data_only=True)
    want = a.name.lower()

    # Templates: "BLOCK n: ..." rows, then a day-title row (only column A filled),
    # then an "Exercise" header row, then exercise rows.
    block, day, header, days = None, None, None, {}
    for r in wb["Workout Templates"].iter_rows(values_only=True):
        first = r[0] if r else None
        if first is None:
            continue
        rest_empty = all(c is None for c in r[1:])
        if str(first).upper().startswith("BLOCK"):
            block, day = str(first), None
        elif rest_empty:
            day = str(first)
        elif first == "Exercise":
            header = [str(h) for h in r]
        elif day and header:
            ex = {h: _clean(c) for h, c in zip(header, r) if c is not None and h not in ("Video", "Sub 1 Video", "Sub 2 Video")}
            days.setdefault((block, day), []).append(ex)

    matches = [k for k in days if want in k[1].lower() and (a.block is None or f"BLOCK {a.block}" in k[0].upper())]
    if not matches:
        sys.exit(f"No template day matching '{a.name}'. Days: {sorted({k[1] for k in days})}")
    block, day = matches[0]

    # Log: group rows of this day into sessions (by Week + Date, in sheet order).
    sessions = []
    for n, row in _table(wb["Workout Log"]):
        if want not in str(row.get("Day", "")).lower() or "Exercise" not in row:
            continue
        key = (row.get("Week"), row.get("Date"))
        if not sessions or sessions[-1]["key"] != key:
            sessions.append({"key": key, "rows": []})
        sessions[-1]["rows"].append({"_row": n, **{k: v for k, v in row.items() if k not in ("Week", "Day", "Date", "Video")}})

    last = sessions[-1] if sessions else None
    print(json.dumps({
        "block": block, "day": day,
        "exercises": days[(block, day)],
        "sessions_logged": [{"week": s["key"][0], "date": s["key"][1], "exercises": len(s["rows"])} for s in sessions],
        "last_session": {"week": last["key"][0], "date": last["key"][1], "rows": last["rows"]} if last else None,
    }, indent=2, default=str))


def main():
    p = argparse.ArgumentParser(description="Workout log (OneDrive xlsx)")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("find").set_defaults(fn=cmd_find)
    sub.add_parser("pull").set_defaults(fn=cmd_pull)
    sub.add_parser("push").set_defaults(fn=cmd_push)
    s = sub.add_parser("show"); s.set_defaults(fn=cmd_show)
    s.add_argument("tab"); s.add_argument("--last", type=int, default=15)
    s.add_argument("--find"); s.add_argument("--all", action="store_true")
    d = sub.add_parser("day", help='template + last logged session for one day, e.g. day "Pull #1"')
    d.set_defaults(fn=cmd_day); d.add_argument("name"); d.add_argument("--block", type=int)
    a = p.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
