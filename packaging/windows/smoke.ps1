<#
.SYNOPSIS
    End-to-end smoke test of the packaged app on a machine without Python: setup, model download,
    notification, Upwork check, one run. Writes every JSON line to -Out\smoke.log.
.DESCRIPTION
    Uses a throw-away app home (-AppHome); never touches an existing install. upwork-check opens a window
    (solve Cloudflare's check if shown). Used by sandbox-test.ps1 inside Windows Sandbox, or by hand:
    powershell -ExecutionPolicy Bypass -File packaging\windows\smoke.ps1 -AppDir dist\gigradar -AppHome C:\temp\gr-home -Out C:\temp\gr-out
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)] [string] $AppDir,
    [Parameter(Mandatory)] [string] $AppHome,
    [Parameter(Mandatory)] [string] $Out
)
$ErrorActionPreference = 'Stop'
New-Item -ItemType Directory -Force $AppHome, $Out | Out-Null
$exe = Join-Path (Resolve-Path $AppDir).Path 'gigradar.exe'
$log = Join-Path $Out 'smoke.log'
$env:GIGRADAR_HOME = (Resolve-Path $AppHome).Path
$answers = Join-Path $Out 'answers.json'
$json = @'
{"searches":[{"name":"py","query":"python","job_type":"hourly","tier":"expert"}],
 "notify":{"channels":["toast"]},
 "profile":{"markdown":"## Web\nI build web apps with Python and TypeScript.\n","skills":["Python","TypeScript"],"min_hourly":40},
 "scoring":{"min_score":0}}
'@
[IO.File]::WriteAllText($answers, $json, (New-Object Text.UTF8Encoding($false)))

function Step([string] $name, [string[]] $arguments) {
    $ErrorActionPreference = 'Continue'   # the app writes progress bars to stderr; that is not a failure
    $watch = [Diagnostics.Stopwatch]::StartNew()
    $lines = & $exe @arguments 2>&1 | ForEach-Object { "$_" }
    $code = $LASTEXITCODE
    $entry = "== {0}: exit {1}, {2:N1}s" -f $name, $code, $watch.Elapsed.TotalSeconds
    Write-Host $entry
    Add-Content -Path $log -Value (@($entry) + $lines) -Encoding utf8
    return $code
}

"python on PATH: $([bool](Get-Command python -ErrorAction SilentlyContinue))" | Tee-Object -FilePath $log
$worst = 0
foreach ($step in @(
        @('setup-apply', @('setup-apply', '--answers', $answers)),
        @('doctor (before)', @('doctor', '--offline')),
        @('model-download', @('model-download')),
        @('notify-test (toast)', @('notify-test')),
        @('upwork-check', @('upwork-check')),
        @('run-once', @('run-once')),
        @('doctor (after)', @('doctor', '--offline')))) {
    $code = Step $step[0] $step[1]
    if ($code -ne 0 -and $step[0] -notlike 'doctor*') { $worst = 1 }
}
exit $worst
