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
   PC). Now: a page that is not ready is retried in the same window and a window that does not start is opened
   again, each at most 3 times with a 2 s / 5 s pause; the result reports `attempts`. If it still fails the
   error code is `window_failed`. In a fresh Windows Sandbox the first fetch needed a retry (Cloudflare's click
   check was shown, the user solved it) and the second attempt passed.
5. A windowless exe returns at once in PowerShell: tests must use `Start-Process -Wait`.
6. **WebView2 runtime missing** (found in Windows Sandbox, which has Edge but no WebView2 runtime): pywebview
   silently falls back to the Internet Explorer engine, which has no cookie access, so `upwork-check` failed
   after 33 s and `run-once` looped for 333 s writing 298 `ERROR` lines. Now the backend is forced to
   `edgechromium`, and `gigradar/webview2.py` reads the runtime version from the registry before any window is
   created: `upwork-check` fails in about 2 s with `webview2_missing` and Microsoft's Evergreen link, a scheduled
   run stops with exit 75 and one log line, and `doctor` has a separate `webview2_runtime` check (FAIL + link).
   The Tauri installer must install the runtime itself (`webviewInstallMode`).
7. The `model-download` size hint was hardcoded and wrong, and the Hugging Face cache holds two copies of the
   file on Windows (134 MB counted). The total now comes from the server's Content-Length and the cache's
   `blobs` folder is counted once. A fresh Windows lacked a root certificate for Python's default trust store,
   so every standard-library HTTPS call (Telegram, the size lookup; `gigradar/netutil.py`) retries once with the
   certifi bundle after a certificate error. The other HTTPS callers bring their own CA bundle: the model
   download (`huggingface_hub`, which worked in the sandbox) and the curl backend (`curl_cffi`). Tested with a
   local HTTPS server whose CA only the certifi stand-in knows (`tests/test_netutil.py`).

## Windows Sandbox results

`sandbox-test.ps1` runs `smoke.ps1` in two fresh sandboxes (Windows 11, no Python on PATH):

| Scenario | Result |
|---|---|
| without WebView2 | `doctor` FAIL with the link; `upwork-check` `webview2_missing` in 2.5 s; `run-once` exit 75 in 3.8 s with 1 log line. 0 failed checks |
| after installing the Evergreen bootstrapper | download 1.9 MB in 8 s, silent install 90 s; `model-download` 15 s; toast test; `upwork-check` ok (1 window, 2 fetch attempts, 63 s including the click check); `run-once` ok 20 s; windowless exe ok 18 s. 0 failed checks |

Nothing about SmartScreen, a missing Visual C++ runtime or antivirus showed up in the logs. Not covered by a
real second PC: other CPUs, third-party antivirus.

## Not done

Installer, signing, auto-update, trimming the folder (PIL and cryptography come from fastembed's and
huggingface_hub's dependencies), and registering the scheduled task for the packaged app (the task script still
starts the venv's `pythonw -m gigradar.watch`).
