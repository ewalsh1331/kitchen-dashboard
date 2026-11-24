"""
oura_to_sheets.py

Pulls a day's worth of health data from the official Oura API v2 and
appends it as a new row to a Google Sheet. Also pulls individual logged
workouts into a separate "Oura Workouts" tab.

Unlike Garmin, Oura has an official public API for personal use — you
authenticate with a Personal Access Token rather than your login
credentials, so this is a supported, stable integration (no scraping).

Intended to be run on a schedule (cron, GitHub Actions, cloud function,
etc.) once per day.

------------------------------------------------------------------------------
SETUP
------------------------------------------------------------------------------

1. Install dependencies:
     pip install requests gspread google-auth python-dotenv --break-system-packages

2. Get an Oura Personal Access Token:
   a. Go to https://cloud.ouraring.com/personal-access-tokens
   b. Log in with your Oura account
   c. Click "Create New Personal Access Token", give it any label
   d. Copy the token — you won't be able to see it again after leaving
      the page

3. Credentials — using a .env file (recommended):
   Copy `.env.example` to `.env` in the same folder as this script (the
   same .env file used by garmin_to_sheets.py works fine — just add the
   Oura variables below to it), then fill in your real values:
     cp .env.example .env

4. Required variables (set in .env or your environment):
     OURA_ACCESS_TOKEN=<your personal access token>
     GOOGLE_SERVICE_ACCOUNT_FILE=/path/to/service_account.json
     GOOGLE_SHEET_ID=<the long id from the sheet's URL>
     GOOGLE_OURA_TAB=Oura                  (optional, defaults to "Oura")
     GOOGLE_OURA_WORKOUTS_TAB=Oura Workouts (optional, defaults to that)

   The daily-summary and workouts tabs are both created automatically
   the first time they're needed, as long as the spreadsheet itself
   already exists and is shared with the service account (Editor access).

5. Run it:
     python oura_to_sheets.py

   With no arguments, this now runs in SYNC mode (see below) rather than
   just pulling today — this is the mode meant for scheduled/automated
   runs (Task Scheduler, cron, etc.).

   Or run it for a specific past date:
     python oura_to_sheets.py 2026-07-28

   Or backfill a whole date range (inclusive):
     python oura_to_sheets.py --backfill 2025-01-01 2026-07-31

   Or re-check days already in the sheet against Oura and update any row
   that differs (never replaces data with blanks):
     python oura_to_sheets.py --repair 2026-08-01 2026-11-03
   (Sync mode now does this automatically for the lookback window.)

   Or force specific dates to exactly what Oura has now, blanks included
   (e.g. a night the ring wasn't worn that shows another night's sleep):
     python oura_to_sheets.py --replace 2026-11-02 2026-10-19

   To wipe both Oura tabs completely and start fresh:
     python oura_to_sheets.py --reset

   To see the raw, unprocessed API response for a given day (useful for
   debugging missing fields):
     python oura_to_sheets.py --raw 2026-07-30

------------------------------------------------------------------------------
SYNC MODE & DEDUPLICATION (for scheduled/automated runs)
------------------------------------------------------------------------------
Running the script with NO arguments triggers sync mode:
  1. Reads the Date column already in the sheet to see what's there.
  2. Looks back SYNC_LOOKBACK_DAYS days (default 14, set via .env) through
     yesterday, and pulls ONLY the dates in that window that are missing
     from the sheet.
  3. If nothing is missing, it does nothing and exits — safe to run as
     often as you like (e.g. daily via Task Scheduler) without creating
     duplicate rows.

This means: if your computer was off for a few days and the scheduled
task didn't run, the next run automatically catches up on those missed
days (as long as they're within the lookback window) — no manual
backfill needed. Every append path (sync, explicit single date, and
--backfill) checks existing dates first and skips any date already
present, so re-running the same date twice never creates duplicates.

------------------------------------------------------------------------------
DATE CONVENTION (decided 2026-11-05 — keep in sync with garmin_to_sheets.py)
------------------------------------------------------------------------------
Every row is dated by WAKE-UP date: the sleep/HRV in the row for D is the
night you woke up from on the morning of D (the same date the Oura app shows
it under), and readiness on that row is based on that same night.

History: an Aug 13 version shifted sleep to the bed date (target_date + 1).
That was reverted on 2026-11-05 so both tabs match the apps; the Oura and
Garmin tabs were converted to wake-up date the same day. Don't re-add a
shift here without also changing garmin_to_sheets.py and the sheet history.

------------------------------------------------------------------------------
DATA PULLED (grouped by category)
------------------------------------------------------------------------------
Not every field will be populated every day — it depends on ring
generation, whether you wore it that night, and which features Oura has
enabled for your account. Missing fields come through as blank cells
rather than causing an error.

  Sleep             total/deep/light/REM/awake duration, efficiency,
                     latency, average HRV, lowest & average heart rate,
                     average breathing rate, sleep score
  Readiness         readiness score, temperature deviation, HRV balance
                     contributor, previous-night contributor, recovery
                     index contributor
  Activity           activity score, steps, total/active calories,
                     equivalent walking distance, high/medium/low
                     activity time, sedentary time, inactivity alerts
  Stress             high-stress time, recovery-high time, day summary
                     label (e.g. "restored", "normal", "stressful")
  SpO2                average blood oxygen saturation, breathing
                     disturbance index

Individual workouts (runs, rides, yoga sessions, etc.) are pulled
separately into their own "Oura Workouts" tab, since a day can have
zero, one, or several logged.

Sheet headers are written as clean, human-readable labels rather than
raw internal field names, so they read well if copied/pasted elsewhere.

------------------------------------------------------------------------------
NOTES
------------------------------------------------------------------------------
- Oura's daily_sleep/daily_readiness/daily_activity endpoints return
  0-100 "contributor" scores, not raw durations, for most factors. Raw
  sleep stage durations (deep/light/REM/awake, in seconds) instead come
  from the "sleep" (per-session) endpoint, which this script also pulls
  and merges in, using that night's longest ("long_sleep") session.
- The API has rate limits; the script pauses briefly between days during
  a backfill to stay well under them.
"""

