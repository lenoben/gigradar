"""Run: .venv/Scripts/python -m unittest discover -s tests   (Windows + windows-toasts + onnxruntime; else skipped)

DLL load order: windows-toasts' `winrt` bundles an old MSVCP140.dll; loaded before onnxruntime it makes
onnxruntime crash the process (access violation). See gigradar.embed.preload_runtime. Each order runs
in its own subprocess, because the crash kills the interpreter."""

import importlib.util
import subprocess
import sys
import unittest

HAVE_BOTH = (sys.platform == "win32" and importlib.util.find_spec("windows_toasts") is not None
             and importlib.util.find_spec("onnxruntime") is not None)


def cli(*argv: str) -> int:
    return subprocess.run([sys.executable, "-m", "gigradar.cli", "selftest", *argv], capture_output=True, timeout=120).returncode


def run(code: str) -> int:
    return subprocess.run([sys.executable, "-c", code], capture_output=True, timeout=120).returncode


@unittest.skipUnless(HAVE_BOTH, "needs Windows with windows-toasts and onnxruntime installed")
class DllLoadOrderTest(unittest.TestCase):
    def test_preload_before_toasts_is_safe(self) -> None:
        code = "from gigradar.embed import preload_runtime; preload_runtime(); import windows_toasts, onnxruntime"
        self.assertEqual(run(code), 0)

    def test_toasts_first_is_the_hazard_preload_exists_for(self) -> None:
        if run("import windows_toasts, onnxruntime") == 0:
            self.skipTest("this machine does not crash toasts-then-onnxruntime: the preload is harmless but not needed")
        # Crashed as expected; the safe order above must still work, which proves the preload is what matters.
        self.assertEqual(run("import onnxruntime, windows_toasts"), 0)

    def test_selftest_command_preload_order_passes_and_matches_the_raw_orders(self) -> None:
        # The same orders through the command a packaged build ships (see packaging/windows/check-frozen.ps1).
        self.assertEqual(cli("--order", "preload"), 0)
        self.assertEqual(cli("--order", "onnx-first"), 0)
        self.assertEqual(cli("--order", "toasts-first") == 0, run("import windows_toasts, onnxruntime") == 0)


if __name__ == "__main__":
    unittest.main()
