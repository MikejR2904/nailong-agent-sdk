# Changelog

All notable changes are listed here. The project has not published a release yet; everything is
under "Unreleased" until the first tag.

## Unreleased

### CLI
- Sessions: an unused session is removed when you `/resume`, `/new` or quit, and `-c` picks the
  latest session that has turns.
- `-p` reads the prompt from standard input when none is given.
- Custom command placeholders (`$1`, `$ARGUMENTS`) are filled in one pass.
- A call to a tool that is not offered returns an error to the model instead of ending the run.
- Hooks accept Claude Code's event names, a `matcher`, JSON on standard input, exit code 2 to
  block and JSON decisions; `/hooks` lists them.
- A project's hooks and MCP servers can be approved with `--trust-project` or `/trust-project`.
- `--output claude-stream-json` writes Claude Code's stream event shapes.
- New commands: `/config`, `/hooks`, `/login`, `/image`.
- `/rewind` says when a project is too large to undo what a command changed.
- `/rewind` lists files through git when it can, so ignored build folders no longer count toward
  its size limit.
- `nailong -r` with no id lets you choose a session; `/rename NAME` renames the current one.
- Hooks can rewrite a call's input and allow it without a prompt; `PreCompact` and `SubagentStop`
  hooks; `--input-format stream-json`; the init event lists the tools.

### SDK
- `BaseAgent` and the starter `Agent` can return an unknown-tool call to the model as an error
  (`recover_from_unknown_tools`).
- Refusals can carry a reason to the model (`Refusal`).

### Project
- CI runs lint, types, SDK and CLI tests on Linux and Windows, a bubblewrap sandbox job, a Docker
  sandbox job and a wheel build.
- Added `SECURITY.md`, `CONTRIBUTING.md` and this file.
