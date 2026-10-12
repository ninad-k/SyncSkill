#!/usr/bin/env python3
"""Sync a Claude Code setup between machines through a shared folder.

Skills in ~/.claude/skills, user-scope MCP servers and uv tools are synced
through a folder every machine can see (OneDrive\\ClaudeSync by default).
Standard library only, Python 3.8+.

  sync.py plan     [--dir PATH]                      what differs, as JSON (changes nothing)
  sync.py apply    [--dir PATH] [--push a,b|all] [--pull a,b|all]
  sync.py manifest [--dir PATH] [--plugins a,b] [--connectors a,b]
  sync.py auto     [--dir PATH]                      unattended sync, for the scheduler
  sync.py status   [--dir PATH]                      last unattended run on every machine
  sync.py schedule install|remove|show [--every HOURS] [--dir PATH]

Nothing is ever deleted. A skill that gets overwritten is backed up first to
<sync dir>/.backup/<timestamp>/<local|remote>/<name>.
"""
import argparse
import datetime
import glob
import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)  # python -I leaves the script's folder off sys.path

HOME = os.environ.get("CLAUDE_SYNC_HOME") or os.path.expanduser("~")
CLAUDE_DIR = os.path.join(HOME, ".claude")
SKILLS = os.path.join(CLAUDE_DIR, "skills")
STATE_FILE = os.path.join(CLAUDE_DIR, "claude-sync-state.json")
LOCK_FILE = os.path.join(CLAUDE_DIR, "claude-sync.lock")
LAST_RUN_FILE = os.path.join(CLAUDE_DIR, "claude-sync-last-run.json")
SKIP = {".git", "__pycache__", "node_modules", ".venv"}
HOME_TOKEN = "{HOME}"
SETTLE_SECONDS = 120      # auto skips remote skills changed this recently (still uploading)
LOCK_STALE_SECONDS = 1800
BACKUP_DAYS = 30
LOG_LINES = 500


def default_dir():
    """CLAUDE_SYNC_DIR if set, else ClaudeSync inside the first OneDrive folder found."""
    if os.environ.get("CLAUDE_SYNC_DIR"):
        return os.environ["CLAUDE_SYNC_DIR"]
    cands = [os.environ.get("OneDrive", ""), os.path.join(HOME, "OneDrive")]
    cands += sorted(glob.glob(os.path.join(HOME, "Library", "CloudStorage", "OneDrive*")))
    for c in cands:
        if c and os.path.isdir(c):
            return os.path.join(c, "ClaudeSync")
    return None


def now_stamp():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ---------- skills ----------

def skill_names(root):
    if not os.path.isdir(root):
        return set()
    return {n for n in os.listdir(root)
            if os.path.isfile(os.path.join(root, n, "SKILL.md"))}


def walk(root):
    for d, dirs, files in os.walk(root):
        dirs[:] = sorted(x for x in dirs if x not in SKIP)
        for f in sorted(files):
            if not f.endswith(".pyc"):
                p = os.path.join(d, f)
                yield os.path.relpath(p, root).replace("\\", "/"), p


def digest(root):
    """Content hash of a skill folder, and its newest file's modified time."""
    h = hashlib.sha256()
    newest = 0.0
    for rel, p in walk(root):
        h.update(rel.encode("utf-8"))
        with open(p, "rb") as fh:
            h.update(hashlib.sha256(fh.read()).digest())
        newest = max(newest, os.path.getmtime(p))
    return h.hexdigest(), newest


def stamp(t):
    return datetime.datetime.fromtimestamp(t).strftime("%Y-%m-%d %H:%M")


def valid_name(n):
    """A skill name must be a plain folder name, never a path."""
    return (bool(n) and n not in (".", "..") and os.path.basename(n) == n
            and "/" not in n and "\\" not in n and ":" not in n)


def copy_skill(src, dst, backup_root):
    if os.path.exists(dst):
        target = os.path.join(backup_root, os.path.basename(dst))
        i = 1
        while os.path.exists(target):  # same skill overwritten twice in one second
            target = os.path.join(backup_root, f"{os.path.basename(dst)}.{i}")
            i += 1
        shutil.copytree(dst, target, ignore=shutil.ignore_patterns(*SKIP, "*.pyc"))
        shutil.rmtree(dst)
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns(*SKIP, "*.pyc"))


def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


def write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, sort_keys=True)
        fh.write("\n")
    os.replace(tmp, path)


def load_state(sync_dir):
    """Hash of each skill as of this machine's last sync with sync_dir."""
    return load_json(STATE_FILE, {}).get(os.path.normcase(os.path.abspath(sync_dir)), {})


