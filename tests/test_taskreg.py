"""Run: .venv/Scripts/python -m unittest discover -s tests   (offline: schtasks is a fake)"""

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from xml.etree import ElementTree

from gigradar import taskreg

NOW = datetime(2026, 10, 9, 12, 0, tzinfo=timezone.utc)
NS = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}


class Recorder:
    def __init__(self, code: int = 0, output: str = "") -> None:
        self.calls: list[list[str]] = []
        self.xml = ""
        self.code, self.output = code, output

    def __call__(self, argv: list[str]) -> tuple[int, str]:
        self.calls.append(argv)
        if argv[0] == "/create":
            self.xml = Path(argv[argv.index("/xml") + 1]).read_text(encoding="utf-16")
        return self.code, self.output


class RegisterTest(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.exe = self.dir / "app" / "gigradarw.exe"
        self.exe.parent.mkdir()
        self.exe.write_bytes(b"x")
        self.config = self.dir / "home" / "gigradar.toml"

    def test_the_task_runs_the_windowless_exe_with_the_config_every_30_minutes(self) -> None:
        run = Recorder()
        result = taskreg.register("gigradar-app-test", self.exe, self.config, 30, NOW, "PC\\ann", run)
        [argv] = run.calls
        self.assertEqual(argv[:3], ["/create", "/tn", "\\gigradar\\gigradar-app-test"])
        self.assertIn("/f", argv)
        task = ElementTree.fromstring(run.xml.split("?>", 1)[1])      # well-formed XML
        text = lambda path: task.find(path, NS).text                                       # noqa: E731
        self.assertEqual(text("t:Actions/t:Exec/t:Command"), str(self.exe))
        self.assertEqual(text("t:Actions/t:Exec/t:Arguments"), f'run-once --config "{self.config}"')
        self.assertEqual(text("t:Triggers/t:TimeTrigger/t:Repetition/t:Interval"), "PT30M")
        self.assertEqual(text("t:Triggers/t:TimeTrigger/t:StartBoundary"), "2026-10-09T12:01:00")
        self.assertEqual(text("t:Settings/t:Priority"), "4")
        self.assertEqual(text("t:Settings/t:ExecutionTimeLimit"), "PT15M")
        self.assertEqual(text("t:Settings/t:MultipleInstancesPolicy"), "IgnoreNew")
        self.assertEqual(text("t:Settings/t:StartWhenAvailable"), "true")
        self.assertEqual(text("t:Principals/t:Principal/t:LogonType"), "InteractiveToken")
        self.assertEqual(text("t:Principals/t:Principal/t:RunLevel"), "LeastPrivilege")
        self.assertEqual(result["task_name"], "gigradar-app-test")

    def test_the_temporary_xml_file_is_removed(self) -> None:
        run = Recorder()
        taskreg.register("t", self.exe, self.config, 30, NOW, "u", run)
        self.assertFalse(Path(run.calls[0][run.calls[0].index("/xml") + 1]).exists())

    def test_special_characters_in_paths_are_escaped(self) -> None:
        run = Recorder()
        odd = self.dir / "a&b" / "gigradarw.exe"
        odd.parent.mkdir()
        odd.write_bytes(b"x")
        taskreg.register("t", odd, self.config, 30, NOW, "PC\\o'brien", run)
        ElementTree.fromstring(run.xml.split("?>", 1)[1])

    def test_bad_input_is_refused_before_schtasks_runs(self) -> None:
        run = Recorder()
        for name, exe, interval in (("bad name", self.exe, 30), ("t", self.dir / "missing.exe", 30), ("t", self.exe, 5)):
            with self.assertRaises(taskreg.TaskError):
                taskreg.register(name, exe, self.config, interval, NOW, "u", run)
        self.assertEqual(run.calls, [])

    def test_schtasks_failure_is_reported(self) -> None:
        with self.assertRaises(taskreg.TaskError) as caught:
            taskreg.register("t", self.exe, self.config, 30, NOW, "u", Recorder(1, "Access is denied"))
        self.assertEqual(caught.exception.code, "schtasks_failed")
        self.assertIn("Access is denied", str(caught.exception))


class UnregisterTest(unittest.TestCase):
    def test_removes_an_existing_task(self) -> None:
        run = Recorder()
        self.assertEqual(taskreg.unregister("t", run), {"task_name": "t", "removed": True})
        self.assertEqual([c[0] for c in run.calls], ["/query", "/delete"])

    def test_a_missing_task_is_not_an_error(self) -> None:
        run = Recorder(1, "ERROR: The system cannot find the file specified.")
        self.assertEqual(taskreg.unregister("t", run), {"task_name": "t", "removed": False})
        self.assertEqual([c[0] for c in run.calls], ["/query"])


if __name__ == "__main__":
    unittest.main()
