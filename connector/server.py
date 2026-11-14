"""Kitchen connector: Eric's kitchen tools for the Claude app (custom MCP connector).

Lets claude.ai chats (including voice mode) use the same tools as Claude Code on
the Pi: Claude Calendar with the kitchen rules, the Weekly Review pending list,
the Fitness Data sheet and the workout log.

Safety:
- Wraps the existing CLI tools in tools/ with fixed argument lists. No shell, no
  arbitrary commands.
- Deleting an event and logging a workout are two-step: a *_request tool returns
  a preview and a one-time code (valid 2 minutes, one use), and only the matching
  *_confirm tool with that code makes the change.
- The endpoint path contains a long random secret (connector/.env, git-ignored).
- Deletions and workout logs are recorded in data/connector.log.

Run:  .venv/bin/python connector/server.py      (listens on 127.0.0.1:8090)
"""
import json
import secrets
import subprocess
import sys
import time
from pathlib import Path

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

HERE = Path(__file__).resolve().parent
BASE = HERE.parent
PY = str(BASE / ".venv" / "bin" / "python")
ENV = HERE / ".env"
LOG = BASE / "data" / "connector.log"
PORT = 8090
CODE_SECONDS = 120


def _secret() -> str:
    if ENV.exists():
        for line in ENV.read_text().splitlines():
            if line.startswith("CONNECTOR_SECRET="):
                return line.split("=", 1)[1].strip()
    token = secrets.token_urlsafe(32)
    ENV.write_text(f"CONNECTOR_SECRET={token}\n")
    ENV.chmod(0o600)
    return token


SECRET = _secret()

INSTRUCTIONS = """Eric's kitchen tools (Raspberry Pi in his Milwaukee kitchen, America/Chicago).

Calendar ("Claude Calendar" only):
- Check calendar_list for conflicts before proposing anything; leave buffer time.
- Ask before creating or changing any event: say the exact title, day, time and type, wait for yes.
- Types: block = work hours and other time blocks; appointment = meetings, golf rounds,
  real appointments; task = chores/reminders. If the type is unclear, ask.
- Tasks: type task with date only. They become a 12:00-12:15 PM reminder; every task on a day
  shares 12:00, never stagger. Title them "Task: ...".
- Golf rounds: title "Golf - [Course]", type appointment, location = course, 4-4.5 h (2 h for 9 holes).
- Recurring events: updating or deleting an occurrence ID changes only that occurrence.
- Deleting: calendar_delete_request, read the preview back, get an explicit yes, then
  calendar_delete_confirm with the code. Never confirm without his yes in this conversation.

Pending list (Weekly Review): undated follow-ups go here (pending_add, no confirmation needed).
Dated follow-ups get their own task event instead.

Fitness (read-only sheet): always label Garmin vs Oura for HRV, sleep, resting HR and readiness;
never blend. Sleep/HRV rows are dated by wake-up day. Look at 7-14 day trends. Context, not
directives; no medical claims. Golf shots capture about 70% of strokes.

Workouts (Jeff Nippard Pure Bodybuilding PPL): workout_day shows the template and last session.
To log: workout_log_request, read the rows back, get a yes, then workout_log_confirm.

Replies may be spoken aloud: keep them short, plain sentences."""

mcp = FastMCP(
    "Kitchen",
    instructions=INSTRUCTIONS,
    host="127.0.0.1",
    port=PORT,
    streamable_http_path=f"/{SECRET}/mcp",
    stateless_http=True,
    # Reached through Tailscale Funnel, whose Host header isn't localhost. The secret path
    # is the access control; DNS-rebinding checks are for browser-reachable local servers.
    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
)

_codes: dict[str, dict] = {}


def log(entry: dict):
    LOG.parent.mkdir(exist_ok=True)
    with LOG.open("a") as f:
        f.write(json.dumps({"at": time.strftime("%Y-%m-%d %H:%M:%S"), **entry}) + "\n")


def run(tool: str, *args) -> object:
    """Run tools/<tool>.py with a fixed argument list and return its JSON output."""
    cmd = [PY, str(BASE / "tools" / f"{tool}.py"), *[str(a) for a in args]]
    p = subprocess.run(cmd, cwd=BASE, capture_output=True, text=True, timeout=180)
    text = p.stdout.strip()
    try:
        data = json.loads(text) if text else {}
    except json.JSONDecodeError:
        data = {"output": text}
    if p.returncode != 0:
        return {"error": (p.stderr.strip().splitlines() or ["failed"])[-1], "result": data}
    return data


