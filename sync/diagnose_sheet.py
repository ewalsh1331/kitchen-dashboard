"""
diagnose_sheet.py

One-off diagnostic: lists every tab in the target spreadsheet, how many
rows each has, and what get_worksheet() actually resolves to given your
current .env settings. Run this from the same folder as garmin_to_sheets.py.
"""

from dotenv import load_dotenv
load_dotenv()

import os
from garmin_to_sheets import _get_spreadsheet, get_worksheet, GOOGLE_SHEET_TAB, GOOGLE_SHEET_ID

print(f"GOOGLE_SHEET_ID from .env: {repr(GOOGLE_SHEET_ID)}")
print(f"GOOGLE_SHEET_TAB from .env: {repr(GOOGLE_SHEET_TAB)}")
print()

sh = _get_spreadsheet()
print(f"Spreadsheet title: {sh.title}")
print(f"Spreadsheet URL: {sh.url}")
print()

print("All tabs in this spreadsheet:")
for ws in sh.worksheets():
    values = ws.get_all_values()
    print(f"  - '{ws.title}'  (row_count={ws.row_count}, actual data rows={len(values)})")

print()
resolved = get_worksheet()
print(f"get_worksheet() resolved to tab: '{resolved.title}'")
print(f"That tab currently has {len(resolved.get_all_values())} rows of data.")
