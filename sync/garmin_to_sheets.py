"""
garmin_to_sheets.py

Pulls a day's worth of health/activity data from Garmin Connect (using the
unofficial `garminconnect` library) and appends it as a new row to a Google
Sheet. Also supports pulling individual logged activities/workouts into a
separate "Activities" tab.

Intended to be run on a schedule (cron, GitHub Actions, cloud function, etc.)
once per day.

------------------------------------------------------------------------------
SETUP
------------------------------------------------------------------------------

1. Install dependencies:
     pip install garminconnect gspread google-auth python-dotenv --break-system-packages

2. Credentials — using a .env file (recommended):
   Copy `.env.example` to `.env` in the same folder as this script, then
   fill in your real values:
     cp .env.example .env

   The script automatically loads `.env` on startup via python-dotenv.
   Never commit your real `.env` file to version control (add it to
   .gitignore).

   Alternatively, you can skip the .env file and export the same variables
   directly in your shell/OS environment — the script works either way,
   and real environment variables always take precedence over .env values.

3. Garmin credentials (set in .env or your environment):
     GARMIN_EMAIL
     GARMIN_PASSWORD

4. Google Sheets access:
   a. Create a Google Cloud project, enable the Google Sheets API.
   b. Create a Service Account, download its JSON key file.
   c. Share your target Google Sheet with the service account's email
      address (found inside the JSON key file), giving it Editor access.
   d. Set these in .env or your environment:
        GOOGLE_SERVICE_ACCOUNT_FILE=/path/to/service_account.json
        GOOGLE_SHEET_ID=<the long id from the sheet's URL>
        GOOGLE_SHEET_TAB=Sheet1        (optional, defaults to first sheet)
        GOOGLE_ACTIVITIES_TAB=Activities  (optional, defaults to "Activities")

   Note: the daily-summary tab and the activities tab are both created
   automatically (as tabs within the same spreadsheet) the first time you
   write to them, as long as the sheet itself already exists and is shared
   with the service account.

5. Run it:
     python garmin_to_sheets.py

   With no arguments, this now runs in SYNC mode (see below) rather than
   just pulling today — this is the mode meant for scheduled/automated
   runs (Task Scheduler, cron, etc.).

   Or run it for a specific past date:
     python garmin_to_sheets.py 2026-07-28

   Or backfill a whole date range (inclusive), e.g. to pull your full
   history in one go:
     python garmin_to_sheets.py --backfill 2025-01-01 2026-07-31

   Backfill mode pauses briefly between requests to stay easy on Garmin's
   servers, and skips (rather than crashes on) any date that errors out,
   printing a warning so you can re-run just that date later if needed.

   Or re-check days already in the sheet against Garmin and update any row
   that changed (e.g. saved before the watch finished syncing). Never
   replaces data with blanks:
     python garmin_to_sheets.py --repair 2026-08-01 2026-11-03
   (Sync mode now does this automatically for the lookback window.)

   To wipe both tabs completely and start fresh (useful if historical
   data ever ends up in the wrong columns):
     python garmin_to_sheets.py --reset

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

Note: yesterday (not today) is the most recent date ever pulled
automatically, since a Garmin device may not have finished syncing
today's data yet — today's data becomes "yesterday" and gets picked up
cleanly on tomorrow's run instead.

------------------------------------------------------------------------------
DATE CONVENTION (decided 2026-11-05 — keep in sync with oura_to_sheets.py)
------------------------------------------------------------------------------
Every row is dated by WAKE-UP date for sleep and overnight HRV: the values in
the row for D are the night you woke up from on the morning of D, which is
how get_sleep_data / get_hrv_data are keyed and how the Garmin app shows
them. Steps, HR, stress etc. are midnight-to-midnight for D as before.

History: an Aug 28 version shifted sleep + HRV to the bed date
(target_date + 1). That was reverted on 2026-11-05 so both tabs match the
apps; the Garmin tab's sleep/HRV columns were moved back one day the same
day. Don't re-add a shift without also changing oura_to_sheets.py and the
sheet history.

------------------------------------------------------------------------------
DATA PULLED (grouped by category)
------------------------------------------------------------------------------
Not every field will be populated for every account — it depends on your
Garmin device and which features it supports (e.g. SpO2 and body
composition need specific hardware). Missing fields come through as blank
cells rather than causing an error.

  Activity & Steps        steps, floors climbed, active/total calories,
                           moderate/vigorous intensity minutes
  Heart Rate & Recovery   resting HR, max HR, overnight HRV avg + status,
                           training readiness score
  Stress & Body Battery   average/max stress, body battery high/low
  Sleep                   total sleep time, sleep score, deep/light/REM/
                           awake stage durations
  Respiration & SpO2      average sleep respiration rate, average/lowest
                           blood oxygen
  Fitness                 VO2 max (running)
  Hydration               logged water intake (if tracked in Garmin app)
  Body Composition        weight, body fat % (only if using a Garmin scale)

Individual workouts/activities (runs, rides, etc.) are pulled separately
and written to their own "Activities" tab, since there can be more than
one per day.

Sheet headers are written as clean, human-readable labels (e.g. "Resting
HR", "HRV Overnight Avg (ms)") rather than the raw internal field names,
so they read well if copied/pasted elsewhere.

------------------------------------------------------------------------------
NOTES
------------------------------------------------------------------------------
- This uses garminconnect, an unofficial library that logs in with your
  regular Garmin Connect credentials (there is no personal-use official API).
  Garmin can change their site/login flow at any time, which may break this
  script — if so, check for an updated version of the `garminconnect` package.
- Garmin session tokens are cached locally (in ~/.garminconnect) so you won't
  have to log in fresh every run.
"""

