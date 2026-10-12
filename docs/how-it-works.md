# How it works

claude-sync compares this machine with a shared folder and copies what is safe to copy. This page covers the folder layout, the per-machine state file, how skills are compared, and what the unattended `auto` command does.

## The shared folder

The folder is `ClaudeSync` inside your OneDrive folder, or whatever you pass with `--dir` or `CLAUDE_SYNC_DIR`. The default is found in this order: `CLAUDE_SYNC_DIR`, the `OneDrive` environment variable, `~/OneDrive`, then `~/Library/CloudStorage/OneDrive*` on macOS. Layout:

```
ClaudeSync/
  skills/
    <skill-name>/            one folder per synced skill, same contents as ~/.claude/skills/<skill-name>
  manifest.json              MCP servers, uv tools, account plugins, connectors (see manifest.md)
  machines/
    <host>/                  one folder per machine, named by its hostname
      last-run.json          result of the last unattended run
      sync.log               one line per unattended run, last 500 lines kept
  .backup/
    <YYYYMMDD-HHMMSS>/
      local/<skill-name>/    this machine's copy, saved before a pull overwrote it
      remote/<skill-name>/   the shared copy, saved before a push overwrote it
```

Folders are created as needed. A backup folder only appears when something was actually overwritten.

## What counts as a skill

A skill is a folder directly under `~/.claude/skills` that contains a `SKILL.md`. The whole folder is synced, except `.git`, `__pycache__`, `node_modules`, `.venv` and `.pyc` files, which are skipped. Each skill is reduced to a SHA-256 hash of its relative file paths and file contents. Modified times are used only to report which side of a conflict is newer, and for the settle check below.

When a skill is copied, the destination folder is replaced by the source folder. The old destination is backed up first, so a file deleted inside a skill is deleted in the copy, but the previous version is in `.backup/`.

## The per-machine state file

Each machine keeps `~/.claude/claude-sync-state.json`. It is not in the shared folder. For each shared folder it records the hash of every skill as of the last sync:

```json
{
  "C:\\users\\you\\onedrive\\claudesync": {
    "example-skill": "<sha256 hash>"
  }
}
```

The key is the absolute path of the shared folder (lower-cased on Windows). The state is updated after each skill is copied, and for skills found identical when `apply` runs. `plan` never writes it. This recorded hash is the "last synced" version in the table below.

The lock file `~/.claude/claude-sync.lock` is also local. Only `auto` uses it. Each `auto` run also writes its status to `~/.claude/claude-sync-last-run.json`, so a run that could not reach the shared folder still leaves a record.

## How skills are compared

For each skill name found on either side, claude-sync compares three hashes: local, remote (the shared folder) and last synced.

| Local | Remote | Last synced | Result |
|---|---|---|---|
| exists | same as local | any | identical, nothing to copy |
| exists | missing | any | push |
| missing | exists | any | pull |
| exists | differs | same as local | pull (the shared copy changed) |
| exists | differs | same as remote | push (the local copy changed) |
| exists | differs | matches neither, or none recorded | conflict |
| any of the pull cases | changed less than 2 minutes ago | any | settling (unattended runs only) |

Notes:

- A pull is "settling" when the newest file in the shared copy was modified less than 120 seconds ago. The skill is skipped on that run and reconsidered on the next. This guards against copying a skill that is still uploading. Only `auto` applies it. `plan` run by hand never reports settling.
- If both machines have the same skill with different content and nothing is recorded, that is a conflict, not a guess.
- A skill missing on one side is always copied there, so deleting a skill on one machine does not delete it elsewhere.

### Conflicts

A conflict is reported with its name, which side is newer, and both modified times. `apply --push all --pull all` skips conflicts. To settle one, name it: `--push NAME` keeps this machine's copy, `--pull NAME` keeps the shared copy. The overwritten copy goes to `.backup/` first. In an interactive run, Claude shows you both times and asks.

## MCP servers, uv tools, plugins and connectors

`plan` also reports:

- `mcp.missing_here`: servers in the manifest that this machine does not have, with the config for this machine (`{HOME}` filled in) and the `env` or `headers` entries that need a value.
- `mcp.not_in_manifest`: servers this machine has that the manifest does not.
- `mcp.differs_from_manifest`: servers present in both whose portable form differs. These are never changed automatically.
- `uv.missing_here` and `uv.not_in_manifest`: tool names, compared with `uv tool list`.
- `account.plugins` and `account.connectors`: what the manifest lists. An interactive run compares this with what the session can see.

Local MCP servers are read from the top-level `mcpServers` in `~/.claude.json`. See [manifest.md](manifest.md) for how they are stored.

## What `auto` does

`sync.py auto` is what the scheduler runs. It does everything that is safe without a person present:

1. Checks that the shared folder, or its parent folder, exists. If not, it records an error.
2. Takes the lock. If another sync holds it, it records "another sync is running".
3. Runs `plan` with the 2-minute settle check, then `apply --push all --pull all`. Skills changed on one side are copied. Conflicts and settling skills are left alone.
4. Adds this machine's MCP servers and uv tools to the manifest (never removes anything).
5. Runs `uv tool install` for each uv tool in the manifest that is missing here. If uv is not installed, the failure is recorded.
6. Deletes backup folders older than 30 days.
7. Writes `machines/<host>/last-run.json` and appends a line to `sync.log`.

It exits with status 1 if the run recorded an error.

### What it leaves for an interactive run

- Conflicts.
- MCP servers missing on this machine. Adding one needs Claude to run `claude mcp add-json`, and often needs a secret from you.
- MCP servers whose config differs from the manifest.
- Failed uv installs.
- Account plugins and connectors. Unattended runs do not touch them.

Anything in these categories sets `needs_you` to true in `last-run.json`. `sync.py status` prints every machine's last run, and `/claude-sync` mentions entries marked `needs_you` or `error`.

### last-run.json

```json
{
  "run_at": "2026-10-12 09:00:00",
  "machine": "example-host",
  "ok": true,
  "pushed": [],
  "pulled": ["example-skill"],
  "conflicts": [],
  "settling": [],
  "manifest_added": {},
  "uv_installed": [],
  "uv_failed": [],
  "mcp_to_add": ["example-server"],
  "mcp_differs": [],
  "needs_you": true
}
```

`ok` is false when a uv install failed. When the run itself fails, the file holds `run_at`, `machine`, `ok: false` and an `error` message instead. Each conflict is an object with `name`, `newer`, `local_modified` and `remote_modified`.

A `sync.log` line looks like this:

```
2026-10-12 09:00:00  pushed 0, pulled 1, conflicts 0, uv installed 0, needs /claude-sync
```

## Interactive mode

`/claude-sync` loads the skill's `SKILL.md`, which tells Claude to work in this order: show `status` and `plan`, apply non-conflicting skill changes, resolve conflicts with you, add missing MCP servers, install missing uv tools, compare plugins and connectors, update the manifest, offer the schedule, and report. Claude reads the `SKILL.md` and scripts of any skill this machine has never had before pulling it, and asks you if something looks wrong.
