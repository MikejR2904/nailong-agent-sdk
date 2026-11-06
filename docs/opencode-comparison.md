# OpenCode compared with nailong

Written 2026-11-05 from OpenCode's own documentation (opencode.ai/docs: custom-tools, plugins,
agents, commands, permissions, config, rules, skills, lsp, formatters, mcp-servers, server, sdk,
acp, github, gitlab, providers, models, tui, keybinds, themes, cli, tools) and from this
repository's code. The `/docs/share/` page returned 404, and the SDK page says sharing has been
discontinued. The providers page was truncated when read, so the provider count is OpenCode's own
claim ("75+") and was not counted.

Status for `nailong`: **have** · **partial** · **missing** · **by decision** (see
[cli-parity.md](cli-parity.md)).

## 1. Where things live

| | OpenCode | nailong |
|---|---|---|
| Settings | `opencode.json(c)` project and `~/.config/opencode/`, `tui.json` | `.nailong/config.toml` and `~/.config/nailong/config.toml` |
| Layers | remote `.well-known`, global, `OPENCODE_CONFIG`, project, `.opencode` dirs, `OPENCODE_CONFIG_CONTENT`, managed files, macOS MDM; merged | user, project, flags; project cannot set hooks, MCP, `ssh_hosts`, `otlp_endpoint`, `offline` |
| Substitution in config | `{env:VAR}`, `{file:path}` | `{env:VAR}`, `{file:path}` in the user file only |
| Extension folders | `.opencode/{tools,plugins,agents,commands,skills,themes}` | `.nailong/{tools,agents,commands,skills,rules}`, plugin folders |
| Instructions | `AGENTS.md` (root, subfolders, global), `CLAUDE.md` fallback, `instructions` list with globs and URLs | `NAILONG.md`/`AGENTS.md`/`CLAUDE.md` from repo root to project, `NAILONG.local.md`, `.nailong/rules/`, `@` imports, subfolder index |

## 2. Extension points

| Feature | OpenCode | nailong | Status |
|---|---|---|---|
| Custom tools | `.opencode/tools/*.ts`, `tool({description, args (Zod), execute(args, context)})`, filename is the tool name, several exports become `file_export`, may override built-ins, `context` gives agent, session, message, directory, worktree | `.nailong/tools/*.py` and `~/.config/nailong/tools/`, `@tool` functions or a `run()` function; filename is the name for `run()`; a project file is loaded only after you read and trust its hash (`/trust-tools`); always asks for approval; may not override built-ins; no context argument | have (Python, not TypeScript) |
| Plugins | `.opencode/plugins/` and npm; hooks `tool.execute.before/after`, `shell.env`, `experimental.session.compacting`, a stream of ~30 events (file, lsp, message, permission, session, todo, tui); can add tools | plugin folders (`plugin.toml`: commands, skills, agents, hooks, mcp); nine hook events (adds `file_edited`, `permission_asked`, `compacted`), command only; custom tools as Python files | partial (fewer events; Python, not npm) |
| Agents | primary (Tab to switch) and subagents (`@name`, `task` tool), built-in build/plan/general/explore, per agent model, temperature, steps, permission, `hidden`, `permission.task` | permission modes (default, accept-edits, auto, plan, bypass) instead of primary agents; subagents as Markdown or tables with a model and a tool level; read-only ones parallel and in the background | partial (no `@name`, temperature, per-agent permission or step limit) |
| Commands | Markdown or JSON; `$ARGUMENTS`, `$1..$3`, `` !`cmd` `` output injection, `@file`, frontmatter `agent`, `model`, `subtask` | Markdown with `$ARGUMENTS`, `$1..$9`, `` !`cmd` `` (read-only runs, others ask), `@file`, header `description`, `agent`, `model`, `subtask` | have (JSON form not supported) |
| Skills | `SKILL.md` folders; reads `.claude/skills` and `.agents/skills`; `skill` tool; permission patterns | same layout; reads `.claude`, `.codex`; `/name`, `$name`; `allowed-tools`; manual-only | have (no skill permission patterns) |
| MCP | local and remote, `enabled`, `timeout`, `oauth`, `opencode mcp add/auth/logout/debug/list` | `/mcp add/remove/preset`, per-tool allow and deny, stdio and http | partial (no OAuth, by decision; no enabled flag or debug command) |