import os
import sys
import json
import time
import datetime

from garminconnect import Garmin
import gspread
from google.oauth2.service_account import Credentials
from dotenv import load_dotenv

# Load variables from a .env file in the same directory as this script,
# if one exists. Real environment variables (e.g. set via `export` or a
# CI secret) always take precedence over values loaded from .env.
load_dotenv()


# ---------------------------------------------------------------------------
# Config (pulled from environment variables)
# ---------------------------------------------------------------------------

GARMIN_EMAIL = os.environ.get("GARMIN_EMAIL")
GARMIN_PASSWORD = os.environ.get("GARMIN_PASSWORD")

GOOGLE_SERVICE_ACCOUNT_FILE = os.environ.get("GOOGLE_SERVICE_ACCOUNT_FILE")
GOOGLE_SHEET_ID = os.environ.get("GOOGLE_SHEET_ID")
GOOGLE_SHEET_TAB = os.environ.get("GOOGLE_SHEET_TAB")  # optional
GOOGLE_ACTIVITIES_TAB = os.environ.get("GOOGLE_ACTIVITIES_TAB", "Activities")
SYNC_LOOKBACK_DAYS = int(os.environ.get("SYNC_LOOKBACK_DAYS", "14"))

# Pause between days during --backfill (seconds). Garmin's library is
# unofficial (no documented rate limit, and it has 429'd on this account
# before during initial setup), so keep this cautious for large backfills.
# Override via .env, e.g. GARMIN_BACKFILL_PAUSE_SECONDS=0.75.
BACKFILL_PAUSE_SECONDS = float(os.environ.get("GARMIN_BACKFILL_PAUSE_SECONDS", "0.75"))

REQUIRED_ENV_VARS = [
    "GARMIN_EMAIL",
    "GARMIN_PASSWORD",
    "GOOGLE_SERVICE_ACCOUNT_FILE",
    "GOOGLE_SHEET_ID",
]


def check_env():
    missing = [v for v in REQUIRED_ENV_VARS if not os.environ.get(v)]
    if missing:
        print(f"Missing required environment variables: {', '.join(missing)}")
        sys.exit(1)


# ---------------------------------------------------------------------------
# Garmin
# ---------------------------------------------------------------------------

def garmin_login() -> Garmin:
    """Log into Garmin Connect and return an authenticated client."""
    client = Garmin(GARMIN_EMAIL, GARMIN_PASSWORD)
    client.login()
    return client


def _safe_call(fn, *args, **kwargs):
    """Call a Garmin API method and swallow errors, since not every metric
    is available for every account/device — a missing metric shouldn't
    crash the whole day's pull."""
    try:
        return fn(*args, **kwargs)
    except Exception:
        return None