def save_state(sync_dir, skills):
    data = load_json(STATE_FILE, {})
    data[os.path.normcase(os.path.abspath(sync_dir))] = skills
    write_json(STATE_FILE, data)


# ---------- MCP and uv ----------

def local_mcp():
    return load_json(os.path.join(HOME, ".claude.json"), {}).get("mcpServers") or {}


def _escaped_home():
    return json.dumps(HOME)[1:-1]


def portable(cfg):
    """Secret values out, home path templated, so the config is safe to share."""
    c = json.loads(json.dumps(cfg).replace(_escaped_home(), HOME_TOKEN))
    for k in ("env", "headers"):
        if c.get(k):
            c[k] = {n: "<SET_ME>" for n in c[k]}
    return c


def localise(cfg):
    return json.loads(json.dumps(cfg).replace(HOME_TOKEN, _escaped_home()))


def find_uv():
    exe = shutil.which("uv")
    if exe:
        return exe
    for name in ("uv.exe", "uv"):
        p = os.path.join(HOME, ".local", "bin", name)
        if os.path.isfile(p):
            return p
    return None


def uv_tools():
    exe = find_uv()
    if not exe:
        return None
    try:
        out = subprocess.run([exe, "tool", "list"], capture_output=True,
                             text=True, timeout=60).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    if "No tools installed" in out:
        return []
    return sorted({l.split()[0] for l in out.splitlines()
                   if l.strip() and not l.startswith(("-", " "))})


# ---------- manifest ----------

def load_manifest(sync_dir):
    m = load_json(os.path.join(sync_dir, "manifest.json"), {})
    for k in ("mcp", "uv_tools", "account_plugins", "connectors"):
        m.setdefault(k, {} if k == "mcp" else [])
    return m


def save_manifest(sync_dir, m):
    m["updated"] = datetime.date.today().isoformat()
    write_json(os.path.join(sync_dir, "manifest.json"), m)


# ---------- plan / apply / manifest ----------

def plan(sync_dir, settle=0):
    """Compare this machine with sync_dir. Changes nothing.

    Skills use a three-way comparison against the hash recorded at the last
    sync, so an edit on one side is copied over, and only an edit on both
    sides is a conflict.
    """
    remote_skills = os.path.join(sync_dir, "skills")
    here, there = skill_names(SKILLS), skill_names(remote_skills)
    base = load_state(sync_dir)
    push, pull, conflicts, settling, identical = [], [], [], [], []
    hashes = {}
    for n in sorted(here | there):
        lh, lt = digest(os.path.join(SKILLS, n)) if n in here else (None, 0)
        rh, rt = digest(os.path.join(remote_skills, n)) if n in there else (None, 0)
        hashes[n] = {"local": lh, "remote": rh}
        if lh and rh and lh == rh:
            identical.append(n)
            continue
        b = base.get(n)
        if lh and not rh:
            push.append(n)
        elif (rh and not lh) or b == lh:
            # the synced copy changed; skip it while it may still be uploading
            (settling if settle and time.time() - rt < settle else pull).append(n)
        elif b == rh:
            push.append(n)
        else:
            conflicts.append({"name": n, "newer": "local" if lt >= rt else "remote",
                              "local_modified": stamp(lt), "remote_modified": stamp(rt)})

    m = load_manifest(sync_dir)
    mine = local_mcp()
    missing_mcp = []
    for name, cfg in sorted(m["mcp"].items()):
        if name not in mine:
            c = localise(cfg)
            secrets = [f"{k}.{n}" for k in ("env", "headers")
                       for n, v in (c.get(k) or {}).items() if v == "<SET_ME>"]
            missing_mcp.append({"name": name, "config": c, "needs_values": secrets})
    differs = [n for n in sorted(set(mine) & set(m["mcp"])) if portable(mine[n]) != m["mcp"][n]]

    tools = uv_tools()
    return {
        "sync_dir": sync_dir,
        "manifest_exists": os.path.isfile(os.path.join(sync_dir, "manifest.json")),
        "manifest_updated": m.get("updated"),
        "skills": {"push": push, "pull": pull, "conflicts": conflicts,
                   "settling": settling, "identical": len(identical)},
        "mcp": {"missing_here": missing_mcp,
                "not_in_manifest": sorted(set(mine) - set(m["mcp"])),
                "differs_from_manifest": differs},
        "uv": {"uv_installed": tools is not None,
               "missing_here": sorted(set(m["uv_tools"]) - set(tools or [])),
               "not_in_manifest": sorted(set(tools or []) - set(m["uv_tools"]))},
        "account": {"plugins": m["account_plugins"], "connectors": m["connectors"]},
        "_identical": identical,
        "_hashes": hashes,
    }


