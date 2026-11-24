"""
garmin_golf_to_sheets.py

Pulls Garmin Golf data and writes it to a Google Sheet: one row per round
to "Golf Rounds", one row per hole to "Golf Holes", and one row per
individual GPS-tracked shot to "Golf Shots".

HOW HOLE-BY-HOLE DATA IS OBTAINED:
The scorecard SUMMARY response for each round already includes a "holes"
array (hole number + strokes for completed holes) and a "holePars" string
(par for all 18 holes, one digit each). Built directly from that, zero
extra API calls.

HOW SHOT-LEVEL DATA IS OBTAINED:
Confirmed by capturing the real request Garmin Connect's website makes
while viewing a shot map:
  GET /golf-api/gcs-golfcommunity/api/v2/shot/scorecard/{id}/hole
        ?image-size=IMG_730X730

This is genuinely rich data — for each recorded shot: start/end GPS
location (Garmin "semicircle" int encoding, converted to degrees below),
distance in yards (converted from Garmin's native meters), shot type
(TEE/APPROACH/CHIP/PUTT/RECOVERY/LAYUP/UNKNOWN), lie (Fairway/Rough/
Green/Bunker/TeeBox), club ID, and whether it was auto-detected
(AutoShot) or manually logged.

KEY EFFICIENCY FINDING: calling this endpoint with any one scorecard ID
from a course returns shots from MANY rounds played at that same course
(each shot carries its own "scorecardId" field) — not just the round
whose ID was passed in. So this script calls it ONCE PER DISTINCT COURSE
(using any round played there as the seed ID) rather than once per round,
then attributes every returned shot back to its correct round via that
shot's own scorecardId. Shots are deduplicated by their own "id" in case
the same course is inadvertently queried more than once.

Club names: Garmin's /club/player endpoint returns your bag (club ID ->
name mapping). This script fetches it once and joins club names onto
each shot row when available; shots with clubId=0 mean no club was
assigned (common for AutoShot-detected shots you didn't manually tag).

------------------------------------------------------------------------------
SETUP
------------------------------------------------------------------------------

1. Install dependencies (same as garmin_to_sheets.py):
     pip install garminconnect gspread google-auth python-dotenv --break-system-packages

2. Uses the SAME Garmin login as garmin_to_sheets.py — no new credentials
   needed. Make sure these are already in your .env:
     GARMIN_EMAIL
     GARMIN_PASSWORD
     GOOGLE_SERVICE_ACCOUNT_FILE
     GOOGLE_SHEET_ID

3. Optional tab name overrides (defaults shown):
     GOOGLE_GOLF_SUMMARY_TAB=Golf Rounds
     GOOGLE_GOLF_HOLES_TAB=Golf Holes
     GOOGLE_GOLF_SHOTS_TAB=Golf Shots

4. Run it:
     python garmin_golf_to_sheets.py

   Writes:
     - One row per round to "Golf Rounds"
     - One row per hole per round to "Golf Holes"
     - One row per individual shot to "Golf Shots" (real GPS/distance/
       club/lie data, one API call per distinct course rather than per
       round)

   To wipe all three tabs and start fresh:
     python garmin_golf_to_sheets.py --reset

   To print raw data without writing to the sheet (useful for checking
   what's available before trusting the output):
     python garmin_golf_to_sheets.py --raw

------------------------------------------------------------------------------
FIELD NOTES
------------------------------------------------------------------------------
- Round-level fields are pulled generically from the summary — whatever
  Garmin returns becomes a column.
- Hole-level rows: scorecard id, course name, date, hole number, par,
  strokes, plus (for rounds processed after this feature was added)
  putts, penalties, handicap score, pin GPS location, and
  fairway_shot_outcome — Garmin's own HIT/LEFT/RIGHT classification of
  whether that hole's tee shot missed the fairway and which direction.
  Blank on par-3s (no fairway shot to classify) and on rounds already in
  the sheet before this field was added (not retroactively backfilled —
  run --reset then a full pull to get it for your whole history).
- Shot-level rows: scorecard id, hole number, shot order, shot type, club
  name (if assigned), distance_yards, start/end lie, start/end lat/
  lon (converted to real degrees), shot source (DEVICE_AUTO vs CLIENT —
  i.e. auto-detected vs manually logged), whether it's excluded from
  stats, hit_green and hit_fairway (True/False, based on end lie ==
  "Green" / "Fairway" — computed for every shot, ground truth for
  "found the green" / "found the fairway" independent of the miss-line
  fields below; hit_fairway is naturally most relevant for tee shots).
  For APPROACH and TEE shots specifically (on rounds processed after
  this feature was added, with a known pin position), four extra
  fields describing where the shot finished relative to the direct
  line from where it was hit to the pin:
    miss_side                LEFT / RIGHT / ON LINE
    miss_distance_yards      how far off that line, sideways, in yards
    miss_depth               SHORT / LONG / PIN HIGH (distance-to-pin
                              comparison — PIN HIGH means it finished
                              level with the pin, just off to one side)
    miss_depth_distance_yards   signed — POSITIVE means short of the
                              pin, NEGATIVE means past/long of the pin
  Not computed for putts (too short a distance for this to be
  meaningful). Note: a shot can show a real miss_side/miss_depth offset
  even when hit_green/hit_fairway is True — very few shots finish dead
  center on the target line, so some offset on a green/fairway shot is
  normal, not a contradiction; use hit_green/hit_fairway as the "did it
  find the target surface" answer and the miss fields as "how far from
  dead-center on the pin line."
  Accuracy note: for tee shots on dogleg holes, the pin isn't the real
  intended target — a well-placed tee shot toward the actual fairway
  landing area can still show a large miss_distance_yards simply because
  the hole bends. On straight holes, or for approach shots generally,
  the result is reliable. Garmin's own fairway_shot_outcome field (in
  Golf Holes) is the more trustworthy source specifically for tee-shot
  fairway accuracy, since it uses Garmin's real fairway geometry.
- PUTT DISTANCE (distance_yards on PUTT rows): NOT taken from Garmin's
  raw per-shot value anymore — that number isn't trustworthy. Verified
  against holes independently confirmed 1-putt (Golf Holes -> Putts ==
  1): the preceding approach shot is often already ~1-2 yards from the
  real pin on those holes, yet Garmin's raw putt distance is routinely
  10+ yards on the same hole, which isn't physically possible for a putt
  that goes straight in from that close. Working theory: without a
  precise GPS fix on the true final ball position, Garmin substitutes an
  assumed pin location (likely the green's center rather than the real
  pin) when computing that shot's distance.
  Fix: where this hole's real, deliberately-set pin position is known
  (pin_positions — same source as miss_distance_yards above), putt
  distance is recomputed as straight-line start-of-putt -> real pin.
  distance_source marks which path each row took:
    PIN_POSITION     recomputed against the real pin — trust this
    GARMIN_BASELINE  no pin position on file for that hole yet, so
                      Garmin's raw (likely inflated) value was kept as a
                      fallback so every row still has a number
  distance_yards_garmin_raw preserves Garmin's original value on every
  PUTT row regardless of which path was used, for comparison/debugging.
  Note pin positions are only on file for rounds processed after that
  feature was added (see the fairway_shot_outcome note above) — run
  --reset then a full pull to backfill PIN_POSITION for your whole
  history instead of relying on GARMIN_BASELINE for older rounds.
- INCOMPLETE PUTT TRACKING (Golf Holes: putts_shots_logged /
  putts_tracking_status): Garmin appears to log at most one PUTT shot
  row per hole no matter how many putts were actually taken — so a
  3-putt hole still only produces one shot row (representing, at best,
  just the first putt). Golf Holes' own putts field (Garmin's real
  per-hole putt count, from scorecard detail) is compared against how
  many PUTT rows actually exist for that hole in Golf Shots
  (putts_shots_logged) to flag the gap:
    FULL       shots logged >= actual putts (the common case: a 1-putt
               hole whose single putt got captured)
    PARTIAL    at least one putt logged, but fewer than the actual putt
               count — a multi-putt hole where only the first putt has a
               shot row; the remaining putt(s) on that hole aren't
               represented anywhere in Golf Shots
    UNTRACKED  putts were taken but zero PUTT rows exist for that hole
               at all (e.g. a short putt off a chip that never got
               logged as its own shot)
  Blank when putts (actual) isn't known for that hole (older rounds
  processed before that field was added).
- Latitude/longitude in the raw API use Garmin's "semicircle" integer
  encoding (value * 180 / 2^31 = degrees) — this script converts to
  ordinary decimal degrees automatically.
- All distances (shot distance, miss distances) are converted from
  Garmin's native meters to yards before writing to the sheet, since
  yards is the standard unit for golf in the US.
"""