import os
import sys
import json
import time
import datetime

import requests
import gspread
from google.oauth2.service_account import Credentials
from dotenv import load_dotenv

load_dotenv()


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

OURA_ACCESS_TOKEN = os.environ.get("OURA_ACCESS_TOKEN")

GOOGLE_SERVICE_ACCOUNT_FILE = os.environ.get("GOOGLE_SERVICE_ACCOUNT_FILE")
GOOGLE_SHEET_ID = os.environ.get("GOOGLE_SHEET_ID")
GOOGLE_OURA_TAB = os.environ.get("GOOGLE_OURA_TAB", "Oura")
GOOGLE_OURA_WORKOUTS_TAB = os.environ.get("GOOGLE_OURA_WORKOUTS_TAB", "Oura Workouts")
# Pause between days during --backfill (seconds). Oura's API is official
# and documented at 5000 requests/5 minutes, so this can run much tighter
# than Garmin's. Override via .env, e.g. OURA_BACKFILL_PAUSE_SECONDS=0.05.
BACKFILL_PAUSE_SECONDS = float(os.environ.get("OURA_BACKFILL_PAUSE_SECONDS", "0.1"))
SYNC_LOOKBACK_DAYS = int(os.environ.get("SYNC_LOOKBACK_DAYS", "14"))

REQUIRED_ENV_VARS = [
    "OURA_ACCESS_TOKEN",
    "GOOGLE_SERVICE_ACCOUNT_FILE",
    "GOOGLE_SHEET_ID",
]

OURA_BASE_URL = "https://api.ouraring.com/v2/usercollection"


def check_env():
    missing = [v for v in REQUIRED_ENV_VARS if not os.environ.get(v)]
    if missing:
        print(f"Missing required environment variables: {', '.join(missing)}")
        sys.exit(1)


# ---------------------------------------------------------------------------
# Oura API
# ---------------------------------------------------------------------------

def oura_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"Authorization": f"Bearer {OURA_ACCESS_TOKEN}"})
    return s


