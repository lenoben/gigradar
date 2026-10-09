<#
.SYNOPSIS
    Installs the Tauri installer in a fresh Windows Sandbox (no WebView2 runtime, no Python) and checks that the
    installer brings the runtime, the app starts and the bundled helper runs. Results: <Out>\install.log (+ a screenshot).
.DESCRIPTION
    Host:    powershell -ExecutionPolicy Bypass -File app\e2e\sandbox-install.ps1 -Installer <...-setup.exe> -Out C:\temp\gg-install
    Inside:  the same script with -Inside (started by the .wsb LogonCommand).
#>
[CmdletBinding()]
param(
    [string] $Installer = '',
    [Parameter(Mandatory)] [string] $Out,
    [switch] $Inside,
    [int] $TimeoutMinutes = 20
)
$ErrorActionPreference = 'Stop'
$utf8 = New-Object Text.UTF8Encoding($false)

if (-not $Inside) {
    $installerFile = (Resolve-Path $Installer).Path
    New-Item -ItemType Directory -Force $Out | Out-Null
    $outDir = (Resolve-Path $Out).Path
    Remove-Item (Join-Path $outDir 'done.flag'), (Join-Path $outDir 'install.log') -ErrorAction SilentlyContinue
    $config = @"
<Configuration>
  <MappedFolders>
    <MappedFolder><HostFolder>$(Split-Path $installerFile)</HostFolder><SandboxFolder>C:\installer</SandboxFolder><ReadOnly>true</ReadOnly></MappedFolder>
    <MappedFolder><HostFolder>$PSScriptRoot</HostFolder><SandboxFolder>C:\scripts</SandboxFolder><ReadOnly>true</ReadOnly></MappedFolder>
    <MappedFolder><HostFolder>$outDir</HostFolder><SandboxFolder>C:\out</SandboxFolder><ReadOnly>false</ReadOnly></MappedFolder>
  </MappedFolders>
  <LogonCommand>
    <Command>powershell -ExecutionPolicy Bypass -File C:\scripts\sandbox-install.ps1 -Inside -Out C:\out -Installer "C:\installer\$(Split-Path $installerFile -Leaf)"</Command>
  </LogonCommand>
</Configuration>
"@
    $wsb = Join-Path $outDir 'install.wsb'
    [IO.File]::WriteAllText($wsb, $config, $utf8)
    Get-Process -Name WindowsSandbox, WindowsSandboxClient, WindowsSandboxRemoteSession -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
    Start-Process -FilePath $wsb
    $deadline = (Get-Date).AddMinutes($TimeoutMinutes)
    while (-not (Test-Path (Join-Path $outDir 'done.flag')) -and (Get-Date) -lt $deadline) { Start-Sleep -Seconds 10 }
    Get-Process -Name WindowsSandbox, WindowsSandboxClient, WindowsSandboxRemoteSession -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
    if (Test-Path (Join-Path $outDir 'done.flag')) { Get-Content (Join-Path $outDir 'install.log') } else { Write-Host 'TIMEOUT' }
    return
}

# ---- inside the sandbox ----
$log = Join-Path $Out 'install.log'
$script:failed = 0
function Log([string] $text) { [IO.File]::AppendAllText($log, $text + "`r`n", $utf8) }
function Check([string] $what, [bool] $pass) { if (-not $pass) { $script:failed++ }; Log ("CHECK {0}: {1}" -f $(if ($pass) { 'PASS' } else { 'FAIL' }), $what) }
function Runtime-Version {
    $key = '{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}'
    foreach ($path in @("HKCU:\SOFTWARE\Microsoft\EdgeUpdate\Clients\$key", "HKLM:\SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\$key")) {
        $value = (Get-ItemProperty -Path $path -ErrorAction SilentlyContinue).pv
        if ($value) { return $value }
    }
    return $null
}

try {
    [IO.File]::WriteAllText($log, '', $utf8)
    Log "windows: $([Environment]::OSVersion.VersionString)   python on PATH: $([bool](Get-Command python -ErrorAction SilentlyContinue))"
    Check 'WebView2 runtime is absent before the install' ($null -eq (Runtime-Version))

    $watch = [Diagnostics.Stopwatch]::StartNew()
    $install = Start-Process -FilePath $Installer -ArgumentList '/S' -Wait -PassThru
    Log ("installer exit {0} after {1:N1}s" -f $install.ExitCode, $watch.Elapsed.TotalSeconds)
    Check 'installer exit code 0' ($install.ExitCode -eq 0)
    $version = Runtime-Version
    Log "WebView2 runtime after the install: $(if ($version) { $version } else { 'none' })"
    Check 'the installer installed the WebView2 runtime' ($null -ne $version)

    $dir = Join-Path $env:LOCALAPPDATA 'Gigradar Desktop'
    $exe = Get-ChildItem $dir -Filter '*.exe' -File -ErrorAction SilentlyContinue | Where-Object { $_.Name -notmatch 'uninstall' } | Select-Object -First 1
    Log "install folder: $dir ($((Get-ChildItem $dir -ErrorAction SilentlyContinue | Select-Object -Expand Name) -join ', '))"
    Check 'the app exe is installed' ($null -ne $exe)
    $helper = Join-Path $dir 'sidecar\gigradar.exe'
    Check 'the helper is installed next to it' (Test-Path $helper)

    $env:GIGRADAR_HOME = 'C:\gg-home'
    $env:GIGRADAR_TASK_NAME = 'gigradar-app-test'
    $app = Start-Process -FilePath $exe.FullName -PassThru
    Start-Sleep -Seconds 20
    $app.Refresh()
    Log "app window title: '$($app.MainWindowTitle)'  running: $(-not $app.HasExited)"
    Check 'the app is running with a window' ((-not $app.HasExited) -and $app.MainWindowTitle -eq 'gigradar')
    Add-Type -AssemblyName System.Windows.Forms, System.Drawing
    $bounds = [Windows.Forms.SystemInformation]::VirtualScreen
    $bitmap = New-Object Drawing.Bitmap $bounds.Width, $bounds.Height
    [Drawing.Graphics]::FromImage($bitmap).CopyFromScreen($bounds.Location, [Drawing.Point]::Empty, $bounds.Size)
    $bitmap.Save((Join-Path $Out 'sandbox-app.png'), [Drawing.Imaging.ImageFormat]::Png)

    $doctor = & $helper doctor --offline 2>&1 | Where-Object { $_ -like '{*' } | Select-Object -Last 1 | ConvertFrom-Json
    Log "helper doctor: status $($doctor.status), first check $($doctor.checks[0].name) $($doctor.checks[0].status)"
    Check 'the installed helper answers' ($null -ne $doctor)
    if (-not $app.HasExited) { $app.Kill() }
} catch {
    $script:failed++
    Log "ERROR: $($_.Exception.Message)"
}
Log "failed checks: $script:failed"
[IO.File]::WriteAllText((Join-Path $Out 'done.flag'), "$script:failed", $utf8)
