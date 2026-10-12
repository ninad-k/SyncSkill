"""MCP servers, uv tools and the manifest."""
import datetime
import json
import os
import subprocess
import unittest
from unittest import mock

from _load import SyncTestCase


class PortableTests(SyncTestCase):
    def cfg(self):
        return {
            "type": "stdio",
            "command": os.path.join(self.home, "bin", "tool"),
            "args": ["--root", os.path.join(self.home, "data"), "--flag"],
            "env": {"API_TOKEN": "s3cr3t", "MODE": "prod"},
            "headers": {"Authorization": "Bearer abc"},
        }

    def under_home(self, path):
        return "{HOME}" + path[len(self.home):]

    def test_home_path_becomes_token(self):
        c = self.cfg()
        out = self.sync.portable(c)
        self.assertEqual(out["command"], self.under_home(c["command"]))
        self.assertEqual(out["args"], ["--root", self.under_home(c["args"][1]), "--flag"])
        self.assertNotIn(self.home, json.dumps(out))

    def test_env_and_header_values_become_set_me(self):
        out = self.sync.portable(self.cfg())
        self.assertEqual(out["env"], {"API_TOKEN": "<SET_ME>", "MODE": "<SET_ME>"})
        self.assertEqual(out["headers"], {"Authorization": "<SET_ME>"})
        self.assertNotIn("s3cr3t", json.dumps(out))
        self.assertNotIn("abc", json.dumps(out))

    def test_input_is_not_modified(self):
        c = self.cfg()
        before = json.dumps(c)
        self.sync.portable(c)
        self.assertEqual(json.dumps(c), before)

    def test_no_env_or_headers_stays_that_way(self):
        out = self.sync.portable({"command": "npx", "args": ["x"], "env": {}})
        self.assertEqual(out, {"command": "npx", "args": ["x"], "env": {}})
        self.assertNotIn("headers", out)

    def test_http_server_url_is_kept(self):
        out = self.sync.portable({"type": "http", "url": "https://example.com/mcp",
                                  "headers": {"X-Key": "k"}})
        self.assertEqual(out["url"], "https://example.com/mcp")
        self.assertEqual(out["headers"], {"X-Key": "<SET_ME>"})

    def test_localise_reverses_the_home_token(self):
        c = self.cfg()
        back = self.sync.localise(self.sync.portable(c))
        self.assertEqual(back["command"], c["command"])
        self.assertEqual(back["args"], c["args"])

    def test_localise_keeps_set_me_placeholders(self):
        back = self.sync.localise(self.sync.portable(self.cfg()))
        self.assertEqual(back["env"]["API_TOKEN"], "<SET_ME>")

    def test_localise_uses_this_machines_home(self):
        shared = {"command": "{HOME}/bin/tool", "args": ["{HOME}"]}
        out = self.sync.localise(shared)
        self.assertTrue(out["command"].startswith(self.home))
        self.assertEqual(out["args"], [self.home])


class LocalMcpTests(SyncTestCase):
    def test_reads_user_servers(self):
        self.write_mcp({"a": {"command": "x"}})
        self.assertEqual(self.sync.local_mcp(), {"a": {"command": "x"}})

    def test_missing_file_is_empty(self):
        self.assertEqual(self.sync.local_mcp(), {})

    def test_null_or_absent_key_is_empty(self):
        self.write(os.path.join(self.home, ".claude.json"), '{"mcpServers": null}')
        self.assertEqual(self.sync.local_mcp(), {})
        self.write(os.path.join(self.home, ".claude.json"), '{"projects": {}}')
        self.assertEqual(self.sync.local_mcp(), {})

    def test_broken_json_is_empty(self):
        self.write(os.path.join(self.home, ".claude.json"), "{oops")
        self.assertEqual(self.sync.local_mcp(), {})


