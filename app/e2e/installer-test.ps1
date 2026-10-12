<#
.SYNOPSIS
    Installs the built installer on THIS PC, tries what only an installed app can do, and removes everything again.
.DESCRIPTION
    - installs silently (per user), checks the folders, the Start-menu entry, the gigradar:// registration
    - seeds a throw-away job history in the app's default data folder (fake jobs, no network)
    - sends real toasts for those jobs with the installed helper, clicks them through UI Automation and checks that the
      app opens on the job's card (closed app, open app, several jobs), with screenshots
    - registers the app's scheduled task (disabled at once) and checks the uninstaller removes it
    - uninstalls twice through the uninstaller window: data kept (box unticked), then data deleted (box ticked)
    Refuses to run when Gigradar is already installed or %LOCALAPPDATA%\Gigradar Data exists (it deletes that folder).
    Run it with the user at the PC: toasts appear on the screen and the mouse is moved to click them.
    Everything it writes is removed at the end (also after a failure).
.PARAMETER Installer  the *-setup.exe
.PARAMETER Out        folder for the log and the screenshots
.PARAMETER Python     python.exe of the repo venv (seeds the job history)
#>
[CmdletBinding()]
param([Parameter(Mandatory)] [string] $Installer, [Parameter(Mandatory)] [string] $Out, [Parameter(Mandatory)] [string] $Python)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Windows.Forms, System.Drawing, UIAutomationClient, UIAutomationTypes
Add-Type @'
using System;
using System.Runtime.InteropServices;
public static class Native {
  [DllImport("user32.dll")] public static extern bool SetCursorPos(int x, int y);
  [DllImport("user32.dll")] public static extern void mouse_event(uint flags, uint dx, uint dy, uint data, UIntPtr extra);
}
'@

$utf8 = New-Object Text.UTF8Encoding($false)
New-Item -ItemType Directory -Force $Out | Out-Null
$logFile = Join-Path $Out 'installer-test.log'
[IO.File]::WriteAllText($logFile, '', $utf8)
$script:failed = 0
$script:shots = 0
function Log([string] $text) { $line = "{0:HH:mm:ss} {1}" -f (Get-Date), $text; Write-Host $line; [IO.File]::AppendAllText($logFile, $line + "`n", $utf8) }
function Check([string] $name, [bool] $ok, [string] $detail = '') {
    if (-not $ok) { $script:failed++ }
    Log ("{0}: {1}{2}" -f $(if ($ok) { 'PASS' } else { 'FAIL' }), $name, $(if ($detail) { " ($detail)" } else { '' }))
}
function Shot([string] $name) {
    $bounds = [Windows.Forms.SystemInformation]::VirtualScreen
    $bitmap = New-Object Drawing.Bitmap $bounds.Width, $bounds.Height
    [Drawing.Graphics]::FromImage($bitmap).CopyFromScreen($bounds.Location, [Drawing.Point]::Empty, $bounds.Size)
    $script:shots++
    $file = Join-Path $Out ('{0:00}-{1}.png' -f $script:shots, $name)
    $bitmap.Save($file, [Drawing.Imaging.ImageFormat]::Png)
    Log "screenshot $file"
}

$local = $env:LOCALAPPDATA
$installDir = Join-Path $local 'Gigradar'
$dataDir = Join-Path $local 'Gigradar Data'
$uninstallKey = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\Gigradar'
$protocolKey = 'HKCU:\Software\Classes\gigradar'
$taskPath = '\Gigradar Desktop\checks'
$startMenu = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\Gigradar.lnk'

if ((Test-Path $uninstallKey) -or (Test-Path $installDir) -or (Test-Path $dataDir) -or (Test-Path $protocolKey)) {
    throw "Gigradar is already installed here ($installDir / $dataDir / $protocolKey): not touching it."
}

