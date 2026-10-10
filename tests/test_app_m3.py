"""Run: .venv/Scripts/python -m unittest discover -s tests   (offline: fake windows_toasts, temp homes, no window)

The desktop app's settings, job cards, notification links and the "Upwork needs a quick check" bookkeeping."""

import json
import logging
import sys
import tempfile
from time import monotonic
import types
import unittest
from datetime import datetime, time, timezone
from pathlib import Path
from unittest import mock

from gigradar import appsettings, appsetup, branding, recheck, watch
from gigradar.config import ConfigError, load_config
from gigradar.notify import Alert, ToastNotifier, alerts_link
from gigradar.score import Score
from gigradar.search import SearchBlocked
from gigradar.store import job_id, mark_seen, open_store, recent_job, run_jobs, save_job_score
from gigradar.tokens import StopRun, TokenUnavailable
from test_cli import NOW, TOKEN, CliCase, make_job
from test_watch import TOML, FakeNotifier as WatchNotifier, FakeSearcher

ANSWERS = {
    "searches": [{"name": "py-hourly", "query": "python", "job_type": "hourly"},
                 {"name": "react-both", "query": "react"}],
    "notify": {"channels": ["toast", "telegram"]},
    "profile": {"markdown": "## Web\nI build web apps.\n", "skills": ["Python"], "min_hourly": 40,
                "constraints": "remote only", "tiers": ["expert"]},
    "scoring": {"min_score": 20},
    "telegram": {"bot_token": TOKEN, "chat_id": "424242", "bot_username": "my_gigradar_bot"},
}


def write_home(home: Path, answers: dict = ANSWERS) -> None:
    appsetup.apply(home, answers, False, False, NOW, {})


class BrandingTest(unittest.TestCase):
    def test_one_name_and_links_toasts_carry(self) -> None:
        self.assertEqual(branding.APP_DISPLAY_NAME, "Gigradar")
        self.assertEqual(branding.job_link("~0123abc"), "gigradar://job/~0123abc")
        self.assertEqual(branding.run_link("2026-10-09T10:00:00+00:00"), "gigradar://jobs?run=2026-10-09T10%3A00%3A00%2B00%3A00")
        self.assertEqual(branding.STATUS_LINK, "gigradar://status")

    def test_the_app_has_its_own_task_that_cannot_be_the_source_checkouts(self) -> None:
        self.assertEqual((branding.TASK_FOLDER, branding.TASK_NAME), ("\\Gigradar Desktop\\", "checks"))

    def test_toasts_use_the_installed_apps_identity_only_when_packaged(self) -> None:
        self.assertEqual(branding.toast_identity(True), branding.APP_ID)
        self.assertEqual(branding.toast_identity(False), "Gigradar")

    def test_registering_the_identity_names_it_and_survives_a_registry_failure(self) -> None:
        icon = Path(tempfile.mkdtemp()) / "app.ico"
        icon.write_bytes(b"ico")
        written: list = []
        branding.register_toast_identity(icon, lambda path, values: written.append((path, values)))
        self.assertEqual(written[0][0], "Software\\Classes\\AppUserModelId\\app.gigradar.desktop")
        self.assertEqual(written[0][1], {"DisplayName": "Gigradar", "IconUri": str(icon)})

        def broken(path: str, values: dict) -> None:
            raise PermissionError("denied")

        with self.assertLogs("gigradar", "WARNING"):
            branding.register_toast_identity(None, broken)


class FakeToasts:
    """A stand-in for the windows_toasts module: records the identity and every toast, shows nothing."""

    def __init__(self) -> None:
        self.shown: list = []
        outer = self

        class Toast:
            def __init__(self, text_fields=None, launch_action=None) -> None:
                self.text_fields, self.launch_action = text_fields, launch_action

        class WindowsToaster:
            def __init__(self, identity: str) -> None:
                outer.identity = identity

            def show_toast(self, toast) -> None:
                outer.shown.append(toast)

        self.module = types.SimpleNamespace(Toast=Toast, WindowsToaster=WindowsToaster)


