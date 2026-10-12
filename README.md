# claude-sync

claude-sync is a Claude Code skill that keeps your Claude Code setup in step across several computers signed into the same account. It copies personal skills, user-scope MCP servers and uv tools through a shared cloud folder (OneDrive by default, or any synced folder you point it at), and checks which account plugins and connectors are enabled on each machine. You can run it interactively with `/claude-sync`, where Claude walks you through conflicts, secrets and MCP servers, or let a scheduler it installs run an unattended sync at login and every few hours.

It is written in standard-library Python (3.8 or newer), has no dependencies, and makes no network calls of its own.

## Why

Claude Code keeps personal skills, user MCP servers and command-line tools on each machine, so nothing follows you from one computer to the next. claude-sync fills that gap with a shared folder you already have, and it is built to be cautious: it never deletes anything, backs up whatever it overwrites, and never writes secret values to the shared folder.

## What syncs

| Item | How | Notes |
|---|---|---|
| Personal skills | Two-way sync of folders under `~/.claude/skills` that contain a `SKILL.md`. Each skill is compared with the version recorded at this machine's last sync (three-way). | A change on one side is copied over. A change on both sides is a conflict and is never resolved automatically. Overwritten copies are backed up first. |
| User-scope MCP servers | Recorded in `manifest.json` and added on other machines by Claude in an interactive run (`claude mcp add-json`). | Values in `env` and `headers` become `<SET_ME>`. Your home path is stored as `{HOME}`. Unattended runs do not add servers. |
| uv tools | Tool names from `uv tool list` are recorded in the manifest. Missing tools are installed with `uv tool install`. | Names only, not versions. Both interactive and unattended runs install missing tools. |
| Account plugins | Names recorded in the manifest and compared with what this machine can see. | Cannot be installed by this tool. It reports what is missing and where to turn it on. |
| Connectors | Same as plugins. | Same as plugins. |

## Install

### Option 1: as a plugin

```
/plugin marketplace add ninad-k/SyncSkill
/plugin install claude-sync@syncskill
```

Plugin skills are namespaced, so the command becomes `/claude-sync:claude-sync`.

### Option 2: copy the folder

Bash (macOS, Linux, Git Bash):

```bash
git clone https://github.com/ninad-k/SyncSkill.git
mkdir -p ~/.claude/skills
cp -r SyncSkill/skills/claude-sync ~/.claude/skills/
```

PowerShell:

```powershell
git clone https://github.com/ninad-k/SyncSkill.git
New-Item -ItemType Directory -Force "$env:USERPROFILE\.claude\skills" | Out-Null
Copy-Item -Recurse SyncSkill\skills\claude-sync "$env:USERPROFILE\.claude\skills\"
```

New skills are picked up without restarting Claude Code. The command is `/claude-sync`.

### Option 3: ask Claude Code

Paste `https://github.com/ninad-k/SyncSkill` into Claude Code and say "install skill".

## Quick start

Examples below use the copied-folder install. `sync.py` is in the skill folder, for example `~/.claude/skills/claude-sync/sync.py`.

**First machine**

1. Make sure the shared folder is available. With OneDrive signed in, the default is `ClaudeSync` inside your OneDrive folder. For another folder, see the `--dir` flag below.
2. Run `/claude-sync` in Claude Code. Claude runs `plan`, copies your skills to the shared folder, records your MCP servers, uv tools, plugins and connectors in `manifest.json`, and offers to set up the schedule.

   From a terminal, the equivalent is:

   ```bash
   python ~/.claude/skills/claude-sync/sync.py apply --push all
   python ~/.claude/skills/claude-sync/sync.py manifest
   ```

**Second machine**

1. Wait for the cloud folder to finish syncing, then install the skill with any option above. You can also copy it from the shared folder:

   ```bash
   cp -r ~/OneDrive/ClaudeSync/skills/claude-sync ~/.claude/skills/
   ```

2. Run `/claude-sync`. Claude shows the plan, pulls skills you do not have yet, adds missing MCP servers (asking you for any secret values), installs missing uv tools, and lists the plugins and connectors you still need to enable.
3. Restart Claude Code if MCP servers were added.

