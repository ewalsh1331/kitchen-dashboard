"""Claude Calendar command-line tool for Claude Code on the Pi.

Examples (run from ~/kitchen-dashboard):
  .venv/bin/python tools/gcal.py list --days 7
  .venv/bin/python tools/gcal.py list --start 2026-11-01 --end 2026-11-30 --q golf
  .venv/bin/python tools/gcal.py get EVENT_ID
  .venv/bin/python tools/gcal.py create --type task --title "Task: Clean sheets" --date 2026-11-08
  .venv/bin/python tools/gcal.py create --type appointment --title "Golf - Tripoli CC" \
      --start "2026-11-07 09:00" --end "2026-11-07 13:30" --location "Tripoli Country Club"
  .venv/bin/python tools/gcal.py update EVENT_ID --start "2026-11-07 10:00" --end "2026-11-07 14:30"
  .venv/bin/python tools/gcal.py delete EVENT_ID
  .venv/bin/python tools/gcal.py pending list
  .venv/bin/python tools/gcal.py pending add "Book dentist appointment"
  .venv/bin/python tools/gcal.py pending remove "dentist"

All output is JSON. Writes never send email notifications.
"""
import argparse
import datetime as dt
import json
import sys
from zoneinfo import ZoneInfo

from googleapiclient.discovery import build

from google_auth import CONFIG, credentials

CAL_ID = CONFIG["calendar_id"]
TZ_NAME = CONFIG["timezone"]
TZ = ZoneInfo(TZ_NAME)
WEEKLY_REVIEW_ID = CONFIG.get("weekly_review_event_id", "u1o6gnc8hkahdq38kftihukh0g")

# Eric's color conventions
TYPE_COLORS = {"block": "7", "appointment": "5", "task": "3"}
COLOR_NAMES = {"7": "teal/Peacock (block)", "5": "yellow/Banana (appointment)", "3": "purple/Grape (task)"}


def svc():
    return build("calendar", "v3", credentials=credentials(), cache_discovery=False)


def out(obj):
    print(json.dumps(obj, indent=2, ensure_ascii=False))


def parse_local(text: str) -> dt.datetime:
    """'2026-11-07 09:00' or ISO -> timezone-aware datetime in Chicago time."""
    d = dt.datetime.fromisoformat(text.replace("T", " "))
    return d if d.tzinfo else d.replace(tzinfo=TZ)


def slim(ev: dict) -> dict:
    s, e = ev.get("start", {}), ev.get("end", {})
    return {
        "id": ev["id"],
        "title": ev.get("summary", ""),
        "start": s.get("dateTime") or s.get("date"),
        "end": e.get("dateTime") or e.get("date"),
        "all_day": "date" in s,
        "color": COLOR_NAMES.get(ev.get("colorId", ""), ev.get("colorId", "calendar default")),
        "location": ev.get("location", ""),
        "description": ev.get("description", ""),
        "recurring": bool(ev.get("recurringEventId") or ev.get("recurrence")),
        "series_id": ev.get("recurringEventId", ""),
    }


def cmd_list(a):
    if a.start:
        t_min = parse_local(a.start)
    else:
        t_min = dt.datetime.now(TZ)
    t_max = parse_local(a.end) if a.end else t_min + dt.timedelta(days=a.days)
    items, token = [], None
    while True:
        resp = svc().events().list(
            calendarId=CAL_ID, timeMin=t_min.isoformat(), timeMax=t_max.isoformat(),
            singleEvents=True, orderBy="startTime", q=a.q, pageToken=token, maxResults=250,
        ).execute()
        items += resp.get("items", [])
        token = resp.get("nextPageToken")
        if not token:
            break
    out([slim(e) for e in items])


def cmd_get(a):
    out(slim(svc().events().get(calendarId=CAL_ID, eventId=a.id).execute()))


def times_body(a, existing=None) -> dict:
    body = {}
    if a.date:  # all-day
        day = dt.date.fromisoformat(a.date)
        end_day = dt.date.fromisoformat(a.end_date) if getattr(a, "end_date", None) else day
        body["start"] = {"date": day.isoformat()}
        body["end"] = {"date": (end_day + dt.timedelta(days=1)).isoformat()}
    elif a.start:
        start = parse_local(a.start)
        end = parse_local(a.end) if a.end else start + dt.timedelta(hours=1)
        body["start"] = {"dateTime": start.isoformat(), "timeZone": TZ_NAME}
        body["end"] = {"dateTime": end.isoformat(), "timeZone": TZ_NAME}
    return body


