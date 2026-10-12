"""scheduler.py: the pure builders, and install/remove/show with every
external program mocked."""
import base64
import os
import plistlib
import shlex
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from _load import load_scheduler, no_subprocess


class FakeModule:
    """Stands in for `os` or `sys` inside scheduler.py only."""

    def __init__(self, real, **overrides):
        self._real = real
        self.__dict__.update(overrides)

    def __getattr__(self, name):
        return getattr(self._real, name)


class SchedulerCase(unittest.TestCase):
    def setUp(self):
        self.s = load_scheduler()
        no_subprocess(self)

    def patch(self, name, value):
        p = mock.patch.object(self.s, name, value)
        p.start()
        self.addCleanup(p.stop)
        return value

    def as_platform(self, os_name, platform):
        self.patch("os", FakeModule(os, name=os_name))
        self.patch("sys", FakeModule(sys, platform=platform))


class CommandTests(SchedulerCase):
    def test_runs_auto_in_isolated_mode(self):
        self.assertEqual(self.s.command("/x/sync.py", python="/usr/bin/python3"),
                         ["/usr/bin/python3", "-I", "/x/sync.py", "auto"])

    def test_dir_only_when_given(self):
        self.assertNotIn("--dir", self.s.command("/x/sync.py", python="py"))
        self.assertNotIn("--dir", self.s.command("/x/sync.py", sync_dir="", python="py"))
        self.assertEqual(self.s.command("/x/sync.py", "/shared", python="py")[-2:], ["--dir", "/shared"])

    def test_default_python_is_the_current_one(self):
        args = self.s.command("/x/sync.py")
        self.assertEqual(args[1:], ["-I", "/x/sync.py", "auto"])
        self.assertEqual(args[0], self.s.python_exe())

    def test_python_exe_prefers_pythonw_on_windows_only(self):
        with tempfile.TemporaryDirectory() as d:
            exe = os.path.join(d, "python.exe")
            pyw = os.path.join(d, "pythonw.exe")
            for p in (exe, pyw):
                open(p, "w").close()
            self.patch("sys", FakeModule(sys, executable=exe))
            expected = pyw if os.name == "nt" else exe
            self.assertEqual(self.s.python_exe(), expected)

    def test_python_exe_without_pythonw(self):
        with tempfile.TemporaryDirectory() as d:
            exe = os.path.join(d, "python.exe")
            open(exe, "w").close()
            self.patch("sys", FakeModule(sys, executable=exe))
            self.assertEqual(self.s.python_exe(), exe)


class WindowsScriptTests(SchedulerCase):
    ARGS = ["C:\\Python\\pythonw.exe", "-I", "C:\\skills\\sync.py", "auto"]

    def script(self, args=None, hours=4):
        return self.s.windows_script(args or self.ARGS, hours)

    def test_task_name(self):
        self.assertIn("-TaskName 'ClaudeSync'", self.script())

    def test_interval(self):
        self.assertIn("-RepetitionInterval (New-TimeSpan -Hours 4)", self.script(hours=4))
        self.assertIn("-RepetitionInterval (New-TimeSpan -Hours 12)", self.script(hours=12))

    def test_program_and_arguments(self):
        s = self.script()
        self.assertIn("-Execute 'C:\\Python\\pythonw.exe'", s)
        self.assertIn("-Argument '-I C:\\skills\\sync.py auto'", s)

    def test_arguments_with_spaces_are_double_quoted(self):
        s = self.script(["C:\\Python\\pythonw.exe", "-I", "C:\\My Skills\\sync.py", "auto", "--dir", "D:\\One Drive\\ClaudeSync"])
        self.assertIn("-Argument '-I \"C:\\My Skills\\sync.py\" auto --dir \"D:\\One Drive\\ClaudeSync\"'", s)

    def test_single_quotes_in_paths_are_doubled(self):
        s = self.script(["C:\\Users\\O'Brien\\pythonw.exe", "-I", "C:\\O'Brien\\sync.py", "auto"])
        self.assertIn("-Execute 'C:\\Users\\O''Brien\\pythonw.exe'", s)
        self.assertIn("-Argument '-I C:\\O''Brien\\sync.py auto'", s)
        self.assertNotIn("O'Brien", s)

    def test_has_logon_fallback(self):
        s = self.script()
        self.assertIn("New-ScheduledTaskTrigger -AtLogOn", s)
        self.assertIn("-Trigger @($logon, $every)", s)
        self.assertIn("catch {", s)
        self.assertIn("-Trigger $every ", s)
        self.assertIn("'logon+interval'", s)
        self.assertEqual(s.rstrip().splitlines()[-2].strip(), "'interval'")

    def test_logon_user_is_domain_backslash_user(self):
        self.assertIn('-User "$env:USERDOMAIN\\$env:USERNAME"', self.script())

    def test_runs_missed_tasks_without_blocking_overlap(self):
        s = self.script()
        for flag in ("-StartWhenAvailable", "-AllowStartIfOnBatteries", "-DontStopIfGoingOnBatteries",
                     "-MultipleInstances IgnoreNew", "-Force"):
            self.assertIn(flag, s)

    def test_win_args_quoting(self):
        self.assertEqual(self.s._win_args(["a", "b c", ""]), 'a "b c" ""')

    def test_ps_quoting(self):
        self.assertEqual(self.s._ps("it's"), "'it''s'")