def _get_endpoint(session: requests.Session, endpoint: str, date_str: str) -> list:
    """Fetch a single day's records for a given Oura endpoint. Returns the
    raw list of records (usually 0 or 1 for daily_* endpoints, possibly
    more for sleep/workout)."""
    try:
        resp = session.get(
            f"{OURA_BASE_URL}/{endpoint}",
            params={"start_date": date_str, "end_date": date_str},
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json().get("data", [])
    except Exception as e:
        print(f"  WARNING: {endpoint} fetch failed for {date_str}: {e}")
        return []


def get_endpoint_for_date_padded(session: requests.Session, endpoint: str,
                                  target_date: str, pad_days: int = 1) -> list:
    """Fetch a day's records for an Oura endpoint, working around an
    apparent bug where several endpoints (confirmed so far: sleep,
    workout, daily_activity) don't reliably match their own 'day' field
    against an exact start_date=end_date query — records can come back
    empty even when data genuinely exists for that date.

    Queries a padded window on each side of the target date, then filters
    results by each record's own 'day' field (the reliable source of
    truth) rather than trusting the query range alone. Used for every
    endpoint as a precaution, since three separate endpoints have shown
    this same behavior."""
    target = datetime.date.fromisoformat(target_date)
    start = (target - datetime.timedelta(days=pad_days)).isoformat()
    end = (target + datetime.timedelta(days=pad_days)).isoformat()

    try:
        resp = session.get(
            f"{OURA_BASE_URL}/{endpoint}",
            params={"start_date": start, "end_date": end},
            timeout=30,
        )
        resp.raise_for_status()
        records = resp.json().get("data", [])
    except Exception as e:
        print(f"  WARNING: {endpoint} fetch failed for {target_date}: {e}")
        return []

    return [r for r in records if r.get("day") == target_date]


def get_sleep_sessions_for_date(session: requests.Session, target_date: str) -> list:
    """Fetch sleep sessions for a given day (see get_endpoint_for_date_padded
    for why the padded-window approach is needed)."""
    return get_endpoint_for_date_padded(session, "sleep", target_date)


def get_oura_data(session: requests.Session, target_date: str) -> dict:
    """Pull a full day's metrics across all categories."""

    daily_sleep = get_endpoint_for_date_padded(session, "daily_sleep", target_date)
    daily_readiness = get_endpoint_for_date_padded(session, "daily_readiness", target_date)
    daily_activity = get_endpoint_for_date_padded(session, "daily_activity", target_date)
    daily_stress = get_endpoint_for_date_padded(session, "daily_stress", target_date)
    daily_spo2 = get_endpoint_for_date_padded(session, "daily_spo2", target_date)
    sleep_sessions = get_sleep_sessions_for_date(session, target_date)

    sleep0 = daily_sleep[0] if daily_sleep else {}
    readiness0 = daily_readiness[0] if daily_readiness else {}
    activity0 = daily_activity[0] if daily_activity else {}
    stress0 = daily_stress[0] if daily_stress else {}
    spo20 = daily_spo2[0] if daily_spo2 else {}

    readiness_contrib = readiness0.get("contributors", {}) or {}

    # Raw sleep-stage durations come from the "sleep" endpoint, not
    # daily_sleep (which only has a 0-100 score). Use the main overnight
    # ("long_sleep") session if there is one.
    long_sleep = next(
        (s for s in sleep_sessions if s.get("type") == "long_sleep"),
        sleep_sessions[0] if sleep_sessions else {},
    )

    # Guard: only accept a night that actually ended (woke up) on target_date.
    # Right after a night is recorded, Oura can briefly file it under the
    # previous date; saving that copied the next night onto days when the
    # ring wasn't worn, and shifted many rows by a day.
    ended = (long_sleep.get("bedtime_end") or "")[:10]
    if long_sleep and ended != target_date:
        print(f"  NOTE: {target_date}: ignoring a night that ended on {ended}")
        long_sleep, sleep0 = {}, {}

    spo2_breathing = spo20.get("breathing_disturbance_index")
    spo2_avg = (spo20.get("spo2_percentage") or {}).get("average")

    row = {}

    # --- Sleep -----------------------------------------------------------
    row["date"] = target_date
    row["sleep_score"] = sleep0.get("score")
    row["sleep_total_seconds"] = long_sleep.get("total_sleep_duration")
    row["sleep_deep_seconds"] = long_sleep.get("deep_sleep_duration")
    row["sleep_light_seconds"] = long_sleep.get("light_sleep_duration")
    row["sleep_rem_seconds"] = long_sleep.get("rem_sleep_duration")
    row["sleep_awake_seconds"] = long_sleep.get("awake_time")
    row["sleep_efficiency_percent"] = long_sleep.get("efficiency")
    row["sleep_latency_seconds"] = long_sleep.get("latency")
    row["sleep_avg_hrv_ms"] = long_sleep.get("average_hrv")
    row["sleep_avg_heart_rate"] = long_sleep.get("average_heart_rate")
    row["sleep_lowest_heart_rate"] = long_sleep.get("lowest_heart_rate")
    row["sleep_avg_breath_rate"] = long_sleep.get("average_breath")

    # --- Readiness ---------------------------------------------------------
    row["readiness_score"] = readiness0.get("score")
    row["readiness_temperature_deviation_c"] = readiness0.get("temperature_deviation")
    row["readiness_hrv_balance"] = readiness_contrib.get("hrv_balance")
    row["readiness_previous_night"] = readiness_contrib.get("previous_night")
    row["readiness_recovery_index"] = readiness_contrib.get("recovery_index")
    row["readiness_activity_balance"] = readiness_contrib.get("activity_balance")
    row["readiness_sleep_balance"] = readiness_contrib.get("sleep_balance")

    # --- Activity -----------------------------------------------------------
    row["activity_score"] = activity0.get("score")
    row["steps"] = activity0.get("steps")
    row["calories_total"] = activity0.get("total_calories")
    row["calories_active"] = activity0.get("active_calories")
    row["equivalent_walking_distance_m"] = activity0.get("equivalent_walking_distance")
    row["high_activity_seconds"] = activity0.get("high_activity_time")
    row["medium_activity_seconds"] = activity0.get("medium_activity_time")
    row["low_activity_seconds"] = activity0.get("low_activity_time")
    row["sedentary_seconds"] = activity0.get("sedentary_time")
    row["inactivity_alerts"] = activity0.get("inactivity_alerts")
    row["target_calories"] = activity0.get("target_calories")
    row["meters_to_target"] = activity0.get("meters_to_target")

    # --- Stress ----------------------------------------------------------------
    row["stress_high_seconds"] = stress0.get("stress_high")
    row["stress_recovery_high_seconds"] = stress0.get("recovery_high")
    row["stress_day_summary"] = stress0.get("day_summary")

    # --- SpO2 --------------------------------------------------------------------
    row["spo2_avg_percent"] = spo2_avg
    row["spo2_breathing_disturbance_index"] = spo2_breathing

    return row


def get_workouts_raw_for_date(session: requests.Session, target_date: str) -> list:
    """Fetch workouts for a given day (see get_endpoint_for_date_padded for
    why the padded-window approach is needed)."""
    return get_endpoint_for_date_padded(session, "workout", target_date)


def get_workouts_for_date(session: requests.Session, target_date: str) -> list:
    """Pull individual logged workouts for a given day."""
    workouts = get_workouts_raw_for_date(session, target_date)

    rows = []
    for w in workouts:
        rows.append({
            "date": target_date,
            "activity": w.get("activity"),
            "intensity": w.get("intensity"),
            "start_datetime": w.get("start_datetime"),
            "end_datetime": w.get("end_datetime"),
            "calories": w.get("calories"),
            "distance_meters": w.get("distance"),
            "source": w.get("source"),
        })
    return rows


# ---------------------------------------------------------------------------
# Header labels
# ---------------------------------------------------------------------------

SUMMARY_HEADER_LABELS = {
    "date": "Date",
    "sleep_score": "Sleep Score",
    "sleep_total_seconds": "Sleep Total (sec)",
    "sleep_deep_seconds": "Sleep - Deep (sec)",
    "sleep_light_seconds": "Sleep - Light (sec)",
    "sleep_rem_seconds": "Sleep - REM (sec)",
    "sleep_awake_seconds": "Sleep - Awake (sec)",
    "sleep_efficiency_percent": "Sleep Efficiency (%)",
    "sleep_latency_seconds": "Sleep Latency (sec)",
    "sleep_avg_hrv_ms": "Sleep Avg HRV (ms)",
    "sleep_avg_heart_rate": "Sleep Avg Heart Rate",
    "sleep_lowest_heart_rate": "Sleep Lowest Heart Rate",
    "sleep_avg_breath_rate": "Sleep Avg Breath Rate",
    "readiness_score": "Readiness Score",
    "readiness_temperature_deviation_c": "Temperature Deviation (C)",
    "readiness_hrv_balance": "Readiness - HRV Balance",
    "readiness_previous_night": "Readiness - Previous Night",
    "readiness_recovery_index": "Readiness - Recovery Index",
    "readiness_activity_balance": "Readiness - Activity Balance",
    "readiness_sleep_balance": "Readiness - Sleep Balance",
    "activity_score": "Activity Score",
    "steps": "Steps",
    "calories_total": "Calories Total",
    "calories_active": "Calories Active",
    "equivalent_walking_distance_m": "Equivalent Walking Distance (m)",
    "high_activity_seconds": "High Activity Time (sec)",
    "medium_activity_seconds": "Medium Activity Time (sec)",
    "low_activity_seconds": "Low Activity Time (sec)",
    "sedentary_seconds": "Sedentary Time (sec)",
    "inactivity_alerts": "Inactivity Alerts",
    "target_calories": "Target Calories",
    "meters_to_target": "Meters To Target",
    "stress_high_seconds": "Stress High (sec)",
    "stress_recovery_high_seconds": "Stress Recovery High (sec)",
    "stress_day_summary": "Stress Day Summary",
    "spo2_avg_percent": "SpO2 Avg (%)",
    "spo2_breathing_disturbance_index": "Breathing Disturbance Index",
}

WORKOUT_HEADER_LABELS = {
    "date": "Date",
    "activity": "Activity",
    "intensity": "Intensity",
    "start_datetime": "Start Time",
    "end_datetime": "End Time",
    "calories": "Calories",
    "distance_meters": "Distance (m)",
    "source": "Source",
}


def _display_headers(keys, label_map):
    return [label_map.get(k, k.replace("_", " ").title()) for k in keys]


# ---------------------------------------------------------------------------
# Google Sheets
# ---------------------------------------------------------------------------

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
_gspread_client = None


def _get_spreadsheet():
    global _gspread_client
    if _gspread_client is None:
        creds = Credentials.from_service_account_file(
            GOOGLE_SERVICE_ACCOUNT_FILE, scopes=SCOPES
        )
        _gspread_client = gspread.authorize(creds)
    return _gspread_client.open_by_key(GOOGLE_SHEET_ID)


def get_worksheet():
    """Daily-summary worksheet — created automatically if it doesn't exist
    yet."""
    sh = _get_spreadsheet()
    try:
        return sh.worksheet(GOOGLE_OURA_TAB)
    except gspread.exceptions.WorksheetNotFound:
        return sh.add_worksheet(title=GOOGLE_OURA_TAB, rows=1000, cols=40)


def get_workouts_worksheet():
    """Workouts worksheet — created automatically if it doesn't exist yet."""
    sh = _get_spreadsheet()
    try:
        return sh.worksheet(GOOGLE_OURA_WORKOUTS_TAB)
    except gspread.exceptions.WorksheetNotFound:
        return sh.add_worksheet(title=GOOGLE_OURA_WORKOUTS_TAB, rows=1000, cols=20)


def _sheet_needs_header(ws) -> bool:
    """Reliably check whether a tab's header row is actually empty.
    get_all_values() pads out to the sheet's declared grid size (which
    survives ws.clear() calls), so it can look non-empty even when every
    cell is blank. row_values(1) trims trailing blanks for that specific
    row, so it's a reliable emptiness check."""
    return not ws.row_values(1)


def get_existing_dates(ws) -> set:
    """Return the set of date strings (YYYY-MM-DD) already present in
    column A of the Oura summary tab. Used to skip dates that have
    already been pulled, so re-running the script never creates
    duplicate rows."""
    values = ws.col_values(1)  # column A, includes header row if present
    dates = set()
    for v in values[1:]:  # skip header
        v = (v or "").strip()
        if v:
            dates.add(v)
    return dates


def append_to_sheet(row_dict: dict):
    """Append a single day's summary row (used by normal daily runs)."""
    ws = get_worksheet()
    if _sheet_needs_header(ws):
        ws.append_row(_display_headers(row_dict.keys(), SUMMARY_HEADER_LABELS),
                      table_range="A1")
    ws.append_row(list(row_dict.values()), table_range="A1")


def append_rows_to_sheet(rows: list):
    """Append many summary rows at once (used by backfill)."""
    if not rows:
        return
    ws = get_worksheet()
    if _sheet_needs_header(ws):
        ws.append_row(_display_headers(rows[0].keys(), SUMMARY_HEADER_LABELS),
                      table_range="A1")
    ws.append_rows([list(r.values()) for r in rows], table_range="A1")


def append_workouts_to_sheet(rows: list):
    """Append workout rows to the Oura Workouts tab."""
    if not rows:
        return
    ws = get_workouts_worksheet()
    if _sheet_needs_header(ws):
        ws.append_row(_display_headers(rows[0].keys(), WORKOUT_HEADER_LABELS),
                      table_range="A1")
    ws.append_rows([list(r.values()) for r in rows], table_range="A1")


def clear_all_data():
    """Wipe both Oura tabs completely so future writes start from a clean,
    predictable A1 origin."""
    ws = get_worksheet()
    ws.clear()
    print(f"Cleared '{ws.title}' tab.")

    wws = get_workouts_worksheet()
    wws.clear()
    print(f"Cleared '{wws.title}' tab.")


# ---------------------------------------------------------------------------
# Backfill
# ---------------------------------------------------------------------------

def daterange(start: datetime.date, end: datetime.date):
    for n in range((end - start).days + 1):
        yield start + datetime.timedelta(days=n)


def run_backfill(start_str: str, end_str: str, session: requests.Session,
                  existing_dates: set, pause_seconds: float = BACKFILL_PAUSE_SECONDS,
                  batch_size: int = 30):
    start_date = datetime.date.fromisoformat(start_str)
    end_date = datetime.date.fromisoformat(end_str)

    if start_date > end_date:
        print("Start date must be before end date.")
        sys.exit(1)

    all_days = list(daterange(start_date, end_date))
    days_to_pull = [d for d in all_days if d.isoformat() not in existing_dates]
    skipped = len(all_days) - len(days_to_pull)

    print(f"Backfilling {start_date} through {end_date}...")
    if skipped:
        print(f"Skipping {skipped} date(s) already in the sheet.")
    if not days_to_pull:
        print("Nothing to do — every date in this range is already present.")
        return

    summary_batch = []
    workout_batch = []
    total_days = len(days_to_pull)
    done = 0

    for day in days_to_pull:
        day_str = day.isoformat()
        try:
            summary_batch.append(get_oura_data(session, day_str))
            workout_batch.extend(get_workouts_for_date(session, day_str))
        except Exception as e:
            print(f"  WARNING: failed to pull {day_str}: {e}")

        done += 1
        print(f"  [{done}/{total_days}] {day_str} pulled")

        if len(summary_batch) >= batch_size:
            append_rows_to_sheet(summary_batch)
            append_workouts_to_sheet(workout_batch)
            print(f"  -> wrote {len(summary_batch)} summary rows, "
                  f"{len(workout_batch)} workout rows")
            summary_batch = []
            workout_batch = []

        time.sleep(pause_seconds)

    if summary_batch:
        append_rows_to_sheet(summary_batch)
    if workout_batch:
        append_workouts_to_sheet(workout_batch)
    if summary_batch or workout_batch:
        print(f"  -> wrote {len(summary_batch)} summary rows, "
              f"{len(workout_batch)} workout rows")

    print("Backfill complete.")


KEY_FIELDS = ["Sleep Score", "Sleep Avg HRV (ms)", "Readiness Score", "Steps"]


def _cells(values) -> list:
    """Google Sheets treats None as "leave this cell unchanged", so blanks
    must be sent as "" to actually clear a cell when overwriting a row."""
    return ["" if v is None else v for v in values]


def _filled(values) -> int:
    return sum(1 for v in values if v not in (None, ""))


def _norm(v) -> str:
    """Compare sheet text and API values on equal terms ("85" == 85 == 85.0)."""
    if v in (None, ""):
        return ""
    try:
        f = float(v)
        return str(int(f)) if f == int(f) else str(round(f, 3))
    except (TypeError, ValueError):
        return str(v).strip()


def refresh_rows(session: requests.Session, start: datetime.date,
                 end: datetime.date):
    """Re-pull every date already in the sheet between start and end, and
    overwrite the row if Oura's current data differs.

    Why: sync mode used to write each date once and skip it forever. Two
    things went wrong with that: (1) if the ring/app hadn't finished
    syncing, the day was saved half-empty; (2) some days were saved with
    the FOLLOWING night's sleep, because Oura hadn't settled which day
    that night belonged to yet. Re-checking recent days fixes both.

    Safety: a row is only replaced if the new pull has at least as many
    filled-in fields, so this never blanks out good data."""
    ws = get_worksheet()
    values = ws.get_all_values()
    if not values:
        return
    header = values[0]
    key_cols = {k: header.index(k) for k in KEY_FIELDS if k in header}
    fixed = checked = 0
    for idx, row in enumerate(values[1:], start=2):
        if not row or not row[0]:
            continue
        try:
            day = datetime.date.fromisoformat(row[0].strip())
        except ValueError:
            continue
        if not (start <= day <= end):
            continue
        checked += 1
        old = row + [""] * (len(header) - len(row))
        new = list(get_oura_data(session, day.isoformat()).values())
        new += [""] * (len(header) - len(new))
        if [_norm(v) for v in old] == [_norm(v) for v in new[:len(header)]]:
            time.sleep(0.5)
            continue
        if _filled(new) < _filled(old):
            print(f"  {day} kept as-is (Oura now has less data than the sheet)")
            time.sleep(0.5)
            continue
        ws.update(range_name=f"A{idx}", values=[_cells(new[:len(header)])])
        fixed += 1
        changes = [f"{k} {old[c] or '-'}->{_norm(new[c]) or '-'}"
                   for k, c in key_cols.items() if _norm(old[c]) != _norm(new[c])]
        print(f"  {day} updated: {', '.join(changes) or 'minor fields'}")
        time.sleep(0.5)
    print(f"Refresh complete: {checked} day(s) checked, {fixed} updated.")


def run_sync(session: requests.Session, existing_dates: set):
    """Incremental catch-up mode for scheduled/automated runs. Looks back
    SYNC_LOOKBACK_DAYS days through yesterday, and pulls only the dates in
    that window missing from the sheet. Safe to run multiple times
    without creating duplicates."""
    yesterday = datetime.date.today() - datetime.timedelta(days=1)
    window_start = yesterday - datetime.timedelta(days=SYNC_LOOKBACK_DAYS - 1)
    candidate_days = list(daterange(window_start, yesterday))
    missing_days = [d for d in candidate_days if d.isoformat() not in existing_dates]

    if not missing_days:
        print(f"No missing dates in the last {SYNC_LOOKBACK_DAYS} days.")
        refresh_rows(session, window_start, yesterday)
        return

    print(f"Found {len(missing_days)} missing date(s) in the last "
          f"{SYNC_LOOKBACK_DAYS} days: {missing_days[0]} through {missing_days[-1]}")

    summary_batch = []
    workout_batch = []
    for day in missing_days:
        day_str = day.isoformat()
        try:
            summary_batch.append(get_oura_data(session, day_str))
            workout_batch.extend(get_workouts_for_date(session, day_str))
            print(f"  {day_str} pulled")
        except Exception as e:
            print(f"  WARNING: failed to pull {day_str}: {e}")
        time.sleep(0.5)

    if summary_batch:
        append_rows_to_sheet(summary_batch)
    if workout_batch:
        append_workouts_to_sheet(workout_batch)

    print(f"Sync complete — wrote {len(summary_batch)} summary row(s), "
          f"{len(workout_batch)} workout row(s).")

    # Also re-check days already in the window that were saved incomplete.
    refresh_rows(session, window_start, yesterday)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    check_env()

    args = sys.argv[1:]

    if args and args[0] == "--reset":
        confirm = input(
            "This will permanently delete ALL rows on both the Oura "
            "summary and Workouts tabs. Type 'yes' to confirm: "
        )
        if confirm.strip().lower() == "yes":
            clear_all_data()
        else:
            print("Cancelled.")
        return

    session = oura_session()

    if args and args[0] == "--raw":
        raw_date = args[1] if len(args) > 1 else datetime.date.today().isoformat()
        print(f"Raw 'daily_sleep' response for {raw_date}:")
        print(json.dumps(_get_endpoint(session, "daily_sleep", raw_date), indent=2))
        print()
        print(f"Raw 'sleep' (per-session) response for {raw_date} "
              f"(padded query, filtered to day={raw_date}):")
        print(json.dumps(get_sleep_sessions_for_date(session, raw_date), indent=2))
        print()
        print(f"Raw 'workout' response for {raw_date} "
              f"(padded query, filtered to day={raw_date}):")
        print(json.dumps(get_workouts_raw_for_date(session, raw_date), indent=2))
        print()
        print(f"Raw 'daily_activity' response for {raw_date} "
              f"(padded query, filtered to day={raw_date}):")
        print(json.dumps(get_endpoint_for_date_padded(session, "daily_activity", raw_date), indent=2))
        return

    if args and args[0] == "--replace":
        # Overwrite specific dates with Oura's current data, even if that
        # means blanking fields (for rows confirmed to hold another night).
        ws = get_worksheet()
        col_a = ws.col_values(1)
        for d in args[1:]:
            if d not in col_a:
                print(f"  {d} not in sheet, skipped")
                continue
            new = list(get_oura_data(session, d).values())
            ws.update(range_name=f"A{col_a.index(d) + 1}", values=[_cells(new)])
            print(f"  {d} replaced with Oura's current data")
            time.sleep(0.5)
        return

    if args and args[0] == "--repair":
        if len(args) != 3:
            print("Usage: python oura_to_sheets.py --repair START_DATE END_DATE")
            sys.exit(1)
        refresh_rows(session, datetime.date.fromisoformat(args[1]),
                           datetime.date.fromisoformat(args[2]))
        return

    ws = get_worksheet()
    existing_dates = get_existing_dates(ws)

    if args and args[0] == "--backfill":
        if len(args) != 3:
            print("Usage: python oura_to_sheets.py --backfill START_DATE END_DATE")
            print("Example: python oura_to_sheets.py --backfill 2025-01-01 2026-07-31")
            sys.exit(1)
        run_backfill(args[1], args[2], session=session, existing_dates=existing_dates)
        return

    if not args:
        # No arguments: sync mode, meant for scheduled/automated runs.
        run_sync(session, existing_dates)
        return

    # Explicit single date (manual testing / spot-filling one day)
    target_date = args[0]

    if target_date in existing_dates:
        print(f"{target_date} is already in the sheet — skipping to avoid "
              f"a duplicate row. Use --reset first if you want to overwrite it.")
        return

    print(f"Pulling Oura data for {target_date}...")
    data = get_oura_data(session, target_date)
    print(json.dumps(data, indent=2))

    workouts = get_workouts_for_date(session, target_date)
    if workouts:
        print(f"Found {len(workouts)} workout(s) for {target_date}:")
        print(json.dumps(workouts, indent=2))

    print("Appending to Google Sheet...")
    append_to_sheet(data)
    append_workouts_to_sheet(workouts)
    print("Done.")


if __name__ == "__main__":
    main()
