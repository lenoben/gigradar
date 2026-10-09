# Packaging spike: gigradar as a Windows app folder (PyInstaller)

The CLI (`docs/app-core.md`) frozen into one folder that runs without Python. This is the spike result,
not a release process: no installer, no code signing, no auto-update.

## Build and check

    pip install -r requirements-build.txt           # into the build venv only
    powershell -ExecutionPolicy Bypass -File packaging\windows\build.ps1
    powershell -ExecutionPolicy Bypass -File packaging\windows\check-frozen.ps1 -AppDir dist\gigradar [-ModelDir <home>\models]

`dist\gigradar\` holds two launchers over one `_internal\` folder:

- `gigradar.exe`: console build. The app reads its JSON stream from stdout; use it for hand testing.
- `gigradarw.exe`: windowless build for the scheduled task (no console window every 30 minutes). It has no
  stdout; `run-once` logs to `<home>/logs/gigradar.log`. From PowerShell, start it with `Start-Process -Wait`
  (a windowless exe is otherwise not waited for).

The scoring model (67 MB) is not bundled; `model-download` fetches it into the app home.

Do not build while the scheduled task starts (`:23` and `:53`): the build saturates the CPU, WebView2 then
misses pywebview's 20 s start window and that run fails with "Main window failed to start". It is retried at
the next run and loses nothing, but it is noise.

## The load-order check

`windows-toasts` pulls in `winrt`, which bundles an old `MSVCP140.dll`; loaded before `onnxruntime` it kills
the process (access violation). gigradar avoids it with `embed.preload_runtime()` (see the 2026-10-08 incident).
The frozen build has the same hazard, so it is checked there too: `gigradar selftest --order <preload |
onnx-first | toasts-first> [--model-dir DIR]`.

| Order | Meaning | Frozen result |
|---|---|---|
| `preload` | what gigradar does (preload, then toasts); loads the model and embeds a text | passes |
| `onnx-first` | onnxruntime, then toasts | passes |
| `toasts-first` | the hazard | crashes (exit 0xC0000005), as from source |

`check-frozen.ps1` fails if the first two do not pass. The hazard also hits the build itself: PyInstaller
imports every collected package in isolated child processes, winrt before onnxruntime, and the child dies.
`packaging/windows/buildenv/sitecustomize.py` (on `PYTHONPATH` only during the build) imports onnxruntime first.

## Measurements (this PC, Windows 11 Pro, Python 3.12.0, PyInstaller 6.22.3)

| | |
|---|---|
| Build time | 2.5 to 4 min (more when the PC is busy) |
| Folder size | 158 MB (onnxruntime 36, numpy.libs 21, PIL 11, cryptography 10, hf_xet 9, tokenizers 8) |
| `doctor --offline` | about 1.0 s warm, 1.3 to 2.4 s cold |
| `selftest preload` incl. model load | 2.7 to 6.8 s (cold start slowest) |
| `model-download` | 10 to 16 s on this connection (67 MB) |
| `run-once`, 3 new jobs scored | model load 2.2 s, scoring 5.4 s, whole run 19 to 26 s (fresh WebView2 profile) |

## Problems hit

1. PyInstaller's analysis crashed (0xC0000005) on the load-order bug; also `collect_all("onnxruntime")` pulled in
   quantization and training code. Fixed as above and with `excludes`.
2. A pipe uses the ANSI code page: job text with emoji raised `UnicodeEncodeError`. The CLI now sets stdin and
   stdout to UTF-8.
3. Windows PowerShell 5.1 writes a byte order mark with `Set-Content -Encoding utf8`; `json.loads` rejects it.
   `setup-apply` and `telegram-connect` strip it.
4. `upwork-check` on a cold browser profile: one attempt got "fetch() gave no result within 30s" (the page was
   still navigating through Cloudflare's check), another "Main window failed to start" (WebView2 slow on a busy
   PC). Now: three attempts in the same window, 8 s apart; a window that does not start returns the error code
   `window_failed` so the UI can offer a retry. Without these, two of four cold-profile attempts passed on the
   first go (never a click needed).
5. A windowless exe returns at once in PowerShell: tests must use `Start-Process -Wait`.
6. Windows Sandbox was not available on the test PC (the feature is off; enabling it needs administrator rights
   and a reboot), and a fresh Windows user needs administrator rights too. The clean-machine test was therefore
   approximated: the folder copied elsewhere, PATH reduced to the Windows folders (no Python), a new app home
   and a new WebView2 profile. Not covered: a PC without the Visual C++ runtime or the WebView2 runtime,
   SmartScreen and antivirus reaction to an unsigned exe, a different CPU. `sandbox-test.ps1` runs the same
   steps (`smoke.ps1`) in Windows Sandbox once it is enabled; it has not been run.

## Not done

Installer, signing, auto-update, trimming the folder (PIL and cryptography come from fastembed's and
huggingface_hub's dependencies), and registering the scheduled task for the packaged app (the task script still
starts the venv's `pythonw -m gigradar.watch`).
