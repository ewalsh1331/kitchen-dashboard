"""Health screen data: sleep, recovery (HRV), readiness, training, last 7 days, golf.

Hooked in from app.py with:  import health_api; health_api.register(app, _credentials)
GET /api/health  ->  everything static/health.html draws (cached 10 minutes)
GET /health      ->  the page

Sources (all read-only):
  Fitness Data sheet  Oura, Garmin, Activities, Golf Rounds, Golf Holes  (dated by wake-up day)
  Workout log         OneDrive Fitness_Program_Log.xlsx, cached copy in data/cache/ (refreshed every 30 min;
                      separate from data/Fitness_Program_Log.xlsx, which tools/workout.py edits)
  Claude Calendar     Work (In-Office) / Work (Remote) blocks -> Office / Remote / Weekend
"""
import datetime as dt
import json
import shutil
import statistics as st
import subprocess
import threading
import time
from pathlib import Path
from zoneinfo import ZoneInfo

from flask import jsonify, send_from_directory
from googleapiclient.discovery import build

BASE = Path(__file__).resolve().parent
CONFIG = json.loads((BASE / "config.json").read_text())
TZ = ZoneInfo(CONFIG["timezone"])
SHEET_ID = CONFIG.get("fitness_sheet_id")
CACHE_SECONDS = 10 * 60
LOG_REMOTE = CONFIG.get("workout_log_remote", "onedrive:Fitness_Program_Log.xlsx")
LOG_CACHE = BASE / "data" / "cache" / "Fitness_Program_Log.xlsx"
LOG_MAX_AGE = 30 * 60
GOLF_DAYS = 3                  # golf card shows for this many days after a real round
CHART_DAYS, RANGE_DAYS, WEEK_DAYS = 14, 60, 7

_lock = threading.Lock()
_cache = {"at": 0.0, "data": None}


# ---------- helpers ----------

def num(v):
    try:
        return float(str(v).replace(",", ""))
    except (TypeError, ValueError):
        return None


def nz(v):
    """Numbers where 0/blank means 'no data' (sleep, HRV, readiness)."""
    x = num(v)
    return x if x else None


def pct(vals, p):
    vals = sorted(v for v in vals if v is not None)
    if len(vals) < 5:
        return None
    return round(st.quantiles(vals, n=100, method="inclusive")[p - 1], 1)


def band(vals):
    v = [x for x in vals if x is not None]
    return {"p10": pct(v, 10), "p25": pct(v, 25), "p75": pct(v, 75), "median": round(st.median(v), 1) if v else None}


def oura_label(score):
    if score is None:
        return ""
    return "optimal" if score >= 85 else "good" if score >= 70 else "pay attention"


def tab(svc, name):
    values = svc.spreadsheets().values().get(spreadsheetId=SHEET_ID, range=f"'{name}'").execute().get("values", [])
    if not values:
        return []
    head = values[0]
    return [dict(zip(head, r + [""] * (len(head) - len(r)))) for r in values[1:] if any(c.strip() for c in r)]


def by_date(rows, key="Date"):
    return {r[key][:10]: r for r in rows if r.get(key)}


def series(rows_by_day, days, col, fn=nz):
    return [fn(rows_by_day.get(d, {}).get(col)) for d in days]


def mi(meters):
    return f"{meters / 1609.34:.1f} mi"


def short_day(name):
    """'Pull #2 (Mid-Back Focused)' -> 'Pull #2'; 'Arms & Weak Points #2' -> 'Arms #2'; 'Legs #1 (Redo)' -> 'Legs #1'."""
    s = name.split(" (")[0].strip()
    return s.replace("Arms & Weak Points", "Arms")


# ---------- workout log (OneDrive) ----------

def workout_sessions():
    """{date: [program day short names]} from the Workout Log tab, using a cached copy."""
    try:
        if not LOG_CACHE.exists() or time.time() - LOG_CACHE.stat().st_mtime > LOG_MAX_AGE:
            LOG_CACHE.parent.mkdir(parents=True, exist_ok=True)
            rclone = shutil.which("rclone") or "/usr/bin/rclone"
            tmp = LOG_CACHE.with_suffix(".tmp")
            subprocess.run([rclone, "copyto", LOG_REMOTE, str(tmp)], check=True, capture_output=True, timeout=60)
            tmp.replace(LOG_CACHE)
    except Exception:
        pass                                   # use the last cached copy if OneDrive is unreachable
    if not LOG_CACHE.exists():
        return {}
    from openpyxl import load_workbook
    ws = load_workbook(LOG_CACHE, read_only=True, data_only=True)["Workout Log"]
    rows = ws.iter_rows(values_only=True)
    head = [str(h) if h else "" for h in next(rows)]
    di, dd, ex = head.index("Date"), head.index("Day"), head.index("Exercise")
    out = {}
    for r in rows:
        d, day, exn = r[di], r[dd], r[ex]
        if isinstance(d, dt.datetime) and day and exn:
            names = out.setdefault(d.date().isoformat(), [])
            if short_day(day) not in names:
                names.append(short_day(day))
    return out