class LaunchdPlistTests(SchedulerCase):
    ARGS = ["/usr/bin/python3", "-I", "/Users/me/.claude/skills/claude-sync/sync.py", "auto"]

    def load(self, args=None, hours=4):
        return plistlib.loads(self.s.launchd_plist(args or self.ARGS, hours))

    def test_parses_and_runs_at_load(self):
        d = self.load()
        self.assertIs(d["RunAtLoad"], True)
        self.assertEqual(d["Label"], "com.claude-sync.auto")
        self.assertEqual(d["ProgramArguments"], self.ARGS)

    def test_start_interval_is_hours_in_seconds(self):
        self.assertEqual(self.load(hours=4)["StartInterval"], 4 * 3600)
        self.assertEqual(self.load(hours=1)["StartInterval"], 3600)
        self.assertEqual(self.load(hours=24)["StartInterval"], 86400)

    def test_paths_with_spaces_stay_separate_arguments(self):
        args = ["/Applications/My Python/python3", "-I", "/Users/me/My Skills/sync.py", "auto"]
        self.assertEqual(self.load(args)["ProgramArguments"], args)

    def test_quiet_background_job(self):
        d = self.load()
        self.assertEqual(d["ProcessType"], "Background")
        self.assertEqual((d["StandardOutPath"], d["StandardErrorPath"]), ("/dev/null", "/dev/null"))


class CronLinesTests(SchedulerCase):
    ARGS = ["/usr/bin/python3", "-I", "/home/me/my skills/sync.py", "auto", "--dir", "/mnt/one drive/ClaudeSync"]

    def test_reboot_and_interval_lines(self):
        reboot, every = self.s.cron_lines(self.ARGS, 4)
        self.assertTrue(reboot.startswith("@reboot "))
        self.assertTrue(every.startswith("0 */4 * * * "))

    def test_both_lines_are_tagged_with_the_marker(self):
        for line in self.s.cron_lines(self.ARGS, 4):
            self.assertTrue(line.endswith(" # claude-sync"))
            self.assertIn(self.s.CRON_MARKER, line)

    def test_paths_are_shell_quoted(self):
        for line in self.s.cron_lines(self.ARGS, 4):
            self.assertIn("'/home/me/my skills/sync.py'", line)
            self.assertIn("'/mnt/one drive/ClaudeSync'", line)
            self.assertIn("/usr/bin/python3 -I ", line)

    def test_command_survives_a_shell_round_trip(self):
        for line in self.s.cron_lines(self.ARGS, 4):
            cmd = line.split(" # claude-sync")[0]
            cmd = cmd[len("@reboot "):] if line.startswith("@reboot") else cmd[len("0 */4 * * * "):]
            self.assertEqual(shlex.split(cmd), self.ARGS + [">/dev/null", "2>&1"])

    def test_interval_follows_hours(self):
        self.assertTrue(self.s.cron_lines(self.ARGS, 6)[1].startswith("0 */6 * * * "))

    def test_a_day_or_more_runs_daily_at_midnight(self):
        self.assertTrue(self.s.cron_lines(self.ARGS, 24)[1].startswith("0 0 */1 * * "))
        self.assertTrue(self.s.cron_lines(self.ARGS, 48)[1].startswith("0 0 */2 * * "))

    def test_output_is_discarded(self):
        for line in self.s.cron_lines(self.ARGS, 4):
            self.assertIn(">/dev/null 2>&1", line)