def names(arg, default):
    if not arg:
        return []
    return list(default) if arg == "all" else [x.strip() for x in arg.split(",") if x.strip()]


def apply(sync_dir, push, pull, p=None):
    p = p or plan(sync_dir)
    remote_skills = os.path.join(sync_dir, "skills")
    backup = os.path.join(sync_dir, ".backup", datetime.datetime.now().strftime("%Y%m%d-%H%M%S"))
    push_names, pull_names = names(push, p["skills"]["push"]), names(pull, p["skills"]["pull"])
    bad = [n for n in push_names + pull_names if not valid_name(n)]
    if bad:
        raise SystemExit(f"not a skill name: {', '.join(bad)}")
    overlap = set(push_names) & set(pull_names)
    if overlap:
        raise SystemExit(f"both pushed and pulled: {', '.join(sorted(overlap))}")

    state = load_state(sync_dir)
    for n in p["_identical"]:
        state[n] = p["_hashes"][n]["local"]
    done = {"pushed": [], "pulled": []}
    for n, src_root, dst_root, side, key in (
            [(n, SKILLS, remote_skills, "remote", "pushed") for n in push_names]
            + [(n, remote_skills, SKILLS, "local", "pulled") for n in pull_names]):
        src = os.path.join(src_root, n)
        if not os.path.isfile(os.path.join(src, "SKILL.md")):
            raise SystemExit(f"no {'local' if key == 'pushed' else 'synced'} skill named {n}")
        copy_skill(src, os.path.join(dst_root, n), os.path.join(backup, side))
        state[n] = digest(src)[0]
        done[key].append(n)
        save_state(sync_dir, state)  # after every copy, so a crash loses nothing
    save_state(sync_dir, state)
    if os.path.isdir(backup):
        done["backup"] = backup
    return done


def manifest(sync_dir, plugins=None, connectors=None, tools=None):
    m = load_manifest(sync_dir)
    added = {"mcp": [], "uv_tools": [], "account_plugins": [], "connectors": []}
    for n, cfg in local_mcp().items():
        if n not in m["mcp"]:
            m["mcp"][n] = portable(cfg)
            added["mcp"].append(n)
    for key, new in (("uv_tools", uv_tools() if tools is None else tools),
                     ("account_plugins", names(plugins, [])),
                     ("connectors", names(connectors, []))):
        new = new or []
        added[key] = sorted(set(new) - set(m[key]))
        m[key] = sorted(set(m[key]) | set(new))
    save_manifest(sync_dir, m)
    return {"manifest": os.path.join(sync_dir, "manifest.json"), "added": added}


# ---------- unattended ----------

