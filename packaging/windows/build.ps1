<#
.SYNOPSIS
    Build the packaged app (PyInstaller onedir) into dist\gigradar\ and report its size.
.DESCRIPTION
    Run from a checkout whose .venv has the runtime requirements and requirements-build.txt. Output:
    dist\gigradar\gigradar.exe (console) and gigradarw.exe (windowless), plus _internal\.
#>
$ErrorActionPreference = 'Stop'
$Root = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
Set-Location $Root
$python = Join-Path $Root '.venv\Scripts\python.exe'
$watch = [System.Diagnostics.Stopwatch]::StartNew()
# Load order for PyInstaller's isolated import children: see buildenv\sitecustomize.py
$env:PYTHONPATH = Join-Path $Root 'packaging\windows\buildenv'
& $python -m PyInstaller --noconfirm --clean --distpath (Join-Path $Root 'dist') --workpath (Join-Path $Root 'build') (Join-Path $Root 'packaging\windows\gigradar.spec')
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed ($LASTEXITCODE)" }
$bytes = (Get-ChildItem (Join-Path $Root 'dist\gigradar') -Recurse -File | Measure-Object Length -Sum).Sum
Write-Host ("Built dist\gigradar in {0:N0} s, {1:N0} MB" -f $watch.Elapsed.TotalSeconds, ($bytes / 1MB))