class ToastLinkTest(unittest.TestCase):
    def toaster(self, identity: str, links: bool = True) -> tuple[ToastNotifier, FakeToasts]:
        fake = FakeToasts()
        with mock.patch.dict(sys.modules, {"windows_toasts": fake.module}):
            return ToastNotifier(identity, links), fake

    def alert(self, cipher: str, value: int | None = 80) -> Alert:
        return Alert(make_job(cipher), None if value is None else Score(value, "Web · matched: Python", "embed", "2"))

    def test_the_toast_is_sent_under_the_given_identity(self) -> None:
        _, fake = self.toaster("app.gigradar.desktop")
        self.assertEqual(fake.identity, "app.gigradar.desktop")

    def test_one_job_opens_its_card_several_open_that_runs_list(self) -> None:
        notifier, fake = self.toaster("Gigradar")
        notifier.notify_jobs([self.alert("~01")], "RUN-1", 0)
        notifier.notify_jobs([self.alert("~01"), self.alert("~02")], "2026-10-09T10:00:00+00:00", 0)
        self.assertEqual(fake.shown[0].launch_action, "gigradar://job/~01")
        self.assertEqual(fake.shown[1].launch_action, branding.run_link("2026-10-09T10:00:00+00:00"))
        self.assertTrue(fake.shown[0].text_fields[0].startswith("Gigradar: 1 new job"))

    def test_a_job_without_an_id_falls_back_to_the_runs_list(self) -> None:
        alert = Alert(make_job("~01").__class__(**{**make_job("~01").__dict__, "url": ""}), None)
        self.assertEqual(alerts_link([alert], "R", 0), branding.run_link("R"))

    def test_without_the_link_handler_a_toast_carries_no_link(self) -> None:
        notifier, fake = self.toaster("Gigradar", links=False)          # not installed: a click would show the Store dialog
        notifier.notify_jobs([self.alert("~01")], "R", 0)
        notifier.notify("t", "m", branding.STATUS_LINK)
        self.assertEqual([t.launch_action for t in fake.shown], [None, None])

    def test_a_status_message_carries_the_link_it_is_given(self) -> None:
        notifier, fake = self.toaster("Gigradar")
        notifier.notify("Gigradar: Upwork needs a quick check", "Open Gigradar and click Check Upwork.", branding.STATUS_LINK)
        self.assertEqual(fake.shown[0].launch_action, "gigradar://status")


class Recorder:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.sent: list[tuple[str, str, str | None]] = []

    def notify(self, title: str, message: str, link: str | None) -> None:
        if self.fail:
            raise RuntimeError("telegram down")
        self.sent.append((title, message, link))

    def notify_jobs(self, alerts, run, below) -> None:
        raise AssertionError("not a job alert")


class UpworkAccessTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.marker = Path(self._tmp.name) / "data" / recheck.MARKER_NAME
        self.notifier = Recorder()

    def note(self, stopped: StopRun | None, notifier: Recorder | None = None) -> None:
        watch.note_upwork_access(self.marker, stopped, notifier or self.notifier, NOW)

    def test_a_blocked_run_tells_the_user_once_and_later_ones_only_log(self) -> None:
        blocked = TokenUnavailable("no Upwork token: challenge not solved within 20s")
        self.note(blocked)
        self.assertEqual(len(self.notifier.sent), 1)
        title, message, link = self.notifier.sent[0]
        self.assertEqual((title, link), ("Gigradar: Upwork needs a quick check", "gigradar://status"))
        self.assertIn("click Check Upwork", message)
        self.assertTrue(recheck.read(self.marker).needed)
        with self.assertLogs("gigradar", "WARNING") as logs:
            for _ in range(3):
                self.note(blocked)
        self.assertEqual(len(self.notifier.sent), 1)                    # nobody is nagged
        self.assertEqual(len(logs.output), 3)                           # one line per later run
        self.assertIn("still needs a quick check", logs.output[0])

    def test_a_run_that_gets_through_resets_it_and_a_new_block_tells_again(self) -> None:
        self.note(TokenUnavailable("x"))
        with self.assertLogs("gigradar", "INFO"):
            self.note(None)
        self.assertFalse(recheck.read(self.marker).needed)
        self.note(SearchBlocked("HTTP 403 cf-mitigated=challenge"))
        self.assertEqual(len(self.notifier.sent), 2)

    def test_only_upwork_turning_the_run_away_counts(self) -> None:
        for stopped in (SearchBlocked("fetch() gave no result within 30s"), SearchBlocked("fetch failed in page: x"),
                        StopRun("webview session exceeded 440s")):
            self.note(stopped)
        self.assertEqual((self.notifier.sent, recheck.read(self.marker).needed), ([], False))
        self.note(SearchBlocked("HTTP 429 cf-mitigated=None"))
        self.assertEqual(len(self.notifier.sent), 1)

    def test_a_failed_notification_leaves_no_marker_so_the_next_run_tries_again(self) -> None:
        with self.assertLogs("gigradar", "ERROR"):
            self.note(TokenUnavailable("x"), Recorder(fail=True))
        self.assertFalse(recheck.read(self.marker).needed)
        self.note(TokenUnavailable("x"))
        self.assertEqual(len(self.notifier.sent), 1)

    def test_a_damaged_marker_still_means_the_check_is_needed(self) -> None:
        self.marker.parent.mkdir(parents=True)
        self.marker.write_text("{not json", encoding="utf-8")
        self.assertEqual(recheck.read(self.marker), recheck.RecheckState(True, None))
        self.assertTrue(recheck.clear(self.marker))
        self.assertFalse(recheck.clear(self.marker))