class ManifestWriteTests(SyncTestCase):
    def test_creates_manifest_with_portable_servers(self):
        self.write_mcp({"gh": {"command": os.path.join(self.home, "bin", "gh"),
                               "env": {"TOKEN": "s3cr3t"}}})
        res = self.sync.manifest(self.sync_dir, tools=[])
        self.assertEqual(res["manifest"], os.path.join(self.sync_dir, "manifest.json"))
        self.assertEqual(res["added"]["mcp"], ["gh"])
        m = self.read_manifest()
        self.assertEqual(m["mcp"]["gh"]["env"], {"TOKEN": "<SET_ME>"})
        self.assertTrue(m["mcp"]["gh"]["command"].startswith("{HOME}"))
        text = self.read(res["manifest"])
        self.assertNotIn("s3cr3t", text)
        self.assertNotIn(json.dumps(self.home)[1:-1], text)

    def test_writes_updated_date(self):
        before = datetime.date.today().isoformat()
        self.sync.manifest(self.sync_dir, tools=[])
        after = datetime.date.today().isoformat()
        self.assertIn(self.read_manifest()["updated"], {before, after})

    def test_never_overwrites_an_existing_server(self):
        self.write_manifest(mcp={"gh": {"command": "from-other-machine"}})
        self.write_mcp({"gh": {"command": "different-here"}})
        res = self.sync.manifest(self.sync_dir, tools=[])
        self.assertEqual(res["added"]["mcp"], [])
        self.assertEqual(self.read_manifest()["mcp"]["gh"], {"command": "from-other-machine"})

    def test_never_removes_anything(self):
        self.write_manifest(mcp={"old": {"command": "x"}}, uv_tools=["black"],
                            account_plugins=["legal"], connectors=["Gmail"])
        self.sync.manifest(self.sync_dir, plugins="marketing", connectors="Notion", tools=["ruff"])
        m = self.read_manifest()
        self.assertIn("old", m["mcp"])
        self.assertEqual(m["uv_tools"], ["black", "ruff"])
        self.assertEqual(m["account_plugins"], ["legal", "marketing"])
        self.assertEqual(m["connectors"], ["Gmail", "Notion"])

    def test_lists_are_unioned_and_deduplicated(self):
        self.write_manifest(uv_tools=["ruff", "black"])
        res = self.sync.manifest(self.sync_dir, plugins="a,b,a", tools=["ruff", "mypy"])
        m = self.read_manifest()
        self.assertEqual(m["uv_tools"], ["black", "mypy", "ruff"])
        self.assertEqual(m["account_plugins"], ["a", "b"])
        self.assertEqual(res["added"]["uv_tools"], ["mypy"])
        self.assertEqual(res["added"]["account_plugins"], ["a", "b"])

    def test_second_run_adds_nothing(self):
        self.write_mcp({"gh": {"command": "x"}})
        self.sync.manifest(self.sync_dir, plugins="a", connectors="b", tools=["t"])
        res = self.sync.manifest(self.sync_dir, plugins="a", connectors="b", tools=["t"])
        self.assertEqual(res["added"], {"mcp": [], "uv_tools": [], "account_plugins": [], "connectors": []})

    def test_uses_installed_uv_tools_by_default(self):
        self.uv_list = ["ruff"]
        self.sync.manifest(self.sync_dir)
        self.assertEqual(self.read_manifest()["uv_tools"], ["ruff"])

    def test_no_uv_does_not_erase_the_list(self):
        self.write_manifest(uv_tools=["ruff"])
        self.uv_list = None
        self.sync.manifest(self.sync_dir)
        self.assertEqual(self.read_manifest()["uv_tools"], ["ruff"])

    def test_load_manifest_fills_defaults(self):
        m = self.sync.load_manifest(self.sync_dir)
        self.assertEqual(m, {"mcp": {}, "uv_tools": [], "account_plugins": [], "connectors": []})


