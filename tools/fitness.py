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


def stats(header, rows, columns, days):
    """Mean, spread and percentiles for numeric columns over the last `days` rows (blanks skipped)."""
    import statistics as st
    rows = rows[-days:]
    out = {"rows": len(rows), "from": rows[0][0] if rows else None, "to": rows[-1][0] if rows else None}
    for col in columns:
        if col not in header:
            out[col] = {"error": f"no column {col!r}"}
            continue
        i = header.index(col)
        vals = sorted(float(r[i]) for r in rows if len(r) > i and r[i].strip() not in ("", "0"))
        if len(vals) < 4:
            out[col] = {"n": len(vals)}
            continue
        q = st.quantiles(vals, n=20, method="inclusive")          # 5% steps
        out[col] = {"n": len(vals), "mean": round(st.mean(vals), 1), "sd": round(st.stdev(vals), 1),
                    "min": vals[0], "p10": round(q[1], 1), "p25": round(q[4], 1), "median": round(st.median(vals), 1),
                    "p75": round(q[14], 1), "p90": round(q[17], 1), "max": vals[-1]}
    return out


def main():
    p = argparse.ArgumentParser(description="Fitness Data sheet (read-only)")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("tabs")
    r = sub.add_parser("read")
    r.add_argument("tab")
    r.add_argument("--last", type=int, default=14, help="last N rows (default 14)")
    r.add_argument("--all", action="store_true")
    s = sub.add_parser("stats", help='summary of numeric columns, e.g. stats Oura "Sleep Avg HRV (ms)" --days 60')
    s.add_argument("tab")
    s.add_argument("columns", nargs="+")
    s.add_argument("--days", type=int, default=60, help="last N dated rows (default 60)")
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
    if a.cmd == "stats":
        print(json.dumps(stats(header, rows, a.columns, a.days), indent=2))
        return
    if not a.all:
        rows = rows[-a.last:]
    data = [dict(zip(header, r + [""] * (len(header) - len(r)))) for r in rows]
    print(json.dumps(data, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
