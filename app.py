"""Kitchen dashboard server.

Serves the full-screen page and a small JSON API that reads Claude Calendar
directly from Google (no Claude usage needed to keep the screen fresh).
"""
import datetime as dt
import json
import threading
import time
from pathlib import Path
from zoneinfo import ZoneInfo

from flask import Flask, jsonify, send_from_directory
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

BASE = Path(__file__).resolve().parent
CONFIG = json.loads((BASE / "config.json").read_text())
TOKEN_PATH = BASE / "token.json"
SCOPES = CONFIG["scopes"]
TZ = ZoneInfo(CONFIG["timezone"])
CACHE_SECONDS = CONFIG.get("cache_seconds", 120)

app = Flask(__name__, static_folder=str(BASE / "static"))
_lock = threading.Lock()
_cache = {"at": 0.0, "data": None}


def _credentials() -> Credentials:
    if not TOKEN_PATH.exists():
        raise RuntimeError("Not signed in to Google yet. Run auth.py.")
    creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), SCOPES)
    if not creds.valid:
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            TOKEN_PATH.write_text(creds.to_json())
        else:
            raise RuntimeError("Google sign-in expired. Run auth.py again.")
    return creds


def _format_event(ev: dict) -> dict:
    start = ev["start"]
    all_day = "date" in start
    if all_day:
        when = dt.date.fromisoformat(start["date"])
        start_iso = when.isoformat()
    else:
        when = dt.datetime.fromisoformat(start["dateTime"]).astimezone(TZ)
        start_iso = when.isoformat()
    return {
        "title": ev.get("summary", "(no title)"),
        "start": start_iso,
        "all_day": all_day,
        "location": ev.get("location", ""),
    }


def _fetch() -> dict:
    service = build("calendar", "v3", credentials=_credentials(), cache_discovery=False)
    cal_id = CONFIG["calendar_id"]
    cal = service.calendars().get(calendarId=cal_id).execute()
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    resp = (
        service.events()
        .list(
            calendarId=cal_id,
            timeMin=now,
            maxResults=CONFIG.get("max_events", 3),
            singleEvents=True,
            orderBy="startTime",
        )
        .execute()
    )
    return {
        "ok": True,
        "calendar": cal.get("summary", cal_id),
        "events": [_format_event(e) for e in resp.get("items", [])],
    }


@app.route("/api/status")
def status():
    with _lock:
        fresh = _cache["data"] and time.time() - _cache["at"] < CACHE_SECONDS
        if not fresh:
            try:
                _cache["data"] = _fetch()
            except Exception as exc:  # show the problem on screen, keep serving
                _cache["data"] = {"ok": False, "error": str(exc), "events": []}
            _cache["at"] = time.time()
            _cache["data"]["fetched_at"] = dt.datetime.now(TZ).isoformat()
        return jsonify(_cache["data"])


@app.route("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


import month_api  # month view + rotation
month_api.register(app)


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=CONFIG.get("port", 8080))
