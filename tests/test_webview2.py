"""Run: .venv/Scripts/python -m unittest discover -s tests   (offline: a fake registry)"""

import unittest

from gigradar import webview2
from gigradar.tokens import StopRun

RUNTIME = webview2.CLIENT_KEYS[0]
USER = rf"SOFTWARE\Microsoft\EdgeUpdate\Clients\{RUNTIME}"
MACHINE = rf"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{RUNTIME}"
MACHINE_32 = rf"SOFTWARE\Microsoft\EdgeUpdate\Clients\{RUNTIME}"


def registry(values: dict[tuple[str, str], str]):
    return lambda hive, subkey: values.get((hive, subkey))


class InstalledVersionTest(unittest.TestCase):
    def test_nothing_installed(self) -> None:
        self.assertIsNone(webview2.installed_version(registry({})))

    def test_machine_wide_and_per_user_installs(self) -> None:
        self.assertEqual(webview2.installed_version(registry({("HKLM", MACHINE): "154.0.3.2"})), "154.0.3.2")
        self.assertEqual(webview2.installed_version(registry({("HKCU", USER): "120.0.1.0"})), "120.0.1.0")
        self.assertEqual(webview2.installed_version(registry({("HKLM", MACHINE_32): "110.0.1.0"})), "110.0.1.0")

    def test_the_newest_install_wins_and_is_compared_numerically(self) -> None:
        values = {("HKCU", USER): "99.0.1.0", ("HKLM", MACHINE): "154.0.3.2"}
        self.assertEqual(webview2.installed_version(registry(values)), "154.0.3.2")

    def test_a_placeholder_garbage_or_too_old_version_does_not_count(self) -> None:
        for value in ("0.0.0.0", "", "not-a-version", "85.0.1.0"):
            self.assertIsNone(webview2.installed_version(registry({("HKLM", MACHINE): value})), value)

    def test_the_preview_channels_count_too(self) -> None:
        beta = rf"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{webview2.CLIENT_KEYS[1]}"
        self.assertEqual(webview2.installed_version(registry({("HKLM", beta): "130.0.0.1"})), "130.0.0.1")


class RequireTest(unittest.TestCase):
    def test_returns_the_version(self) -> None:
        self.assertEqual(webview2.require(registry({("HKLM", MACHINE): "154.0.3.2"})), "154.0.3.2")

    def test_missing_runtime_stops_at_once_with_the_download_link(self) -> None:
        with self.assertRaises(webview2.WebView2Missing) as caught:
            webview2.require(registry({}))
        self.assertIn(webview2.EVERGREEN_URL, str(caught.exception))
        self.assertTrue(webview2.EVERGREEN_URL.startswith("https://go.microsoft.com/"))

    def test_it_is_a_stop_run_so_a_scheduled_run_ends_without_a_crash(self) -> None:
        self.assertTrue(issubclass(webview2.WebView2Missing, StopRun))


if __name__ == "__main__":
    unittest.main()