def get_garmin_data(client: Garmin, target_date: str) -> dict:
    """Pull a full day's metrics across all categories using an
    already-logged-in client."""

    stats = _safe_call(client.get_stats, target_date) or {}
    sleep = _safe_call(client.get_sleep_data, target_date) or {}
    hrv = _safe_call(client.get_hrv_data, target_date) or {}
    readiness = _safe_call(client.get_training_readiness, target_date) or []
    respiration = _safe_call(client.get_respiration_data, target_date) or {}
    spo2 = _safe_call(client.get_spo2_data, target_date) or {}
    max_metrics = _safe_call(client.get_max_metrics, target_date) or []
    hydration = _safe_call(client.get_hydration_data, target_date) or {}
    body_comp = _safe_call(client.get_body_composition, target_date) or {}

    sleep_summary = sleep.get("dailySleepDTO", {}) if isinstance(sleep, dict) else {}
    sleep_scores = sleep_summary.get("sleepScores", {}) if isinstance(sleep_summary, dict) else {}
    hrv_summary = hrv.get("hrvSummary", {}) if isinstance(hrv, dict) else {}
    readiness_today = readiness[0] if readiness else {}
    vo2_generic = (max_metrics[0].get("generic", {}) if max_metrics else {}) or {}

    row = {}

    # --- Activity & Steps -------------------------------------------------
    row["date"] = target_date
    row["steps"] = stats.get("totalSteps")
    row["floors_climbed"] = stats.get("floorsAscended")
    row["calories_total"] = stats.get("totalKilocalories")
    row["calories_active"] = stats.get("activeKilocalories")
    row["intensity_minutes_moderate"] = stats.get("moderateIntensityMinutes")
    row["intensity_minutes_vigorous"] = stats.get("vigorousIntensityMinutes")

    # --- Heart Rate & Recovery --------------------------------------------
    row["resting_hr"] = stats.get("restingHeartRate")
    row["max_hr"] = stats.get("maxHeartRate")
    row["hrv_overnight_avg_ms"] = hrv_summary.get("lastNightAvg")
    row["hrv_status"] = hrv_summary.get("status")
    row["training_readiness_score"] = readiness_today.get("score")

    # --- Stress & Body Battery ---------------------------------------------
    row["avg_stress"] = stats.get("averageStressLevel")
    row["max_stress"] = stats.get("maxStressLevel")
    row["body_battery_high"] = stats.get("bodyBatteryHighestValue")
    row["body_battery_low"] = stats.get("bodyBatteryLowestValue")

    # --- Sleep ---------------------------------------------------------------
    row["sleep_seconds"] = sleep_summary.get("sleepTimeSeconds")
    row["sleep_score"] = (
        sleep_scores.get("overall", {}).get("value")
        if isinstance(sleep_scores.get("overall"), dict)
        else None
    )
    row["sleep_deep_seconds"] = sleep_summary.get("deepSleepSeconds")
    row["sleep_light_seconds"] = sleep_summary.get("lightSleepSeconds")
    row["sleep_rem_seconds"] = sleep_summary.get("remSleepSeconds")
    row["sleep_awake_seconds"] = sleep_summary.get("awakeSleepSeconds")

    # --- Respiration & SpO2 -------------------------------------------------
    row["respiration_avg_sleep"] = respiration.get("avgSleepRespirationValue")
    row["spo2_avg"] = spo2.get("averageSpO2")
    row["spo2_lowest"] = spo2.get("lowestSpO2")

    # --- Fitness --------------------------------------------------------------
    row["vo2max_running"] = vo2_generic.get("vo2MaxValue")

    # --- Hydration --------------------------------------------------------------
    row["hydration_ml"] = hydration.get("valueInML")

    # --- Body Composition (only if you use a Garmin smart scale) ---------------
    row["weight_grams"] = body_comp.get("weight")
    row["body_fat_percent"] = body_comp.get("bodyFat")

    return row


def get_activities_for_date(client: Garmin, target_date: str) -> list:
    """Pull individual logged activities/workouts for a given day.
    Returns a list of flat dicts, one per activity (there can be zero,
    one, or several activities on any given day)."""
    activities = _safe_call(client.get_activities_by_date, target_date, target_date) or []

    rows = []
    for act in activities:
        rows.append({
            "date": target_date,
            "activity_name": act.get("activityName"),
            "activity_type": (act.get("activityType") or {}).get("typeKey"),
            "start_time_local": act.get("startTimeLocal"),
            "duration_seconds": act.get("duration"),
            "distance_meters": act.get("distance"),
            "calories": act.get("calories"),
            "avg_hr": act.get("averageHR"),
            "max_hr": act.get("maxHR"),
            "elevation_gain_meters": act.get("elevationGain"),
            "avg_speed_mps": act.get("averageSpeed"),
        })
    return rows


