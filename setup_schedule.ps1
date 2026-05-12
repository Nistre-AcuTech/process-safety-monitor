# Register a Windows Task Scheduler entry that runs process-safety-monitor every 2 hours.
#
# - Repeats every 2 hours indefinitely starting at midnight
# - Logon type Interactive (no stored credentials; runs when you're logged in)
# - StartWhenAvailable: if the PC is off at the scheduled time, runs at next login
# - Re-run this script any time to update the schedule (it removes-then-creates)
#
# To remove the task entirely:
#   Unregister-ScheduledTask -TaskName "process-safety-monitor 2h" -Confirm:$false

$TaskName = "process-safety-monitor 2h"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$BatPath = Join-Path $ProjectRoot "run_and_push.bat"

if (-not (Test-Path $BatPath)) {
    Write-Error "run_and_push.bat not found at $BatPath"
    exit 1
}

# Remove any existing task with the same name
$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existing) {
    Write-Host "Removing existing task: $TaskName"
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}

$action = New-ScheduledTaskAction `
    -Execute "cmd.exe" `
    -Argument "/c `"$BatPath`"" `
    -WorkingDirectory $ProjectRoot

# Repeating trigger: starts at midnight today, fires every 2 hours forever
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).Date `
    -RepetitionInterval (New-TimeSpan -Hours 2)

$principal = New-ScheduledTaskPrincipal `
    -UserId "$env:USERDOMAIN\$env:USERNAME" `
    -LogonType Interactive `
    -RunLevel Limited

$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -DontStopOnIdleEnd `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 30) `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Principal $principal `
    -Settings $settings `
    -Description "Every 2 hours: scrapes news, updates docs/data/events.json, pushes to GitHub. Logs to $ProjectRoot\logs\run.log" | Out-Null

$task = Get-ScheduledTask -TaskName $TaskName
Write-Host ""
Write-Host "Registered task: $($task.TaskName)"
Write-Host "  State:        $($task.State)"
Write-Host "  Next run:     $((Get-ScheduledTaskInfo -TaskName $TaskName).NextRunTime)"
Write-Host "  Trigger:      Every 2 hours (starting midnight)"
Write-Host "  Runs:         $BatPath"
Write-Host "  Logs:         $ProjectRoot\logs\run.log"
Write-Host ""
Write-Host "Useful commands:"
Write-Host "  Start now:    Start-ScheduledTask -TaskName '$TaskName'"
Write-Host "  Check status: Get-ScheduledTaskInfo -TaskName '$TaskName'"
Write-Host "  Disable:      Disable-ScheduledTask -TaskName '$TaskName'"
Write-Host "  Remove:       Unregister-ScheduledTask -TaskName '$TaskName' -Confirm:`$false"
