"""
inspect_rows.py

Prints the raw contents of the first few rows of the Garmin tab exactly as
Google Sheets has them stored, so we can see if real data is there or if
rows are genuinely empty.
"""

from dotenv import load_dotenv
load_dotenv()

from garmin_to_sheets import get_worksheet, get_activities_worksheet

ws = get_worksheet()
values = ws.get_all_values()

print(f"Total rows in Garmin tab: {len(values)}")
print()
print("Row 1 (should be headers):")
print(values[0] if len(values) > 0 else "  (no rows at all)")
print()
print("Row 2 (should be first data row):")
print(values[1] if len(values) > 1 else "  (no second row)")
print()
print("Row 3:")
print(values[2] if len(values) > 2 else "  (no third row)")
print()
print("Last row:")
print(values[-1] if values else "  (empty)")

print()
print("=" * 60)
aws = get_activities_worksheet()
avalues = aws.get_all_values()
print(f"Total rows in Activities tab: {len(avalues)}")
print("Row 1 (headers):", avalues[0] if avalues else "(none)")
print("Row 2:", avalues[1] if len(avalues) > 1 else "(none)")
