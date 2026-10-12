"""default_dir() and the command line."""
import contextlib
import io
import json
import os
import unittest
from unittest import mock

from _load import SyncTestCase, load_scheduler


class DefaultDirTests(SyncTestCase):
    def setUp(self):
        super().setUp()
        env = mock.patch.dict(os.environ)
        env.start()
        self.addCleanup(env.stop)
        for k in ("CLAUDE_SYNC_DIR", "OneDrive"):
            os.environ.pop(k, None)

    def test_claude_sync_dir_wins(self):
        one = os.path.join(self.tmp, "OneDrive")
        os.makedirs(one)
        os.environ["OneDrive"] = one
        os.environ["CLAUDE_SYNC_DIR"] = os.path.join(self.tmp, "custom")
        self.assertEqual(self.sync.default_dir(), os.path.join(self.tmp, "custom"))

    def test_claude_sync_dir_need_not_exist(self):
        os.environ["CLAUDE_SYNC_DIR"] = os.path.join(self.tmp, "not", "there")
        self.assertEqual(self.sync.default_dir(), os.path.join(self.tmp, "not", "there"))

    def test_onedrive_env_var_plus_claudesync(self):
        one = os.path.join(self.tmp, "Work OneDrive")
        os.makedirs(one)
        os.environ["OneDrive"] = one
        self.assertEqual(self.sync.default_dir(), os.path.join(one, "ClaudeSync"))

    def test_onedrive_env_var_to_missing_folder_is_skipped(self):
        os.environ["OneDrive"] = os.path.join(self.tmp, "gone")
        self.assertIsNone(self.sync.default_dir())

    def test_falls_back_to_onedrive_in_home(self):
        os.makedirs(os.path.join(self.home, "OneDrive"))
        self.assertEqual(self.sync.default_dir(), os.path.join(self.home, "OneDrive", "ClaudeSync"))

    def test_env_var_beats_home_folder(self):
        os.makedirs(os.path.join(self.home, "OneDrive"))
        one = os.path.join(self.tmp, "Other")
        os.makedirs(one)
        os.environ["OneDrive"] = one
        self.assertEqual(self.sync.default_dir(), os.path.join(one, "ClaudeSync"))

    def test_macos_cloud_storage_folder(self):
        cloud = os.path.join(self.home, "Library", "CloudStorage", "OneDrive-Personal")
        os.makedirs(cloud)
        os.makedirs(os.path.join(self.home, "Library", "CloudStorage", "GoogleDrive-me"))
        self.assertEqual(self.sync.default_dir(), os.path.join(cloud, "ClaudeSync"))

    def test_none_when_there_is_no_onedrive(self):
        self.assertIsNone(self.sync.default_dir())


