@echo off
REM process-safety-monitor 2-hour run -- thin wrapper that delegates to
REM run_and_push.ps1 (PowerShell handles tee'ing output to console + log).
REM Invoked by Windows Task Scheduler (see setup_schedule.ps1) or by
REM manual double-click. Either way, live progress shows in the cmd
REM window AND a durable record lands at logs\run.log.

cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0run_and_push.ps1"
exit /b %ERRORLEVEL%