class InstallValidationTests(SchedulerCase):
    def test_rejects_less_than_one_hour(self):
        for hours in (0, -3, 0.5):
            with self.assertRaises(SystemExit) as cm:
                self.s.install("/x/sync.py", hours=hours)
            self.assertIn("at least 1 hour", str(cm.exception))

    def test_nothing_runs_when_rejected(self):
        write = self.patch("_crontab_write", mock.Mock())
        ps = self.patch("_powershell", mock.Mock())
        lc = self.patch("_launchctl", mock.Mock())
        with self.assertRaises(SystemExit):
            self.s.install("/x/sync.py", hours=0)
        for m in (write, ps, lc):
            m.assert_not_called()


class InstallWithMocksTests(SchedulerCase):
    """install/remove/show on each platform, with every helper mocked."""

    def test_windows_install_with_logon_trigger(self):
        self.as_platform("nt", "win32")
        ps = self.patch("_powershell", mock.Mock(return_value="logon+interval"))
        out = self.s.install("C:\\s\\sync.py", sync_dir="D:\\Sync", hours=6)
        self.assertEqual(out["installed"], "Windows Task Scheduler")
        self.assertEqual(out["triggers"], "at login and every 6 hours")
        self.assertEqual(out["command"][1:], ["-I", "C:\\s\\sync.py", "auto", "--dir", "D:\\Sync"])
        self.assertIn("New-TimeSpan -Hours 6", ps.call_args[0][0])

    def test_windows_install_without_logon_trigger(self):
        self.as_platform("nt", "win32")
        self.patch("_powershell", mock.Mock(return_value="interval"))
        out = self.s.install("C:\\s\\sync.py", hours=4)
        self.assertIn("login trigger not permitted", out["triggers"])
        self.assertNotIn("--dir", out["command"])

    def test_windows_remove_and_show(self):
        self.as_platform("nt", "win32")
        ps = self.patch("_powershell", mock.Mock(return_value="none"))
        self.assertEqual(self.s.show(), {"scheduled": False})
        self.assertEqual(self.s.remove()["removed"], "Windows Task Scheduler")
        self.assertIn("Unregister-ScheduledTask -TaskName 'ClaudeSync'", ps.call_args[0][0])
        ps.return_value = '{"state": "Ready", "last_result": 0}'
        self.assertEqual(self.s.show(), {"scheduled": True, "state": "Ready", "last_result": 0})

    def test_cron_install_replaces_old_lines_and_keeps_others(self):
        self.as_platform("posix", "linux")
        self.patch("_crontab_read", mock.Mock(return_value=[
            "MAILTO=me", "5 4 * * * backup.sh", "@reboot old # claude-sync", "0 */1 * * * old # claude-sync"]))
        write = self.patch("_crontab_write", mock.Mock())
        out = self.s.install("/x/sync.py", sync_dir="/shared", hours=3)
        (lines,) = write.call_args[0]
        self.assertEqual(lines[:2], ["MAILTO=me", "5 4 * * * backup.sh"])
        self.assertEqual(len([l for l in lines if "# claude-sync" in l]), 2)
        self.assertTrue(lines[2].startswith("@reboot "))
        self.assertTrue(lines[3].startswith("0 */3 * * * "))
        self.assertNotIn("old", " ".join(lines[2:]))
        self.assertEqual(out["installed"], "cron")
        self.assertEqual(out["triggers"], "at boot and every 3 hours")

    def test_cron_remove_only_drops_tagged_lines(self):
        self.as_platform("posix", "linux")
        self.patch("_crontab_read", mock.Mock(return_value=["5 4 * * * backup.sh", "@reboot x # claude-sync"]))
        write = self.patch("_crontab_write", mock.Mock())
        self.assertEqual(self.s.remove(), {"removed": "cron"})
        write.assert_called_once_with(["5 4 * * * backup.sh"])

    def test_cron_show(self):
        self.as_platform("posix", "linux")
        read = self.patch("_crontab_read", mock.Mock(return_value=["a", "b # claude-sync"]))
        self.assertEqual(self.s.show(), {"scheduled": True, "cron": ["b # claude-sync"]})
        read.return_value = ["a"]
        self.assertEqual(self.s.show(), {"scheduled": False, "cron": []})

    def test_macos_install_writes_plist_and_loads_it(self):
        self.as_platform("posix", "darwin")
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "LaunchAgents", "com.claude-sync.auto.plist")
            self.patch("launchd_path", lambda: path)
            lc = self.patch("_launchctl", mock.Mock(return_value=subprocess.CompletedProcess([], 0, "", "")))
            out = self.s.install("/x/sync.py", hours=2)
            with open(path, "rb") as fh:
                plist = plistlib.load(fh)
        self.assertEqual(plist["StartInterval"], 7200)
        self.assertEqual(out["installed"], "launchd")
        self.assertEqual([c[0][0] for c in lc.call_args_list], ["unload", "load"])

    def test_macos_install_reports_launchctl_failure(self):
        self.as_platform("posix", "darwin")
        with tempfile.TemporaryDirectory() as d:
            self.patch("launchd_path", lambda: os.path.join(d, "x.plist"))
            self.patch("_launchctl", mock.Mock(return_value=subprocess.CompletedProcess([], 1, "", "bad plist")))
            with self.assertRaises(SystemExit) as cm:
                self.s.install("/x/sync.py")
        self.assertIn("bad plist", str(cm.exception))

    def test_macos_remove_deletes_the_plist(self):
        self.as_platform("posix", "darwin")
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "x.plist")
            open(path, "w").close()
            self.patch("launchd_path", lambda: path)
            self.patch("_launchctl", mock.Mock())
            self.assertEqual(self.s.remove()["removed"], "launchd")
            self.assertFalse(os.path.exists(path))
            self.assertEqual(self.s.remove()["removed"], "launchd")