class PlanMcpTests(SyncTestCase):
    def shared_server(self):
        return {"type": "stdio", "command": "{HOME}/bin/gh", "args": ["serve"],
                "env": {"TOKEN": "<SET_ME>", "REGION": "<SET_ME>"},
                "headers": {"Authorization": "<SET_ME>"}}

    def test_missing_here_has_localised_config_and_needs_values(self):
        self.write_manifest(mcp={"gh": self.shared_server()})
        (m,) = self.plan()["mcp"]["missing_here"]
        self.assertEqual(m["name"], "gh")
        self.assertEqual(m["needs_values"], ["env.TOKEN", "env.REGION", "headers.Authorization"])
        self.assertTrue(m["config"]["command"].startswith(self.home))
        self.assertNotIn("{HOME}", json.dumps(m["config"]))

    def test_server_without_secrets_needs_no_values(self):
        self.write_manifest(mcp={"fs": {"command": "npx", "args": ["fs"]}})
        (m,) = self.plan()["mcp"]["missing_here"]
        self.assertEqual(m["needs_values"], [])

    def test_installed_server_is_not_missing(self):
        self.write_manifest(mcp={"gh": self.shared_server()})
        self.write_mcp({"gh": self.sync.localise(self.shared_server())})
        self.assertEqual(self.plan()["mcp"]["missing_here"], [])

    def test_not_in_manifest(self):
        self.write_manifest(mcp={"gh": self.shared_server()})
        self.write_mcp({"gh": self.shared_server(), "extra": {"command": "x"}, "another": {"command": "y"}})
        self.assertEqual(self.plan()["mcp"]["not_in_manifest"], ["another", "extra"])

    def test_differs_from_manifest(self):
        self.write_manifest(mcp={"gh": self.shared_server(), "same": {"command": "x"}})
        mine = self.sync.localise(self.shared_server())
        mine["args"] = ["serve", "--other"]
        self.write_mcp({"gh": mine, "same": {"command": "x"}})
        p = self.plan()["mcp"]
        self.assertEqual(p["differs_from_manifest"], ["gh"])
        self.assertEqual(p["missing_here"], [])

    def test_real_secret_values_do_not_count_as_a_difference(self):
        self.write_manifest(mcp={"gh": self.shared_server()})
        mine = self.sync.localise(self.shared_server())
        mine["env"] = {"TOKEN": "real", "REGION": "eu"}
        mine["headers"] = {"Authorization": "Bearer real"}
        self.write_mcp({"gh": mine})
        self.assertEqual(self.plan()["mcp"]["differs_from_manifest"], [])

    def test_no_manifest_means_everything_is_not_in_manifest(self):
        self.write_mcp({"a": {"command": "x"}})
        p = self.plan()
        self.assertFalse(p["manifest_exists"])
        self.assertIsNone(p["manifest_updated"])
        self.assertEqual(p["mcp"]["not_in_manifest"], ["a"])

    def test_manifest_fields_are_reported(self):
        self.write_manifest(updated="2026-01-02", account_plugins=["legal"], connectors=["Gmail"])
        p = self.plan()
        self.assertTrue(p["manifest_exists"])
        self.assertEqual(p["manifest_updated"], "2026-01-02")
        self.assertEqual(p["account"], {"plugins": ["legal"], "connectors": ["Gmail"]})


class PlanUvTests(SyncTestCase):
    def test_missing_and_extra_tools(self):
        self.write_manifest(uv_tools=["black", "ruff"])
        self.uv_list = ["ruff", "mypy"]
        uv = self.plan()["uv"]
        self.assertEqual(uv, {"uv_installed": True, "missing_here": ["black"], "not_in_manifest": ["mypy"]})

    def test_uv_not_installed(self):
        self.write_manifest(uv_tools=["ruff"])
        self.uv_list = None
        uv = self.plan()["uv"]
        self.assertEqual(uv, {"uv_installed": False, "missing_here": ["ruff"], "not_in_manifest": []})


class UvToolsTests(SyncTestCase):
    """The real uv_tools / find_uv, with the process call faked."""

    def run_with(self, stdout):
        self.patch(self.sync, "find_uv", lambda: "uv")
        fake = mock.Mock(return_value=subprocess.CompletedProcess(["uv"], 0, stdout, ""))
        with mock.patch("subprocess.run", fake):
            out = self.real_uv_tools()
        return out, fake

    def test_parses_tool_names(self):
        out, fake = self.run_with("ruff v0.1.0\n- ruff\nblack v23.1\n- black\n- blackd\n")
        self.assertEqual(out, ["black", "ruff"])
        self.assertEqual(fake.call_args[0][0], ["uv", "tool", "list"])

    def test_no_tools_installed(self):
        out, _ = self.run_with("No tools installed\n")
        self.assertEqual(out, [])

    def test_blank_output_is_empty(self):
        out, _ = self.run_with("\n")
        self.assertEqual(out, [])

    def test_no_uv_is_none(self):
        self.patch(self.sync, "find_uv", lambda: None)
        self.assertIsNone(self.real_uv_tools())

    def test_failing_process_is_none(self):
        self.patch(self.sync, "find_uv", lambda: "uv")
        with mock.patch("subprocess.run", side_effect=OSError("gone")):
            self.assertIsNone(self.real_uv_tools())
        with mock.patch("subprocess.run", side_effect=subprocess.TimeoutExpired("uv", 60)):
            self.assertIsNone(self.real_uv_tools())

    def test_find_uv_prefers_path(self):
        with mock.patch("shutil.which", return_value="/usr/bin/uv"):
            self.assertEqual(self.real_find_uv(), "/usr/bin/uv")

    def test_find_uv_falls_back_to_local_bin(self):
        exe = self.write(os.path.join(self.home, ".local", "bin", "uv"), "")
        with mock.patch("shutil.which", return_value=None):
            found = self.real_find_uv()
        self.assertEqual(os.path.normcase(found), os.path.normcase(exe))

    def test_find_uv_none_when_absent(self):
        with mock.patch("shutil.which", return_value=None):
            self.assertIsNone(self.real_find_uv())


if __name__ == "__main__":
    unittest.main()
