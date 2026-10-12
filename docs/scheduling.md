# Scheduling

`sync.py schedule install` sets up a job that runs `sync.py auto` at login and every N hours. It runs as you, needs no admin rights, and uses the scheduler built into your OS. What `auto` does is described in [how-it-works.md](how-it-works.md).

```bash
python sync.py schedule install --every 4
python sync.py schedule show
python sync.py schedule remove
```

`--every` is a whole number of hours, default 4, minimum 1. Installing again replaces the existing job, so that is also how you change the interval.

## What gets scheduled

The job runs this command:

```
<python> -I <path to sync.py> auto [--dir <folder>]
```

- `<python>` is the interpreter that ran `schedule install`. On Windows it is `pythonw.exe` from the same folder when that exists, so no console window appears.
- `-I` runs Python in isolated mode.
- The path to `sync.py` is recorded as an absolute path. If you move or reinstall the skill, run `schedule install` again.
- `--dir` is included only if you passed `--dir` to `schedule install`. Otherwise the job finds the folder itself each run, using `CLAUDE_SYNC_DIR` and the OneDrive lookup. A scheduler may not see environment variables you set in a shell, so pass `--dir` at install time if you rely on a custom folder.

## Windows

| | |
|---|---|
| Mechanism | Task Scheduler, task name `ClaudeSync` |
| Triggers | At logon of the current user, and every N hours starting 5 minutes after install |
| Settings | Start when available (a missed run starts as soon as possible), run on battery, stop after 30 minutes, ignore a new start if one is already running |

If Windows does not permit the logon trigger for your account, the task is registered with the interval trigger only, and `schedule install` reports "login trigger not permitted". Missed runs still start when possible.

Check it:

```powershell
python sync.py schedule show
Get-ScheduledTask -TaskName ClaudeSync | Get-ScheduledTaskInfo
```

`show` reports the state, last run time, last result, next run time and command. A last result of 0 means the process ended normally. A non-zero result usually means the run recorded an error, so read `last-run.json` (see below).

Run it now, or open the GUI:

```powershell
Start-ScheduledTask -TaskName ClaudeSync
taskschd.msc
```

Remove it with `python sync.py schedule remove`, or `Unregister-ScheduledTask -TaskName ClaudeSync -Confirm:$false`.

## macOS

| | |
|---|---|
| Mechanism | LaunchAgent, label `com.claude-sync.auto` |
| File | `~/Library/LaunchAgents/com.claude-sync.auto.plist` |
| Triggers | `RunAtLoad` (when the agent loads, which is at login) and `StartInterval` of N x 3600 seconds |
| Output | Discarded (`/dev/null`) |

Install unloads any existing agent, writes the plist and loads it with `launchctl load -w`.

Check it:

```bash
python sync.py schedule show
launchctl list com.claude-sync.auto
```

Run it now with `launchctl start com.claude-sync.auto`. Remove it with `python sync.py schedule remove`, which unloads the agent and deletes the plist.

## Linux and other Unix

| | |
|---|---|
| Mechanism | Your user crontab |
| Lines | Two, each ending in `# claude-sync` |
| Triggers | `@reboot`, and `0 */N * * *` |
| Output | Discarded (`>/dev/null 2>&1`) |

The lines look like this, with your own paths:

```
@reboot /usr/bin/python3 -I /home/you/.claude/skills/claude-sync/sync.py auto >/dev/null 2>&1 # claude-sync
0 */4 * * * /usr/bin/python3 -I /home/you/.claude/skills/claude-sync/sync.py auto >/dev/null 2>&1 # claude-sync
```

`*/N` in the hour field means hours 0, N, 2N and so on within each day, so an interval that does not divide 24 evenly leaves an uneven gap at midnight (every 5 hours runs at 0, 5, 10, 15 and 20, then 0 again). An interval of 24 hours or more becomes a run at midnight every `N / 24` days (`0 0 */D * *`), rounded down. `crontab` must be installed. Install removes any existing lines tagged `# claude-sync` before adding new ones, and your other cron lines are kept.

Check it:

```bash
python3 sync.py schedule show
crontab -l | grep claude-sync
```

Remove it with `python3 sync.py schedule remove`, which deletes only the tagged lines.

## Where results go

The scheduler's own output is discarded, so the results are in the shared folder:

- `machines/<host>/last-run.json`: the last run on that machine, including any error.
- `machines/<host>/sync.log`: one line per run, last 500 lines.

To read them without opening the folder, run:

```bash
python sync.py status
```

This prints the last run for every machine. Look for `needs_you` and `error`. If `last-run.json` is old or missing, the job is not running or could not reach the folder: see [troubleshooting.md](troubleshooting.md).
