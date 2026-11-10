"""Daily screen data: today's timeline, today's tasks, and Milwaukee weather.

Hooked in from app.py with:  import today_api; today_api.register(app)
GET /api/today -> {ok, date, events:[...], all_day:[...], tasks:[...], weather:{...}}

Tasks are events colored Grape (colorId "3"), the color tools/gcal.py gives --type task.
Weather is Open-Meteo (free, no key), cached 15 minutes.
"""
import datetime as dt
import json
import threading
import time
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from flask import jsonify
from googleapiclient.discovery import build

BASE = Path(__file__).resolve().parent
CONFIG = json.loads((BASE / "config.json").read_text())
TZ = ZoneInfo(CONFIG["timezone"])
CAL_CACHE = CONFIG.get("cache_seconds", 120)
WX_CACHE = 15 * 60
LAT, LON = CONFIG.get("weather_lat", 43.0389), CONFIG.get("weather_lon", -87.9065)  # Milwaukee

TYPES = {"7": "block", "5": "appointment", "3": "task"}
_lock = threading.Lock()
_cal = {"at": 0.0, "data": None}
_wx = {"at": 0.0, "data": None}


def _events_today(creds_fn):
    service = build("calendar", "v3", credentials=creds_fn(), cache_discovery=False)
    today = dt.datetime.now(TZ).date()
    start = dt.datetime.combine(today, dt.time.min, TZ)
    end = start + dt.timedelta(days=1)
    items = service.events().list(
        calendarId=CONFIG["calendar_id"], timeMin=start.isoformat(), timeMax=end.isoformat(),
        singleEvents=True, orderBy="startTime", maxResults=50,
    ).execute().get("items", [])

    events, all_day, tasks = [], [], []
    for ev in items:
        kind = TYPES.get(ev.get("colorId", ""), "other")
        title = ev.get("summary", "(no title)")
        if kind == "task":
            tasks.append({"title": title.removeprefix("Task:").strip()})
            continue
        if "date" in ev["start"]:
            all_day.append({"title": title, "kind": kind})
            continue
        s = dt.datetime.fromisoformat(ev["start"]["dateTime"]).astimezone(TZ)
        e = dt.datetime.fromisoformat(ev["end"]["dateTime"]).astimezone(TZ)
        events.append({"title": title, "kind": kind, "location": ev.get("location", ""),
                       "start": s.isoformat(), "end": e.isoformat()})
    return {"date": today.isoformat(), "events": events, "all_day": all_day, "tasks": tasks}


WMO = {0: "Clear", 1: "Mostly clear", 2: "Partly cloudy", 3: "Cloudy", 45: "Fog", 48: "Fog",
       51: "Drizzle", 53: "Drizzle", 55: "Drizzle", 56: "Freezing drizzle", 57: "Freezing drizzle",
       61: "Light rain", 63: "Rain", 65: "Heavy rain", 66: "Freezing rain", 67: "Freezing rain",
       71: "Light snow", 73: "Snow", 75: "Heavy snow", 77: "Snow", 80: "Showers", 81: "Showers",
       82: "Heavy showers", 85: "Snow showers", 86: "Snow showers", 95: "Thunderstorms",
       96: "Thunderstorms", 99: "Thunderstorms"}


def _icon(code, is_day=1):
    if code in (0, 1):
        return "sun" if is_day else "moon"
    if code == 2:
        return "partly" if is_day else "cloud"
    if code == 3:
        return "cloud"
    if code in (45, 48):
        return "fog"
    if code in (71, 73, 75, 77, 85, 86):
        return "snow"
    if code >= 95:
        return "storm"
    return "rain"


def _weather():
    r = requests.get("https://api.open-meteo.com/v1/forecast", timeout=25, params={
        "latitude": LAT, "longitude": LON, "timezone": CONFIG["timezone"],
        "temperature_unit": "fahrenheit", "wind_speed_unit": "mph", "forecast_days": 2,
        "current": "temperature_2m,apparent_temperature,weather_code,is_day,wind_speed_10m",
        "hourly": "temperature_2m,precipitation_probability,weather_code,is_day",
        "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max",
    })
    r.raise_for_status()
    w = r.json()
    cur, daily, hourly = w["current"], w["daily"], w["hourly"]
    now_hour = dt.datetime.now(TZ).replace(minute=0, second=0, microsecond=0)
    hours = []
    for t, temp, pop, code, day in zip(hourly["time"], hourly["temperature_2m"],
                                       hourly["precipitation_probability"],
                                       hourly["weather_code"], hourly["is_day"]):
        ht = dt.datetime.fromisoformat(t).replace(tzinfo=TZ)
        if ht > now_hour and len(hours) < 6 and (ht.hour - now_hour.hour) % 2 == 0:
            hours.append({"time": ht.isoformat(), "temp": round(temp), "pop": pop or 0,
                          "icon": _icon(code, day)})
    return {
        "temp": round(cur["temperature_2m"]), "feels": round(cur["apparent_temperature"]),
        "wind": round(cur["wind_speed_10m"]), "label": WMO.get(cur["weather_code"], ""),
        "icon": _icon(cur["weather_code"], cur["is_day"]),
        "high": round(daily["temperature_2m_max"][0]), "low": round(daily["temperature_2m_min"][0]),
        "pop": daily["precipitation_probability_max"][0] or 0, "hours": hours,
    }


def register(app, creds_fn):
    @app.route("/api/today")
    def api_today():
        with _lock:
            now = time.time()
            if not _cal["data"] or now - _cal["at"] > CAL_CACHE:
                try:
                    _cal["data"] = {"ok": True, **_events_today(creds_fn)}
                except Exception as exc:  # keep serving; show the problem on screen
                    _cal["data"] = {"ok": False, "error": str(exc), "events": [], "all_day": [], "tasks": []}
                _cal["at"] = now
            if not _wx["data"] or now - _wx["at"] > WX_CACHE:
                try:
                    _wx["data"] = _weather()
                    _wx["at"] = now
                except Exception as exc:  # keep the last good forecast; retry in a minute
                    _wx["at"] = now - WX_CACHE + 60
                    if not _wx["data"]:
                        _wx["data"] = {"error": str(exc)}
            return jsonify({**_cal["data"], "weather": _wx["data"],
                            "fetched_at": dt.datetime.now(TZ).isoformat()})
