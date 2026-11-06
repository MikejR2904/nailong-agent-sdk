# CLI review against Claude Code, Codex and OpenCode

Date: 2026-11-06. Scope: `apps/nailong_cli`. The question: for the features the other three tools
have and this CLI also claims, does this CLI behave the way a user of those tools would expect?

## How this was checked

- Each behaviour below was run through the real CLI entry point (`nailong_cli.cli.main`) with a
  scripted model, so what is reported is what the code does, not what its documentation says.
- What the other three tools do comes from their public documentation as known to me. I did not
  run them side by side, and their behaviour may have changed.
- **Not checked:** behaviour with a live model (beyond the earlier small-model runs), a real
  terminal (key handling, the prompt menu, Ctrl+C), and macOS.

## Behaviours that match, or go further

| Behaviour | Result |
|---|---|
| Allow and deny rules on shell commands | Correct. A rule for `echo*` does not approve `echo a && echo b > f`, `echo $(...)` or `echo a \| tee f`; each part of a chain is checked. A deny rule matches inside a chain (`echo hi; rm x` is refused by a rule on `rm*`). Deny beats allow. |
| Secrets and boundaries | Correct. `.env` is refused as a credential path, paths outside the project are refused, and `.git/`, `.nailong/` and `NAILONG.md` cannot be written by file tools. |
| Plan mode | Correct. The write and command tools are not offered at all. |
| Memory files | Correct. `AGENTS.md`, `CLAUDE.md` and `NAILONG.md` are read, and an `@other.md` import inside one is followed. |
| Skills | Compatible. Folders with `SKILL.md` are read from `~/.claude/skills`, `~/.codex/skills` and `.claude/skills`. |
| MCP config file | Compatible. `--mcp-config` takes the same `{"mcpServers": {...}}` JSON, with the same stdio and URL forms. |
| Refusals | Better than the others: the model is told why a call was refused and not to retry it. |
| Sessions | Correct after today's fix (see below). |

## Defects found and fixed in this review

1. **`nailong -p` with only piped input failed.** `cat notes.md | nailong -p` was rejected with
   "expected one argument"; Claude Code and Codex accept it. Now `-p` without a prompt reads it
   from standard input, joins it to a prompt when both are given, and says what is missing when
   there is neither (exit 64). Tests: `tests/test_print_stdin.py`.
2. **Custom command placeholders were substituted twice.** In a command file using both `$1` and
   `$ARGUMENTS`, a literal `$2` inside the text you typed was replaced by your second word.
   Placeholders are now filled in one pass. Tests: `tests/test_command_placeholders.py`.
3. **Empty sessions piled up and `-c` could pick one.** See the session fix (`session_hygiene.py`).
4. **`/rewind` said "nothing changed" in projects too large to snapshot.** It now says so.

## Differences that remain

Ordered by how likely they are to surprise someone coming from the other tools.

| # | Difference | Why it matters |
|---|---|---|
| 1 | **A call to a tool that is not offered ends the whole run as failed.** The model gets no chance to recover. Claude Code, Codex and OpenCode return an error to the model. | A model that remembers `write_file` after you switch to plan mode, or invents a tool name, loses the entire run. The cause is in the SDK (`TOOL_NOT_DECLARED` is a terminal failure in `BaseAgent`); the fix needs a recoverable mode threaded through the runtime layers. Not done. |
| 2 | **Hooks are not compatible with theirs.** Input arrives in environment variables, not as JSON on standard input; any non-zero exit blocks (Claude Code: exit 2 blocks, other codes only warn); a hook cannot return a decision, rewrite the input, or add context; there are six events against their dozen or more; hooks come from user settings only. | A hook script written for Claude Code will not work unchanged, and a hook cannot approve or rewrite a call. |
| 3 | **`--output stream-json` is this tool's own schema** (`session`, `text`, `reasoning`, `tool_use`, `tool_result`, `result`). Tool calls carry no id, and the result has no usage, cost or duration. There is no `--input-format stream-json`. | Programs written to read Claude Code's stream will not parse it. |
| 4 | **Project hooks and project MCP servers are ignored with no way to approve them.** Claude Code asks once whether to trust a project's `.mcp.json`. | A team cannot share an MCP setup in the repository; each person must copy it into user settings. |
| 5 | **On a machine with no sandbox (Windows), a command needs `--allow-unsandboxed` even when a rule allows it.** | Safer than the others, but `-p` scripts need an extra flag. The refusal message says exactly this. |
| 6 | **Subagents run one at a time and default to read-only.** | Less parallelism than Claude Code. |
| 7 | **`nailong -r` needs an id; there is no picker at launch.** `/resume` inside the REPL has one. | Minor. |
| 8 | **Nested `AGENTS.md` files in sub-directories are not read.** Claude Code loads a sub-directory's file when the agent works there. | Monorepos with per-package rules lose them. |
| 9 | **No `/config`, `/login`, `/hooks`, `/rename`, themes, or sharing.** Unknown commands say "Unknown command" with a pointer to `/help`. | By design for login and sharing; the rest is a gap. |
| 10 | **No clipboard image paste.** `--image FILE` and `@image.png` work. | Minor. |
| 11 | **Undoing command effects stops at 5,000 files or 100 MB.** Claude Code and Codex lean on git. | Large repositories. |

## Documentation that is out of date

`docs/cli-parity.md` still says "nothing here has been run against a live model" and that the
sandbox backends are "not run here". Both were true when written and are not now. It should be
refreshed from this review.

## Verdict

For everyday work (permissions, memory files, skills, MCP, sessions, undo of file edits) the
behaviour is correct and in places stricter than the others. The two things most likely to hurt
someone arriving from another tool are the run-ending unknown-tool failure (1) and hooks that do
not port (2). Neither is fixed here.