class BlockedRunsTest(unittest.TestCase):
    """The real run_watch with a searcher that Upwork turns away: told once, quiet afterwards, reset when it works."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        (Path(self._tmp.name) / "gigradar.toml").write_text(TOML, encoding="utf-8")
        self.cfg = load_config(Path(self._tmp.name) / "gigradar.toml", {})
        self.conn = open_store(Path(":memory:"), [], NOW)
        self.addCleanup(self.conn.close)
        self.notifier = WatchNotifier(False)
        self.marker = recheck.marker_path(self.cfg.db_path)

    def run_watch(self, searcher) -> int:
        return watch.run_watch(self.cfg, searcher, self.notifier, self.conn, NOW, None, monotonic)

    def test_blocked_blocked_fine_blocked(self) -> None:
        blocked = lambda: FakeSearcher({}, TokenUnavailable("no Upwork token: challenge not solved within 20s"))  # noqa: E731
        self.assertEqual(self.run_watch(blocked()), watch.EXIT_STOPPED)
        self.assertEqual([title for title, _ in self.notifier.sent], ["Gigradar: Upwork needs a quick check"])
        self.assertTrue(self.marker.is_file())
        with self.assertLogs("gigradar", "WARNING") as logs:
            self.assertEqual(self.run_watch(blocked()), watch.EXIT_STOPPED)
            self.assertEqual(self.run_watch(blocked()), watch.EXIT_STOPPED)
        self.assertEqual(len(self.notifier.sent), 1)                              # still just the one
        self.assertEqual(sum("still needs a quick check" in line for line in logs.output), 2)
        self.assertEqual(self.run_watch(FakeSearcher({"a": [], "b": []}, None)), watch.EXIT_OK)
        self.assertFalse(self.marker.exists())                                   # reset after a good run
        self.assertEqual(self.run_watch(blocked()), watch.EXIT_STOPPED)
        self.assertEqual(len(self.notifier.sent), 2)                              # a new block is announced again

    def test_a_search_turned_away_mid_run_counts_too(self) -> None:
        searcher = FakeSearcher({"a": SearchBlocked("HTTP 403 cf-mitigated=challenge"), "b": []}, None)
        self.assertEqual(self.run_watch(searcher), watch.EXIT_STOPPED)
        self.assertEqual(len(self.notifier.sent), 1)


class ScheduleConfigTest(unittest.TestCase):
    def load(self, schedule: str):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(tmp, ignore_errors=True))
        path = tmp / "gigradar.toml"
        path.write_text(schedule + '\n[[searches]]\nname = "a"\n', encoding="utf-8")
        return load_config(path, {}).schedule

    def test_defaults_and_values(self) -> None:
        self.assertEqual(self.load("").interval_minutes, 30)
        s = self.load('[schedule]\ninterval_minutes = 60\nquiet_start = "22:00"\nquiet_end = "07:00"\n')
        self.assertEqual((s.interval_minutes, s.quiet_start, s.quiet_end), (60, "22:00", "07:00"))

    def test_invalid_schedules_are_refused(self) -> None:
        for bad in ('interval_minutes = 5', 'interval_minutes = "30"', 'quiet_start = "22:00"', 'quiet_start = "25:00"\nquiet_end = "07:00"',
                    'quiet_start = "08:00"\nquiet_end = "08:00"', 'colour = 1'):
            with self.subTest(bad), self.assertRaises(ConfigError):
                self.load(f"[schedule]\n{bad}\n")

    def test_quiet_hours_may_cross_midnight(self) -> None:
        night = self.load('[schedule]\nquiet_start = "22:00"\nquiet_end = "07:00"\n')
        self.assertEqual([night.in_quiet_hours(time(h, m)) for h, m in ((23, 0), (3, 30), (6, 59), (7, 0), (12, 0), (21, 59), (22, 0))],
                         [True, True, True, False, False, False, True])
        day = self.load('[schedule]\nquiet_start = "13:00"\nquiet_end = "14:00"\n')
        self.assertEqual([day.in_quiet_hours(time(h)) for h in (12, 13, 14)], [False, True, False])
        self.assertFalse(self.load("").in_quiet_hours(time(3)))


class QuietHoursRunTest(unittest.TestCase):
    def test_a_scheduled_run_in_quiet_hours_does_nothing_and_a_manual_one_runs(self) -> None:
        home = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(home, ignore_errors=True))
        (home / "gigradar.toml").write_text(
            '[notify]\nchannels = []\n[schedule]\nquiet_start = "00:00"\nquiet_end = "23:59"\n[[searches]]\nname = "a"\n',
            encoding="utf-8")

        class Noon(datetime):
            @classmethod
            def now(cls, tz=None):
                return cls(2026, 10, 9, 12, 0, tzinfo=tz)

        argv = ["--config", str(home / "gigradar.toml"), "--log-file", str(home / "log.txt")]
        with mock.patch.object(watch, "datetime", Noon), mock.patch.object(watch, "build_searcher") as build:
            self.assertEqual(watch.main(argv), watch.EXIT_OK)
            build.assert_not_called()                                   # no browser, no request, nothing
            build.side_effect = ConfigError("reached the searcher")      # the manual run goes on past the check
            self.assertEqual(watch.main(argv + ["--ignore-quiet-hours"]), watch.EXIT_CONFIG)
        logging.getLogger("gigradar").handlers.clear()
        self.assertIn("quiet hours", (home / "log.txt").read_text(encoding="utf-8"))


class SettingsTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.home = Path(self._tmp.name)
        write_home(self.home)

    def save(self, section: str, payload: dict) -> dict:
        return appsettings.save_section(self.home, section, payload, NOW, {})

    def test_reads_every_section_prefilled_and_never_the_token(self) -> None:
        settings = appsettings.read_settings(self.home)
        self.assertEqual(settings["searches"], [{"name": "py-hourly", "query": "python", "job_type": "hourly"},
                                                {"name": "react-both", "query": "react", "job_type": None}])
        self.assertEqual(settings["preferences"], {"skills": ["Python"], "constraints": "remote only",
                                                   "min_hourly": 40, "min_fixed": None, "min_score": 20})
        self.assertEqual(settings["profile"]["markdown"], "## Web\nI build web apps.\n")
        self.assertEqual(settings["notifications"], {"toast": True, "telegram": {"enabled": True, "connected": True,
                                                                                  "bot": "my_gigradar_bot"}})
        self.assertEqual(settings["schedule"], {"interval_minutes": 30, "quiet_start": None, "quiet_end": None})
        self.assertNotIn(TOKEN, json.dumps(settings))
        self.assertNotIn("424242", json.dumps(settings))

    def test_a_change_touches_only_its_own_section(self) -> None:
        before = appsettings.read_settings(self.home)
        self.save("schedule", {"interval_minutes": 120, "quiet_start": "22:00", "quiet_end": "07:00"})
        after = appsettings.read_settings(self.home)
        self.assertEqual(after["schedule"], {"interval_minutes": 120, "quiet_start": "22:00", "quiet_end": "07:00"})
        for section in ("profile", "preferences", "searches", "notifications"):
            self.assertEqual(after[section], before[section], section)
        answers, _ = appsettings.current_answers(self.home)
        self.assertEqual(answers["profile"]["tiers"], ["expert"])      # what the screen does not show survives
        self.assertEqual(answers["scoring"], {"min_score": 20})
        self.assertEqual(load_config(self.home / "gigradar.toml", {"TELEGRAM_BOT_TOKEN": "x", "TELEGRAM_CHAT_ID": "1"}).schedule.interval_minutes, 120)

    def test_each_section_saves_and_the_replaced_files_are_backed_up(self) -> None:
        self.save("profile", {"markdown": "## Data\nI clean data with Python.\n"})
        self.save("preferences", {"skills": ["SQL", "Python"], "constraints": "", "min_hourly": None, "min_fixed": 500, "min_score": 35})
        self.save("searches", {"searches": [{"name": "py-hourly", "query": "python scripts", "job_type": "hourly"},
                                            {"query": "etl", "job_type": None}]})
        settings = appsettings.read_settings(self.home)
        self.assertEqual(settings["profile"]["markdown"], "## Data\nI clean data with Python.\n")
        self.assertEqual(settings["preferences"], {"skills": ["SQL", "Python"], "constraints": "", "min_hourly": None, "min_fixed": 500, "min_score": 35})
        self.assertEqual([(s["name"], s["query"], s["job_type"]) for s in settings["searches"]],
                         [("py-hourly", "python scripts", "hourly"), ("etl-both", "etl", None)])
        self.assertTrue(list((self.home / "backups").glob("*/gigradar.toml")))

    def test_an_invalid_change_changes_nothing(self) -> None:
        text = (self.home / "gigradar.toml").read_text(encoding="utf-8")
        bad = [("searches", {"searches": []}), ("searches", {"searches": [{"query": f"q{i}"} for i in range(6)]}),
               ("searches", {"searches": [{"query": "  "}]}), ("searches", {"searches": [{"query": "a", "job_type": "weekly"}]}),
               ("searches", {"searches": [{"name": "x", "query": "a"}, {"name": "x", "query": "b"}]}),
               ("profile", {"markdown": "no heading at all"}), ("preferences", {"min_hourly": -1}),
               ("preferences", {"skills": [3]}), ("schedule", {"interval_minutes": 15}), ("schedule", {"interval_minutes": 45}),
               ("schedule", {"interval_minutes": 60, "quiet_start": "22:00"}), ("schedule", {"interval_minutes": 60, "quiet_start": "9pm", "quiet_end": "07:00"}),
               ("schedule", {"interval_minutes": 60, "quiet_start": "08:00", "quiet_end": "08:00"}),
               ("notifications", {"toast": False, "telegram": {"enabled": False}}), ("nonsense", {})]
        for section, payload in bad:
            with self.subTest(section=section, payload=str(payload)[:60]), self.assertRaises(appsettings.SettingsError):
                self.save(section, payload)
        self.assertEqual((self.home / "gigradar.toml").read_text(encoding="utf-8"), text)

    def test_the_intervals_are_30_60_120_240(self) -> None:
        self.assertEqual(appsettings.INTERVALS, (30, 60, 120, 240))
        for minutes in appsettings.INTERVALS:
            self.save("schedule", {"interval_minutes": minutes})
        self.save("schedule", {"interval_minutes": 30, "quiet_start": None, "quiet_end": None})

    def test_telegram_keeps_the_connected_bot_unless_a_new_one_is_given(self) -> None:
        self.save("notifications", {"toast": True, "telegram": {"enabled": False}})
        self.assertEqual(appsettings.read_settings(self.home)["notifications"]["telegram"],
                         {"enabled": False, "connected": True, "bot": "my_gigradar_bot"})
        self.save("notifications", {"toast": False, "telegram": {"enabled": True}})          # back on, no new token needed
        _, env = appsettings.current_answers(self.home)
        self.assertEqual(env["TELEGRAM_BOT_TOKEN"], TOKEN)
        new = "111111111:BBnewBOTtokenNEWbotTOKENnewBOTtoken_-"
        self.save("notifications", {"toast": True, "telegram": {"enabled": True, "bot_token": new, "chat_id": "777", "bot_username": "other_bot"}})
        _, env = appsettings.current_answers(self.home)
        self.assertEqual((env["TELEGRAM_BOT_TOKEN"], env["TELEGRAM_CHAT_ID"], env["TELEGRAM_BOT_USERNAME"]), (new, "777", "other_bot"))
        self.assertEqual(appsettings.read_settings(self.home)["notifications"]["telegram"]["bot"], "other_bot")

    def test_telegram_without_a_connected_bot_is_refused(self) -> None:
        home = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(home, ignore_errors=True))
        write_home(home, {k: v for k, v in ANSWERS.items() if k != "telegram"} | {"notify": {"channels": ["toast"]}})
        with self.assertRaises(appsettings.SettingsError) as raised:
            appsettings.save_section(home, "notifications", {"toast": True, "telegram": {"enabled": True}}, NOW, {})
        self.assertEqual(raised.exception.code, "telegram_not_connected")

    def test_a_hand_edited_config_is_refused_instead_of_rewritten(self) -> None:
        with (self.home / "gigradar.toml").open("a", encoding="utf-8") as out:
            out.write('\n[token]\nuse_proxy = false\n')
        with self.assertRaises(appsettings.SettingsError) as raised:
            appsettings.read_settings(self.home)
        self.assertEqual(raised.exception.code, "unsupported_config")
        self.assertIn("token", str(raised.exception))

    def test_a_missing_config_is_a_config_error(self) -> None:
        with self.assertRaises(appsettings.SettingsError) as raised:
            appsettings.read_settings(Path(tempfile.mkdtemp()))
        self.assertEqual(raised.exception.code, "config")

    def test_setup_caps_the_searches_at_five(self) -> None:
        answers = {**ANSWERS, "searches": [{"name": f"s{i}", "query": f"q{i}"} for i in range(6)]}
        with self.assertRaises(appsetup.SetupError):
            appsetup.render_toml(answers)


class SettingsCommandTest(CliCase):
    def setUp(self) -> None:
        super().setUp()
        write_home(self.home)
        self.exe = self.home / "gigradarw.exe"
        self.exe.write_bytes(b"x")

    def test_settings_get_returns_the_sections_without_secrets(self) -> None:
        code, result = self.result("settings-get")
        self.assertEqual(code, 0, result)
        self.assertEqual(result["settings"]["searches"][0]["name"], "py-hourly")
        self.assertNotIn("bot_token", json.dumps(result))

    def test_saving_the_schedule_re_registers_the_task_with_the_new_interval(self) -> None:
        code, result = self.result("settings-save", "--section", "schedule", "--task-name", "gigradar-app-test", "--exe", str(self.exe),
                                   stdin=json.dumps({"interval_minutes": 120, "quiet_start": "23:00", "quiet_end": "06:30"}))
        self.assertEqual((code, result["section"], result["rescheduled"]), (0, "schedule", True), result)
        create = self.schtasks_calls[0]
        self.assertEqual(create[:3], ["/create", "/tn", "\\Gigradar Desktop\\gigradar-app-test"])
        xml = Path(create[4]).name                                   # the temp file is removed; the call is what counts
        self.assertTrue(xml.startswith("gigradar-task-"))
        self.assertEqual(appsettings.read_settings(self.home)["schedule"]["interval_minutes"], 120)

    def test_the_schedule_is_saved_but_not_registered_from_a_source_checkout(self) -> None:
        code, result = self.result("settings-save", "--section", "schedule", stdin=json.dumps({"interval_minutes": 60}))
        self.assertEqual((code, result["rescheduled"], self.schtasks_calls), (0, False, []))

    def test_a_failing_task_registration_says_the_settings_were_saved(self) -> None:
        self.schtasks_code = 1
        code, result = self.result("settings-save", "--section", "schedule", "--exe", str(self.exe), stdin=json.dumps({"interval_minutes": 60}))
        self.assertEqual((code, result["error"]["code"]), (1, "schtasks_failed"))
        self.assertIn("saved", result["error"]["message"])
        self.assertEqual(appsettings.read_settings(self.home)["schedule"]["interval_minutes"], 60)

    def test_a_new_bot_token_arrives_on_stdin_and_never_appears_in_any_output(self) -> None:
        new = "111111111:BBnewBOTtokenNEWbotTOKENnewBOTtoken_-"
        payload = {"toast": True, "telegram": {"enabled": True, "bot_token": new, "chat_id": "777", "bot_username": "other_bot"}}
        code, lines = self.run_cli("settings-save", "--section", "notifications", stdin=json.dumps(payload))
        self.assertEqual(code, 0, lines)
        self.assertNotIn(new, json.dumps(lines))
        code, lines = self.run_cli("settings-save", "--section", "notifications",
                                   stdin=json.dumps({"toast": False, "telegram": {"enabled": False}}))
        self.assertEqual((code, lines[-1]["error"]["code"]), (1, "no_channel"))

    def test_bad_input_is_a_clean_error(self) -> None:
        code, result = self.result("settings-save", "--section", "profile", stdin="{not json")
        self.assertEqual((code, result["error"]["code"]), (1, "invalid_settings"))
        code, result = self.result("settings-save", "--section", "schedule", stdin=json.dumps({"interval_minutes": 10}))
        self.assertEqual((code, result["error"]["code"]), (1, "invalid_settings"))


class JobCardTest(CliCase):
    def setUp(self) -> None:
        super().setUp()
        self.write_config()
        conn = open_store(self.home / "data" / "gigradar.db", [], NOW)
        first, second, third = make_job("~01"), make_job("~02"), make_job("~03")
        run_a, run_b = NOW, datetime(2026, 10, 9, 11, 0, tzinfo=timezone.utc)
        mark_seen(conn, [first, second], run_a)
        mark_seen(conn, [third], run_b)
        save_job_score(conn, "~01", Score(82, "Web development · matched: Python, React", "embed", "2"), NOW)
        save_job_score(conn, "~02", Score(5, "Data work", "embed", "2"), NOW)
        conn.close()
        self.run_a = run_a.isoformat()

    def test_the_card_has_score_section_matched_skills_and_a_short_description(self) -> None:
        code, result = self.result("job", "--job-id", "~01")
        self.assertEqual(code, 0, result)
        job = result["job"]
        self.assertEqual((job["id"], job["score"], job["section"], job["matched"]), ("~01", 82, "Web development", ["Python", "React"]))
        self.assertEqual({"title", "url", "pay", "published", "description", "label", "first_seen"} - set(job), set())

    def test_a_long_description_is_cut(self) -> None:
        conn = open_store(self.home / "data" / "gigradar.db", [], NOW)
        long = make_job("~04").__class__(**{**make_job("~04").__dict__, "description": "word " * 2000})
        mark_seen(conn, [long], NOW)
        conn.close()
        job = self.result("job", "--job-id", "~04")[1]["job"]
        self.assertLessEqual(len(job["description"]), 1501)
        self.assertTrue(job["description"].endswith("…"))

    def test_a_label_is_stored_and_shown_again_by_the_card_and_the_list(self) -> None:
        self.assertEqual(self.result("label", "--job-id", "~01", "--value", "up")[0], 0)
        self.assertEqual(self.result("job", "--job-id", "~01")[1]["job"]["label"], 1)
        self.assertEqual({j["id"]: j["label"] for j in self.result("jobs")[1]["jobs"]}["~01"], 1)
        self.assertEqual(self.result("label", "--job-id", "~01", "--value", "down")[0], 0)
        self.assertEqual(self.result("job", "--job-id", "~01")[1]["job"]["label"], -1)
        self.assertEqual(self.result("label", "--job-id", "~01", "--value", "clear")[0], 0)
        self.assertIsNone(self.result("job", "--job-id", "~01")[1]["job"]["label"])

    def test_an_unknown_job_is_a_clean_error(self) -> None:
        code, result = self.result("job", "--job-id", "~nope")
        self.assertEqual((code, result["error"]["code"]), (1, "not_found"))

    def test_a_digest_link_lists_only_the_jobs_that_run_announced(self) -> None:
        code, result = self.result("jobs", "--run", self.run_a)
        self.assertEqual(code, 0, result)
        self.assertEqual({j["id"] for j in result["jobs"]}, {"~01", "~02"})
        self.assertEqual(self.result("jobs", "--run", "2026-01-01T00:00:00+00:00")[1]["jobs"], [])

    def test_jobs_below_min_score_were_not_announced_so_the_run_list_leaves_them_out(self) -> None:
        self.write_config('[scoring]\nmin_score = 20\n')
        self.assertEqual({j["id"] for j in self.result("jobs", "--run", self.run_a)[1]["jobs"]}, {"~01"})

    def test_a_test_notification_can_announce_stored_jobs_so_the_click_can_be_tried(self) -> None:
        sent: list = []

        class Fake:
            def notify_jobs(self, alerts, run, below):
                sent.append((alerts, run))

        with mock.patch("gigradar.notify.build_toast_notifier", return_value=Fake()):
            code, result = self.result("notify-test", "--channel", "toast", "--job-id", "~02", "--job-id", "~01")
        self.assertEqual((code, result["channels"], result["jobs"]), (0, ["toast"], 2), result)
        alerts, run = sent[0]
        self.assertEqual([a.job.url.rsplit("/", 1)[-1] for a in alerts], ["~01", "~02"])      # best score first
        self.assertEqual((alerts[0].score.value, run), (82, self.run_a))                       # the digest link lists that run
        code, result = self.result("notify-test", "--channel", "toast", "--job-id", "~zzz")
        self.assertEqual((code, result["error"]["code"]), (1, "not_found"))

    def test_store_helpers(self) -> None:
        conn = open_store(self.home / "data" / "gigradar.db", [], NOW)
        try:
            self.assertEqual([r.job_id for r in run_jobs(conn, self.run_a, 10, 0)], ["~01", "~02"])
            self.assertIsNone(recent_job(conn, "~zzz"))
            self.assertEqual(recent_job(conn, "~01").score, 82)
            self.assertEqual(job_id(recent_job(conn, "~03").job), "~03")
        finally:
            conn.close()


class DoctorUpworkAccessTest(CliCase):
    def check(self) -> dict:
        code, result = self.result("doctor", "--offline")
        return {c["name"]: c for c in result["checks"]}["upwork_access"]

    def test_fine_until_a_run_is_turned_away_then_a_warning_with_the_fix(self) -> None:
        self.write_config()
        self.assertEqual(self.check()["status"], "OK")
        recheck.mark_needed(recheck.marker_path(self.home / "data" / "gigradar.db"), NOW)
        check = self.check()
        self.assertEqual(check["status"], "WARN")
        self.assertIn("quick check", check["message"])
        self.assertIn("Check Upwork", check["fix"])
        recheck.clear(recheck.marker_path(self.home / "data" / "gigradar.db"))
        self.assertEqual(self.check()["status"], "OK")


if __name__ == "__main__":
    unittest.main()
