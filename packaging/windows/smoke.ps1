<#
.SYNOPSIS
    End-to-end smoke test of the packaged app on a machine without Python. Writes everything, UTF-8, to
    -Out\smoke.log and finishes with -Out\done.flag (the number of failed checks).
.DESCRIPTION
    Uses a throw-away app home (-AppHome); never touches an existing install.
    -Scenario missing    : WebView2 runtime is NOT installed (leave it alone, or run on a PC without it):
                           doctor must FAIL clearly, upwork-check and run-once must stop fast.
    -Scenario installed  : installs Microsoft's Evergreen bootstrapper first (needs internet), then runs the
                           whole flow: setup, model download, toast, upwork-check, run-once, windowless exe.
    -Scenario flow       : the whole flow without installing anything (the runtime is already there).
    Used by sandbox-test.ps1 inside Windows Sandbox, or by hand:
    powershell -ExecutionPolicy Bypass -File packaging\windows\smoke.ps1 -AppDir dist\gigradar -AppHome C:\temp\gr-home -Out C:\temp\gr-out -Scenario flow
    (upwork-check opens a window: solve Cloudflare's check if it is shown.)
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)] [string] $AppDir,
    [Parameter(Mandatory)] [string] $AppHome,
    [Parameter(Mandatory)] [string] $Out,
    [ValidateSet('missing', 'installed', 'flow')] [string] $Scenario = 'flow'
)
$ErrorActionPreference = 'Stop'
$utf8 = New-Object Text.UTF8Encoding($false)
[Console]::OutputEncoding = $utf8      # the app writes UTF-8; Windows PowerShell 5.1 would decode it as ANSI
New-Item -ItemType Directory -Force $AppHome, $Out | Out-Null
$appDir = (Resolve-Path $AppDir).Path
$exe = Join-Path $appDir 'gigradar.exe'
$exeW = Join-Path $appDir 'gigradarw.exe'
$log = Join-Path $Out 'smoke.log'
$env:GIGRADAR_HOME = (Resolve-Path $AppHome).Path
$script:failed = 0

function Log([string] $text) {
    Write-Host $text
    [IO.File]::AppendAllText($log, $text + "`r`n", $utf8)
}

function Check([string] $what, [bool] $pass) {
    if (-not $pass) { $script:failed++ }
    Log ("CHECK {0}: {1}" -f $(if ($pass) { 'PASS' } else { 'FAIL' }), $what)
}

function Last-Json($lines) {
    $line = $lines | Where-Object { $_ -like '{*' } | Select-Object -Last 1
    if ($line) { return $line | ConvertFrom-Json } else { return $null }
}

# Runs gigradar.exe, logs its output (progress lines collapsed), returns code, seconds, all lines, last JSON line.
function Step([string] $name, [string[]] $arguments) {
    $ErrorActionPreference = 'Continue'   # the app writes progress bars to stderr; that is not a failure
    $watch = [Diagnostics.Stopwatch]::StartNew()
    $lines = @(& $exe @arguments 2>&1 | ForEach-Object { "$_" } | Where-Object { $_ -notmatch '^System\.Management\.Automation\.RemoteException' })
    $code = $LASTEXITCODE
    $seconds = $watch.Elapsed.TotalSeconds
    Log ("== {0}: exit {1}, {2:N1}s" -f $name, $code, $seconds)
    $progress = @($lines | Where-Object { $_ -like '*"event": "progress"*' })
    foreach ($line in ($lines | Where-Object { $_ -notlike '*"event": "progress"*' -and $_ -notmatch '^Fetching \d+ files' })) { Log $line }
    if ($progress.Count -gt 0) { Log ("   ({0} progress lines; first {1}; last {2})" -f $progress.Count, $progress[0], $progress[-1]) }
    return @{ Code = $code; Seconds = $seconds; Lines = $lines; Json = (Last-Json $lines) }
}

function Runtime-Version {
    $keys = '{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}'
    foreach ($path in @("HKCU:\SOFTWARE\Microsoft\EdgeUpdate\Clients\$keys",
                        "HKLM:\SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\$keys",
                        "HKLM:\SOFTWARE\Microsoft\EdgeUpdate\Clients\$keys")) {
        $value = (Get-ItemProperty -Path $path -ErrorAction SilentlyContinue).pv
        if ($value) { return $value }
    }
    return $null
}

function Check-Of($doctor, [string] $name) { return $doctor.Json.checks | Where-Object { $_.name -eq $name } }

function Do-Setup {
    $answers = Join-Path $Out 'answers.json'
    $json = @'
{"searches":[{"name":"py","query":"python","job_type":"hourly","tier":"expert"}],
 "notify":{"channels":["toast"]},
 "profile":{"markdown":"## Web\nI build web apps with Python and TypeScript.\n","skills":["Python","TypeScript"],"min_hourly":40},
 "scoring":{"min_score":0}}
'@
    [IO.File]::WriteAllText($answers, $json, $utf8)
    $r = Step 'setup-apply' @('setup-apply', '--answers', $answers)
    Check 'setup-apply ok' ($r.Code -eq 0)
}