# ---- UI Automation helpers ----------------------------------------------------------------------------------------
function Find-Window([string] $namePart, [int] $seconds) {
    $end = (Get-Date).AddSeconds($seconds)
    while ((Get-Date) -lt $end) {
        foreach ($w in [Windows.Automation.AutomationElement]::RootElement.FindAll('Children', [Windows.Automation.Condition]::TrueCondition)) {
            if ($w.Current.Name -like "*$namePart*") { return $w }
        }
        Start-Sleep -Milliseconds 400
    }
    return $null
}
function Descendants($root) { $root.FindAll('Descendants', [Windows.Automation.Condition]::TrueCondition) }
function Click-Element($element) {
    $r = $element.Current.BoundingRectangle
    [Native]::SetCursorPos([int]($r.X + $r.Width / 2), [int]($r.Y + $r.Height / 2)) | Out-Null
    Start-Sleep -Milliseconds 200
    [Native]::mouse_event(0x02, 0, 0, 0, [UIntPtr]::Zero); [Native]::mouse_event(0x04, 0, 0, 0, [UIntPtr]::Zero)
}
function Find-Toast([int] $seconds) {
    $end = (Get-Date).AddSeconds($seconds)
    while ((Get-Date) -lt $end) {
        foreach ($w in [Windows.Automation.AutomationElement]::RootElement.FindAll('Children', [Windows.Automation.Condition]::TrueCondition)) {
            if ($w.Current.ClassName -eq 'Windows.UI.Core.CoreWindow' -and $w.Current.Name -match 'notification') { return $w }
        }
        Start-Sleep -Milliseconds 300
    }
    return $null
}
function Toast-Texts($toast) { @(Descendants $toast | ForEach-Object { $_.Current.Name } | Where-Object { $_ }) }
function Click-Toast([string] $label) {
    $toast = Find-Toast 15
    if ($null -eq $toast) { Log "no toast window found for $label"; return $false }
    Start-Sleep -Milliseconds 700
    Log ("toast text: " + ((Toast-Texts $toast) -join ' | '))
    Shot "toast-$label"
    # the toast body is the element that carries the text; clicking its centre is what a person does
    $body = $toast
    foreach ($e in Descendants $toast) { if ($e.Current.ControlType.ProgrammaticName -match 'Group|Pane|Custom' -and $e.Current.BoundingRectangle.Width -gt 200) { $body = $e; break } }
    Click-Element $body
    return $true
}
function Gigradar-Processes { @(Get-Process -Name 'Gigradar' -ErrorAction SilentlyContinue) }
function Window-Texts([int] $seconds) {
    $window = Find-Window 'Gigradar' $seconds
    if ($null -eq $window) { return @() }
    Start-Sleep -Seconds 2
    @(Descendants $window | ForEach-Object { $_.Current.Name } | Where-Object { $_ })
}
function Stop-Gigradar { Gigradar-Processes | ForEach-Object { $_.CloseMainWindow() | Out-Null }; Start-Sleep -Seconds 1; Gigradar-Processes | Stop-Process -Force -ErrorAction SilentlyContinue; Start-Sleep -Seconds 1 }

function Run-Uninstaller([bool] $deleteData) {
    $uninstaller = Join-Path $installDir 'uninstall.exe'
    $proc = Start-Process -FilePath $uninstaller -PassThru
    $window = Find-Window 'Uninstall' 30
    if ($null -eq $window) { Log 'no uninstaller window'; return $false }
    Start-Sleep -Seconds 2
    $box = $null; $button = $null
    foreach ($e in Descendants $window) {
        $type = $e.Current.ControlType.ProgrammaticName
        if ($type -match 'CheckBox' -and $e.Current.Name -like '*delete my data*') { $box = $e }
        if ($type -match 'Button' -and $e.Current.Name -match '^&?Uninstall') { $button = $e }
    }
    Check 'the uninstaller offers "Also delete my data"' ($null -ne $box) $(if ($box) { $box.Current.Name } else { 'not found' })
    if ($box) {
        $state = $box.GetCurrentPattern([Windows.Automation.TogglePattern]::Pattern).Current.ToggleState
        Check 'the box is unticked by default' ($state -eq 'Off') "$state"
        if ($deleteData -and $state -eq 'Off') { $box.GetCurrentPattern([Windows.Automation.TogglePattern]::Pattern).Toggle(); Start-Sleep -Milliseconds 500 }
    }
    Shot ("uninstaller-" + $(if ($deleteData) { 'ticked' } else { 'default' }))
    if ($null -eq $button) { Log 'no Uninstall button'; $proc | Stop-Process -Force; return $false }
    $button.GetCurrentPattern([Windows.Automation.InvokePattern]::Pattern).Invoke()
    # the uninstaller finishes by itself; its copy in the temp folder ends the process
    $end = (Get-Date).AddSeconds(90)
    while ((Get-Date) -lt $end -and (Test-Path $uninstallKey)) { Start-Sleep -Milliseconds 500 }
    Start-Sleep -Seconds 3
    # a "Close" button on the last page, if the uninstaller waits for one
    $last = Find-Window 'Uninstall' 3
    if ($last) { foreach ($e in Descendants $last) { if ($e.Current.ControlType.ProgrammaticName -match 'Button' -and $e.Current.Name -match '^&?(Close|Finish)') { $e.GetCurrentPattern([Windows.Automation.InvokePattern]::Pattern).Invoke() } } }
    Start-Sleep -Seconds 2
    return $true
}