# ---------------------------------------------------------------------------
# Header labels
# ---------------------------------------------------------------------------
# Internal dict keys stay as snake_case (used throughout the code above).
# These are just the human-readable labels written to row 1 of each sheet,
# so headers read cleanly if copied/pasted elsewhere. Any key not listed
# here falls back to a title-cased version of the raw key automatically.

SUMMARY_HEADER_LABELS = {
    "date": "Date",
    "steps": "Steps",
    "floors_climbed": "Floors Climbed",
    "calories_total": "Calories Total",
    "calories_active": "Calories Active",
    "intensity_minutes_moderate": "Intensity Minutes (Moderate)",
    "intensity_minutes_vigorous": "Intensity Minutes (Vigorous)",
    "resting_hr": "Resting HR",
    "max_hr": "Max HR",
    "hrv_overnight_avg_ms": "HRV Overnight Avg (ms)",
    "hrv_status": "HRV Status",
    "training_readiness_score": "Training Readiness Score",
    "avg_stress": "Avg Stress",
    "max_stress": "Max Stress",
    "body_battery_high": "Body Battery High",
    "body_battery_low": "Body Battery Low",
    "sleep_seconds": "Sleep Duration (sec)",
    "sleep_score": "Sleep Score",
    "sleep_deep_seconds": "Sleep - Deep (sec)",
    "sleep_light_seconds": "Sleep - Light (sec)",
    "sleep_rem_seconds": "Sleep - REM (sec)",
    "sleep_awake_seconds": "Sleep - Awake (sec)",
    "respiration_avg_sleep": "Respiration Avg (Sleep)",
    "spo2_avg": "SpO2 Avg (%)",
    "spo2_lowest": "SpO2 Lowest (%)",
    "vo2max_running": "VO2 Max (Running)",
    "hydration_ml": "Hydration (mL)",
    "weight_grams": "Weight (g)",
    "body_fat_percent": "Body Fat (%)",
}

ACTIVITY_HEADER_LABELS = {
    "date": "Date",
    "activity_name": "Activity Name",
    "activity_type": "Activity Type",
    "start_time_local": "Start Time (Local)",
    "duration_seconds": "Duration (sec)",
    "distance_meters": "Distance (m)",
    "calories": "Calories",
    "avg_hr": "Avg HR",
    "max_hr": "Max HR",
    "elevation_gain_meters": "Elevation Gain (m)",
    "avg_speed_mps": "Avg Speed (m/s)",
}


def _display_headers(keys, label_map):
    """Turn raw dict keys into clean header labels, falling back to a
    title-cased version of the key if it isn't in the label map."""
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
    """Daily-summary worksheet (created earlier by default as the sheet's
    first tab, or GOOGLE_SHEET_TAB if set)."""
    sh = _get_spreadsheet()
    return sh.worksheet(GOOGLE_SHEET_TAB) if GOOGLE_SHEET_TAB else sh.sheet1


def get_activities_worksheet():
    """Activities worksheet — created automatically if it doesn't exist
    yet."""
    sh = _get_spreadsheet()
    try:
        return sh.worksheet(GOOGLE_ACTIVITIES_TAB)
    except gspread.exceptions.WorksheetNotFound:
        return sh.add_worksheet(title=GOOGLE_ACTIVITIES_TAB, rows=1000, cols=20)


def _sheet_needs_header(ws) -> bool:
    """Reliably check whether a tab's header row is actually empty.

    ws.get_all_values() pads out to the sheet's declared grid size (which
    survives ws.clear() calls), so it can return a big list of entirely
    blank rows that still counts as 'truthy' in Python. row_values(1)
    trims trailing blanks for that specific row, so it correctly returns
    an empty list when row 1 truly has no content."""
    return not ws.row_values(1)


def get_existing_dates(ws) -> set:
    """Return the set of date strings (YYYY-MM-DD) already present in
    column A of the summary tab. Used to skip dates that have already
    been pulled, so re-running the script (manually or on a schedule)
    never creates duplicate rows."""
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
    """Append many summary rows at once (used by backfill) — one API call
    instead of one per day, which is both faster and friendlier to Sheets
    rate limits."""
    if not rows:
        return
    ws = get_worksheet()
    if _sheet_needs_header(ws):
        ws.append_row(_display_headers(rows[0].keys(), SUMMARY_HEADER_LABELS),
                      table_range="A1")
    ws.append_rows([list(r.values()) for r in rows], table_range="A1")


