# The manifest

`manifest.json` lives in the root of the shared folder. It records the parts of your setup that are not skill folders: MCP servers, uv tools, account plugins and connectors. Every machine reads it and adds to it. See [how-it-works.md](how-it-works.md) for how it is used.

## Schema

```json
{
  "account_plugins": ["example-plugin"],
  "connectors": ["Example Connector"],
  "mcp": {
    "example-server": {
      "type": "stdio",
      "command": "{HOME}/.local/bin/example-mcp",
      "args": ["--stdio"],
      "env": {
        "API_KEY": "<SET_ME>"
      }
    },
    "example-remote": {
      "type": "http",
      "url": "https://example.com/mcp",
      "headers": {
        "Authorization": "<SET_ME>"
      }
    }
  },
  "updated": "2026-10-12",
  "uv_tools": ["example-tool"]
}
```

| Key | Type | Meaning |
|---|---|---|
| `mcp` | object | User-scope MCP servers, keyed by name. Each value is the server's config as Claude Code stores it, made portable (below). |
| `uv_tools` | list of strings | Names of uv tools. Versions are not recorded. |
| `account_plugins` | list of strings | Plugin names that should be enabled on every machine. |
| `connectors` | list of strings | Connector names that should be enabled on every machine. |
| `updated` | string | Date of the last write, `YYYY-MM-DD`. |

Keys missing from an existing file are treated as empty. The file is written with sorted keys.

## What is stripped from MCP servers

When a server is added to the manifest, two changes are made so the file is safe to share:

- Every value under `env` and `headers` is replaced with `<SET_ME>`. The names are kept.
- Your home directory path is replaced with `{HOME}` wherever it appears in the config. On another machine, `{HOME}` is replaced with that machine's home path.

Nothing else is changed. `command`, `args` and `url` are copied as they are, so keep secrets out of them.

When a machine is missing a server, `plan` lists the `env` and `headers` entries still set to `<SET_ME>` as `needs_values`, for example `env.API_KEY`. Claude asks you for those values, or tells you which variable to set. They are never written back to the manifest.

Commands that start with a Windows-only wrapper, such as `cmd.exe /d /c npx ...`, are copied as they are. In an interactive run on macOS or Linux, Claude rewrites them (`npx ...`) before adding the server.

## How entries get added

| Entry | Added by |
|---|---|
| MCP servers | `sync.py manifest`, and every `auto` run. Only servers not already in the manifest are added. An existing entry is never overwritten. |
| uv tools | `sync.py manifest`, and every `auto` run, from `uv tool list`. |
| Account plugins | `sync.py manifest --plugins a,b`, run by Claude in an interactive session. |
| Connectors | `sync.py manifest --connectors "A,B"`, run by Claude in an interactive session. |

Entries are only ever added. No command removes one.

## Editing the manifest

The file is plain JSON, so you can edit it by hand. Edit it while no sync is running, and keep the `<SET_ME>` placeholders in place of real values. To change a server that is already listed, edit its entry in the file. `sync.py manifest` will not update it.

If a server on a machine differs from its manifest entry, `plan` lists it under `mcp.differs_from_manifest` and nothing is changed until you decide which one is right.

## Stopping syncing something

Because every machine adds what it has to the manifest, removing an entry from the file is not enough while some machine still has the item. To stop syncing:

- **An MCP server.** Remove it from the `mcp` object in `manifest.json`, and remove it from every machine that has it (for example `claude mcp remove NAME --scope user`). Otherwise the next `manifest` or `auto` run on a machine that still has it adds it back.
- **A uv tool.** Remove its name from `uv_tools`, and run `uv tool uninstall NAME` on every machine that has it, for the same reason.
- **An account plugin or connector.** Remove the name from `account_plugins` or `connectors`. These are only added by interactive runs, so it stays removed until someone records it again.
- **A skill.** Delete it from `~/.claude/skills` on every machine and from `skills/` in the shared folder. Syncing never deletes, so a copy left anywhere comes back.