class ProcessHelperTests(SchedulerCase):
    def run_mock(self, returncode=0, stdout="", stderr=""):
        done = subprocess.CompletedProcess([], returncode, stdout, stderr)
        return mock.patch("subprocess.run", mock.Mock(return_value=done))

    def test_powershell_sends_the_script_encoded(self):
        with self.run_mock(stdout=" ok \n") as run:
            self.assertEqual(self.s._powershell("Get-Date 'x'"), "ok")
        args = run.call_args[0][0]
        self.assertEqual(args[:4], ["powershell", "-NoProfile", "-NonInteractive", "-EncodedCommand"])
        self.assertEqual(base64.b64decode(args[4]).decode("utf-16-le"), "Get-Date 'x'")

    def test_powershell_failure_is_reported(self):
        with self.run_mock(returncode=1, stderr="Access denied"):
            with self.assertRaises(SystemExit) as cm:
                self.s._powershell("x")
        self.assertIn("Access denied", str(cm.exception))

    def test_crontab_read(self):
        with self.run_mock(stdout="a\nb\n"):
            self.assertEqual(self.s._crontab_read(), ["a", "b"])
        with self.run_mock(returncode=1):
            self.assertEqual(self.s._crontab_read(), [])

    def test_crontab_write(self):
        with self.run_mock() as run:
            self.s._crontab_write(["a", "b"])
        self.assertEqual(run.call_args[0][0], ["crontab", "-"])
        self.assertEqual(run.call_args[1]["input"], "a\nb\n")
        with self.run_mock(returncode=1, stderr="bad"):
            with self.assertRaises(SystemExit):
                self.s._crontab_write(["a"])


if __name__ == "__main__":
    unittest.main()
