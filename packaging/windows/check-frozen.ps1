<#
.SYNOPSIS
    Check that the packaged app keeps the native-library load order that scoring needs.
.DESCRIPTION
    windows-toasts' winrt bundles an old MSVCP140.dll; loaded before onnxruntime it crashes the process
    (see gigradar.embed.preload_runtime). Runs `gigradar selftest` once per import order:
      preload       what gigradar really does (preload_runtime, then toasts)         MUST pass
      onnx-first    onnxruntime, then toasts                                         MUST pass
      toasts-first  the hazard; on an affected machine it crashes, which shows the check can fail (info)
    With -ModelDir each run also loads the cached model and embeds one text (the step that crashed in the
    2026-10-08 incident). Exit code 1 if a MUST-pass order fails.
.EXAMPLE
    powershell -ExecutionPolicy Bypass -File packaging\windows\check-frozen.ps1 -AppDir dist\gigradar -ModelDir <home>\models
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)] [string] $AppDir,
    [string] $ModelDir = ''
)
$ErrorActionPreference = 'Stop'
$exe = Join-Path (Resolve-Path $AppDir).Path 'gigradar.exe'
$failed = $false
foreach ($order in 'preload', 'onnx-first', 'toasts-first') {
    $arguments = @('selftest', '--order', $order)
    if ($ModelDir) { $arguments += @('--model-dir', $ModelDir) }
    $output = & $exe @arguments 2>&1 | Out-String
    $code = $LASTEXITCODE
    $mustPass = $order -ne 'toasts-first'
    $verdict = if ($code -eq 0) { 'ok' } elseif ($mustPass) { 'FAILED' } else { 'crashed (the known hazard)' }
    Write-Host ("{0,-13} exit {1,-11} {2}" -f $order, $code, $verdict)
    if ($mustPass -and $code -ne 0) { $failed = $true; Write-Host $output }
}
if ($failed) { exit 1 }