# ---------- calendar (work type) ----------

def work_types(creds_fn, days):
    service = build("calendar", "v3", credentials=creds_fn(), cache_discovery=False)
    start = dt.datetime.combine(dt.date.fromisoformat(days[0]), dt.time.min, TZ)
    end = dt.datetime.combine(dt.date.fromisoformat(days[-1]) + dt.timedelta(days=1), dt.time.min, TZ)
    items = service.events().list(calendarId=CONFIG["calendar_id"], timeMin=start.isoformat(), timeMax=end.isoformat(),
                                  singleEvents=True, q="Work", maxResults=100).execute().get("items", [])
    out = {}
    for ev in items:
        title = ev.get("summary", "")
        s = ev["start"].get("dateTime") or ev["start"].get("date")
        d = dt.datetime.fromisoformat(s).astimezone(TZ).date().isoformat() if "T" in s else s
        if "In-Office" in title:
            out[d] = "Office"
        elif "Remote" in title and out.get(d) != "Office":
            out[d] = "Remote"
    return out


# ---------- golf ----------

def golf(rounds, holes, today):
    def local_date(r):
        return dt.datetime.fromisoformat(r["Starttime"].replace("Z", "+00:00")).astimezone(TZ).date()
    real = [r for r in rounds if r.get("Starttime") and (num(r.get("Holescompleted")) or 0) >= 9
            and num(r.get("Strokes"))]
    real.sort(key=local_date)
    if not real:
        return None, []
    recent = [{"date": local_date(r).isoformat(), "strokes": int(num(r["Strokes"])),
               "holes": int(num(r["Holescompleted"]))} for r in real[-3:]]
    last = real[-1]
    day = local_date(last)
    if (today - day).days > GOLF_DAYS:
        return None, recent
    n = int(num(last["Holescompleted"]))
    hs = sorted((h for h in holes if h.get("Scorecard Id") == last["Id"]), key=lambda h: num(h["Hole Number"]) or 0)
    par = sum(int(c) for c in (last.get("Holepars") or "")[:n] if c.isdigit()) or sum(int(num(h["Par"]) or 0) for h in hs)
    strokes = int(num(last["Strokes"]))
    diffs = [int(num(h["Strokes"])) - int(num(h["Par"])) for h in hs if num(h.get("Strokes")) and num(h.get("Par"))]
    fw = [h for h in hs if (num(h.get("Par")) or 0) >= 4]
    card = {
        "date": day.isoformat(), "course": last.get("Coursename", ""), "holes": n,
        "strokes": strokes, "to_par": strokes - par if par else None,
        "putts": int(sum(num(h.get("Putts")) or 0 for h in hs)) if hs else None,
        "fairways": f"{sum(1 for h in fw if h.get('Fairway Shot Outcome') == 'HIT')}/{len(fw)}" if fw else None,
        "penalties": int(sum(num(h.get("Penalties")) or 0 for h in hs)) if hs else None,
        "birdies": sum(1 for d in diffs if d <= -1), "pars": sum(1 for d in diffs if d == 0),
        "bogeys": sum(1 for d in diffs if d == 1), "doubles": sum(1 for d in diffs if d >= 2),
    }
    return card, recent


# ---------- assemble ----------

