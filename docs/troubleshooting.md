# Troubleshooting

Commands below use `python sync.py ...`. Run them from the skill folder (for example `~/.claude/skills/claude-sync`) or give the full path to `sync.py`. On Windows you may need `py` instead of `python`.

## "no OneDrive folder found: pass --dir or set CLAUDE_SYNC_DIR"

claude-sync could not find a OneDrive folder. It looks, in order, at `CLAUDE_SYNC_DIR`, the `OneDrive` environment variable, `~/OneDrive`, and `~/Library/CloudStorage/OneDrive*`.

Tell it where the shared folder is, either per command or permanently:

```bash
python sync.py plan --dir ~/Dropbox/ClaudeSync
export CLAUDE_SYNC_DIR=~/Dropbox/ClaudeSync
```

```powershell
python sync.py plan --dir "D:\Sync\ClaudeSync"
setx CLAUDE_SYNC_DIR "D:\Sync\ClaudeSync"
```

Any folder that your machines share through a sync client works. Use the same folder on every machine. If you use the scheduler, install it with `--dir` so the job does not depend on your shell's environment.

## "sync folder not reachable"

Recorded by an unattended run when the shared folder and its parent folder both do not exist. This usually happens when the job fires before OneDrive has mounted its folder at login. The next run will try again. The folder is never created just to report the error, so this status is only in the machine's local copy, `~/.claude/claude-sync-last-run.json`. If it persists, check the path with `python sync.py plan`.

## "another sync is running"

An unattended run takes a lock file, `~/.claude/claude-sync.lock`, so two runs cannot overlap. If a run is genuinely in progress, wait for it. A lock older than 30 minutes is treated as stale and replaced automatically. If a crashed run left one behind and you do not want to wait, delete the file:

```bash
rm ~/.claude/claude-sync.lock
```

```powershell
Remove-Item "$env:USERPROFILE\.claude\claude-sync.lock"
```

Only `auto` uses the lock. `plan`, `apply` and `manifest` do not.

## Conflicts

A conflict means the skill changed on both this machine and in the shared folder since the last sync (or the two copies differ and nothing is recorded). Nothing is overwritten until you choose.

```bash
python sync.py plan                    # shows both modified times and which side is newer
python sync.py apply --push my-skill   # keep this machine's copy
python sync.py apply --pull my-skill   # keep the shared copy
```

The copy that loses is saved under `.backup/<timestamp>/` in the shared folder first, so you can recover edits from it. In `/claude-sync`, Claude asks you which to keep and runs this for you. Unattended runs only list conflicts.

## A skill keeps coming back after I delete it

Deletions are never synced. If a skill exists on any machine or in the shared folder, it is copied back to the others. To remove it for good, delete it from `~/.claude/skills` on every machine and from `skills/` in the shared folder.

## An MCP server is missing after a sync

Unattended runs never add MCP servers. They list them under `mcp_to_add` in `last-run.json`. Run `/claude-sync` and Claude will add them with `claude mcp add-json`, asking you for any value shown as `<SET_ME>`. Restart Claude Code afterwards so it picks the servers up.

If a server is listed but behaves wrongly, check its config. The manifest stores your home path as `{HOME}`, and a Windows-only command such as `cmd.exe /d /c npx ...` needs to become `npx ...` on macOS and Linux. A server whose command is a uv tool needs that tool installed first.

If a server exists on this machine but differs from the manifest, `plan` lists it under `mcp.differs_from_manifest` and nothing is changed until you decide.

## uv is not installed

Without uv, uv tools cannot be installed. An unattended run records `uv is not installed` under `uv_failed`, and `plan` shows `"uv_installed": false`. claude-sync looks for `uv` on `PATH`, then in `~/.local/bin`. Install uv from [astral.sh](https://docs.astral.sh/uv/), then run `/claude-sync` or wait for the next scheduled run.

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

```powershell
powershell -c "irm https://astral.sh/uv/install.ps1 | iex"
```

A scheduler may have a shorter `PATH` than your shell. If uv is installed but a scheduled run cannot find it, make sure `uv` is in `~/.local/bin` or on the system `PATH`.

## The scheduled job is not running

First look at what the machine recorded:

```bash
python sync.py status
```

If `run_at` is old or the machine is missing, the job did not run. Then check the scheduler.

**Windows**

```powershell
python sync.py schedule show
Get-ScheduledTask -TaskName ClaudeSync | Get-ScheduledTaskInfo
```

Look at `state`, `last_run` and `last_result`. Run it by hand with `Start-ScheduledTask -TaskName ClaudeSync`. Or run the command shown by `show` in a terminal, using `python.exe` instead of `pythonw.exe`, to see errors printed.

**macOS**

```bash
launchctl list com.claude-sync.auto
```

If this returns an error, the agent is not loaded: run `python sync.py schedule install` again. The agent's output is discarded, so to see errors run `python3 -I /path/to/sync.py auto` in a terminal.

**Linux**

```bash
crontab -l | grep claude-sync
```

If there are no lines, run `schedule install` again. Check that the cron service is running. Cron output is discarded, so run the command from the cron line in a terminal to see errors.

If you moved the skill folder or changed the Python installation, run `schedule install` again, because the job stores absolute paths. See [scheduling.md](scheduling.md).

## Python not found

claude-sync needs Python 3.8 or newer. The skill tries `python`, `python3` and `py`.

```bash
python --version
python3 --version
```

```powershell
py --version
```

On Windows, if `python` opens the Microsoft Store, install Python from python.org or turn off the "App execution aliases" for it in Settings, then use `py` or `python`. After installing Python, run `schedule install` again so the job uses the new interpreter.

## A skill I just pulled is not listed, or a pull is skipped

Unattended runs skip a skill whose shared copy changed in the last 2 minutes, in case it is still uploading. It shows as `settling` and is picked up on a later run. In an interactive run there is no such delay, so wait for your sync client to finish uploading before you run it.
