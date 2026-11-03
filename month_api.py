"""Month view + screen rotation for the kitchen dashboard.

Adds to the Flask app:
  /month         full-screen month calendar page
  /api/month     events for the current month (?offset=1 for next month)
  /rotate        cycles between pages (set in config.json "rotation")

Hooked in from app.py with:  import month_api; month_api.register(app)
"""
import calendar
import datetime as dt
import json
import threading
import time
from pathlib import Path
from zoneinfo import ZoneInfo

from flask import jsonify, request, send_from_directory
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

BASE = Path(__file__).resolve().parent
TOKEN_PATH = BASE / "token.json"
DEFAULT_ROTATION = [{"page": "/", "seconds": 45}, {"page": "/month", "seconds": 30}]

_lock = threading.Lock()
_cache: dict = {}


def _config() -> dict:
    return json.loads((BASE / "config.json").read_text())


def _credentials(cfg) -> Credentials:
    if not TOKEN_PATH.exists():
        raise RuntimeError("Not signed in to Google yet. Run auth.py.")
    creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), cfg["scopes"])
    if not creds.valid:
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            TOKEN_PATH.write_text(creds.to_json())
        else:
            raise RuntimeError("Google sign-in expired. Run auth.py again.")
    return creds


def _month_bounds(today: dt.date, offset: int):
    y, m = today.year, today.month + offset
    y += (m - 1) // 12
    m = (m - 1) % 12 + 1
    first = dt.date(y, m, 1)
    last = dt.date(y, m, calendar.monthrange(y, m)[1])
    return first, last


def _grid(first: dt.date, last: dt.date):
    """Weeks (Sunday first) covering the month, as ISO date strings."""
    start = first - dt.timedelta(days=(first.weekday() + 1) % 7)
    end = last + dt.timedelta(days=(5 - last.weekday()) % 7)
    days, d = [], start
    while d <= end:
        days.append(d.isoformat())
        d += dt.timedelta(days=1)
    return [days[i:i + 7] for i in range(0, len(days), 7)]


def _fetch_month(offset: int) -> dict:
    cfg = _config()
    tz = ZoneInfo(cfg["timezone"])
    today = dt.datetime.now(tz).date()
    first, last = _month_bounds(today, offset)
    weeks = _grid(first, last)
    grid_start = dt.date.fromisoformat(weeks[0][0])
    grid_end = dt.date.fromisoformat(weeks[-1][-1])

    service = build("calendar", "v3", credentials=_credentials(cfg), cache_discovery=False)
    t_min = dt.datetime.combine(grid_start, dt.time.min, tz).isoformat()
    t_max = dt.datetime.combine(grid_end + dt.timedelta(days=1), dt.time.min, tz).isoformat()
    items, token = [], None
    while True:
        resp = service.events().list(
            calendarId=cfg["calendar_id"], timeMin=t_min, timeMax=t_max,
            singleEvents=True, orderBy="startTime", maxResults=250, pageToken=token,
        ).execute()
        items += resp.get("items", [])
        token = resp.get("nextPageToken")
        if not token:
            break

    by_day: dict = {}
    for ev in items:
        s, e = ev["start"], ev["end"]
        entry = {"title": ev.get("summary", "(no title)"), "color": ev.get("colorId", "")}
        if "date" in s:  # all-day, possibly multi-day (end date is exclusive)
            d, end = dt.date.fromisoformat(s["date"]), dt.date.fromisoformat(e["date"])
            entry.update(time="", all_day=True)
            while d < end:
                by_day.setdefault(d.isoformat(), []).append(entry)
                d += dt.timedelta(days=1)
        else:
            start = dt.datetime.fromisoformat(s["dateTime"]).astimezone(tz)
            label = start.strftime("%-I:%M%p").lower().replace(":00", "")
            entry.update(time=label[:-1] if label.endswith("m") else label, all_day=False)
            by_day.setdefault(start.date().isoformat(), []).append(entry)

    return {
        "ok": True,
        "month": first.strftime("%B %Y"),
        "month_index": first.month,
        "today": today.isoformat(),
        "weeks": weeks,
        "events": by_day,
        "fetched_at": dt.datetime.now(tz).isoformat(),
    }


def register(app):
    static = str(BASE / "static")

    @app.route("/api/month")
    def api_month():
        offset = max(-1, min(12, int(request.args.get("offset", 0))))
        ttl = _config().get("cache_seconds", 120)
        with _lock:
            hit = _cache.get(offset)
            if not hit or time.time() - hit[0] > ttl:
                try:
                    data = _fetch_month(offset)
                except Exception as exc:  # keep the screen up, show the error
                    data = {"ok": False, "error": str(exc)}
                _cache[offset] = (time.time(), data)
            return jsonify(_cache[offset][1])

    @app.route("/api/rotation")
    def api_rotation():
        return jsonify(_config().get("rotation", DEFAULT_ROTATION))

    @app.route("/month")
    def month_page():
        return send_from_directory(static, "month.html")

    @app.route("/rotate")
    def rotate_page():
        return send_from_directory(static, "rotate.html")
