"""auto(): the unattended run, its lock, log and housekeeping."""
import json
import os
import subprocess
import time
import unittest
from unittest import mock

from _load import SyncTestCase


class AutoCase(SyncTestCase):
    def setUp(self):
        super().setUp()
        self.machine = os.path.join(self.sync_dir, "machines", "testbox")
        self.last_run_path = os.path.join(self.machine, "last-run.json")
        self.log_path = os.path.join(self.machine, "sync.log")

    def auto(self):
        return self.sync.auto(self.sync_dir)

    def last_run(self):
        return json.loads(self.read(self.last_run_path))

    def log_lines(self):
        return self.read(self.log_path).splitlines()

    def make_conflict(self):
        self.local_skill("alpha", {"SKILL.md": "v1\n"})
        self.sync_all()
        self.write(os.path.join(self.skills, "alpha", "SKILL.md"), "local\n")
        self.write(os.path.join(self.remote, "alpha", "SKILL.md"), "remote\n")


class AutoRunTests(AutoCase):
    def test_clean_run_pushes_and_records_status(self):
        self.local_skill("alpha")
        st = self.auto()
        self.assertTrue(st["ok"])
        self.assertEqual((st["pushed"], st["pulled"]), (["alpha"], []))
        self.assertEqual(st["machine"], "testbox")
        self.assertRegex(st["run_at"], r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d$")
        self.assertFalse(st["needs_you"])
        self.assertNotIn("error", st)
        self.assertTrue(os.path.isfile(os.path.join(self.remote, "alpha", "SKILL.md")))

    def test_writes_last_run_json(self):
        self.local_skill("alpha")
        st = self.auto()
        self.assertEqual(self.last_run(), json.loads(json.dumps(st)))

    def test_writes_sync_log(self):
        self.local_skill("alpha")
        st = self.auto()
        (line,) = self.log_lines()
        self.assertEqual(line, st["run_at"] + "  pushed 1, pulled 0, conflicts 0, uv installed 0")

    def test_log_line_added_for_each_run(self):
        self.auto()
        self.auto()
        self.assertEqual(len(self.log_lines()), 2)

    def test_pulls_old_remote_skill_but_not_a_fresh_one(self):
        self.age(self.remote_skill("old"), 3600)
        self.remote_skill("fresh")
        st = self.auto()
        self.assertEqual((st["pulled"], st["settling"]), (["old"], ["fresh"]))
        self.assertTrue(os.path.isdir(os.path.join(self.skills, "old")))
        self.assertFalse(os.path.exists(os.path.join(self.skills, "fresh")))

    def test_second_run_is_quiet(self):
        self.local_skill("alpha")
        self.auto()
        st = self.auto()
        self.assertEqual((st["pushed"], st["pulled"], st["conflicts"]), ([], [], []))

    def test_first_run_creates_the_sync_folder(self):
        fresh = os.path.join(self.tmp, "share", "NewSync")
        self.local_skill("alpha")
        st = self.sync.auto(fresh)
        self.assertTrue(st["ok"])
        self.assertTrue(os.path.isfile(os.path.join(fresh, "skills", "alpha", "SKILL.md")))
        self.assertTrue(os.path.isfile(os.path.join(fresh, "manifest.json")))

    def test_manifest_is_updated(self):
        self.write_mcp({"gh": {"command": "x"}})
        self.uv_list = ["ruff"]
        st = self.auto()
        self.assertEqual(st["manifest_added"], {"mcp": ["gh"], "uv_tools": ["ruff"]})
        self.assertEqual(self.read_manifest()["mcp"], {"gh": {"command": "x"}})

    def test_unreachable_sync_folder_is_an_error(self):
        gone = os.path.join(self.tmp, "no-such-drive", "ClaudeSync")
        st = self.sync.auto(gone)
        self.assertFalse(st["ok"])
        self.assertIn("sync folder not reachable", st["error"])

    def test_unreachable_sync_folder_is_not_created(self):
        """A missing sync folder is reported, never created."""
        parent = os.path.join(self.tmp, "no-such-drive")
        self.sync.auto(os.path.join(parent, "ClaudeSync"))
        self.assertFalse(os.path.exists(parent))

    def test_status_is_kept_locally_even_when_unreachable(self):
        """The local last-run file records the error the shared folder can't."""
        self.sync.auto(os.path.join(self.tmp, "no-such-drive", "ClaudeSync"))
        with open(self.sync.LAST_RUN_FILE, encoding="utf-8") as fh:
            self.assertIn("not reachable", json.load(fh)["error"])

    def test_status_is_kept_locally_after_a_good_run(self):
        st = self.auto()
        with open(self.sync.LAST_RUN_FILE, encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["run_at"], st["run_at"])


class NeedsYouTests(AutoCase):
    def test_conflict(self):
        self.make_conflict()
        st = self.auto()
        self.assertTrue(st["needs_you"])
        self.assertTrue(st["ok"])
        self.assertEqual([c["name"] for c in st["conflicts"]], ["alpha"])
        self.assertEqual(self.read(os.path.join(self.skills, "alpha", "SKILL.md")), "local\n")
        self.assertEqual(self.read(os.path.join(self.remote, "alpha", "SKILL.md")), "remote\n")
        self.assertTrue(self.last_run()["needs_you"])

    def test_conflict_is_flagged_in_the_log(self):
        self.make_conflict()
        self.auto()
        self.assertTrue(self.log_lines()[-1].endswith("conflicts 1, uv installed 0, needs /claude-sync"))

    def test_missing_mcp_server(self):
        self.write_manifest(mcp={"gh": {"command": "x"}})
        st = self.auto()
        self.assertTrue(st["needs_you"])
        self.assertEqual(st["mcp_to_add"], ["gh"])
        self.assertIn("gh", self.read_manifest()["mcp"])

    def test_mcp_that_differs(self):
        self.write_manifest(mcp={"gh": {"command": "x"}})
        self.write_mcp({"gh": {"command": "y"}})
        st = self.auto()
        self.assertTrue(st["needs_you"])
        self.assertEqual(st["mcp_differs"], ["gh"])
        self.assertEqual(self.read_manifest()["mcp"]["gh"], {"command": "x"})

    def test_nothing_to_do(self):
        self.write_manifest(mcp={"gh": {"command": "x"}})
        self.write_mcp({"gh": {"command": "x"}})
        self.assertFalse(self.auto()["needs_you"])

    def test_extra_local_mcp_server_does_not_need_you(self):
        self.write_mcp({"mine": {"command": "x"}})
        self.assertFalse(self.auto()["needs_you"])


class LockTests(AutoCase):
    def lock(self):
        return self.sync.LOCK_FILE

    def test_held_lock_records_an_error_and_does_not_raise(self):
        self.write(self.lock(), "999")
        self.local_skill("alpha")
        st = self.auto()
        self.assertFalse(st["ok"])
        self.assertEqual(st["error"], "another sync is running")
        self.assertEqual(self.last_run()["error"], "another sync is running")
        self.assertEqual(self.log_lines()[-1].split("  ", 1)[1], "error: another sync is running")
        self.assertFalse(os.path.exists(os.path.join(self.remote, "alpha")))

    def test_held_lock_is_left_alone(self):
        self.write(self.lock(), "999")
        self.auto()
        self.assertEqual(self.read(self.lock()), "999")

    def test_stale_lock_is_cleared(self):
        self.write(self.lock(), "999")
        self.age(self.lock(), 31 * 60)
        self.local_skill("alpha")
        st = self.auto()
        self.assertTrue(st["ok"])
        self.assertNotIn("error", st)
        self.assertEqual(st["pushed"], ["alpha"])

    def test_lock_just_under_the_limit_is_still_held(self):
        self.write(self.lock(), "999")
        self.age(self.lock(), 29 * 60)
        self.assertEqual(self.auto()["error"], "another sync is running")

    def test_lock_is_released_after_a_run(self):
        self.auto()
        self.assertFalse(os.path.exists(self.lock()))

    def test_lock_holds_the_pid(self):
        with self.sync.Lock():
            self.assertEqual(self.read(self.lock()), str(os.getpid()))
        self.assertFalse(os.path.exists(self.lock()))

    def test_second_lock_is_refused(self):
        with self.sync.Lock():
            with self.assertRaises(SystemExit):
                with self.sync.Lock():
                    pass
            self.assertTrue(os.path.exists(self.lock()))

    def test_exit_without_a_lock_file_is_fine(self):
        with self.sync.Lock():
            os.remove(self.lock())

    def test_error_inside_the_run_is_recorded_and_lock_released(self):
        self.patch(self.sync, "plan", mock.Mock(side_effect=RuntimeError("boom")))
        st = self.auto()
        self.assertEqual(st["error"], "boom")
        self.assertFalse(st["ok"])
        self.assertFalse(os.path.exists(self.lock()))
        self.assertEqual(self.last_run()["error"], "boom")

    def test_error_without_a_message_uses_the_type(self):
        self.patch(self.sync, "plan", mock.Mock(side_effect=RuntimeError()))
        self.assertEqual(self.auto()["error"], "RuntimeError")


class UvInstallTests(AutoCase):
    def setUp(self):
        super().setUp()
        self.write_manifest(uv_tools=["ruff"])

    def fake_run(self, returncode, stderr=""):
        done = subprocess.CompletedProcess(["uv"], returncode, "", stderr)
        return mock.patch("subprocess.run", mock.Mock(return_value=done))

    def test_missing_tool_is_installed(self):
        self.patch(self.sync, "find_uv", lambda: "uv-bin")
        with self.fake_run(0) as run:
            st = self.auto()
        self.assertEqual(st["uv_installed"], ["ruff"])
        self.assertTrue(st["ok"])
        self.assertEqual(run.call_args[0][0], ["uv-bin", "tool", "install", "ruff"])

    def test_failed_install_is_reported(self):
        self.patch(self.sync, "find_uv", lambda: "uv-bin")
        with self.fake_run(1, "no such package"):
            st = self.auto()
        self.assertEqual(st["uv_installed"], [])
        self.assertEqual(st["uv_failed"], ["ruff: no such package"])
        self.assertFalse(st["ok"])
        self.assertTrue(st["needs_you"])

    def test_process_error_is_reported(self):
        self.patch(self.sync, "find_uv", lambda: "uv-bin")
        with mock.patch("subprocess.run", side_effect=OSError("cannot start")):
            st = self.auto()
        self.assertEqual(st["uv_failed"], ["ruff: cannot start"])

    def test_no_uv_is_reported_without_running_anything(self):
        st = self.auto()
        self.assertEqual(st["uv_failed"], ["ruff: uv is not installed"])
        self.assertTrue(st["needs_you"])

    def test_installed_tool_is_left_alone(self):
        self.uv_list = ["ruff"]
        self.patch(self.sync, "find_uv", lambda: "uv-bin")
        st = self.auto()
        self.assertEqual((st["uv_installed"], st["uv_failed"]), ([], []))


class LogTests(AutoCase):
    def test_log_is_trimmed_to_log_lines(self):
        self.patch(self.sync, "LOG_LINES", 5)
        for i in range(8):
            self.sync.append_log(self.sync_dir, "line %d" % i)
        self.assertEqual(self.log_lines(), ["line %d" % i for i in range(3, 8)])

    def test_default_limit_is_500(self):
        self.assertEqual(self.sync.LOG_LINES, 500)
        for i in range(502):
            self.sync.append_log(self.sync_dir, "line %d" % i)
        lines = self.log_lines()
        self.assertEqual((len(lines), lines[0], lines[-1]), (500, "line 2", "line 501"))

    def test_auto_trims_its_own_log(self):
        self.patch(self.sync, "LOG_LINES", 3)
        for _ in range(6):
            self.auto()
        self.assertEqual(len(self.log_lines()), 3)

    def test_log_ends_with_a_newline(self):
        self.sync.append_log(self.sync_dir, "x")
        self.assertTrue(self.read(self.log_path).replace("\r\n", "\n").endswith("x\n"))


class PruneBackupsTests(AutoCase):
    def backup(self, name, days):
        d = os.path.join(self.sync_dir, ".backup", name)
        self.write(os.path.join(d, "local", "alpha", "SKILL.md"), "x")
        self.age(d, 0)
        t = time.time() - days * 86400
        os.utime(d, (t, t))
        return d

    def test_old_backups_go_recent_ones_stay(self):
        old = self.backup("20250101-000000", 45)
        recent = self.backup("20260101-000000", 5)
        self.sync.prune_backups(self.sync_dir)
        self.assertFalse(os.path.exists(old))
        self.assertTrue(os.path.isdir(recent))

    def test_only_backups_are_touched(self):
        self.backup("old", 400)
        self.local_skill("alpha")
        self.remote_skill("alpha")
        self.write_manifest()
        self.write(self.log_path, "log\n")
        for p in (os.path.join(self.remote, "alpha"), os.path.join(self.sync_dir, "manifest.json"), self.log_path):
            self.age(p, 400 * 86400)
        self.sync.prune_backups(self.sync_dir)
        self.assertTrue(os.path.isdir(os.path.join(self.remote, "alpha")))
        self.assertTrue(os.path.isfile(os.path.join(self.sync_dir, "manifest.json")))
        self.assertTrue(os.path.isfile(self.log_path))
        self.assertTrue(os.path.isdir(os.path.join(self.skills, "alpha")))

    def test_days_argument(self):
        d = self.backup("x", 3)
        self.sync.prune_backups(self.sync_dir, days=2)
        self.assertFalse(os.path.exists(d))

    def test_missing_backup_folder_is_fine(self):
        self.sync.prune_backups(self.sync_dir)

    def test_auto_prunes(self):
        old = self.backup("old", 45)
        recent = self.backup("recent", 1)
        self.auto()
        self.assertFalse(os.path.exists(old))
        self.assertTrue(os.path.isdir(recent))


class StatusTests(AutoCase):
    def test_reads_every_machine(self):
        self.write(os.path.join(self.sync_dir, "machines", "pc", "last-run.json"), '{"ok": true}')
        self.write(os.path.join(self.sync_dir, "machines", "laptop", "last-run.json"), '{"ok": false}')
        self.write(os.path.join(self.sync_dir, "machines", "broken", "last-run.json"), "{nope")
        self.write(os.path.join(self.sync_dir, "machines", "empty", "sync.log"), "x")
        self.assertEqual(self.sync.all_status(self.sync_dir),
                         {"broken": {}, "laptop": {"ok": False}, "pc": {"ok": True}})

    def test_no_machines_is_empty(self):
        self.assertEqual(self.sync.all_status(self.sync_dir), {})

    def test_shows_the_run_just_made(self):
        self.auto()
        self.assertEqual(list(self.sync.all_status(self.sync_dir)), ["testbox"])


if __name__ == "__main__":
    unittest.main()
