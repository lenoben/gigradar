"""Run: .venv/Scripts/python -m unittest discover -s tests

The app's name, id, task and data folder are written in several places that cannot import each other (Python, the
Tauri config, Rust, TypeScript, the installer's NSIS hooks). They must say the same thing."""

import json
import re
import unittest
from pathlib import Path

from gigradar import branding

APP = Path(__file__).resolve().parent.parent / "app"
CONF = json.loads((APP / "src-tauri" / "tauri.conf.json").read_text(encoding="utf-8"))
HOOKS = (APP / "src-tauri" / "windows" / "hooks.nsh").read_text(encoding="utf-8")
LIB_RS = (APP / "src-tauri" / "src" / "lib.rs").read_text(encoding="utf-8")
LINKS_RS = (APP / "src-tauri" / "src" / "links.rs").read_text(encoding="utf-8")
STRINGS_TS = (APP / "src" / "lib" / "strings.ts").read_text(encoding="utf-8")
ENGLISH_NSH = (APP / "src-tauri" / "windows" / "English.nsh").read_text(encoding="utf-8")


def nsis_define(name: str) -> str:
    match = re.search(rf'^!define {name} "([^"]*)"', HOOKS, re.MULTILINE)
    assert match, name
    return match.group(1)


class OneNameEverywhereTest(unittest.TestCase):
    def test_the_display_name_is_the_same_in_python_tauri_rust_and_typescript(self) -> None:
        self.assertEqual(branding.APP_DISPLAY_NAME, "Gigradar")
        self.assertEqual(CONF["productName"], branding.APP_DISPLAY_NAME)
        self.assertEqual(CONF["mainBinaryName"], branding.APP_DISPLAY_NAME)
        self.assertEqual(CONF["app"]["windows"][0]["title"], branding.APP_DISPLAY_NAME)
        self.assertIn(f'const APP_DISPLAY_NAME: &str = "{branding.APP_DISPLAY_NAME}";', LIB_RS)
        self.assertIn(f'export const APP_DISPLAY_NAME = "{branding.APP_DISPLAY_NAME}";', STRINGS_TS)

    def test_the_toast_identity_is_the_installers_application_id(self) -> None:
        self.assertEqual(CONF["identifier"], branding.APP_ID)             # the installer puts it on the Start-menu shortcut
        self.assertEqual(nsis_define("GIGRADAR_AUMID"), branding.APP_ID)

    def test_the_installer_removes_exactly_the_apps_own_task_and_data_folder(self) -> None:
        task = branding.TASK_FOLDER + branding.TASK_NAME
        self.assertEqual(nsis_define("GIGRADAR_TASK"), task)
        self.assertEqual(task, "\\Gigradar Desktop\\checks")
        code = " ".join(line for line in HOOKS.splitlines() if not line.lstrip().startswith(";"))
        self.assertNotIn("gigradar-watch", code)                           # never the source checkout's task
        self.assertEqual(nsis_define("GIGRADAR_DATA"), "$LOCALAPPDATA\\" + branding.DATA_DIR_NAME)
        self.assertIn(f'const DATA_DIR_NAME: &str = "{branding.DATA_DIR_NAME}";', LIB_RS)
        self.assertIn(f'const DEFAULT_TASK: &str = "{branding.TASK_NAME}";', LIB_RS)
        self.assertNotEqual(branding.DATA_DIR_NAME.lower(), branding.APP_DISPLAY_NAME.lower())   # the install folder is named after the product

    def test_the_link_scheme_is_registered_on_install_and_removed_on_uninstall(self) -> None:
        self.assertEqual(nsis_define("GIGRADAR_SCHEME"), branding.LINK_SCHEME)
        self.assertIn(f'pub const SCHEME: &str = "{branding.LINK_SCHEME}";', LINKS_RS)
        self.assertIn('"URL Protocol"', HOOKS)
        self.assertIn('shell\\open\\command', HOOKS)
        self.assertIn('DeleteRegKey HKCU "Software\\Classes\\${GIGRADAR_SCHEME}"', HOOKS)

    def test_uninstall_keeps_the_data_unless_the_box_was_ticked_and_never_on_an_update(self) -> None:
        post = HOOKS.split("NSIS_HOOK_POSTUNINSTALL")[1]
        self.assertIn("$DeleteAppDataCheckboxState = 1", post)
        self.assertEqual(HOOKS.count("RmDir /r"), 1)                       # the one place data is deleted
        self.assertIn("$UpdateMode <> 1", HOOKS.split("NSIS_HOOK_PREUNINSTALL")[1])
        self.assertIn("$UpdateMode <> 1", post)
        self.assertIn("Also delete my data", ENGLISH_NSH)
        raw = (APP / "src-tauri" / "windows" / "English.nsh").read_bytes()
        self.assertNotEqual(raw[:3], bytes([0xEF, 0xBB, 0xBF]))                 # a BOM makes makensis stop: 'Invalid command'
        self.assertGreaterEqual(ENGLISH_NSH.count("LangString "), 27)           # the whole file: the installer's other texts too
        self.assertIn('LangString uninstallApp ${LANG_ENGLISH} "Uninstall ${PRODUCTNAME}"', ENGLISH_NSH)
        self.assertEqual(CONF["bundle"]["windows"]["nsis"]["installerHooks"], "windows/hooks.nsh")
        self.assertEqual(CONF["bundle"]["windows"]["nsis"]["customLanguageFiles"], {"English": "windows/English.nsh"})

    def test_the_icon_the_toast_identity_uses_is_shipped_next_to_the_helper_folder(self) -> None:
        self.assertEqual(CONF["bundle"]["resources"]["icons/icon.ico"], "app.ico")
        self.assertEqual(CONF["bundle"]["resources"]["resources/"], "")     # resources/sidecar/ -> <install>/sidecar/

    def test_the_installer_still_installs_webview2_itself(self) -> None:
        self.assertEqual(CONF["bundle"]["windows"]["webviewInstallMode"], {"type": "embedBootstrapper", "silent": True})

    def test_the_tray_has_the_three_entries(self) -> None:
        for entry in ('"open"', '"check"', '"quit"', "Run check now"):
            self.assertIn(entry, LIB_RS)


if __name__ == "__main__":
    unittest.main()