def append_activities_to_sheet(rows: list):
    """Append activity rows to the Activities tab. Safe to call with an
    empty list (does nothing)."""
    if not rows:
        return
    ws = get_activities_worksheet()
    if _sheet_needs_header(ws):
        ws.append_row(_display_headers(rows[0].keys(), ACTIVITY_HEADER_LABELS),
                      table_range="A1")
    ws.append_rows([list(r.values()) for r in rows], table_range="A1")


def clear_all_data():
    """Wipe both tabs completely so future writes start from a clean,
    predictable A1 origin. Use this once to fix drifted historical data."""
    ws = get_worksheet()
    ws.clear()
    print(f"Cleared '{ws.title}' tab.")

    aws = get_activities_worksheet()
    aws.clear()
    print(f"Cleared '{aws.title}' tab.")


# ---------------------------------------------------------------------------
# Backfill
# ---------------------------------------------------------------------------

def daterange(start: datetime.date, end: datetime.date):
    for n in range((end - start).days + 1):
        yield start + datetime.timedelta(days=n)


def run_backfill(start_str: str, end_str: str, client: Garmin, existing_dates: set,
                  pause_seconds: float = BACKFILL_PAUSE_SECONDS, batch_size: int = 30):
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
    activity_batch = []
    total_days = len(days_to_pull)
    done = 0

    for day in days_to_pull:
        day_str = day.isoformat()
        try:
            summary_batch.append(get_garmin_data(client, day_str))
            activity_batch.extend(get_activities_for_date(client, day_str))
        except Exception as e:
            print(f"  WARNING: failed to pull {day_str}: {e}")

        done += 1
        print(f"  [{done}/{total_days}] {day_str} pulled")

        # Flush periodically so a crash partway through doesn't lose
        # everything, and to avoid one giant Sheets API call.
        if len(summary_batch) >= batch_size:
            append_rows_to_sheet(summary_batch)
            append_activities_to_sheet(activity_batch)
            print(f"  -> wrote {len(summary_batch)} summary rows, "
                  f"{len(activity_batch)} activity rows")
            summary_batch = []
            activity_batch = []

        time.sleep(pause_seconds)

    if summary_batch:
        append_rows_to_sheet(summary_batch)
    if activity_batch:
        append_activities_to_sheet(activity_batch)
    if summary_batch or activity_batch:
        print(f"  -> wrote {len(summary_batch)} summary rows, "
              f"{len(activity_batch)} activity rows")

    print("Backfill complete.")


def _cells(values) -> list:
    """Google Sheets treats None as "leave this cell unchanged", so blanks
    must be sent as "" when overwriting a row."""
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


def refresh_rows(client: Garmin, start: datetime.date, end: datetime.date,
                 pause_seconds: float = 1.0):
    """Re-pull every date already in the summary tab between start and end,
    and overwrite the row if Garmin's current data differs.

    Why: sync mode used to write each date once and skip it forever. If
    the watch hadn't finished syncing when the PC ran, that day was saved
    with missing steps / HRV / sleep and never fixed.

    Safety: a row is only replaced if the new pull has at least as many
    filled-in fields, so this never blanks out good data (e.g. if Garmin
    is having a bad day and returns nothing)."""
    ws = get_worksheet()
    values = ws.get_all_values()
    if not values:
        return
    header = values[0]
    watch = ["Steps", "HRV Overnight Avg (ms)", "Sleep Score", "Resting HR"]
    key_cols = {k: header.index(k) for k in watch if k in header}
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
        try:
            new = list(get_garmin_data(client, day.isoformat()).values())
        except Exception as e:
            print(f"  WARNING: {day} re-check failed: {e}")
            time.sleep(pause_seconds)
            continue
        new += [""] * (len(header) - len(new))
        new = new[:len(header)]
        if [_norm(v) for v in old] == [_norm(v) for v in new]:
            time.sleep(pause_seconds)
            continue
        if _filled(new) < _filled(old):
            print(f"  {day} kept as-is (Garmin returned less data than the sheet)")
            time.sleep(pause_seconds)
            continue
        ws.update(range_name=f"A{idx}", values=[_cells(new)])
        fixed += 1
        changes = [f"{k} {old[c] or '-'}->{_norm(new[c]) or '-'}"
                   for k, c in key_cols.items() if _norm(old[c]) != _norm(new[c])]
        print(f"  {day} updated: {', '.join(changes) or 'other fields'}")
        time.sleep(pause_seconds)
    print(f"Refresh complete: {checked} day(s) checked, {fixed} updated.")


