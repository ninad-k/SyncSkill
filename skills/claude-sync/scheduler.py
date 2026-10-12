"""Run `sync.py auto` on a schedule: Windows Task Scheduler, macOS launchd or cron.

Each backend runs the sync at login and every N hours, as the current user,
with no admin rights. install() replaces an existing schedule; remove() is a
no-op if there is none. The builders (windows_script, launchd_plist,
cron_lines) are pure so they can be tested on any OS.
"""
import base64
import os
import plistlib
import shlex
import subprocess
import sys

TASK_NAME = "ClaudeSync"
LAUNCHD_LABEL = "com.claude-sync.auto"
CRON_MARKER = "# claude-sync"


def python_exe():
    """pythonw on Windows, so the scheduled run opens no console window."""
    exe = sys.executable
    if os.name == "nt":
        w = os.path.join(os.path.dirname(exe), "pythonw.exe")
        if os.path.isfile(w):
            return w
    return exe


def command(script, sync_dir=None, python=None):
    args = [python or python_exe(), "-I", script, "auto"]
    if sync_dir:
        args += ["--dir", sync_dir]
    return args


# ---------- Windows ----------

def _ps(s):
    return "'" + s.replace("'", "''") + "'"


def _win_args(args):
    return " ".join('"%s"' % a if (" " in a or not a) else a for a in args)


def windows_script(args, hours):
    exe, rest = args[0], _win_args(args[1:])
    return f"""
$ErrorActionPreference = 'Stop'
$a = New-ScheduledTaskAction -Execute {_ps(exe)} -Argument {_ps(rest)}
$every = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(5) -RepetitionInterval (New-TimeSpan -Hours {int(hours)})
$s = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 30) -MultipleInstances IgnoreNew
$d = 'Syncs Claude Code skills, MCP servers and tools between machines (claude-sync).'
try {{
  $logon = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\\$env:USERNAME"
  Register-ScheduledTask -TaskName {_ps(TASK_NAME)} -Action $a -Trigger @($logon, $every) -Settings $s -Description $d -Force | Out-Null
  'logon+interval'
}} catch {{
  Register-ScheduledTask -TaskName {_ps(TASK_NAME)} -Action $a -Trigger $every -Settings $s -Description $d -Force | Out-Null
  'interval'
}}
""".strip()


def _powershell(script):
    enc = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-EncodedCommand", enc],
                       capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        raise SystemExit(f"PowerShell failed: {r.stderr.strip()[-500:]}")
    return r.stdout.strip()


# ---------- macOS ----------

def launchd_path():
    return os.path.expanduser(f"~/Library/LaunchAgents/{LAUNCHD_LABEL}.plist")


def launchd_plist(args, hours):
    return plistlib.dumps({
        "Label": LAUNCHD_LABEL,
        "ProgramArguments": args,
        "RunAtLoad": True,
        "StartInterval": int(hours) * 3600,
        "ProcessType": "Background",
        "StandardOutPath": "/dev/null",
        "StandardErrorPath": "/dev/null",
    })


def _launchctl(*args):
    return subprocess.run(["launchctl", *args], capture_output=True, text=True)


# ---------- Linux / other Unix ----------

def cron_lines(args, hours):
    cmd = " ".join(shlex.quote(a) for a in args) + " >/dev/null 2>&1"
    hours = int(hours)
    when = f"0 */{hours} * * *" if hours < 24 else f"0 0 */{max(1, hours // 24)} * *"
    return [f"@reboot {cmd} {CRON_MARKER}", f"{when} {cmd} {CRON_MARKER}"]


def _crontab_read():
    r = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    return r.stdout.splitlines() if r.returncode == 0 else []


def _crontab_write(lines):
    r = subprocess.run(["crontab", "-"], input="\n".join(lines) + "\n",
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit(f"crontab failed: {r.stderr.strip()}")


# ---------- public ----------

def install(script, sync_dir=None, hours=4):
    if hours < 1:
        raise SystemExit("--every must be at least 1 hour")
    args = command(script, sync_dir)
    if os.name == "nt":
        triggers = _powershell(windows_script(args, hours))
        return {"installed": "Windows Task Scheduler", "task": TASK_NAME,
                "triggers": "at login and every %d hours" % hours if triggers == "logon+interval"
                else "every %d hours (login trigger not permitted, missed runs start when possible)" % hours,
                "command": args}
    if sys.platform == "darwin":
        path = launchd_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        _launchctl("unload", path)
        with open(path, "wb") as fh:
            fh.write(launchd_plist(args, hours))
        r = _launchctl("load", "-w", path)
        if r.returncode != 0:
            raise SystemExit(f"launchctl failed: {r.stderr.strip()}")
        return {"installed": "launchd", "plist": path,
                "triggers": "at login and every %d hours" % hours, "command": args}
    lines = [l for l in _crontab_read() if CRON_MARKER not in l] + cron_lines(args, hours)
    _crontab_write(lines)
    return {"installed": "cron", "triggers": "at boot and every %d hours" % hours, "command": args}


def remove(script=None, sync_dir=None, hours=None):
    if os.name == "nt":
        _powershell(f"Unregister-ScheduledTask -TaskName {_ps(TASK_NAME)} -Confirm:$false "
                    "-ErrorAction SilentlyContinue")
        return {"removed": "Windows Task Scheduler", "task": TASK_NAME}
    if sys.platform == "darwin":
        path = launchd_path()
        _launchctl("unload", path)
        if os.path.exists(path):
            os.remove(path)
        return {"removed": "launchd", "plist": path}
    _crontab_write([l for l in _crontab_read() if CRON_MARKER not in l])
    return {"removed": "cron"}


def show(script=None, sync_dir=None, hours=None):
    if os.name == "nt":
        out = _powershell(f"""
$t = Get-ScheduledTask -TaskName {_ps(TASK_NAME)} -ErrorAction SilentlyContinue
if (-not $t) {{ 'none'; exit }}
$i = $t | Get-ScheduledTaskInfo
[pscustomobject]@{{ state = "$($t.State)"; last_run = "$($i.LastRunTime)"; last_result = $i.LastTaskResult;
  next_run = "$($i.NextRunTime)"; command = "$($t.Actions[0].Execute) $($t.Actions[0].Arguments)" }} | ConvertTo-Json
""")
        if out == "none":
            return {"scheduled": False}
        import json
        return {"scheduled": True, **json.loads(out)}
    if sys.platform == "darwin":
        r = _launchctl("list", LAUNCHD_LABEL)
        return {"scheduled": r.returncode == 0, "plist": launchd_path()}
    lines = [l for l in _crontab_read() if CRON_MARKER in l]
    return {"scheduled": bool(lines), "cron": lines}