def build_health(creds_fn):
    svc = build("sheets", "v4", credentials=creds_fn(), cache_discovery=False)
    oura, garmin = by_date(tab(svc, "Oura")), by_date(tab(svc, "Garmin"))
    acts = tab(svc, "Activities")
    today = dt.datetime.now(TZ).date()
    # Latest day = most recent date with Oura sleep (normally yesterday's wake-up row... or today's once synced).
    filled = sorted(d for d, r in oura.items() if nz(r.get("Sleep Score")))
    latest = dt.date.fromisoformat(filled[-1]) if filled else today - dt.timedelta(days=1)
    span = lambda n: [(latest - dt.timedelta(days=i)).isoformat() for i in range(n - 1, -1, -1)]
    d14, d60, d7 = span(CHART_DAYS), span(RANGE_DAYS), span(WEEK_DAYS)
    L, G = oura.get(latest.isoformat(), {}), garmin.get(latest.isoformat(), {})

    o_hrv60, g_hrv60 = series(oura, d60, "Sleep Avg HRV (ms)"), series(garmin, d60, "HRV Overnight Avg (ms)")
    o_ready60 = series(oura, d60, "Readiness Score")
    ranges = {"oura_hrv": band(o_hrv60), "garmin_hrv": band(g_hrv60), "oura_ready": band(o_ready60)}

    intensity = [((num(garmin.get(d, {}).get("Intensity Minutes (Moderate)")) or 0)
                  + (num(garmin.get(d, {}).get("Intensity Minutes (Vigorous)")) or 0)) for d in d14]

    # Activities per day (Garmin + workout log program days + real golf rounds)
    sessions = workout_sessions()
    rounds, holes = tab(svc, "Golf Rounds"), tab(svc, "Golf Holes")
    golf_card, recent_rounds = golf(rounds, holes, today)
    golf_by_day = {}
    for r in rounds:
        if r.get("Starttime") and (num(r.get("Holescompleted")) or 0) >= 9 and num(r.get("Strokes")):
            d = dt.datetime.fromisoformat(r["Starttime"].replace("Z", "+00:00")).astimezone(TZ).date().isoformat()
            golf_by_day[d] = int(num(r["Strokes"]))
    try:
        work = work_types(creds_fn, d7)
    except Exception:
        work = {}

    week, lifts, runs, cardio, active = [], 0, 0, 0, 0
    for d in d7:
        day = dt.date.fromisoformat(d)
        chips = []
        if d in golf_by_day:
            chips.append(["golf", f"Golf {golf_by_day[d]}"])
        day_acts = [a for a in acts if a.get("Date", "")[:10] == d]
        for a in sorted(day_acts, key=lambda a: a.get("Start Time (Local)", "")):
            typ = a.get("Activity Type", "")
            if typ == "golf":
                continue                                   # golf comes from the scorecard above
            if typ == "strength_training":
                lifts += 1
                continue                                   # program days added below
            if "running" in typ:
                runs += 1
                chips.append(["run", "Run " + mi(num(a.get("Distance (m)")) or 0)])
            else:
                cardio += 1
                mins = round((num(a.get("Duration (sec)")) or 0) / 60)
                label = "Cardio" if "cardio" in typ else (a.get("Activity Name") or typ.replace("_", " ").title())
                chips.append(["cardio", f"{label} {mins}m"])
        logged = sessions.get(d, [])
        strength_count = sum(1 for a in day_acts if a.get("Activity Type") == "strength_training")
        if logged:
            chips += [["lift", n] for n in logged]
            lifts += max(0, len(logged) - strength_count)  # logged without the watch still counts
        elif strength_count:
            chips.append(["lift", "Lift"])
        if chips:
            active += 1
        wtype = "Weekend" if day.weekday() >= 5 else work.get(d, "")
        week.append({"date": d, "label": day.strftime("%a ") + str(day.day), "type": wtype, "chips": chips,
                     "steps": int(num(oura.get(d, {}).get("Steps")) or 0)})

    o_hrv, g_hrv = nz(L.get("Sleep Avg HRV (ms)")), nz(G.get("HRV Overnight Avg (ms)"))

    def rel(v, b):
        if v is None or b["p25"] is None:
            return None
        return "above" if v > b["p75"] else "below" if v < b["p25"] else "in"
    ro, rg = rel(o_hrv, ranges["oura_hrv"]), rel(g_hrv, ranges["garmin_hrv"])
    if ro and ro == rg:
        hrv_line = {"above": "HRV above your usual", "below": "HRV below your usual", "in": "HRV in your usual range"}[ro]
    elif ro:
        hrv_line = f"Oura HRV {'in your usual range' if ro == 'in' else ro + ' your usual'}"
    else:
        hrv_line = ""

    o_ready = nz(L.get("Readiness Score"))
    return {
        "ok": True, "latest": latest.isoformat(), "days14": d14,
        "summary": {"hrv": hrv_line, "active": active, "of": WEEK_DAYS},
        "sleep": {"oura": nz(L.get("Sleep Score")), "oura_sec": nz(L.get("Sleep Total (sec)")),
                  "garmin": nz(G.get("Sleep Score")), "garmin_sec": nz(G.get("Sleep Duration (sec)")),
                  "chart": {"oura": series(oura, d14, "Sleep Score"), "garmin": series(garmin, d14, "Sleep Score")}},
        "recovery": {"oura_hrv": o_hrv, "garmin_hrv": g_hrv, "rhr": nz(G.get("Resting HR")),
                     "chart": {"oura": series(oura, d14, "Sleep Avg HRV (ms)"), "garmin": series(garmin, d14, "HRV Overnight Avg (ms)")}},
        "readiness": {"oura": o_ready, "label": oura_label(o_ready), "garmin": nz(G.get("Training Readiness Score")),
                      "chart": {"oura": series(oura, d14, "Readiness Score"),
                                "garmin": series(garmin, d14, "Training Readiness Score")}},
        "training": {"lifts": lifts, "runs": runs, "cardio": cardio, "chart": intensity},
        "ranges": ranges, "week": week, "golf": golf_card, "recent_rounds": recent_rounds,
        "fetched_at": dt.datetime.now(TZ).isoformat(),
    }


def register(app, creds_fn):
    static = str(BASE / "static")

    @app.route("/api/health")
    def api_health():
        with _lock:
            if not _cache["data"] or time.time() - _cache["at"] > CACHE_SECONDS:
                try:
                    _cache["data"] = build_health(creds_fn)
                except Exception as exc:          # keep the screen up, show the problem
                    _cache["data"] = {"ok": False, "error": str(exc)}
                _cache["at"] = time.time()
            return jsonify(_cache["data"])

    @app.route("/health")
    def health_page():
        return send_from_directory(static, "health.html")