## 3. Permissions

OpenCode has `allow`, `ask` and `deny` per tool, with wildcard patterns where the last match wins
(`"bash": {"*": "ask", "git *": "allow", "rm *": "deny"}`), keys for `external_directory` and
`doom_loop`, `.env` files that ask by default, and `--auto` to approve what is not denied.
nailong has `[[rules]]` with allow, ask and deny and last-match-wins patterns, five modes, a
saved-approval store, an OS sandbox, protected folders, a repeat guard (the `doom_loop`
equivalent), and file tools that already refuse credential files such as `.env`. **Partial:**
subagents take `deny:` rules, `temperature` and `steps` in their files, but there is no per-agent `allow`, and a project file may not add `allow` rules.

## 4. Language intelligence and editing

| Feature | OpenCode | nailong | Status |
|---|---|---|---|
| LSP diagnostics fed to the model | `lsp` key, ~30 built-in servers, off by default, auto-download | `lsp_on_edit` and `[lsp.NAME]`: a real language-server client (pyright, typescript, gopls, rust-analyzer when installed) reporting errors and warnings for edited files; plus `diagnostics_on_edit` linters; tested only against a stand-in server | partial (no go-to-definition, no auto-download) |
| Formatters after edits | `formatter` key, ~25 built-ins plus custom commands | `format_on_edit` (ruff, prettier, gofmt, rustfmt, shfmt) and `[formatters.NAME]` | have (5 built-ins) |
| `apply_patch` multi-file patch tool | yes | `apply_patch` in OpenCode's patch format, all-or-nothing, rewindable | have |
| Built-in tools | bash, edit, write, read, grep, glob, apply_patch, skill, todowrite, webfetch, websearch, question, lsp (experimental) | run_command, edit_file, write_file, read_file, grep, glob, todo_write, web_fetch, web_search, ask_human_question, notebook_edit, use_skill, plus shells, notes, background jobs | have, except apply_patch |
| Undo and redo with snapshots | `/undo`, `/redo`, internal git snapshots | `/rewind N` from checkpoints, `/redo` after a rewind | have (redo lasts until the next turn) |
| Compaction | `auto`, `prune`, `reserved` buffer | `/compact`, `autocompact`, context budget | have |

## 5. Surfaces and integration

| Feature | OpenCode | nailong | Status |
|---|---|---|---|
| Whole-agent HTTP server | `opencode serve`, OpenAPI 3.1, SSE events, basic auth; `attach`, `web` | `--serve` (`POST /run`, SSE with `?stream=1`, bearer token) and `--attach`; plan or accept-edits runs only; no OpenAPI document or web UI | partial |
| SDK | `@opencode-ai/sdk` (TypeScript), structured output | Python SDK (`nailong_agent_sdk`), JSON-schema answers | have (Python) |
| Editor protocol (ACP) | `opencode acp` for Zed, JetBrains, Neovim | `--acp`: initialize, session/new, session/prompt with updates; no cancel; not tried with a real editor | partial |
| GitHub / GitLab | `opencode github install`, `/opencode` mentions in issues and PRs, CI component | `/ci github`, `/ci gitlab`: workflow files that answer trusted `/nailong` comments or a manual pipeline, read-only | partial (no GitHub App, no PR review comments) |
| Headless run | `opencode run` with `--format json`, `--file`, `--attach` | `-p`, `--output json|stream-json`, `--image` | have |
| Scheduled runs | not documented | `/schedule`, `--run-schedules` | ahead |

## 6. Models

OpenCode claims 75+ providers through the AI SDK and Models.dev, `/connect` and `opencode auth`,
`provider/model` ids, `small_model` for titles, per-model `variants` cycled with a key, provider
black and white lists, and any OpenAI-compatible local server. nailong has Anthropic and
OpenAI-compatible services (so OpenRouter, Ollama, LM Studio and vLLM work), fallback models,
`--effort` and parameters, prices and budgets, an offline mode, and prompted tools for models with
no tool calling, `small_model`, `[variants.NAME]` with `/variant`, and a service menu of 16 (OpenRouter,
Anthropic, OpenAI, Gemini, xAI, Groq, Together, Fireworks, DeepSeek, Mistral, Cerebras and local
servers) with `/connect` to switch in a session. **Missing:** a 75-provider catalogue.

