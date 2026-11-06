# CLI parity: what Claude Code and Codex have, and what `nailong` has

Compared against the public documentation of Claude Code (CLI reference, commands, memory,
permissions) and the Codex CLI at the time of writing. "Built" means implemented in
`apps/nailong_cli` and tested with scripted models. A small live-model run has been made
(`openai/gpt-4o-mini` through OpenRouter); see `docs/cli-review-2026-11-06.md` for what was checked
against Claude Code, Codex and OpenCode and what still differs.

## Built

| Feature | In Claude Code / Codex | In `nailong` |
|---|---|---|
| Interactive session, start with a message | `claude "q"`, `codex` | `nailong "q"` |
| Run once and exit | `claude -p`, `codex exec` | `nailong -p [PROMPT]`; piped stdin is the prompt, or is added to it |
| Output formats | text, json, stream-json | `--output text|json|stream-json` |
| Exit codes for scripts | yes | 0 completed, 1 failed, 2 blocked, 64 setup, 130 interrupted |
| Continue / resume / fork a session | `-c`, `-r`, `--fork-session` | `-c`, `-r ID`, `--fork-session`, `--fork ID --at N`, `/resume` picker |
| Permission modes | default, accept edits, plan, bypass | `--permission-mode default|accept-edits|plan|bypass`, `/mode` |
| Plan mode, then carry it out | yes | read-only tools; after the plan you choose to proceed |
| Allow and deny rules | `--allowedTools`, `--disallowedTools` | `--allow TOOL[:PATTERN]`, `--disallow`, saved rules, `/permissions` |
| Approval prompts with a diff | yes | yes, as an arrow-key menu with session and always choices |
| Esc to interrupt, spinner | yes | Esc or Ctrl+C stops a turn; animated spinner with elapsed time |
| Markdown answers, collapsed tool output | yes | formatted markdown a block at a time; three-line previews, Ctrl+O or `/expand` for all |
| Sandbox | Seatbelt, bubblewrap; Codex sandbox modes | bubblewrap, Seatbelt, Docker; says plainly when there is none |
| Memory files | `CLAUDE.md`, `AGENTS.md` | `NAILONG.md`, `AGENTS.md`, `CLAUDE.md`, and a user file |
| `/init` | writes the memory file | drafts `AGENTS.md` read-only, asks before saving |
| `@file` mentions | yes | files and directories (not secrets, not outside the project) |
| `!` shell mode | yes | runs your own command, not the agent's |
| Custom slash commands | `.claude/commands` | `.nailong/commands/*.md` with `$ARGUMENTS` |
| Skills | `.claude/skills` | `.nailong/skills/*.md`; an index is sent and the agent loads one with `use_skill` when it matches |
| Hooks | many events | `pre_tool`, `post_tool`, `session_start`, `session_end`, `prompt_submit`, `turn_end` shell hooks, in your user settings only |
| MCP servers | `claude mcp`, `--mcp-config` | `[mcp.NAME]` in user settings and `--mcp-config` JSON |
| Subagents | `--agents` | `[agents.NAME]` read-only subagents the agent can call as tools |
| Task list | todo tool | `todo_write`, shown as a checklist, `/todos` |
| Undo the agent's file changes | `/rewind` | `/rewind [N]` for files changed through the file tools |
| `/diff`, `/review` | `/review`, `codex review` | `/diff`, `/review` (read-only agent) |
| `/model`, `/status`, `/doctor`, `/context`, `/cost` | yes | yes (`/cost` needs prices) |
| Web fetch | yes | `--web`, asks every time |
| Structured output | `--json-schema` | `--json-schema FILE` |
| Step limit, extra instructions | `--max-turns`, `--append-system-prompt` | `--max-turns`, `--system-prompt-append` |
| Line editing and history | yes | `readline` where the platform has it; a prompt_toolkit prompt in a terminal: rules above and below the input, a command menu on `/`, `@` file completion, multi-line input, Shift+Tab mode cycling and a status bar |
| `/compact`, auto-compact | summarises older turns | `/compact [focus]` and `--autocompact`: the model writes a summary kept in the session log and sent in place of the older turns |
| Images | `--image`, paste | `--image FILE` and `@image.png`; sent as image blocks |
| Web search | `--search` | `--search` adds the SDK's search tool |
| Background commands | yes | `run_command` can start a background job; `/jobs`, `/kill ID` |
| More directories | `--add-dir`, `/add-dir` | `--add-dir DIR`, `/add-dir PATH [name]`: file tools, sandbox and undo list all cover them |
| Git worktree per session | `-w` | `--worktree [NAME]` |
| Plugins | plugin directories | `--plugin-dir DIR`, `~/.config/nailong/plugins/`; `/plugins` |
| Budget, effort, parameters | `--max-budget-usd`, effort | `--max-budget-usd`, `--effort`, `--param KEY=JSON`, `[prices.MODEL]` settings |
| Mode cycling, status line | Shift+Tab | yes, in a terminal |
| Auto mode | classifier | `auto` mode: read-only commands and edits run, the rest asks |
| Session names, search, export | `/rename`, search | `-n NAME`, `/search TEXT`, `/export [file]` |
| `--verbose`, `--debug-file` | yes | yes |
| Prompt suggestions | yes | `/suggest` |
| Rewind after commands | file checkpoints | whole-tree checkpoints; `/rewind N` restores and removes what the agent and its commands changed |
| `--review` | `codex review` | `--review [BASE]` prints a read-only review and exits |
| Creating its own tools | no | `--forge`: proposed, reviewed, tested, versioned, rollbackable |

## Partly built

| Feature | What is missing |
|---|---|
| Hooks | Shell commands only, user settings only (a hook is a program, so project files may not add one) |
| Subagents | Defined in settings, run one at a time; the tool level is read-only, edit or all |
| Permission rules | Command-aware matching on each part of a chained command; no learned classifier |
| Image input | PNG, JPEG, GIF and WebP up to a size limit; no clipboard paste |
| Background commands | Output is kept in memory per job; jobs end with the session |
| Sandbox backends | bubblewrap and Docker run in CI on Linux; Seatbelt is unit-tested on its command line only |

## Deliberately not built

| Feature | Why |
|---|---|
| Login flows (OAuth), accounts | This is for research and runs locally; you bring an API key |
| IDE integration, desktop, remote and cloud sessions | Out of scope for a terminal app |
| Plugin marketplace | Plugins are directories you install yourself |
| `/cd` | One project root; use `--add-dir` for more |

## Differences worth knowing

- A refused or declined action goes back to the model as a failed tool result, so it can adapt;
  the SDK's default for a governed backend is to end the run as blocked. The CLI opts into the
  first with `recoverable_refusals`.
- Saved permission rules live in your user directory, never in the project, and `.nailong/` and
  `NAILONG.md` cannot be written by the agent's file tools.
- Hooks and MCP servers in a project's settings file are ignored (and `/doctor` says so), because
  they run programs.