import os
import sys
import json
import time
import math

from garminconnect import Garmin
import gspread
from google.oauth2.service_account import Credentials
from dotenv import load_dotenv

load_dotenv()

# All distances from Garmin's API come in meters. This script converts
# everything to yards (the standard unit for golf in the US) before
# writing to the sheet.
METERS_TO_YARDS = 1.0936133


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

GARMIN_EMAIL = os.environ.get("GARMIN_EMAIL")
GARMIN_PASSWORD = os.environ.get("GARMIN_PASSWORD")

GOOGLE_SERVICE_ACCOUNT_FILE = os.environ.get("GOOGLE_SERVICE_ACCOUNT_FILE")
GOOGLE_SHEET_ID = os.environ.get("GOOGLE_SHEET_ID")
GOOGLE_GOLF_SUMMARY_TAB = os.environ.get("GOOGLE_GOLF_SUMMARY_TAB", "Golf Rounds")
GOOGLE_GOLF_HOLES_TAB = os.environ.get("GOOGLE_GOLF_HOLES_TAB", "Golf Holes")
GOOGLE_GOLF_SHOTS_TAB = os.environ.get("GOOGLE_GOLF_SHOTS_TAB", "Golf Shots")

REQUIRED_ENV_VARS = [
    "GARMIN_EMAIL",
    "GARMIN_PASSWORD",
    "GOOGLE_SERVICE_ACCOUNT_FILE",
    "GOOGLE_SHEET_ID",
]