class MainTests(SyncTestCase):
    def run_main(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = self.sync.main(list(argv))
        return rc, out.getvalue()

    def test_plan_prints_json_without_private_keys(self):
        self.local_skill("alpha")
        rc, out = self.run_main("plan", "--dir", self.sync_dir)
        self.assertEqual(rc, 0)
        data = json.loads(out)
        self.assertEqual(data["skills"]["push"], ["alpha"])
        self.assertEqual(data["sync_dir"], self.sync_dir)
        self.assertEqual([k for k in data if k.startswith("_")], [])
        self.assertNotIn("_identical", data)
        self.assertNotIn("_hashes", data)

    def test_public_strips_only_underscore_keys(self):
        self.assertEqual(self.sync.public({"a": 1, "_b": 2, "c_": 3}), {"a": 1, "c_": 3})

    def test_plan_changes_nothing(self):
        self.local_skill("alpha")
        self.run_main("plan", "--dir", self.sync_dir)
        self.assertEqual(os.listdir(self.remote), [])

    def test_relative_dir_is_made_absolute(self):
        cwd = os.getcwd()
        self.addCleanup(os.chdir, cwd)
        os.chdir(self.tmp)
        _, out = self.run_main("plan", "--dir", os.path.join("share", "ClaudeSync"))
        self.assertEqual(json.loads(out)["sync_dir"], self.sync_dir)

    def test_apply_push(self):
        self.local_skill("alpha")
        rc, out = self.run_main("apply", "--dir", self.sync_dir, "--push", "alpha")
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(out), {"pushed": ["alpha"], "pulled": []})
        self.assertTrue(os.path.isfile(os.path.join(self.remote, "alpha", "SKILL.md")))

    def test_apply_pull_all(self):
        self.remote_skill("beta")
        _, out = self.run_main("apply", "--dir", self.sync_dir, "--pull", "all")
        self.assertEqual(json.loads(out)["pulled"], ["beta"])

    def test_apply_without_flags_does_nothing(self):
        self.local_skill("alpha")
        _, out = self.run_main("apply", "--dir", self.sync_dir)
        self.assertEqual(json.loads(out), {"pushed": [], "pulled": []})
        self.assertEqual(os.listdir(self.remote), [])

    def test_apply_with_unknown_name_exits(self):
        with self.assertRaises(SystemExit):
            self.run_main("apply", "--dir", self.sync_dir, "--push", "nope")

    def test_manifest_command(self):
        rc, out = self.run_main("manifest", "--dir", self.sync_dir, "--plugins", "legal,marketing",
                                "--connectors", "Gmail")
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(out)["added"]["account_plugins"], ["legal", "marketing"])
        self.assertEqual(self.read_manifest()["connectors"], ["Gmail"])

    def test_status_command(self):
        self.write(os.path.join(self.sync_dir, "machines", "pc", "last-run.json"), '{"ok": true}')
        _, out = self.run_main("status", "--dir", self.sync_dir)
        self.assertEqual(json.loads(out), {"pc": {"ok": True}})

    def test_auto_returns_zero_on_success(self):
        self.local_skill("alpha")
        rc, out = self.run_main("auto", "--dir", self.sync_dir)
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(out)["pushed"], ["alpha"])

    def test_auto_returns_one_on_error(self):
        self.write(self.sync.LOCK_FILE, "1")
        rc, out = self.run_main("auto", "--dir", self.sync_dir)
        self.assertEqual(rc, 1)
        self.assertIn("error", json.loads(out))

    def test_no_dir_and_no_onedrive_exits(self):
        self.patch(self.sync, "default_dir", lambda: None)
        with self.assertRaises(SystemExit) as cm:
            self.run_main("plan")
        self.assertIn("--dir", str(cm.exception))

    def test_default_dir_is_used_without_dir_flag(self):
        self.patch(self.sync, "default_dir", lambda: self.sync_dir)
        self.local_skill("alpha")
        _, out = self.run_main("plan")
        self.assertEqual(json.loads(out)["sync_dir"], self.sync_dir)

    def test_unknown_command_is_rejected(self):
        with self.assertRaises(SystemExit) as cm:
            self.run_main("frobnicate")
        self.assertEqual(cm.exception.code, 2)


class ScheduleCommandTests(SyncTestCase):
    """`schedule` hands over to scheduler.py; the real one is never called."""

    def setUp(self):
        super().setUp()
        self.scheduler = load_scheduler()
        self.install = mock.Mock(return_value={"installed": "fake"})
        self.patch(self.scheduler, "install", self.install)
        self.patch(self.sync, "default_dir", lambda: self.sync_dir)

    def run_main(self, *argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = self.sync.main(list(argv))
        return rc, json.loads(out.getvalue())

    def test_install_with_dir_and_interval(self):
        rc, out = self.run_main("schedule", "install", "--every", "6", "--dir", self.sync_dir)
        self.assertEqual((rc, out), (0, {"installed": "fake"}))
        self.install.assert_called_once_with(
            script=os.path.abspath(self.sync.__file__), sync_dir=self.sync_dir, hours=6)

    def test_install_without_dir_leaves_it_to_the_default(self):
        self.run_main("schedule", "install")
        self.install.assert_called_once_with(
            script=os.path.abspath(self.sync.__file__), sync_dir=None, hours=4)

    def test_schedule_needs_an_action(self):
        with self.assertRaises(SystemExit) as cm:
            self.run_main("schedule")
        self.assertIn("install, remove or show", str(cm.exception))
        self.install.assert_not_called()


if __name__ == "__main__":
    unittest.main()
