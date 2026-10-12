# Security Policy

## What claude-sync writes, and where

- **The sync folder** (by default `OneDrive\ClaudeSync`, or the folder set with `CLAUDE_SYNC_DIR`): synced skills, the MCP server manifest, uv tool list and backups under `.backup/`.
- **`~/.claude/claude-sync-state.json`**: the record of what was last synced on this machine, used for the three-way merge.
- **`~/.claude/claude-sync-last-run.json`**: the status of this machine's last unattended run.
- **`~/.claude/claude-sync.lock`**: a lock file that stops two syncs running at once on the same machine. A lock older than 30 minutes is treated as stale.
- **The OS scheduler**: when you turn on the schedule, a Windows Task Scheduler task, a launchd plist or a crontab entry is created to run `sync.py auto`.

## Secrets

MCP server `env` and header values are replaced with `<SET_ME>` before the manifest is written to the sync folder. Fill them in on each machine by hand.

## Trust model

Skills pulled from the sync folder are run as code by Claude Code. Only point claude-sync at a sync folder that you control, and that no one else can write to.

## Reporting a vulnerability

Please do not open a public issue for security problems. Report them privately using GitHub's private vulnerability reporting: go to the repository's **Security** tab and choose **Report a vulnerability**.