def cmd_create(a):
    body = {"summary": a.title}
    if a.type == "task" and not (a.start or a.date_only):
        # Task convention: 12:00-12:15 PM timed block, popup at start, shows as free.
        day = a.date or dt.date.today().isoformat()
        a.start, a.end, a.date = f"{day} 12:00", f"{day} 12:15", None
    body.update(times_body(a))
    if "start" not in body:
        sys.exit("Give --start (timed) or --date (all-day).")
    color = a.color or TYPE_COLORS.get(a.type)
    if color:
        body["colorId"] = color
    if a.type == "task":
        body["transparency"] = "transparent"
        body["reminders"] = {"useDefault": False, "overrides": [{"method": "popup", "minutes": 0}]}
    if a.location:
        body["location"] = a.location
    if a.description:
        body["description"] = a.description
    ev = svc().events().insert(calendarId=CAL_ID, body=body, sendUpdates="none").execute()
    out({"created": slim(ev)})


def cmd_update(a):
    body = times_body(a)
    if a.title:
        body["summary"] = a.title
    if a.location is not None:
        body["location"] = a.location
    if a.color:
        body["colorId"] = a.color
    if a.type:
        body["colorId"] = TYPE_COLORS[a.type]
    if a.description is not None:
        body["description"] = a.description
    if a.append_description:
        current = svc().events().get(calendarId=CAL_ID, eventId=a.id).execute().get("description", "")
        body["description"] = (current.rstrip() + "\n" + a.append_description).strip()
    if not body:
        sys.exit("Nothing to update.")
    ev = svc().events().patch(calendarId=CAL_ID, eventId=a.id, body=body, sendUpdates="none").execute()
    out({"updated": slim(ev)})


def cmd_delete(a):
    ev = svc().events().get(calendarId=CAL_ID, eventId=a.id).execute()
    svc().events().delete(calendarId=CAL_ID, eventId=a.id, sendUpdates="none").execute()
    out({"deleted": slim(ev)})


# ---- Weekly Review pending list -------------------------------------------

def _review():
    return svc().events().get(calendarId=CAL_ID, eventId=WEEKLY_REVIEW_ID).execute()


def _split(desc: str):
    """Return (lines before the pending items, pending item lines)."""
    lines = desc.replace("<br>", "\n").splitlines()
    idx = next((i for i, l in enumerate(lines) if l.strip().lower().startswith("pending")), None)
    if idx is None:
        return lines + ["", "Pending:"], []
    head = lines[: idx + 1]
    items = [l for l in lines[idx + 1:] if l.strip()]
    return head, items


def cmd_pending(a):
    ev = _review()
    head, items = _split(ev.get("description", ""))
    if a.action == "list":
        out({"pending": [i.lstrip("-•* ").strip() for i in items]})
        return
    if not a.text:
        sys.exit("Give the item text.")
    if a.action == "add":
        items.append(f"- {a.text.strip()}")
    else:  # remove
        matches = [i for i in items if a.text.lower() in i.lower()]
        if len(matches) != 1:
            out({"error": f"{len(matches)} items match; be more specific", "matches": matches})
            sys.exit(1)
        items.remove(matches[0])
    desc = "\n".join(head + items)
    svc().events().patch(calendarId=CAL_ID, eventId=WEEKLY_REVIEW_ID,
                         body={"description": desc}, sendUpdates="none").execute()
    out({"pending": [i.lstrip("-•* ").strip() for i in items]})


def main():
    p = argparse.ArgumentParser(description="Claude Calendar tool")
    sub = p.add_subparsers(dest="cmd", required=True)

    l = sub.add_parser("list"); l.set_defaults(fn=cmd_list)
    l.add_argument("--days", type=int, default=7)
    l.add_argument("--start"); l.add_argument("--end"); l.add_argument("--q")

    g = sub.add_parser("get"); g.set_defaults(fn=cmd_get); g.add_argument("id")

    for name, fn in (("create", cmd_create), ("update", cmd_update)):
        c = sub.add_parser(name); c.set_defaults(fn=fn)
        if name == "update":
            c.add_argument("id")
        c.add_argument("--title", required=(name == "create"))
        c.add_argument("--type", choices=list(TYPE_COLORS))
        c.add_argument("--start", help="'YYYY-MM-DD HH:MM' Chicago time")
        c.add_argument("--end")
        c.add_argument("--date", help="all-day date YYYY-MM-DD (tasks: the day to remind)")
        c.add_argument("--end-date", help="last day of a multi-day all-day event")
        c.add_argument("--date-only", action="store_true", help="task as all-day instead of 12:00 block")
        c.add_argument("--color", help="raw Google colorId override")
        c.add_argument("--location"); c.add_argument("--description")
        if name == "update":
            c.add_argument("--append-description")

    d = sub.add_parser("delete"); d.set_defaults(fn=cmd_delete); d.add_argument("id")

    pe = sub.add_parser("pending"); pe.set_defaults(fn=cmd_pending)
    pe.add_argument("action", choices=["list", "add", "remove"]); pe.add_argument("text", nargs="?")

    a = p.parse_args()
    for attr in ("date", "date_only", "end_date"):
        if not hasattr(a, attr):
            setattr(a, attr, None)
    a.fn(a)


if __name__ == "__main__":
    main()
