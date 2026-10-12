"""apply(): copying, backups and the refusals."""
import os
import types
import unittest
import datetime

from _load import SyncTestCase


class NamesTests(SyncTestCase):
    def test_empty_is_nothing(self):
        self.assertEqual(self.sync.names(None, ["a"]), [])
        self.assertEqual(self.sync.names("", ["a"]), [])

    def test_all_is_the_default_list(self):
        default = ["a", "b"]
        got = self.sync.names("all", default)
        self.assertEqual(got, ["a", "b"])
        self.assertIsNot(got, default)

    def test_comma_list_is_trimmed(self):
        self.assertEqual(self.sync.names(" a, b ,,c", []), ["a", "b", "c"])


class CopyTests(SyncTestCase):
    def test_push_copies_whole_folder(self):
        self.local_skill("alpha", {"SKILL.md": "s\n", "scripts/run.py": "print(1)\n", "ref/a/b.md": "deep"})
        done = self.apply(push="alpha")
        self.assertEqual(done, {"pushed": ["alpha"], "pulled": []})
        for rel, text in (("SKILL.md", "s\n"), ("scripts/run.py", "print(1)\n"), ("ref/a/b.md", "deep")):
            self.assertEqual(self.read(os.path.join(self.remote, "alpha", *rel.split("/"))), text)

    def test_pull_copies_whole_folder(self):
        self.remote_skill("beta", {"SKILL.md": "s\n", "x/y.txt": "y"})
        done = self.apply(pull="beta")
        self.assertEqual(done, {"pushed": [], "pulled": ["beta"]})
        self.assertEqual(self.read(os.path.join(self.skills, "beta", "x", "y.txt")), "y")

    def test_push_and_pull_in_one_call(self):
        self.local_skill("a")
        self.remote_skill("b")
        done = self.apply(push="a", pull="b")
        self.assertEqual((done["pushed"], done["pulled"]), (["a"], ["b"]))

    def test_all_uses_the_plan(self):
        self.local_skill("a")
        self.local_skill("b")
        self.remote_skill("c")
        done = self.sync_all()
        self.assertEqual((done["pushed"], done["pulled"]), (["a", "b"], ["c"]))

    def test_ignored_files_are_not_copied(self):
        src = self.local_skill("alpha")
        for rel in (".git/HEAD", "__pycache__/x.pyc", "node_modules/p/i.js", "top.pyc"):
            self.write(os.path.join(src, *rel.split("/")), "junk")
        self.apply(push="alpha")
        self.assertEqual(os.listdir(os.path.join(self.remote, "alpha")), ["SKILL.md"])

    def test_state_recorded_so_next_plan_is_clean(self):
        self.local_skill("a")
        self.remote_skill("b")
        self.sync_all()
        p = self.plan()
        self.assertClean(p)
        self.assertEqual(p["skills"]["identical"], 2)

    def test_identical_skills_get_recorded_too(self):
        self.local_skill("same")
        self.remote_skill("same")
        self.sync_all()
        self.assertIn("same", self.sync.load_state(self.sync_dir))

    def test_no_backup_when_nothing_is_overwritten(self):
        self.local_skill("a")
        done = self.apply(push="a")
        self.assertNotIn("backup", done)
        self.assertFalse(os.path.exists(os.path.join(self.sync_dir, ".backup")))


