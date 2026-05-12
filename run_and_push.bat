@echo off
REM process-safety-monitor 2-hour run.
REM Invoked by Windows Task Scheduler (see setup_schedule.ps1) or
REM run manually by double-click.
REM
REM Updates docs/data/events.json (the GitHub Pages dashboard data) and
REM pushes the change if there are new events.
REM Log: logs\run.log (rotates manually).

cd /d "%~dp0"
if not exist logs mkdir logs

echo. >> logs\run.log
echo ===== %DATE% %TIME% ===== >> logs\run.log

python main.py >> logs\run.log 2>&1
set RC=%ERRORLEVEL%
if %RC% NEQ 0 goto :end

git add docs/data/events.json >> logs\run.log 2>&1
git diff --cached --quiet
if %ERRORLEVEL% EQU 0 goto :end

git commit -m "Update events data" >> logs\run.log 2>&1
git push >> logs\run.log 2>&1
set RC=%ERRORLEVEL%

:end
echo ===== exit %RC% ===== >> logs\run.log
exit /b %RC%
