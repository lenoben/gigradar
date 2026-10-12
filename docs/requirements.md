# Requirements

## To use the desktop app

| | |
|---|---|
| **Operating system** | Windows 10 or 11, 64-bit. There is no macOS or Linux build; the hidden-browser part uses WebView2. |
| **Rights** | A normal user. No administrator needed to install, run or uninstall. |
| **WebView2** | The Microsoft Edge WebView2 runtime. Windows 11 has it; the installer adds it on a PC that lacks it (needs internet during the install). |
| **Internet** | A **home (residential) connection**. Upwork's Cloudflare blocks datacenter, VPS and many VPN addresses. A proxy is supported only for the command line of a source checkout, not for the app. |
| **PC state** | Checks run only while you are logged on (the scheduled task is interactive) and the PC is awake. Nothing is lost while it is off: new jobs are found at the next check. |
| **Disk** | The installer is about 60 MB; the bundled helper program is about 160 MB installed; the scoring model is 67 MB (downloaded once); the job history grows with the retention setting (default one month). |
| **CPU and memory** | A check starts a hidden browser page and, when there are new jobs, loads a small ONNX model: a few seconds of CPU per check. No GPU. |
| **Upwork account** | **None.** Gigradar reads the public visitor view only. |
| **Telegram** | Optional. You create your own bot with @BotFather (free); it messages only your own chat. |

Limits by design: up to 5 searches, a check at most every 30 minutes, personal volume.

## Building

| | |
|---|---|
| **Git** | to clone the repository. |
| **Python** | 3.11 or newer (CI and development use 3.12). |
| **Node.js** | 22 (Vite, Svelte 5, vitest; `npm ci` in `app/`). |
| **Rust** | the stable toolchain with the **MSVC** target (`x86_64-pc-windows-msvc`) and the Visual Studio Build Tools "Desktop development with C++" workload. |
| **Tauri CLI** | comes with `npm ci` (it is in `app/package.json`); Tauri fetches NSIS on the first build. |
| **Python packages** | `curl_cffi`, `requirements-webview.txt` (pywebview), `requirements-toast.txt` (windows-toasts), `requirements-scoring.txt` (fastembed), `requirements-build.txt` (PyInstaller). All pinned. |
| **Optional packages** | `requirements-profile.txt` (import a profile from documents). Not needed to run or build the app. |
| **Time and CPU** | The helper build takes a few minutes; the first Tauri build takes longer. Avoid building close to a scheduled check on the same PC. |

The Python tests run offline (`python -m unittest discover -s tests`): no network, no Upwork, no Telegram.

## Source checkout without the app

Python 3.11+ and the packages above. `upwork_search.py` alone needs only `curl_cffi` and runs on any OS. The
scheduled watcher (`gigradar.watch`) with the WebView2 search backend, toasts and the Task Scheduler script is
Windows only.

The web UI in `web/` needs Node 20+ and the repo-root virtual environment (see the [README](../README.md#web-ui)).