class BackupTests(SyncTestCase):
    def conflict(self):
        self.local_skill("alpha", {"SKILL.md": "old\n"})
        self.sync_all()
        self.write(os.path.join(self.skills, "alpha", "SKILL.md"), "local new\n")
        self.write(os.path.join(self.remote, "alpha", "SKILL.md"), "remote new\n")

    def test_push_backs_up_the_remote_copy(self):
        self.conflict()
        done = self.apply(push="alpha")
        backup = done["backup"]
        self.assertEqual(os.path.dirname(backup), os.path.join(self.sync_dir, ".backup"))
        self.assertRegex(os.path.basename(backup), r"^\d{8}-\d{6}$")
        self.assertEqual(self.read(os.path.join(backup, "remote", "alpha", "SKILL.md")), "remote new\n")
        self.assertFalse(os.path.exists(os.path.join(backup, "local")))
        self.assertEqual(self.read(os.path.join(self.remote, "alpha", "SKILL.md")), "local new\n")

    def test_pull_backs_up_the_local_copy(self):
        self.conflict()
        done = self.apply(pull="alpha")
        backup = done["backup"]
        self.assertEqual(self.read(os.path.join(backup, "local", "alpha", "SKILL.md")), "local new\n")
        self.assertFalse(os.path.exists(os.path.join(backup, "remote")))
        self.assertEqual(self.read(os.path.join(self.skills, "alpha", "SKILL.md")), "remote new\n")

    def test_backup_drops_ignored_files(self):
        self.conflict()
        self.write(os.path.join(self.remote, "alpha", "node_modules", "x.js"), "junk")
        backup = self.apply(push="alpha")["backup"]
        self.assertEqual(os.listdir(os.path.join(backup, "remote", "alpha")), ["SKILL.md"])

    def test_backup_keeps_nested_files(self):
        self.conflict()
        self.write(os.path.join(self.remote, "alpha", "ref", "deep.md"), "deep")
        backup = self.apply(push="alpha")["backup"]
        self.assertEqual(self.read(os.path.join(backup, "remote", "alpha", "ref", "deep.md")), "deep")

    def test_overwrite_removes_files_that_are_gone_from_the_source(self):
        self.conflict()
        self.write(os.path.join(self.remote, "alpha", "extra.md"), "only remote")
        self.apply(push="alpha")
        self.assertFalse(os.path.exists(os.path.join(self.remote, "alpha", "extra.md")))

    def test_two_overwrites_in_the_same_second_are_both_backed_up(self):
        """Two overwrites of one skill in the same second both get a backup."""
        class Fixed(datetime.datetime):
            @classmethod
            def now(cls, tz=None):
                return cls(2026, 1, 2, 3, 4, 5)

        self.patch(self.sync, "datetime", types.SimpleNamespace(datetime=Fixed, date=datetime.date))
        self.conflict()
        self.apply(push="alpha")
        self.write(os.path.join(self.skills, "alpha", "SKILL.md"), "local newer\n")
        self.apply(push="alpha")
        self.assertEqual(self.read(os.path.join(self.remote, "alpha", "SKILL.md")), "local newer\n")


class RefusalTests(SyncTestCase):
    def test_name_in_both_push_and_pull(self):
        self.local_skill("a")
        self.remote_skill("a", {"SKILL.md": "other\n"})
        with self.assertRaises(SystemExit) as cm:
            self.apply(push="a", pull="a")
        self.assertIn("both pushed and pulled", str(cm.exception))
        self.assertIn("a", str(cm.exception))
        self.assertEqual(self.read(os.path.join(self.remote, "a", "SKILL.md")), "other\n")

    def test_overlap_is_found_in_a_longer_list(self):
        for n in ("a", "b", "c"):
            self.local_skill(n)
        with self.assertRaises(SystemExit) as cm:
            self.apply(push="a,b", pull="b,c")
        self.assertIn("b", str(cm.exception))
        self.assertEqual(os.listdir(self.remote), [])

    def test_unknown_name_to_push(self):
        self.local_skill("a")
        with self.assertRaises(SystemExit) as cm:
            self.apply(push="nope")
        self.assertIn("no local skill named nope", str(cm.exception))

    def test_unknown_name_to_pull(self):
        with self.assertRaises(SystemExit) as cm:
            self.apply(pull="nope")
        self.assertIn("no synced skill named nope", str(cm.exception))

    def test_folder_without_skill_md_is_refused(self):
        self.write(os.path.join(self.skills, "notes", "README.md"), "x")
        with self.assertRaises(SystemExit):
            self.apply(push="notes")
        self.assertFalse(os.path.exists(os.path.join(self.remote, "notes")))

    def test_name_cannot_escape_the_skills_folder(self):
        """A name that is a path is refused before anything is copied or deleted."""
        self.write(os.path.join(self.tmp, "outside", "SKILL.md"), "x")
        with self.assertRaises(SystemExit):
            self.apply(push=os.path.join("..", "..", "..", "outside"))
        self.assertTrue(os.path.isfile(os.path.join(self.tmp, "outside", "SKILL.md")))


