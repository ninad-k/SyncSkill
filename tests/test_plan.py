"""plan(): which skills to push, pull, hold back or flag as conflicts."""
import hashlib
import os
import shutil
import time
import unittest

from _load import SyncTestCase


class SkillNamesTests(SyncTestCase):
    def test_only_folders_with_skill_md(self):
        self.local_skill("good")
        self.write(os.path.join(self.skills, "no-skill-md", "README.md"), "x")
        self.write(os.path.join(self.skills, "loose-file.txt"), "x")
        self.assertEqual(self.sync.skill_names(self.skills), {"good"})

    def test_missing_folder_is_empty(self):
        self.assertEqual(self.sync.skill_names(os.path.join(self.tmp, "nope")), set())


class DigestTests(SyncTestCase):
    def pair(self):
        files = {"SKILL.md": "# s\n", "scripts/run.py": "print(1)\n"}
        return self.local_skill("one", files), self.local_skill("two", files)

    def test_ignored_files_do_not_change_hash(self):
        one, two = self.pair()
        for rel in (".git/config", "__pycache__/m.cpython-38.pyc", "node_modules/p/index.js",
                    ".venv/lib/x.py", "stray.pyc", "scripts/__pycache__/r.pyc", "scripts/old.pyc"):
            self.write(os.path.join(two, *rel.split("/")), "junk")
        self.assertEqual(self.sync.digest(one)[0], self.sync.digest(two)[0])

    def test_ignored_files_do_not_set_modified_time(self):
        one, _ = self.pair()
        self.age(one, 5000)
        junk = self.write(os.path.join(one, "node_modules", "x.js"), "junk")
        self.age(junk, 0)
        self.assertAlmostEqual(self.sync.digest(one)[1], time.time() - 5000, delta=5)

    def test_content_change_changes_hash(self):
        one, two = self.pair()
        self.write(os.path.join(two, "scripts", "run.py"), "print(2)\n")
        self.assertNotEqual(self.sync.digest(one)[0], self.sync.digest(two)[0])

    def test_rename_changes_hash(self):
        one, two = self.pair()
        os.rename(os.path.join(two, "scripts", "run.py"), os.path.join(two, "scripts", "go.py"))
        self.assertNotEqual(self.sync.digest(one)[0], self.sync.digest(two)[0])

    def test_hash_uses_forward_slash_paths(self):
        one, _ = self.pair()
        h = hashlib.sha256()
        for rel, text in (("SKILL.md", "# s\n"), ("scripts/run.py", "print(1)\n")):
            h.update(rel.encode("utf-8"))
            h.update(hashlib.sha256(text.encode("utf-8")).digest())
        self.assertEqual(self.sync.digest(one)[0], h.hexdigest())

    def test_newest_file_time(self):
        one, _ = self.pair()
        self.age(one, 9000)
        self.age(os.path.join(one, "scripts", "run.py"), 100)
        self.assertAlmostEqual(self.sync.digest(one)[1], time.time() - 100, delta=5)