def opt(**kw) -> list[str]:
    """Turn keyword args into CLI flags, skipping empty ones (snake_case -> --kebab-case)."""
    out = []
    for k, v in kw.items():
        if v is None or v == "":
            continue
        out += [f"--{k.replace('_', '-')}", str(v)]
    return out


def new_code(action: str, **data) -> str:
    now = time.time()
    for c in [c for c, v in _codes.items() if v["expires"] < now]:
        del _codes[c]
    code = f"{secrets.randbelow(10**6):06d}"
    _codes[code] = {"action": action, "expires": now + CODE_SECONDS, **data}
    return code


def take_code(code: str, action: str) -> dict | None:
    entry = _codes.pop(str(code).strip(), None)
    if not entry or entry["action"] != action or entry["expires"] < time.time():
        return None
    return entry


# ---- Calendar ---------------------------------------------------------------

@mcp.tool()
def calendar_list(start: str = "", end: str = "", days: int = 7, query: str = "") -> object:
    """List Claude Calendar events. start/end as YYYY-MM-DD (end exclusive); otherwise the next `days` days. query filters by text."""
    if start:
        return run("gcal", "list", *opt(start=start, end=end, q=query))
    return run("gcal", "list", *opt(days=days, q=query))


@mcp.tool()
def calendar_get(event_id: str) -> object:
    """Get one event by ID."""
    return run("gcal", "get", event_id)


@mcp.tool()
def calendar_create(title: str, type: str, start: str = "", end: str = "", date: str = "",
                    end_date: str = "", location: str = "", description: str = "") -> object:
    """Create an event AFTER Eric said yes to the exact details.
    type: block | appointment | task. Timed: start/end 'YYYY-MM-DD HH:MM' (Chicago).
    Tasks and all-day events: date 'YYYY-MM-DD' (end_date for multi-day all-day)."""
    if type not in ("block", "appointment", "task"):
        return {"error": "type must be block, appointment or task"}
    return run("gcal", "create", *opt(title=title, type=type, start=start, end=end, date=date,
                                       end_date=end_date, location=location, description=description))


@mcp.tool()
def calendar_update(event_id: str, title: str = "", type: str = "", start: str = "", end: str = "",
                    date: str = "", location: str = "", description: str = "",
                    append_description: str = "") -> object:
    """Change an event AFTER Eric said yes. Only given fields change. For a recurring event,
    an occurrence ID changes only that occurrence. Times as 'YYYY-MM-DD HH:MM' (Chicago)."""
    if type and type not in ("block", "appointment", "task"):
        return {"error": "type must be block, appointment or task"}
    return run("gcal", "update", event_id, *opt(title=title, type=type, start=start, end=end, date=date,
                                                 location=location, description=description,
                                                 append_description=append_description))


@mcp.tool()
def calendar_delete_request(event_id: str, whole_series: bool = False) -> object:
    """Step 1 of deleting: returns what would be deleted and a one-time code (valid 2 minutes).
    Nothing is deleted. Read the event back to Eric and ask for a clear yes.
    whole_series=True targets every occurrence of a recurring event; only if he asked for the whole series."""
    ev = run("gcal", "get", event_id)
    if not isinstance(ev, dict) or "error" in ev:
        return ev
    target = event_id
    if whole_series:
        if not ev.get("series_id"):
            return {"error": "This event isn't recurring; request without whole_series."}
        target = ev["series_id"]
    code = new_code("delete", event_id=target, title=ev.get("title"), start=ev.get("start"),
                    whole_series=whole_series)
    return {
        "would_delete": {"title": ev.get("title"), "start": ev.get("start"), "end": ev.get("end"),
                         "scope": "entire recurring series" if whole_series else
                                  ("this occurrence only" if ev.get("recurring") else "this event")},
        "confirm_code": code,
        "next": "Read this back and ask Eric to confirm. Only after he says yes, call calendar_delete_confirm with the code.",
    }