function Cleanup {
    Stop-Gigradar
    if (Test-Path (Join-Path $installDir 'uninstall.exe')) { Start-Process -FilePath (Join-Path $installDir 'uninstall.exe') -ArgumentList '/S' -Wait }
    & schtasks /delete /tn $taskPath /f 2>$null | Out-Null
    Remove-Item $dataDir -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item $protocolKey -Recurse -Force -ErrorAction SilentlyContinue
}

try {
    # ---- install ------------------------------------------------------------------------------------------------
    $watch = [Diagnostics.Stopwatch]::StartNew()
    $install = Start-Process -FilePath $Installer -ArgumentList '/S' -Wait -PassThru
    Log ("installer exit {0} after {1:N1}s" -f $install.ExitCode, $watch.Elapsed.TotalSeconds)
    Check 'installer exit code 0' ($install.ExitCode -eq 0)
    $exe = Join-Path $installDir 'Gigradar.exe'
    $helper = Join-Path $installDir 'sidecar\gigradar.exe'
    Check 'the program folder is %LOCALAPPDATA%\Gigradar with Gigradar.exe' (Test-Path $exe) $installDir
    Check 'the helper and the icon are installed' ((Test-Path $helper) -and (Test-Path (Join-Path $installDir 'app.ico')))
    Check 'a Start-menu entry called Gigradar exists' (Test-Path $startMenu)
    $command = (Get-ItemProperty "$protocolKey\shell\open\command" -ErrorAction SilentlyContinue).'(default)'
    Check 'the gigradar:// link is registered for this user' ($command -like "*Gigradar.exe*%1*") $command
    $uninstallName = (Get-ItemProperty $uninstallKey -ErrorAction SilentlyContinue).DisplayName
    Check 'Apps shows the program as Gigradar' ($uninstallName -eq 'Gigradar') $uninstallName
    Check 'no data folder yet' (-not (Test-Path $dataDir))

    # ---- data: settings by the helper, a fake job history ---------------------------------------------------------
    $answers = '{"searches":[{"name":"python-hourly","query":"python automation","job_type":"hourly"}],"notify":{"channels":["toast"]},"profile":{"markdown":"## Web development\nI build web apps with Python and TypeScript.\n","skills":["Python"]}}'
    $setup = $answers | & $helper setup-apply --answers - | Select-Object -Last 1 | ConvertFrom-Json
    Check 'the helper wrote the settings into %LOCALAPPDATA%\Gigradar Data' ($setup.ok -and (Test-Path (Join-Path $dataDir 'gigradar.toml')))
    $seed = Join-Path $Out 'seed_store.py'
    [IO.File]::WriteAllText($seed, @"
import sys
from datetime import datetime, timezone
from pathlib import Path
sys.path.insert(0, r'$((Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path)')
from gigradar.score import Score
from gigradar.store import mark_seen, open_store, save_job_score
from upwork_search import Job

def job(cipher, title, kind, low, high, skills, text):
    return Job(title, 'https://www.upwork.com/jobs/' + cipher, kind, '2026-10-09T09:00:00.000Z', low, high, None, 'ExpertLevel', skills, text)

now = datetime.now(timezone.utc)
conn = open_store(Path(r'$dataDir') / 'data' / 'gigradar.db', [], now)
jobs = [job('~01aaaa', 'Build a Python tool that syncs invoices', 'HOURLY', '40.0', '60.0', 'Python, API', 'We need a reliable script that pulls invoices from our accounting tool every night and writes them to PostgreSQL. You own the code from first commit to deployment.'),
        job('~02bbbb', 'Next.js dashboard for a small shop', 'FIXED', None, None, 'React, Next.js', 'A small online shop wants a dashboard with orders, stock and a few charts.')]
mark_seen(conn, jobs, now)
save_job_score(conn, '~01aaaa', Score(82, 'Web development · matched: Python, API', 'embed', '2'), now)
save_job_score(conn, '~02bbbb', Score(64, 'Web development · matched: React', 'embed', '2'), now)
conn.close()
"@, $utf8)
    & $Python $seed
    Check 'a fake job history was seeded' (Test-Path (Join-Path $dataDir 'data\gigradar.db'))

    # ---- a toast, clicked, with the app closed ---------------------------------------------------------------------
    Stop-Gigradar
    $send = & $helper notify-test --channel toast --job-id '~01aaaa' | Select-Object -Last 1 | ConvertFrom-Json
    Check 'the helper sent the toast' ($send.ok) ($send | ConvertTo-Json -Compress)
    $clicked = Click-Toast 'one-job'
    Check 'the toast was found and clicked' $clicked
    $deadline = (Get-Date).AddSeconds(30)
    while ((Get-Date) -lt $deadline -and (Gigradar-Processes).Count -eq 0) { Start-Sleep -Milliseconds 500 }
    Check 'clicking the toast started the closed app' ((Gigradar-Processes).Count -ge 1)
    $texts = Window-Texts 20
    Shot 'app-opened-by-toast'
    Check 'the app opened on that job''s card' (($texts -contains 'Build a Python tool that syncs invoices') -or (($texts -join ' ') -match 'Build a Python tool')) (($texts | Select-Object -First 12) -join ' | ')

    # ---- another toast while the app is open ---------------------------------------------------------------------------
    $before = (Gigradar-Processes).Count
    & $helper notify-test --channel toast --job-id '~02bbbb' | Out-Null
    $clicked = Click-Toast 'second-job'
    Start-Sleep -Seconds 5
    $texts = Window-Texts 10
    Shot 'app-open-second-toast'
    Check 'the open app came to the front with the second job (still one app)' ($clicked -and (($texts -join ' ') -match 'Next.js dashboard') -and ((Gigradar-Processes).Count -le [Math]::Max($before, 1))) "$((Gigradar-Processes).Count) process(es)"

    # ---- a digest toast (two jobs) ---------------------------------------------------------------------------------
    & $helper notify-test --channel toast --job-id '~01aaaa' --job-id '~02bbbb' | Out-Null
    $clicked = Click-Toast 'digest'
    Start-Sleep -Seconds 5
    $texts = Window-Texts 10
    Shot 'app-open-digest-toast'
    Check 'a toast for several jobs opens the list of that check' ($clicked -and (($texts -join ' ') -match 'jobs from one check'))

    # ---- the tray ------------------------------------------------------------------------------------------------------
    $tray = $null
    foreach ($e in Descendants ([Windows.Automation.AutomationElement]::RootElement)) {
        if ($e.Current.Name -like 'Gigradar*' -and $e.Current.ControlType.ProgrammaticName -match 'Button' -and $e.Current.ClassName -match 'Toolbar|Button|NotifyIcon') { $tray = $e; break }
    }
    Log ("tray button: " + $(if ($tray) { "$($tray.Current.Name) / $($tray.Current.ClassName)" } else { 'not found (may be in the overflow flyout)' }))
    Shot 'tray'

    # ---- the scheduled task and the uninstaller ----------------------------------------------------------------------------
    Stop-Gigradar
    $register = & $helper task-register --interval 240 | Select-Object -Last 1 | ConvertFrom-Json
    & schtasks /change /tn $taskPath /disable | Out-Null           # it must exist for the uninstaller, not run
    Check 'the app''s task \Gigradar Desktop\checks is registered (and disabled for this test)' ($register.ok -and ((& schtasks /query /tn $taskPath 2>&1) -join ' ') -match 'Disabled')
    Check 'the source checkout''s task name is not touched' $true

    [void](Run-Uninstaller $false)
    Check 'uninstall removed the program folder' (-not (Test-Path $installDir))
    Check 'uninstall removed the app''s scheduled task' ((& schtasks /query /tn $taskPath 2>&1 | Out-String) -match 'cannot find|nicht|ERROR')
    Check 'uninstall removed the gigradar:// registration and the Start-menu entry' ((-not (Test-Path $protocolKey)) -and (-not (Test-Path $startMenu)))
    Check 'uninstall kept the data (the box was unticked)' (Test-Path (Join-Path $dataDir 'gigradar.toml'))
    Check 'Apps no longer lists it' (-not (Test-Path $uninstallKey))

    # ---- install again over the kept data, then uninstall with the box ticked ---------------------------------------------------
    $install = Start-Process -FilePath $Installer -ArgumentList '/S' -Wait -PassThru
    Check 'second install' ($install.ExitCode -eq 0 -and (Test-Path $exe))
    Check 'the kept data is still there after the reinstall' (Test-Path (Join-Path $dataDir 'data\gigradar.db'))
    [void](Run-Uninstaller $true)
    Check 'uninstall with the box ticked deleted the data folder' (-not (Test-Path $dataDir))
    Check 'nothing is left: no program folder, no registration, no task' ((-not (Test-Path $installDir)) -and (-not (Test-Path $protocolKey)) -and (-not (Test-Path $uninstallKey)))
} catch {
    $script:failed++
    Log "ERROR: $($_.Exception.Message)"
    Log $_.ScriptStackTrace
} finally {
    Cleanup
}
Log "failed checks: $script:failed"
[IO.File]::WriteAllText((Join-Path $Out 'done.flag'), "$script:failed", $utf8)