GOLF_SCORECARD_SUMMARY_PATH = "/gcs-golfcommunity/api/v2/scorecard/summary"
# Confirmed by capturing the real browser request (see module docstring).
GOLF_SHOT_HISTORY_PATH_TMPL = "/gcs-golfcommunity/api/v2/shot/scorecard/{}/hole"
GOLF_CLUB_PLAYER_PATH = "/gcs-golfcommunity/api/v2/club/player"
# Confirmed working parameters (captured via browser dev tools): the
# param is 'scorecard-ids' (kebab-case, plural), plus these required
# flags. Real shape: {"scorecardDetails": [{"scorecard": {..., "holes":
# [...]}}]}. Each hole may include 'fairwayShotOutcome' (HIT/LEFT/RIGHT)
# — Garmin's own fairway miss-direction classification for that hole's
# tee shot (blank on par-3s, which have no fairway shot).
GOLF_SCORECARD_DETAIL_PATH = "/gcs-golfcommunity/api/v2/scorecard/detail"

# Static reference table: clubTypeId -> display name. Captured from Garmin
# Connect's own club-type reference request. This is a fixed catalog of
# club types (not user-specific data), so it's safe to hardcode rather
# than fetch on every run. /club/player only returns each club's
# clubTypeId (e.g. 16) — the actual name ("7 Iron") comes from this table.
CLUB_TYPE_NAMES = {
    1: "Driver", 2: "3 Wood", 3: "5 Wood", 4: "1 Hybrid", 5: "2 Hybrid",
    6: "3 Hybrid", 7: "4 Hybrid", 8: "5 Hybrid", 9: "6 Hybrid",
    10: "1 Iron", 11: "2 Iron", 12: "3 Iron", 13: "4 Iron", 14: "5 Iron",
    15: "6 Iron", 16: "7 Iron", 17: "8 Iron", 18: "9 Iron",
    19: "Pitching Wedge", 20: "Gap Wedge", 21: "Sand Wedge", 22: "Lob Wedge",
    23: "Putter", 24: "2 Wood", 25: "4 Wood", 26: "7 Wood", 27: "9 Wood",
    28: "11 Wood", 29: "13 Wood", 30: "15 Wood", 31: "17 Wood",
    32: "19 Wood", 33: "21 Wood", 34: "23 Wood", 35: "25 Wood",
    36: "7 Hybrid", 37: "8 Hybrid", 38: "9 Hybrid", 39: "PW Hybrid",
    40: "GW Hybrid", 41: "SW Hybrid", 42: "LW Hybrid",
}


def check_env():
    missing = [v for v in REQUIRED_ENV_VARS if not os.environ.get(v)]
    if missing:
        print(f"Missing required environment variables: {', '.join(missing)}")
        sys.exit(1)


# ---------------------------------------------------------------------------
# Garmin
# ---------------------------------------------------------------------------

def garmin_login() -> Garmin:
    client = Garmin(GARMIN_EMAIL, GARMIN_PASSWORD)
    client.login()
    return client


def get_scorecard_summaries(client: Garmin) -> list:
    """Fetch every round Garmin has on file."""
    try:
        response = client.connectapi(
            GOLF_SCORECARD_SUMMARY_PATH, params={"per-page": 10000}
        )
    except Exception as e:
        print(f"ERROR: failed to fetch scorecard summaries: {e}")
        return []

    if isinstance(response, list):
        return response
    if isinstance(response, dict):
        for key in ("scorecardSummaries", "scorecards", "data", "content"):
            if key in response and isinstance(response[key], list):
                return response[key]
    print("WARNING: unrecognized response shape from scorecard summary endpoint.")
    print(json.dumps(response, indent=2)[:2000])
    return []


def parse_hole_pars(hole_pars: str) -> list:
    """holePars is a string like '453444354453434444' — one digit of par
    per hole, 18 holes."""
    if not hole_pars:
        return []
    try:
        return [int(ch) for ch in hole_pars]
    except ValueError:
        return []


def get_holes_for_round(summary: dict, fairway_outcomes: dict = None,
                         putts_logged: dict = None) -> list:
    """Build hole-by-hole rows directly from a round summary — no separate
    API call needed for strokes/par. If fairway_outcomes is given (a dict
    of {hole_number: outcome_dict} from get_fairway_outcomes_for_round),
    those fields (putts, fairway hit/miss direction, penalties, pin
    position) are merged in too.

    putts_logged, if given, is {(scorecard_id, hole_number): count} —
    how many PUTT-type rows actually made it into Golf Shots for that
    hole (see main(), built from deduped_shots). Compared against
    Garmin's own 'putts' count (from fairway_outcomes) to flag holes
    where shot-level putt data is incomplete:
      FULL      - shots logged >= actual putts (typically a 1-putt hole
                  with its single putt captured)
      PARTIAL   - at least one putt logged, but fewer than the actual
                  putt count (multi-putt hole where only the first putt
                  got a shot row — the rest were never captured)
      UNTRACKED - actual putts taken but zero PUTT rows logged at all
                  (e.g. a short putt off a chip that never got marked)
    Left blank when we don't have an actual putts count for this hole
    (older rounds processed before that field was added — see module
    docstring)."""
    pars = parse_hole_pars(summary.get("holePars", ""))
    round_date = (summary.get("startTime") or "")[:10]
    fairway_outcomes = fairway_outcomes or {}
    putts_logged = putts_logged or {}
    scorecard_id = summary.get("id")

    rows = []
    for hole in summary.get("holes", []):
        number = hole.get("number")
        par = pars[number - 1] if pars and number and number <= len(pars) else None
        row = {
            "scorecard_id": scorecard_id,
            "course_name": summary.get("courseName"),
            "date": round_date,
            "hole_number": number,
            "par": par,
            "strokes": hole.get("strokes"),
        }
        row.update(fairway_outcomes.get(number, {}))

        shots_logged = putts_logged.get((scorecard_id, number), 0)
        row["putts_shots_logged"] = shots_logged
        actual_putts = row.get("putts")
        if actual_putts is None:
            row["putts_tracking_status"] = ""
        elif shots_logged == 0:
            row["putts_tracking_status"] = "UNTRACKED"
        elif shots_logged < actual_putts:
            row["putts_tracking_status"] = "PARTIAL"
        else:
            row["putts_tracking_status"] = "FULL"

        rows.append(row)
    return rows