def run_sync(client: Garmin, existing_dates: set):
    """Incremental catch-up mode for scheduled/automated runs. Looks back
    SYNC_LOOKBACK_DAYS days through yesterday, and pulls only the dates in
    that window missing from the sheet — so a normal daily run just grabs
    yesterday, but if a few days were missed (e.g. computer was off), it
    automatically catches up on all of them in one run. Safe to run
    multiple times without creating duplicates, since it only acts on
    dates not already present."""
    yesterday = datetime.date.today() - datetime.timedelta(days=1)
    window_start = yesterday - datetime.timedelta(days=SYNC_LOOKBACK_DAYS - 1)
    candidate_days = list(daterange(window_start, yesterday))
    missing_days = [d for d in candidate_days if d.isoformat() not in existing_dates]

    if not missing_days:
        print(f"No missing dates in the last {SYNC_LOOKBACK_DAYS} days.")
        refresh_rows(client, window_start, yesterday)
        return

    print(f"Found {len(missing_days)} missing date(s) in the last "
          f"{SYNC_LOOKBACK_DAYS} days: {missing_days[0]} through {missing_days[-1]}")

    summary_batch = []
    activity_batch = []
    for day in missing_days:
        day_str = day.isoformat()
        try:
            summary_batch.append(get_garmin_data(client, day_str))
            activity_batch.extend(get_activities_for_date(client, day_str))
            print(f"  {day_str} pulled")
        except Exception as e:
            print(f"  WARNING: failed to pull {day_str}: {e}")
        time.sleep(1.0)

    if summary_batch:
        append_rows_to_sheet(summary_batch)
    if activity_batch:
        append_activities_to_sheet(activity_batch)

    print(f"Sync complete — wrote {len(summary_batch)} summary row(s), "
          f"{len(activity_batch)} activity row(s).")

    # Also re-check days already in the window that were saved before the
    # watch finished syncing.
    refresh_rows(client, window_start, yesterday)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    check_env()

    args = sys.argv[1:]

    if args and args[0] == "--reset":
        confirm = input(
            "This will permanently delete ALL rows on both the summary "
            "and Activities tabs. Type 'yes' to confirm: "
        )
        if confirm.strip().lower() == "yes":
            clear_all_data()
        else:
            print("Cancelled.")
        return

    print("Logging into Garmin Connect...")
    client = garmin_login()

    if args and args[0] == "--repair":
        if len(args) != 3:
            print("Usage: python garmin_to_sheets.py --repair START_DATE END_DATE")
            sys.exit(1)
        refresh_rows(client, datetime.date.fromisoformat(args[1]),
                     datetime.date.fromisoformat(args[2]), pause_seconds=1.5)
        return

    ws = get_worksheet()
    existing_dates = get_existing_dates(ws)

    if args and args[0] == "--backfill":
        if len(args) != 3:
            print("Usage: python garmin_to_sheets.py --backfill START_DATE END_DATE")
            print("Example: python garmin_to_sheets.py --backfill 2025-01-01 2026-07-31")
            sys.exit(1)
        run_backfill(args[1], args[2], client=client, existing_dates=existing_dates)
        return

    if not args:
        # No arguments: sync mode, meant for scheduled/automated runs.
        run_sync(client, existing_dates)
        return

    # Explicit single date (manual testing / spot-filling one day)
    target_date = args[0]

    if target_date in existing_dates:
        print(f"{target_date} is already in the sheet — skipping to avoid "
              f"a duplicate row. Use --reset first if you want to overwrite it.")
        return

    print(f"Pulling Garmin data for {target_date}...")
    data = get_garmin_data(client, target_date)
    print(json.dumps(data, indent=2))

    activities = get_activities_for_date(client, target_date)
    if activities:
        print(f"Found {len(activities)} activity/activities for {target_date}:")
        print(json.dumps(activities, indent=2))

    print("Appending to Google Sheet...")
    append_to_sheet(data)
    append_activities_to_sheet(activities)
    print("Done.")


if __name__ == "__main__":
    main()