**Turn on the schedule**

```bash
python ~/.claude/skills/claude-sync/sync.py schedule install --every 4
```

PowerShell:

```powershell
python "$env:USERPROFILE\.claude\skills\claude-sync\sync.py" schedule install --every 4
```

This runs `sync.py auto` at login and every 4 hours. Details are in [docs/scheduling.md](docs/scheduling.md).

## Commands

All commands print JSON. Run them as `python sync.py <command>` from the skill folder, or give the full path.

| Command | What it does |
|---|---|
| `plan` | Compares this machine with the shared folder and prints the differences. Changes nothing. |
| `apply` | Copies skills. `--push` copies from this machine to the shared folder, `--pull` copies the other way. Each takes a comma-separated list of names or `all`. `all` skips conflicts. Name a conflicting skill explicitly to choose which copy wins. |
| `manifest` | Adds this machine's MCP servers and uv tools to `manifest.json`, plus any `--plugins` and `--connectors` you pass. It only adds entries. |
| `auto` | Unattended sync, used by the scheduler. See [docs/how-it-works.md](docs/how-it-works.md). |
| `status` | Shows the last unattended run recorded by every machine. |
| `schedule install` | Installs the scheduled job. Replaces an existing one. |
| `schedule remove` | Removes the scheduled job. Does nothing if there is none. |
| `schedule show` | Shows whether the job is installed and, on Windows, when it last ran. |

| Flag | Used with | Meaning |
|---|---|---|
| `--dir PATH` | all commands | Shared folder. Default: `CLAUDE_SYNC_DIR` if set, otherwise `ClaudeSync` inside the first OneDrive folder found. |
| `--push NAMES` | `apply` | Skills to copy to the shared folder. Names or `all`. |
| `--pull NAMES` | `apply` | Skills to copy from the shared folder. Names or `all`. |
| `--plugins a,b` | `manifest` | Account plugins visible on this machine, to record in the manifest. |
| `--connectors a,b` | `manifest` | Connectors visible on this machine, to record in the manifest. |
| `--every HOURS` | `schedule install` | Interval in hours. Default 4, minimum 1. |

## How it works

Each skill is hashed and compared three ways: this machine, the shared folder, and the version recorded at this machine's last sync. That tells claude-sync whether to push, pull or flag a conflict. The shared folder holds `skills/`, `manifest.json`, one folder per machine with its last unattended run, and `.backup/`.

See [docs/how-it-works.md](docs/how-it-works.md) for the folder layout and the decision table, [docs/manifest.md](docs/manifest.md) for the manifest format, and [docs/scheduling.md](docs/scheduling.md) for the scheduler.

## Safety

- Nothing is deleted. A skill removed on one machine is not removed elsewhere.
- Before a skill is overwritten, the old copy goes to `.backup/` in the shared folder. Unattended runs delete backups older than 30 days.
- Secret values are never written. In MCP servers, every value under `env` and `headers` is replaced with `<SET_ME>`.
- Your home path is stored as `{HOME}` and filled in again on each machine.
- Unattended runs take a lock, so two cannot overlap on one machine. A lock older than 30 minutes is treated as stale.
- Unattended runs skip a skill whose shared copy changed in the last 2 minutes, in case it is still uploading.
- Conflicts are never resolved automatically. They are listed for you to decide.

## Limitations

- Deletions do not propagate. A skill you delete on one machine comes back from another. To remove one everywhere, delete it on every machine and from the shared folder.
- Account plugins and connectors cannot be installed by this tool. It tells you what is missing.
- Unattended mode does not add MCP servers. They wait for an interactive `/claude-sync`.
- OneDrive is the default, but any folder your machines share through a sync client works.
- The 2-minute settle check uses file modified times, so it is a heuristic. It cannot see an upload that is still in progress.
- Only `env` and `headers` values are stripped from MCP servers. Do not put secrets in `args` or URLs.
- There is no ignore list. Every skill folder under `~/.claude/skills` is synced.

## Development

```bash
python -m unittest discover -s tests -v
```

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

GPL-3.0. See [LICENSE](LICENSE).