def get_scorecard_detail(client: Garmin, scorecard_id) -> dict:
    """Fetch full scorecard detail for one round — used here specifically
    for fairwayShotOutcome (miss-direction) and other hole-level fields
    not present in the summary."""
    try:
        response = client.connectapi(
            GOLF_SCORECARD_DETAIL_PATH,
            params={
                "scorecard-ids": scorecard_id,
                "include-next-previous-ids": "true",
                "user-locale": "en",
                "include-longest-shot-distance": "true",
            },
        )
        return response if response else {}
    except Exception as e:
        print(f"  WARNING: detail fetch failed for scorecard {scorecard_id}: {e}")
        return {}


def get_fairway_outcomes_for_round(detail: dict) -> dict:
    """Extract {hole_number: {putts, penalties, fairway_shot_outcome,
    handicap_score, pin_lat, pin_lon}} from a scorecard detail response.
    fairway_shot_outcome is Garmin's own HIT/LEFT/RIGHT classification
    for that hole's tee shot — blank on par-3s, which have no fairway
    shot to classify."""
    scorecard_details = detail.get("scorecardDetails") if isinstance(detail, dict) else None
    if not scorecard_details:
        return {}

    record = scorecard_details[0].get("scorecard", {})
    holes = record.get("holes", []) if isinstance(record, dict) else []

    outcomes = {}
    for hole in holes:
        number = hole.get("number")
        if number is None:
            continue
        outcomes[number] = {
            "putts": hole.get("putts"),
            "penalties": hole.get("penalties"),
            "fairway_shot_outcome": hole.get("fairwayShotOutcome"),
            "handicap_score": hole.get("handicapScore"),
            "pin_lat": semicircle_to_degrees(hole.get("pinPositionLat")),
            "pin_lon": semicircle_to_degrees(hole.get("pinPositionLon")),
        }
    return outcomes


def semicircle_to_degrees(value):
    """Convert Garmin's 'semicircle' integer lat/lon encoding to ordinary
    decimal degrees: degrees = value * (180 / 2^31)."""
    if value is None:
        return None
    return value * (180 / 2**31)


def distance_yards_between(lat1, lon1, lat2, lon2):
    """Straight-line (haversine) distance between two lat/lon points, in
    yards. Used to recompute putt distance as start-of-putt -> real pin,
    since Garmin's own per-shot distance for PUTT shots isn't trustworthy
    (see PUTT DISTANCE note in module docstring and in the putt-recompute
    block in main())."""
    if None in (lat1, lon1, lat2, lon2):
        return None
    R = 6371000.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    meters = R * c
    return round(meters * METERS_TO_YARDS, 1)


