---
name: claude-sync
description: >-
  Syncs this machine's Claude Code setup with the user's other machines through
  a shared OneDrive folder: personal skills in ~/.claude/skills (two-way, edits
  carried over, never deletes), user-scope MCP servers (secrets never stored),
  uv command-line tools, and a check of which account plugins and connectors
  are enabled. Can also schedule itself to run unattended. Use when the user
  says "sync my setup", "sync skills", "set up this laptop like my PC", "copy my
  skills to my other machine", "schedule the sync", "claude-sync", or just set
  up Claude Code on a new machine.
---

# claude-sync

The shared folder is `OneDrive\ClaudeSync` (on macOS
`~/Library/CloudStorage/OneDrive*/ClaudeSync`), or `CLAUDE_SYNC_DIR` if set.
It holds `skills/`, `manifest.json` (MCP servers, uv tools, account plugins,
connectors), `machines/<name>/` (each machine's last unattended run) and
`.backup/`. If the user wants a different folder, pass `--dir` to every command.

The tool is `sync.py` in this skill's base directory (shown when the skill
loads; usually `~/.claude/skills/claude-sync`). Below, `SYNC` means
`python "<base directory>/sync.py"`. It needs Python 3.8 or newer (`python`,
`python3` or `py`). If none is installed, say so and stop.

## Step 1: plan

```bash
SYNC status
SYNC plan
```

Neither changes anything. `status` shows each machine's last unattended run.
Mention anything in it marked `needs_you` or `error`. Then summarise `plan` in
a few lines: skills to push and pull, conflicts, MCP servers and uv tools
missing here, and anything on this machine that isn't in the manifest yet. If
`manifest_exists` is false, this is the first run: say so, and this machine
becomes the starting point.

Skills are compared against the version recorded at this machine's last sync:
changed only here means push, changed only in the shared folder means pull,
changed on both sides means conflict.

## Step 2: skills

Copy everything that isn't a conflict:

```bash
SYNC apply --push all --pull all
```

`all` never touches conflicts. For each conflict, show the user both modified
times and which side is newer, and ask which copy to keep (suggest the newer
one). Then pass the names explicitly: `--push name` keeps this machine's copy,
`--pull name` keeps the synced copy. The copy that gets overwritten is backed
up to `.backup/` first.

Before pulling a skill this machine has never had, read its SKILL.md and any
scripts in it. If something looks wrong, ask before pulling it.

## Step 3: MCP servers

For each server in `mcp.missing_here`:

- If `needs_values` isn't empty, ask the user for those values, or tell them
  which variable to set. Never invent a value, and never write one into the
  sync folder.
- On macOS or Linux, rewrite Windows-only commands (`cmd.exe /d /c npx ...`
  becomes `npx ...`). Leave paths to `.exe` files for the user to sort out.
- Add it with the Bash tool. Use single quotes so the JSON survives the shell:

```bash
claude mcp add-json --scope user NAME '{"type":"stdio","command":"...","args":[...]}'
```

If a server's command is a program installed by a uv tool (for example under
`~/.local/bin`), install that tool first (step 4).

For anything in `mcp.differs_from_manifest`, show the user the difference and
ask which one is right. Don't change it on your own.

## Step 4: uv tools

For each name in `uv.missing_here`, run `uv tool install NAME`. If uv itself
isn't installed, ask before installing it (Windows:
`powershell -c "irm https://astral.sh/uv/install.ps1 | iex"`, macOS and Linux:
`curl -LsSf https://astral.sh/uv/install.sh | sh`).

## Step 5: account plugins and connectors

These come from the user's Claude account and the desktop app, not from files,
so this skill can't install them. Work out which ones this session can see:

- plugins: the prefixes of namespaced skills (`legal:review-contract` means
  `legal`) and `plugin:<name>:...` MCP servers. Leave out `anthropic-skills`,
  which every account has.
- connectors: the claude.ai connectors and MCP tools available (Notion, Gmail,
  Google Drive, Todoist and so on)

Compare them with `account.plugins` and `account.connectors` in the plan. For
each one that's missing, tell the user where to turn it on: the plugin's
settings in the desktop app, claude.ai connector settings, or `/mcp` to sign in.

## Step 6: update the manifest

Record what this machine has, so the other machines pick it up:

```bash
SYNC manifest --plugins a,b,c --connectors "Notion,Gmail"
```

It only adds entries. It never removes any, and it never stores secret values
or this machine's home path.

## Step 7: the schedule

```bash
SYNC schedule show
```

If nothing is scheduled, offer to set it up. It's a standing change to the
machine, so wait for a yes:

```bash
SYNC schedule install --every 4
```

It runs `sync.py auto` at login and every 4 hours, as the user, with no admin
rights (Windows Task Scheduler, launchd on macOS, cron on Linux). The
unattended run syncs skills that changed on one side only, updates the
manifest and installs missing uv tools. It leaves conflicts, MCP servers and
anything needing a secret for the next `/claude-sync`, and records them in
`machines/<name>/last-run.json`. `SYNC schedule remove` turns it off.

## Step 8: report

Keep it short: skills pushed and pulled, conflicts and how they were settled,
MCP servers added, tools installed, plugins and connectors still missing,
whether the schedule is on, and anything the user has to do by hand. Remind
them to restart Claude Code if MCP servers were added. New skills show up
without a restart.

## Stopping a skill from syncing

Sync never deletes, so a skill removed on one machine comes back from another.
To drop one everywhere, delete it from `~/.claude/skills` on every machine and
from `ClaudeSync/skills`. Do this only when the user asks.

## A new machine

On a machine without this skill, once OneDrive has finished syncing, copy the
skill across, then run `/claude-sync`:

```bash
cp -r ~/OneDrive/ClaudeSync/skills/claude-sync ~/.claude/skills/
```
