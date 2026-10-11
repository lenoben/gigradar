# PyInstaller spec: python -m PyInstaller packaging/windows/gigradar.spec  (see build.ps1)
# One onedir folder with two launchers sharing it:
#   gigradar.exe   console build: for the app (stdout is the JSON stream) and for hand testing
#   gigradarw.exe  windowless build: for the scheduled task (no console window flashing every 30 minutes)
# The scoring model is NOT bundled: `gigradar model-download` fetches it into the app home.
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_submodules

root = Path(SPECPATH).resolve().parents[1]

datas, binaries, hiddenimports = [], [], []
# onnxruntime is left to its PyInstaller hook: collect_all would drag in quantization/transformers/training.
for package in ("webview", "fastembed", "windows_toasts", "curl_cffi", "clr_loader", "pythonnet"):
    d, b, h = collect_all(package)
    datas += d
    binaries += b
    hiddenimports += h
# winrt ships one distribution per namespace; the toast module imports them dynamically.
hiddenimports += collect_submodules("winrt")
hiddenimports += ["upwork_search"]

analysis = Analysis(
    [str(root / "packaging" / "windows" / "entry.py")],
    pathex=[str(root)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    # Optional pieces the app never imports: the MCP server and the document importer.
    excludes=["mcp", "uvicorn", "starlette", "firecrawl_anydoc", "tkinter", "pytest",
              "onnxruntime.quantization", "onnxruntime.transformers", "onnxruntime.tools", "onnxruntime.training"],
)
pyz = PYZ(analysis.pure)

console = EXE(pyz, analysis.scripts, [], exclude_binaries=True, name="gigradar", console=True)
windowless = EXE(pyz, analysis.scripts, [], exclude_binaries=True, name="gigradarw", console=False)
COLLECT(console, windowless, analysis.binaries, analysis.datas, name="gigradar")
