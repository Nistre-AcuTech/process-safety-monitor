# process-safety-monitor 2-hour run.
# Invoked by run_and_push.bat (which is what Windows Task Scheduler and
# manual double-click both call). Output streams BOTH to the console
# (visible during double-click) AND to logs\run.log (durable record).
#
# Updates docs/data/events.json (the GitHub Pages dashboard data) and
# pushes the change if there are new events. Exit code is python's exit
# code (or git push's, if python succeeded).

Set-Location -Path $PSScriptRoot

$logDir = Join-Path $PSScriptRoot "logs"
$logFile = Join-Path $logDir "run.log"
if (-not (Test-Path $logDir)) {
    New-Item -ItemType Directory -Path $logDir | Out-Null
}

# Helper: write a single line to console + log, UTF-8 to avoid PS 5.1's
# default-UTF-16-LE encoding which breaks tail/grep on the log file.
function Append-Log([string]$line) {
    Write-Host $line
    Add-Content -Path $logFile -Value $line -Encoding utf8
}

# Helper: stream a native-command pipeline to console + log, line-by-line.
# Tee-Object would be cleaner but in PS 5.1 it defaults to UTF-16 LE.
function Stream-And-Log([scriptblock]$cmd) {
    & $cmd 2>&1 | ForEach-Object {
        $line = $_.ToString()
        Write-Host $line
        Add-Content -Path $logFile -Value $line -Encoding utf8
    }
}

Append-Log ""
Append-Log "===== $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') ====="

Stream-And-Log { python -u main.py }
$rc = $LASTEXITCODE

if ($rc -eq 0) {
    Stream-And-Log { git add docs/data/events.json }
    & git diff --cached --quiet
    if ($LASTEXITCODE -ne 0) {
        # staged changes exist — commit + push
        Stream-And-Log { git commit -m "Update events data" }
        Stream-And-Log { git push }
        $rc = $LASTEXITCODE
    }
}

Append-Log "===== exit $rc ====="
exit $rc
