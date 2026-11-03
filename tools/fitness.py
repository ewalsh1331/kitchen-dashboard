"""Read-only access to the Fitness Data Google Sheet (Garmin, Oura, golf).

Examples (run from ~/kitchen-dashboard):
  .venv/bin/python tools/fitness.py tabs
  .venv/bin/python tools/fitness.py read Oura --last 14
  .venv/bin/python tools/fitness.py read "Golf Rounds" --last 10
  .venv/bin/python tools/fitness.py read "Golf Shots" --all > /tmp/shots.json

Output is JSON: a list of rows keyed by the header row.
"""
import argparse
import json

from googleapiclient.discovery import build

from google_auth import CONFIG, credentials

SHEET_ID = CONFIG.get("fitness_sheet_id", "1XMXL46VKuKVSKxEkyuJnUj9rBUUxeuewxMJ5VDVMBsw")


def svc():
    return build("sheets", "v4", credentials=credentials(), cache_discovery=False)


def main():
    p = argparse.ArgumentParser(description="Fitness Data sheet (read-only)")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("tabs")
    r = sub.add_parser("read")
    r.add_argument("tab")
    r.add_argument("--last", type=int, default=14, help="last N rows (default 14)")
    r.add_argument("--all", action="store_true")
    a = p.parse_args()

    if a.cmd == "tabs":
        meta = svc().spreadsheets().get(spreadsheetId=SHEET_ID, fields="sheets.properties").execute()
        print(json.dumps([
            {"tab": s["properties"]["title"], "rows": s["properties"]["gridProperties"]["rowCount"]}
            for s in meta["sheets"]
        ], indent=2))
        return

    values = svc().spreadsheets().values().get(
        spreadsheetId=SHEET_ID, range=f"'{a.tab}'", valueRenderOption="FORMATTED_VALUE"
    ).execute().get("values", [])
    if not values:
        print("[]")
        return
    header, rows = values[0], values[1:]
    rows = [r for r in rows if any(c.strip() for c in r)]
    if not a.all:
        rows = rows[-a.last:]
    data = [dict(zip(header, r + [""] * (len(header) - len(r)))) for r in rows]
    print(json.dumps(data, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
