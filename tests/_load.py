"""Load sync.py and scheduler.py for the tests, with a temp-dir base case.

sync.py reads CLAUDE_SYNC_HOME at import time, so it is imported once with a
throwaway home. Each test then points the module-level paths at its own
temporary directory, so nothing real is ever read or written.
"""
import importlib
import importlib.util
import json
import os
import sys
import tempfile
import time
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILL_DIR = os.path.join(ROOT, "skills", "claude-sync")
_cache = {}


def load_sync():
    if "sync" not in _cache:
        old = os.environ.get("CLAUDE_SYNC_HOME")
        with tempfile.TemporaryDirectory() as scratch:
            os.environ["CLAUDE_SYNC_HOME"] = scratch
            try:
                spec = importlib.util.spec_from_file_location(
                    "claude_sync_under_test", os.path.join(SKILL_DIR, "sync.py"))
                mod = importlib.util.module_from_spec(spec)
                sys.modules[spec.name] = mod
                spec.loader.exec_module(mod)
            finally:
                if old is None:
                    os.environ.pop("CLAUDE_SYNC_HOME", None)
                else:
                    os.environ["CLAUDE_SYNC_HOME"] = old
        _cache["sync"] = mod
    return _cache["sync"]


def load_scheduler():
    if SKILL_DIR not in sys.path:
        sys.path.insert(0, SKILL_DIR)
    return importlib.import_module("scheduler")


def no_subprocess(case):
    """Fail the test if anything tries to start a real process."""
    p = mock.patch("subprocess.run",
                   side_effect=AssertionError("subprocess.run must not be called"))
    p.start()
    case.addCleanup(p.stop)


class SyncTestCase(unittest.TestCase):
    """A fake home and a fake shared folder inside one temporary directory."""

    def setUp(self):
        self.sync = load_sync()
        self.real_uv_tools = self.sync.uv_tools
        self.real_find_uv = self.sync.find_uv

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = os.path.realpath(tmp.name)
        self.home = os.path.join(self.tmp, "home")
        self.claude = os.path.join(self.home, ".claude")
        self.skills = os.path.join(self.claude, "skills")
        self.sync_dir = os.path.join(self.tmp, "share", "ClaudeSync")
        self.remote = os.path.join(self.sync_dir, "skills")
        for d in (self.skills, self.remote):
            os.makedirs(d)

        self.uv_list = []
        self.patch(self.sync, "HOME", self.home)
        self.patch(self.sync, "CLAUDE_DIR", self.claude)
        self.patch(self.sync, "SKILLS", self.skills)
        self.patch(self.sync, "STATE_FILE", os.path.join(self.claude, "claude-sync-state.json"))
        self.patch(self.sync, "LOCK_FILE", os.path.join(self.claude, "claude-sync.lock"))
        self.patch(self.sync, "LAST_RUN_FILE", os.path.join(self.claude, "claude-sync-last-run.json"))
        self.patch(self.sync, "uv_tools", lambda: self.uv_list)
        self.patch(self.sync, "find_uv", lambda: None)
        self.patch(self.sync.socket, "gethostname", lambda: "testbox")
        no_subprocess(self)

    def patch(self, obj, name, value):
        p = mock.patch.object(obj, name, value)
        p.start()
        self.addCleanup(p.stop)
        return value

    # ---------- file helpers ----------

    def write(self, path, text):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
        return path

    def read(self, path):
        with open(path, encoding="utf-8", newline="") as fh:
            return fh.read()

    def make_skill(self, root, name, files=None):
        files = files if files is not None else {"SKILL.md": "# " + name + "\n"}
        for rel, text in files.items():
            self.write(os.path.join(root, name, *rel.split("/")), text)
        return os.path.join(root, name)

    def local_skill(self, name, files=None):
        return self.make_skill(self.skills, name, files)

    def remote_skill(self, name, files=None):
        return self.make_skill(self.remote, name, files)

    def age(self, path, seconds):
        """Make a file, or every file under a folder, `seconds` old."""
        t = time.time() - seconds
        if os.path.isfile(path):
            os.utime(path, (t, t))
            return
        for d, _, files in os.walk(path):
            for f in files:
                os.utime(os.path.join(d, f), (t, t))

    # ---------- project helpers ----------

    def plan(self, settle=0):
        return self.sync.plan(self.sync_dir, settle=settle)

    def apply(self, push=None, pull=None, p=None):
        return self.sync.apply(self.sync_dir, push, pull, p)

    def sync_all(self):
        return self.apply("all", "all")

    def write_mcp(self, servers):
        self.write(os.path.join(self.home, ".claude.json"), json.dumps({"mcpServers": servers}))

    def write_manifest(self, **fields):
        m = {"mcp": {}, "uv_tools": [], "account_plugins": [], "connectors": []}
        m.update(fields)
        self.write(os.path.join(self.sync_dir, "manifest.json"), json.dumps(m))

    def read_manifest(self):
        return json.loads(self.read(os.path.join(self.sync_dir, "manifest.json")))

    def assertClean(self, p):
        s = p["skills"]
        self.assertEqual(
            (s["push"], s["pull"], s["conflicts"], s["settling"]), ([], [], [], []))