@mcp.tool()
def calendar_delete_confirm(code: str) -> object:
    """Step 2 of deleting: only after Eric explicitly said yes to the preview from calendar_delete_request."""
    entry = take_code(code, "delete")
    if not entry:
        return {"error": "Code is invalid, used or expired. Run calendar_delete_request again."}
    result = run("gcal", "delete", entry["event_id"])
    log({"action": "delete", "event_id": entry["event_id"], "title": entry["title"],
         "start": entry["start"], "whole_series": entry["whole_series"], "result": result})
    return result


# ---- Pending list -----------------------------------------------------------

@mcp.tool()
def pending_list() -> object:
    """Items on the Weekly Review pending list."""
    return run("gcal", "pending", "list")


@mcp.tool()
def pending_add(text: str) -> object:
    """Add an undated follow-up to the Weekly Review pending list."""
    return run("gcal", "pending", "add", text)


@mcp.tool()
def pending_remove(text: str) -> object:
    """Remove the one pending item containing this text."""
    return run("gcal", "pending", "remove", text)


# ---- Fitness sheet (read-only) ---------------------------------------------

@mcp.tool()
def fitness_tabs() -> object:
    """Tabs in the Fitness Data sheet (Garmin, Oura, Activities, Oura Workouts, Golf Rounds, Golf Holes, Golf Shots)."""
    return run("fitness", "tabs")


@mcp.tool()
def fitness_read(tab: str, last: int = 14) -> object:
    """Last N rows of a Fitness Data tab."""
    return run("fitness", "read", tab, "--last", max(1, min(int(last), 120)))


@mcp.tool()
def fitness_stats(tab: str, columns: list[str], days: int = 60) -> object:
    """Mean and percentiles (p10/p25/median/p75/p90) of numeric columns over the last N days."""
    return run("fitness", "stats", tab, *columns, "--days", days)


# ---- Workout log -----------------------------------------------------------

@mcp.tool()
def workout_day(name: str, block: int = 0) -> object:
    """Template for a program day (e.g. 'Pull #1', 'Legs #2', 'Arms #1') plus what he did last time.
    Downloads a fresh copy of the log first."""
    pulled = run("workout", "pull")
    if isinstance(pulled, dict) and "error" in pulled:
        return pulled
    return run("workout", "day", name, *opt(block=block or None))


@mcp.tool()
def workout_show(tab: str = "Workout Log", last: int = 15, find: str = "") -> object:
    """Recent rows of a workout log tab (fresh download first). find filters rows by text."""
    pulled = run("workout", "pull")
    if isinstance(pulled, dict) and "error" in pulled:
        return pulled
    return run("workout", "show", tab, "--last", max(1, min(int(last), 60)), *opt(find=find))


@mcp.tool()
def workout_log_request(date: str, week: str, day: str, rows: list[dict]) -> object:
    """Step 1 of logging a workout. date YYYY-MM-DD, week like 'Week 3', day like 'Push #2'.
    rows: one object per exercise, keyed by Workout Log column names (see workout_show).
    Nothing is written. Read the rows back and ask Eric for a yes."""
    if not rows:
        return {"error": "No rows given."}
    code = new_code("workout", date=date, week=week, day=day, rows=rows)
    return {"would_log": {"date": date, "week": week, "day": day, "rows": rows},
            "confirm_code": code,
            "next": "Read the rows back. Only after Eric says yes, call workout_log_confirm with the code."}


@mcp.tool()
def workout_log_confirm(code: str) -> object:
    """Step 2: fresh download, append the confirmed rows, upload to OneDrive."""
    entry = take_code(code, "workout")
    if not entry:
        return {"error": "Code is invalid, used or expired. Run workout_log_request again."}
    steps = {"pull": run("workout", "pull")}
    if isinstance(steps["pull"], dict) and "error" in steps["pull"]:
        return {"error": "Download failed; nothing was logged.", "steps": steps}
    steps["log"] = run("workout", "log", "--date", entry["date"], "--week", entry["week"],
                       "--day", entry["day"], "--rows", json.dumps(entry["rows"]))
    if isinstance(steps["log"], dict) and "error" in steps["log"]:
        return {"error": "Logging failed; nothing was uploaded.", "steps": steps}
    steps["push"] = run("workout", "push")
    log({"action": "workout_log", "date": entry["date"], "day": entry["day"],
         "rows": len(entry["rows"]), "steps": steps})
    return steps


if __name__ == "__main__":
    if "--print-path" in sys.argv:  # for setup only; never logged
        print(f"/{SECRET}/mcp")
        sys.exit(0)
    mcp.run(transport="streamable-http")
