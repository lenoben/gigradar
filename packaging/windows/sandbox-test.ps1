<#
.SYNOPSIS
    Run smoke.ps1 inside Windows Sandbox (a clean, throw-away Windows: no Python, nothing installed).
.DESCRIPTION
    Needs the "Windows Sandbox" optional feature (Windows Pro/Enterprise; enable it once as administrator:
    Enable-WindowsOptionalFeature -Online -FeatureName Containers-DisposableClientVM, then reboot).
    Maps dist\gigradar and packaging\windows read-only and -Out read-write. Results: <Out>\smoke.log.
    UNTESTED on the machine this was written on: the feature was not available there.
#>
[CmdletBinding()]
param(
    [string] $AppDir = '',
    [Parameter(Mandatory)] [string] $Out
)
$ErrorActionPreference = 'Stop'
$Root = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$app = if ($AppDir) { (Resolve-Path $AppDir).Path } else { Join-Path $Root 'dist\gigradar' }
New-Item -ItemType Directory -Force $Out | Out-Null
$config = @"
<Configuration>
  <MappedFolders>
    <MappedFolder><HostFolder>$app</HostFolder><SandboxFolder>C:\gigradar-app</SandboxFolder><ReadOnly>true</ReadOnly></MappedFolder>
    <MappedFolder><HostFolder>$PSScriptRoot</HostFolder><SandboxFolder>C:\gigradar-scripts</SandboxFolder><ReadOnly>true</ReadOnly></MappedFolder>
    <MappedFolder><HostFolder>$((Resolve-Path $Out).Path)</HostFolder><SandboxFolder>C:\out</SandboxFolder><ReadOnly>false</ReadOnly></MappedFolder>
  </MappedFolders>
  <LogonCommand>
    <Command>powershell -ExecutionPolicy Bypass -File C:\gigradar-scripts\smoke.ps1 -AppDir C:\gigradar-app -AppHome C:\gigradar-home -Out C:\out</Command>
  </LogonCommand>
</Configuration>
"@
$file = Join-Path $Out 'gigradar.wsb'
Set-Content -Path $file -Value $config -Encoding utf8
Start-Process -FilePath $file
Write-Host "Sandbox started. Results appear in $Out\smoke.log"
