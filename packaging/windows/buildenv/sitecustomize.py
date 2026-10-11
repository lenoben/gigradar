"""Build-time only (build.ps1 puts this folder on PYTHONPATH).

PyInstaller imports every collected package in isolated child processes to find their DLLs. Those children
import windows-toasts' winrt before onnxruntime, and that order crashes the process (0xC0000005): see
gigradar.embed.preload_runtime. Importing onnxruntime first in every child makes the build pass.
"""

try:
    import onnxruntime  # noqa: F401
except ImportError:
    pass