def compute_shot_miss(start_lat, start_lon, end_lat, end_lon,
                       pin_lat, pin_lon):
    """Determine how a shot finished relative to the direct line from
    where it was hit to the pin — both sideways (left/right) and along
    the line (short/long/pin-high) — returning a dict:
      {lateral_side, lateral_distance_yards, depth_side, depth_distance_yards}
    or None if inputs are missing / degenerate.

    Method: build a 'target' vector (start -> pin) and an 'actual' vector
    (start -> where the shot ended), converting lat/lon degrees to local
    east/north meters first (flat-earth approximation — accurate enough
    over golf-hole distances).
      - Lateral: the 2D cross product of these vectors gives the side
        (positive = left of the target line facing the pin, negative =
        right); dividing by the target vector's length converts that
        into a perpendicular distance in meters.
      - Depth: the dot product of 'actual' onto the target direction
        gives how far along the target line the shot traveled; compared
        to the full distance to the pin, this tells you SHORT (didn't
        reach pin distance), LONG (went past it), or PIN HIGH (landed
        at the same distance, just off to the side).

    Applied to APPROACH and TEE shots. Accuracy caveat: the pin is the
    only target geometry available from the API — for approach shots
    that's genuinely what you're aiming at, so results are meaningful.
    For tee shots on a straight hole it's also meaningful (the fairway
    runs roughly toward the pin). On a DOGLEG hole, though, the real
    intended target is a fairway landing area off to one side, not the
    pin — so a tee shot correctly aimed at that landing area can show up
    here as a large 'miss' simply because the hole bends. Treat tee-shot
    results as approximate on doglegs; Garmin's own fairway_shot_outcome
    field (HIT/LEFT/RIGHT, in the Golf Holes tab) is more reliable for
    tee shots specifically, since it uses Garmin's actual fairway
    geometry rather than a straight line to the pin. Not applied to
    putts (too short a distance for 'miss side' to be meaningful).

    Note: this describes the LINE the shot finished on, not whether it
    was a good shot — a shot that lands on the green will often still
    show some lateral/depth offset here (very few approach shots finish
    dead center), which is normal, not an error. Check the separate
    hit_green flag (based on end lie) to see whether the shot actually
    found the green regardless of this offset."""
    if None in (start_lat, start_lon, end_lat, end_lon, pin_lat, pin_lon):
        return None

    ref_lat_rad = math.radians(start_lat)
    meters_per_deg_lat = 111320.0
    meters_per_deg_lon = 111320.0 * math.cos(ref_lat_rad)

    def to_local_meters(lat, lon):
        return (
            (lon - start_lon) * meters_per_deg_lon,  # east (+) / west (-)
            (lat - start_lat) * meters_per_deg_lat,  # north (+) / south (-)
        )

    target_e, target_n = to_local_meters(pin_lat, pin_lon)
    actual_e, actual_n = to_local_meters(end_lat, end_lon)

    target_len = math.hypot(target_e, target_n)
    if target_len < 1.0:  # started essentially on top of the pin
        return None

    # Lateral (left/right of the target line)
    cross = target_e * actual_n - target_n * actual_e
    lateral_m = abs(cross) / target_len
    if lateral_m < 1.0:
        lateral_side = "ON LINE"
    else:
        lateral_side = "LEFT" if cross > 0 else "RIGHT"

    # Depth (short/long/pin-high, i.e. distance traveled along the
    # target line compared to the full distance to the pin). Signed so
    # the number alone tells you direction: positive = short of the
    # pin, negative = past/long of the pin.
    along = (actual_e * target_e + actual_n * target_n) / target_len
    depth_diff = along - target_len  # negative = short, positive = long
    signed_depth_m = -depth_diff  # flip: positive=short, negative=long
    if abs(depth_diff) < 1.0:
        depth_side = "PIN HIGH"
    else:
        depth_side = "SHORT" if depth_diff < 0 else "LONG"

    return {
        "lateral_side": lateral_side,
        "lateral_distance_yards": round(lateral_m * METERS_TO_YARDS, 1),
        "depth_side": depth_side,
        "depth_distance_yards": round(signed_depth_m * METERS_TO_YARDS, 1),
    }


def get_club_names(client: Garmin) -> dict:
    """Fetch the player's club bag and return {clubId: clubName}.

    Confirmed: /club/player returns each club's clubTypeId (a numeric
    code, e.g. 16) but NOT a display name. The name comes from the
    separate CLUB_TYPE_NAMES reference table above. So this does a
    two-step lookup: club's own id -> clubTypeId (from /club/player) ->
    display name (from CLUB_TYPE_NAMES)."""
    try:
        response = client.connectapi(
            GOLF_CLUB_PLAYER_PATH,
            params={"per-page": 1000, "include-stats": "false", "maxClubTypeId": 42},
        )
    except Exception as e:
        print(f"  WARNING: failed to fetch club bag: {e}")
        return {}

    clubs = response if isinstance(response, list) else response.get("clubs", []) if isinstance(response, dict) else []
    mapping = {}
    for club in clubs:
        club_id = club.get("id")
        club_type_id = club.get("clubTypeId")
        name = CLUB_TYPE_NAMES.get(club_type_id, f"Unknown type {club_type_id}")
        if club_id is not None:
            mapping[club_id] = name
    return mapping


def get_shot_history_for_course(client: Garmin, seed_scorecard_id) -> list:
    """Fetch shot history using one round's ID as the seed — returns shots
    from potentially many rounds played at that course (each shot carries
    its own scorecardId), not just the seed round."""
    path = GOLF_SHOT_HISTORY_PATH_TMPL.format(seed_scorecard_id)
    try:
        response = client.connectapi(path, params={"image-size": "IMG_730X730"})
    except Exception as e:
        print(f"  WARNING: shot history fetch failed for seed scorecard "
              f"{seed_scorecard_id}: {e}")
        return []

    if not isinstance(response, dict):
        return []
    return response.get("holeShots", [])


def flatten_shot_history(hole_shots: list, club_names: dict, scorecard_to_round: dict) -> list:
    """Turn the raw holeShots structure into flat rows, one per shot."""
    rows = []
    for hole_entry in hole_shots:
        for shot in hole_entry.get("shots", []):
            scorecard_id = shot.get("scorecardId")
            round_info = scorecard_to_round.get(scorecard_id, {})
            start = shot.get("startLoc", {}) or {}
            end = shot.get("endLoc", {}) or {}
            club_id = shot.get("clubId")

            rows.append({
                "shot_id": shot.get("id"),
                "scorecard_id": scorecard_id,
                "course_name": round_info.get("courseName"),
                "date": round_info.get("date"),
                "hole_number": shot.get("holeNumber"),
                "shot_order": shot.get("shotOrder"),
                "shot_type": shot.get("shotType"),
                "club_id": club_id,
                "club_name": club_names.get(club_id, "" if club_id else "(not assigned)"),
                "distance_yards": (
                    round(shot.get("meters") * METERS_TO_YARDS, 1)
                    if shot.get("meters") is not None else None
                ),
                "start_lie": start.get("lie"),
                "start_lat": semicircle_to_degrees(start.get("lat")),
                "start_lon": semicircle_to_degrees(start.get("lon")),
                "end_lie": end.get("lie"),
                "end_lat": semicircle_to_degrees(end.get("lat")),
                "end_lon": semicircle_to_degrees(end.get("lon")),
                "shot_source": shot.get("shotSource"),
                "auto_shot_type": shot.get("autoShotType"),
                "exclude_from_stats": shot.get("excludeFromStats"),
            })
    return rows