## 7. Terminal interface and commands

OpenCode has `/connect /compact /details /editor /exit /export /help /init /models /new /redo
/sessions /themes /thinking /undo /update`, a leader-key keymap configured in `tui.json`, 11
themes plus custom theme files, and an `$EDITOR` handoff. nailong has 51 slash commands,
four themes (default, dark, light, mono), emacs or vi input, Shift+Tab mode cycling, type-ahead, an Esc menu and a status
toolbar, configurable keybinds (`[keybinds]`) and theme files. `/editor`, `/stats`, `/sessions delete`, `--completion` and `--upgrade` (which
prints the command) are in. **Missing:** a model picker (we have `/model` by name), session import,
and an in-place updater.
Its CLI also has `stats`, `session list/delete`, `export/import`, `models`, `pr`, `db`, `debug`,
`completion`, `upgrade` and `uninstall`; ours has the session flags, `--forget-key`, `--setup`
and `/doctor`. **Missing:** usage statistics, session delete, session import, `completion`,
self-update.

## 8. What nailong has that OpenCode's docs do not show

Hash-chained audit and telemetry that export to OpenTelemetry with signed integrity records;
`/pause` and `/unpause` that freeze work in place; persistent and SSH shells; task notes;
scheduled prompts; the forge (model-written, reviewed tools); a planner that splits work into a
team; background subagents; fallback models and a spend cap; offline mode; and prompted tools for
models that cannot call tools; and, since 2026-11-06, reading PDF/Word/Excel files, a cited
knowledge base (`/kb`), an agent-written wiki (`/wiki`) and eight built-in skills.

## 9. The gap list, and where it stands

Closed on 2026-11-05: permission `ask` rules with last-match-wins patterns, `apply_patch`, command
placeholders and `` !`cmd` `` injection with `agent`/`model` headers, formatter-after-edit and
linter diagnostics, `{env:}`/`{file:}` substitution and an `instruction_files` list, `small_model`,
model variants and `@name` subagent mentions, a whole-agent service (`--serve`, `--attach`), and
configurable keybinds and theme files.

Also closed (second pass): a language-server client behind `lsp_on_edit`, a redo for `/rewind`,
per-subagent `deny`, `temperature` and `steps`, three more hook events, `/editor`, `/stats`,
`/sessions delete`, shell completion, `--upgrade`, six more hosted providers with `/connect`,
GitHub and GitLab workflow files (`/ci`), an OpenAPI description and a web page for `--serve`, and
an `--acp` mode for editors.

Still open, and why:

1. **Language servers:** closed in part on 2026-11-06: the agent has `code_navigate`
   (definition, references, hover) and the client answers it; rename and automatic download of
   servers are still missing, and the client has only met a stand-in server, not pyright or gopls.
2. ~~**Approvals over the service or ACP**~~ closed on 2026-11-06: `POST /run?stream=1` with
   `approvals` emits `approval_request` events answered by `POST /approve`; `--acp` in `default`
   mode sends `session/request_permission`. Only one-time yes/no answers, tested with scripted
   clients.
3. **ACP is unverified against an editor.** Cancel is closed on 2026-11-06 (`session/cancel`, and
   `POST /cancel` over the service).
4. **Plugins as npm packages.** The event stream gained `assistant_message` and `tool_failed` on
   2026-11-06; custom tools are still Python files.
5. **A GitHub App.** `/ci github-review` writes a pull-request workflow that posts one inline
   COMMENT review (not for forks, read-only agent); it has not run against GitHub, and a hosted App
   with its own identity is not built.
6. **A 75-provider catalogue and an in-place updater.** `/models` (the service's own list),
   `/sessions import` (Claude Code and Codex transcripts) and 20 hosted providers exist.
7. **An interactive "ask" for subagents running in the background.** Closed by decision: a
   background subagent never asks; a user-level agent file may carry an `allow:` list (project agent
   files cannot) and everything else is refused.