class Lock:
    """One sync at a time on this machine. A lock older than 30 minutes is stale."""

    def __enter__(self):
        os.makedirs(CLAUDE_DIR, exist_ok=True)
        try:
            if time.time() - os.path.getmtime(LOCK_FILE) > LOCK_STALE_SECONDS:
                os.remove(LOCK_FILE)
        except OSError:
            pass
        try:
            fd = os.open(LOCK_FILE, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            raise SystemExit("another sync is running")
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        return self

    def __exit__(self, *exc):
        try:
            os.remove(LOCK_FILE)
        except OSError:
            pass


def machine_dir(sync_dir):
    return os.path.join(sync_dir, "machines", socket.gethostname())


def prune_backups(sync_dir, days=BACKUP_DAYS):
    cutoff = time.time() - days * 86400
    for d in glob.glob(os.path.join(sync_dir, ".backup", "*")):
        try:
            if os.path.getmtime(d) < cutoff:
                shutil.rmtree(d)
        except OSError:
            pass


def append_log(sync_dir, line):
    path = os.path.join(machine_dir(sync_dir), "sync.log")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    try:
        with open(path, encoding="utf-8") as fh:
            lines = fh.read().splitlines()[-(LOG_LINES - 1):]
    except OSError:
        lines = []
    lines.append(line)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


def auto(sync_dir):
    """Everything that is safe without a person: one-sided and one-side-changed
    skills, manifest updates, missing uv tools. Conflicts, MCP servers and
    secrets are left for an interactive /claude-sync and listed in the status."""
    status = {"run_at": now_stamp(), "machine": socket.gethostname(), "ok": False}
    try:
        if not os.path.isdir(sync_dir) and not os.path.isdir(os.path.dirname(sync_dir)):
            raise RuntimeError(f"sync folder not reachable: {sync_dir}")
        with Lock():
            p = plan(sync_dir, settle=SETTLE_SECONDS)
            done = apply(sync_dir, "all", "all", p)
            m = manifest(sync_dir)

            installed, failed = [], []
            uv = find_uv()
            for tool in p["uv"]["missing_here"]:
                if not uv:
                    failed.append(f"{tool}: uv is not installed")
                    continue
                try:
                    r = subprocess.run([uv, "tool", "install", tool], capture_output=True,
                                       text=True, timeout=900)
                    (installed if r.returncode == 0 else failed).append(
                        tool if r.returncode == 0 else f"{tool}: {r.stderr.strip()[-300:]}")
                except (OSError, subprocess.SubprocessError) as e:
                    failed.append(f"{tool}: {e}")
            prune_backups(sync_dir)

        status.update({
            "ok": not failed,
            "pushed": done["pushed"], "pulled": done["pulled"],
            "conflicts": p["skills"]["conflicts"], "settling": p["skills"]["settling"],
            "manifest_added": {k: v for k, v in m["added"].items() if v},
            "uv_installed": installed, "uv_failed": failed,
            "mcp_to_add": [s["name"] for s in p["mcp"]["missing_here"]],
            "mcp_differs": p["mcp"]["differs_from_manifest"],
            "needs_you": bool(p["skills"]["conflicts"] or p["mcp"]["missing_here"]
                              or p["mcp"]["differs_from_manifest"] or failed),
        })
    except (Exception, SystemExit) as e:  # unattended: record it, never crash silently
        status["error"] = str(e) or type(e).__name__
    try:
        write_json(LAST_RUN_FILE, status)  # local copy, readable even when the folder isn't
    except OSError:
        pass
    if not os.path.isdir(sync_dir):
        return status  # never create the folder just to report it is missing
    try:
        write_json(os.path.join(machine_dir(sync_dir), "last-run.json"), status)
        summary = ("error: " + status["error"]) if "error" in status else (
            f"pushed {len(status['pushed'])}, pulled {len(status['pulled'])}, "
            f"conflicts {len(status['conflicts'])}, uv installed {len(status['uv_installed'])}"
            + (", needs /claude-sync" if status["needs_you"] else ""))
        append_log(sync_dir, f"{status['run_at']}  {summary}")
    except OSError:
        pass
    return status


def all_status(sync_dir):
    out = {}
    for f in sorted(glob.glob(os.path.join(sync_dir, "machines", "*", "last-run.json"))):
        out[os.path.basename(os.path.dirname(f))] = load_json(f, {})
    return out


# ---------- CLI ----------

def public(p):
    return {k: v for k, v in p.items() if not k.startswith("_")}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["plan", "apply", "manifest", "auto", "status", "schedule"])
    ap.add_argument("action", nargs="?", choices=["install", "remove", "show"],
                    help="for schedule")
    ap.add_argument("--dir", help="sync folder (default: OneDrive\\ClaudeSync)")
    ap.add_argument("--push", help="skills to copy to the sync folder: names, or all")
    ap.add_argument("--pull", help="skills to copy from the sync folder: names, or all")
    ap.add_argument("--plugins", help="account plugins visible on this machine")
    ap.add_argument("--connectors", help="connectors visible on this machine")
    ap.add_argument("--every", type=int, default=4, help="schedule interval in hours")
    a = ap.parse_args(argv)

    sync_dir = a.dir or default_dir()
    if not sync_dir:
        raise SystemExit("no OneDrive folder found: pass --dir or set CLAUDE_SYNC_DIR")
    sync_dir = os.path.abspath(sync_dir)

    if a.command == "plan":
        out = public(plan(sync_dir))
    elif a.command == "apply":
        out = apply(sync_dir, a.push, a.pull)
    elif a.command == "manifest":
        out = manifest(sync_dir, a.plugins, a.connectors)
    elif a.command == "auto":
        out = auto(sync_dir)
    elif a.command == "status":
        out = all_status(sync_dir)
    else:
        import scheduler
        if not a.action:
            raise SystemExit("schedule needs install, remove or show")
        out = getattr(scheduler, a.action)(
            script=os.path.abspath(__file__), sync_dir=sync_dir if a.dir else None,
            hours=a.every)
    print(json.dumps(out, indent=2))
    if a.command == "auto" and "error" in out:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