class PlanBasicsTests(SyncTestCase):
    def test_local_only_is_push(self):
        self.local_skill("alpha")
        s = self.plan()["skills"]
        self.assertEqual((s["push"], s["pull"], s["conflicts"], s["identical"]), (["alpha"], [], [], 0))

    def test_remote_only_is_pull(self):
        self.remote_skill("beta")
        s = self.plan()["skills"]
        self.assertEqual((s["push"], s["pull"], s["conflicts"], s["identical"]), ([], ["beta"], [], 0))

    def test_identical_is_counted(self):
        files = {"SKILL.md": "same\n", "ref/a.md": "a"}
        self.local_skill("gamma", files)
        self.remote_skill("gamma", files)
        p = self.plan()
        self.assertClean(p)
        self.assertEqual(p["skills"]["identical"], 1)
        self.assertEqual(p["_identical"], ["gamma"])
        self.assertEqual(p["_hashes"]["gamma"]["local"], p["_hashes"]["gamma"]["remote"])

    def test_folders_without_skill_md_are_ignored(self):
        self.write(os.path.join(self.skills, "notes", "README.md"), "x")
        self.write(os.path.join(self.remote, "junk", "file.txt"), "x")
        self.write(os.path.join(self.skills, "loose.txt"), "x")
        p = self.plan()
        self.assertClean(p)
        self.assertEqual(p["skills"]["identical"], 0)
        self.assertNotIn("notes", p["_hashes"])
        self.assertNotIn("junk", p["_hashes"])

    def test_ignored_files_do_not_cause_a_difference(self):
        a = self.local_skill("delta")
        b = self.remote_skill("delta")
        self.write(os.path.join(a, ".git", "HEAD"), "ref")
        self.write(os.path.join(a, "__pycache__", "x.pyc"), "1")
        self.write(os.path.join(b, "node_modules", "m", "i.js"), "2")
        self.write(os.path.join(b, "cache.pyc"), "3")
        p = self.plan()
        self.assertClean(p)
        self.assertEqual(p["skills"]["identical"], 1)

    def test_names_come_out_sorted(self):
        for n in ("zed", "alpha", "mid"):
            self.local_skill(n)
        self.assertEqual(self.plan()["skills"]["push"], ["alpha", "mid", "zed"])

    def test_missing_sync_folder_is_fine(self):
        self.local_skill("alpha")
        missing = os.path.join(self.tmp, "elsewhere", "ClaudeSync")
        p = self.sync.plan(missing)
        self.assertEqual(p["skills"]["push"], ["alpha"])
        self.assertFalse(p["manifest_exists"])
        self.assertFalse(os.path.exists(missing))

    def test_plan_changes_nothing(self):
        self.local_skill("alpha")
        self.remote_skill("beta")
        self.plan()
        self.assertEqual(sorted(os.listdir(self.skills)), ["alpha"])
        self.assertEqual(sorted(os.listdir(self.remote)), ["beta"])
        self.assertFalse(os.path.exists(self.sync.STATE_FILE))

    def test_different_copies_with_no_history_are_a_conflict(self):
        self.local_skill("alpha", {"SKILL.md": "local"})
        self.remote_skill("alpha", {"SKILL.md": "remote"})
        s = self.plan()["skills"]
        self.assertEqual([c["name"] for c in s["conflicts"]], ["alpha"])
        self.assertEqual((s["push"], s["pull"]), ([], []))

    def test_removed_remotely_comes_back_as_push(self):
        self.local_skill("alpha")
        self.sync_all()
        shutil.rmtree(os.path.join(self.remote, "alpha"))
        self.assertEqual(self.plan()["skills"]["push"], ["alpha"])

    def test_removed_locally_comes_back_as_pull(self):
        self.local_skill("alpha")
        self.sync_all()
        shutil.rmtree(os.path.join(self.skills, "alpha"))
        self.assertEqual(self.plan()["skills"]["pull"], ["alpha"])


class ThreeWayTests(SyncTestCase):
    def setUp(self):
        super().setUp()
        self.local_skill("alpha", {"SKILL.md": "v1\n", "ref/notes.md": "n\n"})
        self.sync_all()
        self.assertClean(self.plan())
        self.lpath = os.path.join(self.skills, "alpha", "SKILL.md")
        self.rpath = os.path.join(self.remote, "alpha", "SKILL.md")

    def test_state_is_recorded_after_sync(self):
        self.assertIn("alpha", self.sync.load_state(self.sync_dir))
        self.assertEqual(self.plan()["skills"]["identical"], 1)

    def test_edit_local_only_is_push(self):
        self.write(self.lpath, "v2 local\n")
        s = self.plan()["skills"]
        self.assertEqual((s["push"], s["pull"], s["conflicts"]), (["alpha"], [], []))

    def test_edit_remote_only_is_pull(self):
        self.write(self.rpath, "v2 remote\n")
        s = self.plan()["skills"]
        self.assertEqual((s["push"], s["pull"], s["conflicts"]), ([], ["alpha"], []))

    def test_new_file_in_subfolder_counts_as_an_edit(self):
        self.write(os.path.join(self.skills, "alpha", "ref", "more.md"), "m")
        self.assertEqual(self.plan()["skills"]["push"], ["alpha"])

    def edit_both(self, local_age, remote_age):
        self.write(self.lpath, "local change\n")
        self.write(self.rpath, "remote change\n")
        self.age(os.path.join(self.skills, "alpha"), local_age)
        self.age(os.path.join(self.remote, "alpha"), remote_age)
        return self.plan()["skills"]

    def test_edit_both_is_conflict_local_newer(self):
        s = self.edit_both(local_age=60, remote_age=7200)
        self.assertEqual((s["push"], s["pull"]), ([], []))
        (c,) = s["conflicts"]
        self.assertEqual(c["name"], "alpha")
        self.assertEqual(c["newer"], "local")
        self.assertEqual(c["local_modified"], self.sync.stamp(time.time() - 60))
        self.assertEqual(c["remote_modified"], self.sync.stamp(time.time() - 7200))

    def test_edit_both_is_conflict_remote_newer(self):
        (c,) = self.edit_both(local_age=7200, remote_age=60)["conflicts"]
        self.assertEqual(c["newer"], "remote")

    def test_same_edit_on_both_sides_is_identical(self):
        self.write(self.lpath, "same\n")
        self.write(self.rpath, "same\n")
        p = self.plan()
        self.assertClean(p)
        self.assertEqual(p["skills"]["identical"], 1)

    def test_resolve_conflict_with_push(self):
        self.edit_both(local_age=60, remote_age=7200)
        done = self.apply(push="alpha")
        self.assertEqual(done["pushed"], ["alpha"])
        p = self.plan()
        self.assertClean(p)
        self.assertEqual(p["skills"]["identical"], 1)
        self.assertEqual(self.read(self.rpath), "local change\n")
        self.assertEqual(self.read(self.lpath), "local change\n")

    def test_resolve_conflict_with_pull(self):
        self.edit_both(local_age=7200, remote_age=60)
        done = self.apply(pull="alpha")
        self.assertEqual(done["pulled"], ["alpha"])
        p = self.plan()
        self.assertClean(p)
        self.assertEqual(self.read(self.lpath), "remote change\n")

    def test_all_leaves_conflicts_alone(self):
        self.edit_both(local_age=60, remote_age=7200)
        done = self.sync_all()
        self.assertEqual((done["pushed"], done["pulled"]), ([], []))
        self.assertEqual(self.read(self.lpath), "local change\n")
        self.assertEqual(self.read(self.rpath), "remote change\n")
        self.assertEqual(len(self.plan()["skills"]["conflicts"]), 1)

    def test_state_is_kept_per_sync_folder(self):
        other = os.path.join(self.tmp, "other", "ClaudeSync")
        self.make_skill(os.path.join(other, "skills"), "alpha", {"SKILL.md": "elsewhere\n"})
        s = self.sync.plan(other)["skills"]
        self.assertEqual([c["name"] for c in s["conflicts"]], ["alpha"])