[IO.File]::WriteAllText($log, '', $utf8)
Log "scenario: $Scenario   $(Get-Date -Format s)"
Log "python on PATH: $([bool](Get-Command python -ErrorAction SilentlyContinue))"
Log "windows: $([Environment]::OSVersion.VersionString)"

if ($Scenario -eq 'installed') {
    Check 'WebView2 runtime is absent before the install' ($null -eq (Runtime-Version))
    $setup = Join-Path $env:TEMP 'MicrosoftEdgeWebview2Setup.exe'
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    $watch = [Diagnostics.Stopwatch]::StartNew()
    Invoke-WebRequest -UseBasicParsing -Uri 'https://go.microsoft.com/fwlink/p/?LinkId=2124703' -OutFile $setup
    Log ("downloaded the Evergreen bootstrapper: {0:N0} bytes in {1:N1}s" -f (Get-Item $setup).Length, $watch.Elapsed.TotalSeconds)
    $watch.Restart()
    $install = Start-Process -FilePath $setup -ArgumentList '/silent', '/install' -Wait -PassThru
    Log ("bootstrapper exit {0} after {1:N1}s" -f $install.ExitCode, $watch.Elapsed.TotalSeconds)
}

$version = Runtime-Version
Log "WebView2 runtime (registry): $(if ($version) { $version } else { 'none' })"
Do-Setup

$doctor = Step 'doctor (before)' @('doctor', '--offline')
$wv2 = Check-Of $doctor 'webview2_runtime'
Log ("doctor webview2_runtime: {0} | {1} | fix: {2}" -f $wv2.status, $wv2.message, $wv2.fix)

if ($Scenario -eq 'missing') {
    Check 'registry has no WebView2 runtime' ($null -eq $version)
    Check 'doctor FAILs the webview2_runtime check' ($wv2.status -eq 'FAIL')
    Check 'doctor names the download link' ("$($wv2.fix)" -like '*go.microsoft.com*')
    $check = Step 'upwork-check' @('upwork-check')
    Check 'upwork-check fails with webview2_missing' ($check.Code -eq 1 -and $check.Json.error.code -eq 'webview2_missing')
    Check 'upwork-check failed fast (under 15 s)' ($check.Seconds -lt 15)
    Check 'the message has the download link' ("$($check.Json.error.message)" -like '*go.microsoft.com*')
    $run = Step 'run-once' @('run-once')
    Check 'run-once stops with exit 75' ($run.Code -eq 75)
    Check 'run-once stopped fast (under 30 s)' ($run.Seconds -lt 30)
    $appLog = Join-Path $env:GIGRADAR_HOME 'logs\gigradar.log'
    $mentions = @(Select-String -Path $appLog -Pattern 'WebView2').Count
    Check "no log spam ($mentions WebView2 lines in the app log)" ($mentions -le 2)
    [void](Step 'doctor (after)' @('doctor', '--offline'))
} else {
    Check 'WebView2 runtime is installed' ($null -ne $version)
    Check 'doctor reports the runtime OK' ($wv2.status -eq 'OK')
    $dl = Step 'model-download' @('model-download')
    Check 'model-download ok' ($dl.Code -eq 0)
    if ($dl.Json.bytes) { Log ("   model on disk: {0:N0} bytes" -f $dl.Json.bytes) }
    $toast = Step 'notify-test (toast)' @('notify-test')
    Check 'notify-test ok (look for the toast on screen)' ($toast.Code -eq 0)
    $check = Step 'upwork-check' @('upwork-check')
    Check 'upwork-check ok' ($check.Code -eq 0)
    $retries = @($check.Lines | Where-Object { $_ -like '*"event": "retry"*' }).Count
    Log ("upwork-check attempts: {0}; retry events: {1}" -f ($check.Json.attempts | ConvertTo-Json -Compress), $retries)
    $run = Step 'run-once' @('run-once')
    Check 'run-once ok' ($run.Code -eq 0)
    # the windowless launcher the scheduled task uses: no console, so judge it by exit code and the app log
    $appLog = Join-Path $env:GIGRADAR_HOME 'logs\gigradar.log'
    $watch = [Diagnostics.Stopwatch]::StartNew()
    $win = Start-Process -FilePath $exeW -ArgumentList 'run-once' -Wait -PassThru
    Log ("== windowless gigradarw.exe run-once: exit {0}, {1:N1}s" -f $win.ExitCode, $watch.Elapsed.TotalSeconds)
    Check 'windowless exe run-once ok' ($win.ExitCode -eq 0)
    foreach ($line in (Get-Content $appLog -Tail 6)) { Log "   log: $line" }
    [void](Step 'doctor (after)' @('doctor', '--offline'))
}

Log "failed checks: $script:failed"
[IO.File]::WriteAllText((Join-Path $Out 'done.flag'), "$script:failed", $utf8)
exit $(if ($script:failed -gt 0) { 1 } else { 0 })
