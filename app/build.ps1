<#
.SYNOPSIS
    Build the desktop app: the PyInstaller helper (packaging\windows), then the Tauri NSIS installer.
.DESCRIPTION
    1. packaging\windows\build.ps1 -> dist\gigradar (the helper program, onedir; skip with -SkipHelper)
    2. copy it to app\src-tauri\resources\sidecar (Tauri bundles that folder as resources)
    3. npm ci + tauri build -> app\src-tauri\target\release\bundle\nsis\*.exe (per-user installer; it installs the
       WebView2 runtime when missing: bundle.windows.webviewInstallMode)
    Needs the repo's .venv (runtime + requirements-build.txt), Node 22, Rust with the MSVC toolchain.
    CPU heavy: do not run it close to a scheduled watcher run (:23 / :53 on the live PC).
#>
[CmdletBinding()]
param([switch] $SkipHelper)
$ErrorActionPreference = 'Stop'
$App = $PSScriptRoot
$Root = (Resolve-Path (Join-Path $App '..')).Path

if (-not $SkipHelper) {
    & powershell -ExecutionPolicy Bypass -File (Join-Path $Root 'packaging\windows\build.ps1')
    if ($LASTEXITCODE -ne 0) { throw "helper build failed ($LASTEXITCODE)" }
}
$helper = Join-Path $Root 'dist\gigradar'
if (-not (Test-Path (Join-Path $helper 'gigradar.exe'))) { throw "$helper\gigradar.exe is missing: build the helper first" }

$target = Join-Path $App 'src-tauri\resources\sidecar'
& robocopy $helper $target /MIR /NFL /NDL /NJH /NJS /NP | Out-Null
if ($LASTEXITCODE -ge 8) { throw "robocopy failed ($LASTEXITCODE)" }

Push-Location $App
try {
    & npm.cmd ci
    if ($LASTEXITCODE -ne 0) { throw "npm ci failed" }
    & npm.cmd run tauri -- build
    if ($LASTEXITCODE -ne 0) { throw "tauri build failed" }
} finally {
    Pop-Location
}
Get-ChildItem (Join-Path $App 'src-tauri\target\release\bundle\nsis') -Filter *.exe |
    ForEach-Object { Write-Host ("{0}  {1:N0} MB" -f $_.FullName, ($_.Length / 1MB)) }