class SettleTests(SyncTestCase):
    def setUp(self):
        super().setUp()
        self.local_skill("alpha", {"SKILL.md": "v1\n"})
        self.sync_all()
        self.lpath = os.path.join(self.skills, "alpha", "SKILL.md")
        self.rpath = os.path.join(self.remote, "alpha", "SKILL.md")

    def test_fresh_remote_change_is_settling(self):
        self.write(self.rpath, "v2 remote\n")
        s = self.plan(settle=120)["skills"]
        self.assertEqual((s["settling"], s["pull"]), (["alpha"], []))

    def test_old_remote_change_is_pulled(self):
        self.write(self.rpath, "v2 remote\n")
        self.age(self.rpath, 3600)
        s = self.plan(settle=120)["skills"]
        self.assertEqual((s["settling"], s["pull"]), ([], ["alpha"]))

    def test_without_settle_a_fresh_change_is_pulled(self):
        self.write(self.rpath, "v2 remote\n")
        s = self.plan(settle=0)["skills"]
        self.assertEqual((s["settling"], s["pull"]), ([], ["alpha"]))

    def test_fresh_remote_only_skill_is_settling(self):
        self.remote_skill("beta")
        s = self.plan(settle=120)["skills"]
        self.assertEqual((s["settling"], s["pull"]), (["beta"], []))

    def test_old_remote_only_skill_is_pulled(self):
        self.age(self.remote_skill("beta"), 3600)
        s = self.plan(settle=120)["skills"]
        self.assertEqual((s["settling"], s["pull"]), ([], ["beta"]))

    def test_local_edit_is_never_held_back(self):
        self.write(self.lpath, "v2 local\n")
        s = self.plan(settle=120)["skills"]
        self.assertEqual((s["push"], s["settling"]), (["alpha"], []))

    def test_new_local_skill_is_never_held_back(self):
        self.local_skill("fresh")
        s = self.plan(settle=10 ** 6)["skills"]
        self.assertEqual((s["push"], s["settling"]), (["fresh"], []))

    def test_conflict_is_not_settling(self):
        self.write(self.lpath, "local\n")
        self.write(self.rpath, "remote\n")
        s = self.plan(settle=120)["skills"]
        self.assertEqual(len(s["conflicts"]), 1)
        self.assertEqual(s["settling"], [])

    def test_apply_all_skips_settling_skills(self):
        self.write(self.rpath, "v2 remote\n")
        p = self.plan(settle=120)
        done = self.apply("all", "all", p)
        self.assertEqual((done["pushed"], done["pulled"]), ([], []))
        self.assertEqual(self.read(self.lpath), "v1\n")


if __name__ == "__main__":
    unittest.main()
