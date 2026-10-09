<#
.SYNOPSIS
    Run smoke.ps1 inside Windows Sandbox (a clean, throw-away Windows: no Python, nothing installed).
.DESCRIPTION
    Needs the "Windows Sandbox" optional feature (Windows Pro/Enterprise; enable it once as administrator:
    Enable-WindowsOptionalFeature -Online -FeatureName Containers-DisposableClientVM, then reboot).
    Two scenarios, each in its own fresh sandbox (results in <Out>\<scenario>\smoke.log, UTF-8):
      missing   : no WebView2 runtime -> doctor FAIL, upwork-check/run-once fail fast
      installed : install Microsoft's Evergreen bootstrapper first, then the whole flow
    Maps dist\gigradar and packaging\windows read-only and the scenario's out folder read-write.
    -Screenshots saves the host screen every 20 s to <Out>\<scenario>\shots (to see what Windows shows).
    Run it away from the scheduled task's :23 and :53, it is CPU heavy.
#>
[CmdletBinding()]
param(
    [string] $AppDir = '',
    [Parameter(Mandatory)] [string] $Out,
    [ValidateSet('missing', 'installed', 'both')] [string] $Scenario = 'both',
    [switch] $Screenshots,
    [int] $TimeoutMinutes = 25
)
$ErrorActionPreference = 'Stop'
$Root = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$app = if ($AppDir) { (Resolve-Path $AppDir).Path } else { Join-Path $Root 'dist\gigradar' }
New-Item -ItemType Directory -Force $Out | Out-Null
Add-Type -AssemblyName System.Windows.Forms, System.Drawing

function Stop-Sandbox {
    Get-Process -Name WindowsSandbox, WindowsSandboxClient, WindowsSandboxRemoteSession -ErrorAction SilentlyContinue |
        Stop-Process -Force -ErrorAction SilentlyContinue
    for ($i = 0; $i -lt 30 -and (Get-Process -Name WindowsSandboxClient, WindowsSandboxRemoteSession -ErrorAction SilentlyContinue); $i++) {
        Start-Sleep -Seconds 2
    }
}

function Shot([string] $dir) {
    $bounds = [Windows.Forms.SystemInformation]::VirtualScreen
    $bitmap = New-Object Drawing.Bitmap $bounds.Width, $bounds.Height
    $graphics = [Drawing.Graphics]::FromImage($bitmap)
    $graphics.CopyFromScreen($bounds.Location, [Drawing.Point]::Empty, $bounds.Size)
    $bitmap.Save((Join-Path $dir ('{0:HHmmss}.png' -f (Get-Date))), [Drawing.Imaging.ImageFormat]::Png)
    $graphics.Dispose(); $bitmap.Dispose()
}

function Run-Scenario([string] $name) {
    $dir = Join-Path (Resolve-Path $Out).Path $name
    Remove-Item -Recurse -Force $dir -ErrorAction SilentlyContinue
    New-Item -ItemType Directory -Force (Join-Path $dir 'shots') | Out-Null
    $config = @"
<Configuration>
  <MappedFolders>
    <MappedFolder><HostFolder>$app</HostFolder><SandboxFolder>C:\gigradar-app</SandboxFolder><ReadOnly>true</ReadOnly></MappedFolder>
    <MappedFolder><HostFolder>$PSScriptRoot</HostFolder><SandboxFolder>C:\gigradar-scripts</SandboxFolder><ReadOnly>true</ReadOnly></MappedFolder>
    <MappedFolder><HostFolder>$dir</HostFolder><SandboxFolder>C:\out</SandboxFolder><ReadOnly>false</ReadOnly></MappedFolder>
  </MappedFolders>
  <LogonCommand>
    <Command>powershell -ExecutionPolicy Bypass -File C:\gigradar-scripts\smoke.ps1 -AppDir C:\gigradar-app -AppHome C:\gigradar-home -Out C:\out -Scenario $name</Command>
  </LogonCommand>
</Configuration>
"@
    $file = Join-Path $dir 'gigradar.wsb'
    [IO.File]::WriteAllText($file, $config, (New-Object Text.UTF8Encoding($false)))
    Stop-Sandbox
    Start-Process -FilePath $file
    Write-Host "[$name] sandbox started; waiting for $dir\done.flag"
    $deadline = (Get-Date).AddMinutes($TimeoutMinutes)
    while (-not (Test-Path (Join-Path $dir 'done.flag')) -and (Get-Date) -lt $deadline) {
        if ($Screenshots) { Shot (Join-Path $dir 'shots') }
        Start-Sleep -Seconds 20
    }
    if (Test-Path (Join-Path $dir 'done.flag')) {
        if ($Screenshots) { Shot (Join-Path $dir 'shots') }
        Write-Host "[$name] done, failed checks: $(Get-Content (Join-Path $dir 'done.flag'))"
    } else {
        Write-Host "[$name] TIMEOUT after $TimeoutMinutes minutes"
    }
    Stop-Sandbox
}

foreach ($name in $(if ($Scenario -eq 'both') { @('missing', 'installed') } else { @($Scenario) })) { Run-Scenario $name }
Write-Host "Results: $Out\<scenario>\smoke.log"
