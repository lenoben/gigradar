<#
.SYNOPSIS
    Register, inspect or remove the Windows scheduled task that runs `python -m gigradar.watch`.

.DESCRIPTION
    Registers \gigradar\gigradar-watch for the current user:
      - every -IntervalMinutes (default 30, minimum 15), first run ~1 minute after registering
      - "Run only when user is logged on" (interactive logon): WebView2 needs your desktop session,
        and so does showing the window if Cloudflare ever wants a click
      - runs the repo's .venv\Scripts\pythonw.exe (no console window flashing)
      - logs to <repo>\logs\gigradar.log (rotating, gitignored)
      - never overlaps itself (a run still going -> the next start is skipped), 10-min time limit
      - a run missed while the PC was off/asleep starts once when it's available again (no burst)
    No admin rights needed. Re-running -Register replaces the existing task.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\windows\gigradar-task.ps1 -Register
    powershell -ExecutionPolicy Bypass -File scripts\windows\gigradar-task.ps1 -Status
    powershell -ExecutionPolicy Bypass -File scripts\windows\gigradar-task.ps1 -Unregister
#>
[CmdletBinding(DefaultParameterSetName = 'Register')]
param(
    [Parameter(ParameterSetName = 'Register')] [switch] $Register,
    [Parameter(ParameterSetName = 'Register')] [ValidateRange(15, 1440)] [int] $IntervalMinutes = 30,
    [Parameter(ParameterSetName = 'Unregister', Mandatory)] [switch] $Unregister,
    [Parameter(ParameterSetName = 'Status', Mandatory)] [switch] $Status
)

$ErrorActionPreference = 'Stop'
$TaskName = 'gigradar-watch'
$TaskPath = '\gigradar\'

# Repo root = two levels up from scripts\windows\
$Root    = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$Python  = Join-Path $Root '.venv\Scripts\pythonw.exe'
$Config  = Join-Path $Root 'gigradar.toml'
$LogFile = Join-Path $Root 'logs\gigradar.log'

function Get-GigradarTask {
    Get-ScheduledTask -TaskName $TaskName -TaskPath $TaskPath -ErrorAction SilentlyContinue
}

switch ($PSCmdlet.ParameterSetName) {
    'Unregister' {
        if (Get-GigradarTask) {
            Unregister-ScheduledTask -TaskName $TaskName -TaskPath $TaskPath -Confirm:$false
            Write-Host "Removed scheduled task $TaskPath$TaskName."
        } else {
            Write-Host "No scheduled task $TaskPath$TaskName found; nothing to remove."
        }
        return
    }

    'Status' {
        $task = Get-GigradarTask
        if (-not $task) { Write-Host "Scheduled task $TaskPath$TaskName is not registered."; return }
        $info = $task | Get-ScheduledTaskInfo
        $meaning = @{ 0 = 'ok'; 1 = 'error (see log)'; 2 = 'config error'; 75 = 'stopped early, retried next run';
                      267009 = 'running now'; 267011 = 'has not run yet' }
        $code = [int]$info.LastTaskResult
        Write-Host "Task:        $TaskPath$TaskName ($($task.State))"
        Write-Host "Last run:    $($info.LastRunTime)  result=$code ($($meaning[$code]))"
        Write-Host "Next run:    $($info.NextRunTime)"
        Write-Host "Log:         $LogFile"
        if (Test-Path $LogFile) { Write-Host '--- last 5 log lines ---'; Get-Content $LogFile -Tail 5 }
        return
    }

    'Register' {
        foreach ($required in @($Python, $Config)) {
            if (-not (Test-Path $required)) { throw "Missing $required (see README / gigradar.example.toml)." }
        }

        $action = New-ScheduledTaskAction -Execute $Python `
            -Argument "-m gigradar.watch --config `"$Config`" --log-file `"$LogFile`"" `
            -WorkingDirectory $Root
        # No -RepetitionDuration: repeats indefinitely.
        $trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) `
            -RepetitionInterval (New-TimeSpan -Minutes $IntervalMinutes)
        $principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" `
            -LogonType Interactive -RunLevel Limited
        $settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew `
            -ExecutionTimeLimit (New-TimeSpan -Minutes 10) -StartWhenAvailable `
            -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries

        Register-ScheduledTask -TaskName $TaskName -TaskPath $TaskPath -Action $action -Trigger $trigger `
            -Principal $principal -Settings $settings -Force `
            -Description "gigradar: Upwork job watcher (python -m gigradar.watch), every $IntervalMinutes min." | Out-Null

        Write-Host "Registered $TaskPath$TaskName : every $IntervalMinutes min, first run ~1 min from now."
        Write-Host "Runs:  $Python -m gigradar.watch"
        Write-Host "Log:   $LogFile"
        Write-Host "Check: powershell -ExecutionPolicy Bypass -File `"$PSCommandPath`" -Status"
    }
}
