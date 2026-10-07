<#
.SYNOPSIS
    Register, inspect or remove the Windows scheduled task that runs `python -m gigradar.watch`.

.DESCRIPTION
    Registers \gigradar\gigradar-watch for the current user:
      - every -IntervalMinutes (default 30, minimum 15), first run ~1 minute after registering
      - "Run only when user is logged on" (interactive logon): WebView2 needs your desktop session,
        and so does showing the window if Cloudflare ever wants a click
      - runs the repo's .venv\Scripts\pythonw.exe (no console window flashing) at normal priority
        (Task Scheduler's default below-normal priority made WebView2 start too slowly)
      - logs to <repo>\logs\gigradar.log (rotating, gitignored)
      - never overlaps itself (a run still going -> the next start is skipped), 10-min time limit
      - a run missed while the PC was off/asleep starts once when it's available again (no burst)
    No admin rights needed. Re-running -Register replaces the existing task.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\windows\gigradar-task.ps1 -Register
    powershell -ExecutionPolicy Bypass -File scripts\windows\gigradar-task.ps1 -Run
    powershell -ExecutionPolicy Bypass -File scripts\windows\gigradar-task.ps1 -Status
    powershell -ExecutionPolicy Bypass -File scripts\windows\gigradar-task.ps1 -Unregister
#>
[CmdletBinding(DefaultParameterSetName = 'Register')]
param(
    [Parameter(ParameterSetName = 'Register')] [switch] $Register,
    [Parameter(ParameterSetName = 'Register')] [ValidateRange(15, 1440)] [int] $IntervalMinutes = 30,
    [Parameter(ParameterSetName = 'Run', Mandatory)] [switch] $Run,
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

    'Run' {
        # Start the registered task now, in exactly the scheduler's context (one normal run).
        if (-not (Get-GigradarTask)) { throw "Scheduled task $TaskPath$TaskName is not registered; use -Register first." }
        Start-ScheduledTask -TaskName $TaskName -TaskPath $TaskPath
        Write-Host "Started $TaskPath$TaskName. Check in ~1 min: powershell -ExecutionPolicy Bypass -File `"$PSCommandPath`" -Status"
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
        # The log is UTF-8; Windows PowerShell 5.1 would otherwise read it as the ANSI codepage.
        if (Test-Path $LogFile) { Write-Host '--- last 5 log lines ---'; Get-Content $LogFile -Tail 5 -Encoding UTF8 }
        return
    }

    'Register' {
        foreach ($required in @($Python, $Config)) {
            if (-not (Test-Path $required)) { throw "Missing $required (see README / gigradar.example.toml)." }
        }

        $arguments = "-m gigradar.watch --config `"$Config`" --log-file `"$LogFile`""
        $action = New-ScheduledTaskAction -Execute $Python -Argument $arguments -WorkingDirectory $Root
        # No -RepetitionDuration: repeats indefinitely.
        $trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) `
            -RepetitionInterval (New-TimeSpan -Minutes $IntervalMinutes)
        $principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" `
            -LogonType Interactive -RunLevel Limited
        # -Priority 4 = normal. Task Scheduler's default 7 (below normal, plus low I/O/memory priority)
        # delayed interpreter startup by ~40 s and let WebView2 miss pywebview's 20 s window-start wait.
        $settings = New-ScheduledTaskSettingsSet -Priority 4 -MultipleInstances IgnoreNew `
            -ExecutionTimeLimit (New-TimeSpan -Minutes 10) -StartWhenAvailable `
            -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries

        Register-ScheduledTask -TaskName $TaskName -TaskPath $TaskPath -Action $action -Trigger $trigger `
            -Principal $principal -Settings $settings -Force `
            -Description "gigradar: Upwork job watcher (python -m gigradar.watch), every $IntervalMinutes min." | Out-Null

        Write-Host "Registered $TaskPath$TaskName : every $IntervalMinutes min, first run ~1 min from now."
        Write-Host "Runs:  $Python $arguments"
        Write-Host "Log:   $LogFile"
        Write-Host "Check: powershell -ExecutionPolicy Bypass -File `"$PSCommandPath`" -Status"
    }
}
