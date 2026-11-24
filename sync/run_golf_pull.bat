@echo off
REM run_golf_pull.bat
REM
REM Runs the Garmin Golf data pull (rounds/holes/shots), logging output
REM to a file so you can check what happened on a given run.
REM
REM Scheduled to run Mondays and Fridays via Task Scheduler.

set SCRIPT_DIR=C:\Users\Eric\OneDrive\Documents\Fitness Data Connect\files
set PY_CMD=py -3.9
set LOG_FILE=%SCRIPT_DIR%\golf_pull_log.txt

cd /d "%SCRIPT_DIR%"

echo ============================================== >> "%LOG_FILE%"
echo Run started: %DATE% %TIME% >> "%LOG_FILE%"
echo ============================================== >> "%LOG_FILE%"

echo. >> "%LOG_FILE%"
echo --- Garmin Golf pull --- >> "%LOG_FILE%"
%PY_CMD% garmin_golf_to_sheets.py >> "%LOG_FILE%" 2>&1

echo. >> "%LOG_FILE%"
echo Run finished: %DATE% %TIME% >> "%LOG_FILE%"
echo. >> "%LOG_FILE%"