class NeverDeletesTests(SyncTestCase):
    def test_push_all_keeps_remote_only_skills(self):
        self.local_skill("mine")
        self.remote_skill("theirs")
        self.apply(push="all")
        self.assertTrue(os.path.isfile(os.path.join(self.remote, "theirs", "SKILL.md")))
        self.assertFalse(os.path.exists(os.path.join(self.skills, "theirs")))

    def test_pull_all_keeps_local_only_skills(self):
        self.local_skill("mine")
        self.remote_skill("theirs")
        self.apply(pull="all")
        self.assertTrue(os.path.isfile(os.path.join(self.skills, "mine", "SKILL.md")))
        self.assertFalse(os.path.exists(os.path.join(self.remote, "mine")))

    def test_full_sync_keeps_both_sides(self):
        self.local_skill("mine")
        self.remote_skill("theirs")
        self.sync_all()
        self.assertEqual(sorted(os.listdir(self.skills)), ["mine", "theirs"])
        self.assertEqual(sorted(os.listdir(self.remote)), ["mine", "theirs"])

    def test_unrelated_files_in_the_skills_folder_survive(self):
        keep = self.write(os.path.join(self.skills, "README.txt"), "keep")
        other = self.local_skill("loose", {"notes.md": "not a skill"})
        self.remote_skill("theirs")
        self.sync_all()
        self.assertTrue(os.path.isfile(keep))
        self.assertTrue(os.path.isfile(os.path.join(other, "notes.md")))


class StateTests(SyncTestCase):
    def test_roundtrip_and_per_folder(self):
        self.sync.save_state(self.sync_dir, {"a": "h1"})
        self.sync.save_state(os.path.join(self.tmp, "x"), {"b": "h2"})
        self.assertEqual(self.sync.load_state(self.sync_dir), {"a": "h1"})
        self.assertEqual(self.sync.load_state(os.path.join(self.tmp, "x")), {"b": "h2"})

    def test_missing_or_corrupt_file_is_empty(self):
        self.assertEqual(self.sync.load_state(self.sync_dir), {})
        self.write(self.sync.STATE_FILE, "{not json")
        self.assertEqual(self.sync.load_state(self.sync_dir), {})

    def test_state_is_saved_after_each_copy(self):
        self.local_skill("a")
        self.local_skill("b")
        real_copy = self.sync.copy_skill
        calls = []

        def flaky(src, dst, backup_root):
            if os.path.basename(src) == "b":
                raise OSError("disk went away")
            calls.append(src)
            return real_copy(src, dst, backup_root)

        self.patch(self.sync, "copy_skill", flaky)
        with self.assertRaises(OSError):
            self.apply(push="a,b")
        self.assertEqual(list(self.sync.load_state(self.sync_dir)), ["a"])
        self.assertEqual(len(calls), 1)

    def test_write_json_leaves_no_temp_file(self):
        path = os.path.join(self.tmp, "j", "x.json")
        self.sync.write_json(path, {"b": 1, "a": 2})
        self.assertEqual(os.listdir(os.path.dirname(path)), ["x.json"])
        text = self.read(path).replace("\r\n", "\n")
        self.assertEqual(text, '{\n  "a": 2,\n  "b": 1\n}\n')


if __name__ == "__main__":
    unittest.main()