# ---------------------------------------------------------------------------
# Generic flattening (for the round-level summary tab)
# ---------------------------------------------------------------------------

def flatten_dict(d: dict, parent_key: str = "", sep: str = "_") -> dict:
    """Flatten a nested dict into a single-level dict. Lists are JSON-
    encoded as a single cell (the 'holes' list is excluded — handled
    separately for the Golf Holes tab)."""
    items = {}
    for k, v in d.items():
        if k == "holes":
            continue
        new_key = f"{parent_key}{sep}{k}" if parent_key else k
        if isinstance(v, dict):
            items.update(flatten_dict(v, new_key, sep=sep))
        elif isinstance(v, list):
            items[new_key] = json.dumps(v) if v else ""
        else:
            items[new_key] = v
    return items


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


def get_worksheet(tab_name: str, cols: int = 60):
    sh = _get_spreadsheet()
    try:
        return sh.worksheet(tab_name)
    except gspread.exceptions.WorksheetNotFound:
        return sh.add_worksheet(title=tab_name, rows=8000, cols=cols)


def _sheet_needs_header(ws) -> bool:
    """row_values(1) trims trailing blanks for that row specifically, so
    it's a reliable emptiness check even on a previously-cleared sheet."""
    return not ws.row_values(1)


def get_existing_keys(ws, key_field_index: int, extra_field_index: int = None) -> set:
    """Read one or two columns (1-based index) from a tab and return the
    set of already-present keys, so rows already written can be skipped
    on future runs. If extra_field_index is given, keys are composite
    (joined with '||') — used for the Golf Holes tab, which has no single
    unique ID column."""
    col_a = ws.col_values(key_field_index)
    col_b = ws.col_values(extra_field_index) if extra_field_index else None

    keys = set()
    max_len = max(len(col_a), len(col_b) if col_b else 0)
    for i in range(1, max_len):  # skip header row (index 0)
        a = col_a[i].strip() if i < len(col_a) else ""
        if not a:
            continue
        if col_b is not None:
            b = col_b[i].strip() if i < len(col_b) else ""
            keys.add(f"{a}||{b}")
        else:
            keys.add(a)
    return keys


def write_flattened_rows(tab_name: str, rows: list, key_fn=None,
                          key_field_index: int = 1, extra_field_index: int = None):
    """Write a list of flat dicts to a tab. Header is the union of all
    keys seen (first-seen order); each row padded to match.

    If key_fn is given, rows whose key (per key_fn) already exists in the
    tab (checked via key_field_index / extra_field_index columns) are
    skipped — this is what prevents duplicate rows on repeated runs."""
    if not rows:
        print(f"No rows to write to '{tab_name}'.")
        return

    all_keys = []
    seen = set()
    for row in rows:
        for k in row.keys():
            if k not in seen:
                seen.add(k)
                all_keys.append(k)

    ws = get_worksheet(tab_name, cols=max(len(all_keys) + 2, 20))
    header = [k.replace("_", " ").title() for k in all_keys]
    if _sheet_needs_header(ws):
        ws.append_row(header, table_range="A1")

    if key_fn is not None:
        existing = get_existing_keys(ws, key_field_index, extra_field_index)
        before = len(rows)
        rows = [r for r in rows if key_fn(r) not in existing]
        skipped = before - len(rows)
        if skipped:
            print(f"  Skipping {skipped} row(s) already present in '{tab_name}'.")
        if not rows:
            print(f"No new rows to write to '{tab_name}' — already up to date.")
            return

    data_rows = [[row.get(k, "") for k in all_keys] for row in rows]

    # Sheets API caps a single append at ~10MB / large row counts —
    # chunk large shot-history writes to stay safe.
    CHUNK = 2000
    for i in range(0, len(data_rows), CHUNK):
        ws.append_rows(data_rows[i:i + CHUNK], table_range="A1")

    print(f"Wrote {len(data_rows)} rows to '{tab_name}' ({len(all_keys)} columns).")


