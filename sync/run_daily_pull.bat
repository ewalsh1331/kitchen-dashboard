@echo off
REM run_daily_pull.bat
REM
REM Runs the Garmin and Oura daily data pulls, in sequence, with output
REM logged to a file so you can check what happened on a given day.
REM
REM SETUP:
REM 1. Edit the SCRIPT_DIR line below to match the folder containing
REM    garmin_to_sheets.py, oura_to_sheets.py, and your .env file.
REM 2. Uses the "py" launcher with -3.9 (only 3.8/3.9 are installed on
REM    this machine). Make sure dependencies are installed for 3.9:
REM      py -3.9 -m pip install garminconnect gspread google-auth python-dotenv requests
REM 3. Double-click this file once to test it manually before scheduling.
REM 4. Once it works, point Windows Task Scheduler at this .bat file
REM    (see the setup instructions shared alongside this file).

set SCRIPT_DIR=C:\Users\Eric\OneDrive\Documents\Fitness Data Connect\files
set PY_CMD=py -3.9
set LOG_FILE=%SCRIPT_DIR%\daily_pull_log.txt

cd /d "%SCRIPT_DIR%"

echo ============================================== >> "%LOG_FILE%"
echo Run started: %DATE% %TIME% >> "%LOG_FILE%"
echo ============================================== >> "%LOG_FILE%"

echo. >> "%LOG_FILE%"
echo --- Garmin pull --- >> "%LOG_FILE%"
%PY_CMD% garmin_to_sheets.py >> "%LOG_FILE%" 2>&1

echo. >> "%LOG_FILE%"
echo --- Oura pull --- >> "%LOG_FILE%"
%PY_CMD% oura_to_sheets.py >> "%LOG_FILE%" 2>&1

echo. >> "%LOG_FILE%"
echo Run finished: %DATE% %TIME% >> "%LOG_FILE%"
echo. >> "%LOG_FILE%"
