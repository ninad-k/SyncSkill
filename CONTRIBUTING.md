# Contributing

Thanks for helping improve claude-sync.

## Running the tests

From the repository root:

```sh
python -m unittest discover -s tests -v
```

CI runs the same command on Ubuntu, Windows and macOS with Python 3.8 and 3.12.

## Ground rules

- **Standard library only.** Do not add third-party dependencies.
- **Python 3.8 compatible.** Avoid syntax and standard library APIs newer than 3.8.
- **Never delete user files.** Overwrites must go through the backup folder first.
- **Never write secret values to the sync folder.** MCP env and header values are replaced with `<SET_ME>` before anything is written.
- **Keep docs in step with behaviour.** If a change alters what the tool does, update the README and `docs/` in the same pull request.
- **Add a changelog entry.** Put it under `## [Unreleased]` in `CHANGELOG.md`.

## Pull requests

Fill in the pull request template. Make sure the tests pass, the docs are updated and the changelog entry is in before you ask for review.