def clear_all_data():
    ws = get_worksheet(GOOGLE_GOLF_SUMMARY_TAB)
    ws.clear()
    print(f"Cleared '{ws.title}' tab.")

    hws = get_worksheet(GOOGLE_GOLF_HOLES_TAB)
    hws.clear()
    print(f"Cleared '{hws.title}' tab.")

    sws = get_worksheet(GOOGLE_GOLF_SHOTS_TAB)
    sws.clear()
    print(f"Cleared '{sws.title}' tab.")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    check_env()
    args = sys.argv[1:]

    if args and args[0] == "--reset":
        confirm = input(
            "This will permanently delete ALL rows on all three golf tabs. "
            "Type 'yes' to confirm: "
        )
        if confirm.strip().lower() == "yes":
            clear_all_data()
        else:
            print("Cancelled.")
        return

    raw_mode = args and args[0] == "--raw"

    print("Logging into Garmin Connect...")
    client = garmin_login()

    print("Fetching scorecard summaries...")
    summaries = get_scorecard_summaries(client)
    print(f"Found {len(summaries)} round(s).")

    if not summaries:
        print("No rounds found — nothing to write.")
        return

    # scorecardId -> {courseName, date}, used to attribute shots (which
    # only carry scorecardId) back to a human-readable round.
    scorecard_to_round = {
        s.get("id"): {
            "courseName": s.get("courseName"),
            "date": (s.get("startTime") or "")[:10],
        }
        for s in summaries
    }

    # One seed scorecard ID per distinct course — the shot-history
    # endpoint returns all rounds at that course from one call.
    seed_by_course = {}
    for s in summaries:
        course = s.get("courseName")
        if course and course not in seed_by_course:
            seed_by_course[course] = s.get("id")

    print(f"Fetching club bag (for club-name lookups)...")
    club_names = get_club_names(client)
    print(f"  {len(club_names)} club(s) found.")

    print(f"Fetching shot history for {len(seed_by_course)} distinct course(s)...")
    all_hole_shots = []
    for course, seed_id in seed_by_course.items():
        print(f"  {course} (seed scorecard {seed_id})...")
        all_hole_shots.extend(get_shot_history_for_course(client, seed_id))

    all_shots = flatten_shot_history(all_hole_shots, club_names, scorecard_to_round)

    # Dedupe by shot id, in case of any overlap across course queries.
    seen_ids = set()
    deduped_shots = []
    for shot in all_shots:
        sid = shot.get("shot_id")
        if sid in seen_ids:
            continue
        seen_ids.add(sid)
        deduped_shots.append(shot)

    print(f"Found {len(deduped_shots)} unique shot(s) across all rounds.")

    # (scorecard_id, hole_number) -> count of PUTT-type shots actually
    # logged for that hole. Used below both to flag incomplete putt
    # tracking on the Golf Holes tab (see get_holes_for_round) and,
    # indirectly, wherever a hole has 0 here despite putts having been
    # taken.
    putts_logged = {}
    for shot in deduped_shots:
        if shot.get("shot_type") != "PUTT":
            continue
        key = (shot.get("scorecard_id"), shot.get("hole_number"))
        putts_logged[key] = putts_logged.get(key, 0) + 1

    if raw_mode:
        print(json.dumps(summaries[:2], indent=2))
        print(f"... ({len(summaries)} total rounds; showing first 2)")
        print()
        first_id = summaries[0].get("id")
        print(f"Fetching scorecard detail for fairway outcomes on scorecard {first_id}...")
        detail = get_scorecard_detail(client, first_id)
        outcomes = get_fairway_outcomes_for_round(detail)
        print("Hole-by-hole rows (including fairway_shot_outcome) for round 1:")
        print(json.dumps(get_holes_for_round(summaries[0], fairway_outcomes=outcomes), indent=2))
        print()
        print("Raw club bag response (to debug club-name mapping):")
        try:
            raw_clubs = client.connectapi(
                GOLF_CLUB_PLAYER_PATH,
                params={"per-page": 1000, "include-stats": "false", "maxClubTypeId": 42},
            )
            print(json.dumps(raw_clubs, indent=2)[:3000])
        except Exception as e:
            print(f"  WARNING: raw club fetch failed: {e}")
        print()
        print("First 5 shot rows:")
        print(json.dumps(deduped_shots[:5], indent=2))
        return

    flat_summaries = [flatten_dict(s) for s in summaries]
    write_flattened_rows(
        GOOGLE_GOLF_SUMMARY_TAB, flat_summaries,
        key_fn=lambda r: str(r.get("id")), key_field_index=1,
    )

    # Only fetch scorecard detail (needed for fairway_shot_outcome) for
    # rounds not already written to Golf Holes — avoids re-fetching detail
    # for hundreds of already-processed rounds on every run, and stays
    # easy on Garmin's rate limits.
    holes_ws = get_worksheet(GOOGLE_GOLF_HOLES_TAB)
    already_processed_rounds = get_existing_keys(holes_ws, key_field_index=1)

    new_rounds = [s for s in summaries if str(s.get("id")) not in already_processed_rounds]
    print(f"Fetching fairway-outcome detail for {len(new_rounds)} new round(s) "
          f"(skipping {len(summaries) - len(new_rounds)} already processed)...")

    all_holes = []
    # (scorecard_id, hole_number) -> (pin_lat, pin_lon), used below to
    # compute approach-shot miss side. Only populated for rounds whose
    # detail we actually fetched this run (new rounds) — see docstring
    # note on this not being retroactively backfilled.
    pin_positions = {}
    for i, summary in enumerate(summaries, start=1):
        scorecard_id = summary.get("id")
        if str(scorecard_id) in already_processed_rounds:
            all_holes.extend(get_holes_for_round(summary, putts_logged=putts_logged))
            continue
        detail = get_scorecard_detail(client, scorecard_id)
        outcomes = get_fairway_outcomes_for_round(detail)
        for hole_number, data in outcomes.items():
            if data.get("pin_lat") is not None and data.get("pin_lon") is not None:
                pin_positions[(scorecard_id, hole_number)] = (data["pin_lat"], data["pin_lon"])
        all_holes.extend(get_holes_for_round(summary, fairway_outcomes=outcomes, putts_logged=putts_logged))
        if i % 20 == 0:
            print(f"  ...{i}/{len(summaries)} rounds checked")
        time.sleep(0.3)

    if all_holes:
        # Composite key: scorecard_id + hole_number (no single unique
        # column on this tab). Field order: scorecard_id(1), course_name
        # (2), date(3), hole_number(4), par(5), strokes(6).
        write_flattened_rows(
            GOOGLE_GOLF_HOLES_TAB, all_holes,
            key_fn=lambda r: f"{r.get('scorecard_id')}||{r.get('hole_number')}",
            key_field_index=1, extra_field_index=4,
        )

    # hit_green / hit_fairway: whether the shot's end lie was literally
    # "Green" or "Fairway" — added for EVERY shot (not just approach/tee),
    # since it's a simple lookup on data already present. These are the
    # ground truth for "found the green" / "found the fairway",
    # independent of the miss-line geometry below (a shot can land on
    # the green or fairway while still showing a lateral/depth offset
    # from the pin — that's normal, not a contradiction; very few shots
    # finish dead center on the target line). hit_fairway is naturally
    # most relevant for tee shots — approach/chip/putt shots aren't
    # "aiming" for the fairway, so it'll usually be False for those.
    for shot in deduped_shots:
        shot["hit_green"] = (shot.get("end_lie") == "Green")
        shot["hit_fairway"] = (shot.get("end_lie") == "Fairway")

    # Compute miss geometry (lateral left/right + depth short/long/pin-
    # high) for approach AND tee shots (see compute_shot_miss docstring
    # for accuracy caveats, especially on dogleg holes for tee shots).
    # Only possible for shots on holes whose pin position we have — i.e.
    # rounds processed this run.
    miss_count = 0
    for shot in deduped_shots:
        if shot.get("shot_type") not in ("APPROACH", "TEE"):
            continue
        pin = pin_positions.get((shot.get("scorecard_id"), shot.get("hole_number")))
        if not pin:
            continue
        result = compute_shot_miss(
            shot.get("start_lat"), shot.get("start_lon"),
            shot.get("end_lat"), shot.get("end_lon"),
            pin[0], pin[1],
        )
        if result is not None:
            shot["miss_side"] = result["lateral_side"]
            shot["miss_distance_yards"] = result["lateral_distance_yards"]
            shot["miss_depth"] = result["depth_side"]
            shot["miss_depth_distance_yards"] = result["depth_distance_yards"]
            miss_count += 1
    if miss_count:
        print(f"Computed miss side/depth for {miss_count} approach and tee shot(s).")

    # PUTT DISTANCE FIX: Garmin's own per-shot distance for a PUTT isn't
    # trustworthy. Cross-checked against holes verified 1-putt (Golf
    # Holes -> Putts == 1, an independent field from scorecard detail):
    # the approach shot immediately before those putts often already
    # sits ~1-2 yards from the real pin (per pin_positions below), yet
    # Garmin's own putt "meters" value is routinely 10+ yards on the
    # same hole — not plausible for a putt that goes straight in from a
    # yard and a half away. Best working theory: when a putt is logged
    # without a precise GPS fix on the true final ball position, Garmin
    # falls back to distance from an assumed pin location (likely the
    # green's center) rather than the actual pin you set for the round,
    # which inflates the number.
    #
    # Fix: where we know this hole's real pin position (pin_positions,
    # sourced from the deliberately-set pinPositionLat/Lon — the same
    # trusted source already used for miss_distance_yards above),
    # recompute putt distance ourselves as straight-line distance from
    # where the putt started to that real pin. distance_source records
    # which path a given row took, so it's visible in the sheet.
    #
    # Fallback: for rounds where we don't have a pin position on file
    # (not retroactively backfilled — see module docstring; run --reset
    # then a full pull to get pin positions for your whole history),
    # keep Garmin's raw baseline value and flag it as such.
    putt_fixed_count, putt_fallback_count = 0, 0
    for shot in deduped_shots:
        if shot.get("shot_type") != "PUTT":
            continue
        shot["distance_yards_garmin_raw"] = shot.get("distance_yards")
        pin = pin_positions.get((shot.get("scorecard_id"), shot.get("hole_number")))
        recomputed = (
            distance_yards_between(
                shot.get("start_lat"), shot.get("start_lon"), pin[0], pin[1]
            ) if pin else None
        )
        if recomputed is not None:
            shot["distance_yards"] = recomputed
            shot["distance_source"] = "PIN_POSITION"
            putt_fixed_count += 1
        else:
            # Baseline: Garmin's raw value, which assumes an unknown
            # (likely green-center, not real) pin location. Left as-is
            # in distance_yards so every row still has a usable number,
            # just flagged as lower-confidence via distance_source.
            shot["distance_source"] = "GARMIN_BASELINE"
            putt_fallback_count += 1
    if putt_fixed_count or putt_fallback_count:
        print(f"Putt distance: recomputed {putt_fixed_count} from real pin position, "
              f"{putt_fallback_count} fell back to Garmin's baseline value "
              f"(no pin position on file for that hole yet).")

    if deduped_shots:
        # shot_id is globally unique per shot and is the first field.
        write_flattened_rows(
            GOOGLE_GOLF_SHOTS_TAB, deduped_shots,
            key_fn=lambda r: str(r.get("shot_id")), key_field_index=1,
        )
    else:
        print("No shot-level data found.")

    print("Done.")


if __name__ == "__main__":
    main()
