# Feature gaps against Claude Code and Codex

What Claude Code and Codex document that `nailong` does not do, and how each compares. Written
2026-11-05 from their public documentation (code.claude.com/docs, learn.chatgpt.com/codex) and
from this repository's code.

**How reliable this is.** The two product inventories were read by a fetch agent and returned as
summaries. Items tagged *partial* in its report came from truncated pages, and the Codex GitHub
repository was not read. A dash (`-`) under a product means *not seen in the pages read*, not
*absent*. Our own column was checked against the code (searches of `apps/nailong_cli` and `src`).

**Status key for `nailong`.** `have` · `partial` · `SDK only` (the SDK has it, the CLI does not
expose it) · `missing` · `by decision` (left out on purpose; see
[cli-parity.md](cli-parity.md)).

## 1. Surfaces and distribution

| Feature | Claude Code | Codex | nailong |
|---|---|---|---|
| Terminal CLI | yes | yes | have |
| IDE extension | VS Code, JetBrains | VS Code family, Xcode, JetBrains | by decision |
| Desktop app | yes | yes (ChatGPT desktop) | by decision |
| Hosted cloud tasks that keep running | `claude --cloud`, claude.ai/code | Codex Cloud, `codex cloud exec`, `codex apply` | by decision |
| Mobile | yes | yes | by decision |
| Move work between surfaces | `--teleport`, `/desktop` | "continue in cloud", `codex apply` | by decision |
| Drive a local session remotely | `claude remote-control` | `codex remote-control` (experimental) | by decision |
| Installers, package managers, self-update | script, Homebrew, WinGet, `claude update` | script, npm, Homebrew, `codex update` | missing (uv/pip only) |
| Shell completion | - | `codex completion` | missing |

## 2. Accounts and enterprise

| Feature | Claude Code | Codex | nailong |
|---|---|---|---|
| Login with a subscription, device login, token login | yes | `codex login` variants | by decision (API keys; keys kept in the OS credential store) |
| Managed / MDM / server-delivered settings, managed-only switches | yes | `requirements.toml`, admin guides | missing |
| Forced login method or workspace | `forceLoginMethod` | `forced_login_method` | missing |
| Usage limits and plan allowances | `/usage` | `/status`, `/usage` | n/a (no plans) |

## 3. Command line

| Feature | Claude Code | Codex | nailong |
|---|---|---|---|
| Run once and exit | `-p` | `codex exec` | have |
| Text / JSON / streamed JSON output | `--output-format` | `--json` (JSON Lines) | have |
| Streamed JSON *input* | `--input-format` | stdin as prompt | missing |
| Answer constrained by a JSON Schema | `--json-schema` | `--output-schema` | have |
| Write the final message to a file | - | `-o` | missing (redirect works) |
| Run without saving a session | - | `--ephemeral` | missing |
| Spending cap | `--max-budget-usd` | - | have |
| Fallback model on failure | `--fallback-model` | - | have (`fallback_models`, `--fallback-model`) |
| Replace the system prompt | `--system-prompt[-file]` | `model_instructions_file` | partial (append only) |
| Load an extra settings file / choose sources | `--settings`, `--setting-sources` | `-c key=value`, `--profile` | missing |
| Named configuration profiles | - | `--profile` | missing |
| Restrict the built-in tool set | `--tools` | per-tool config | missing (rules can deny) |
| Minimal mode (no hooks, memory, plugins) | `--bare` | `--ignore-user-config`, `--ignore-rules` | missing |
| Strict MCP config | `--strict-mcp-config` | `required` servers | missing |
| Continue, resume, fork, name sessions | `-c`, `-r`, `--fork-session`, `-n` | `resume`, `fork` | have |
| Start from a pull request | `--from-pr` | - | missing |
| Archive, delete, rename saved sessions | `/rename`, `claude purge` | `archive`, `unarchive`, `delete` | missing (list, resume, fork only) |
| Queue a message for an existing session | - | `codex queue` | missing |
| Diagnostics | `claude doctor` | `codex doctor` | have (`/doctor`) |
| Local open-source models | env / gateway | `--oss --local-provider` | have |
| Run a command inside the sandbox / check a rule | - | `codex sandbox`, `codex execpolicy check` | missing |
| Review changes non-interactively | `claude ultrareview` | `codex review` | have (`--review`) |
| Feature flags you can toggle | - | `codex features` | missing |

## 4. Interactive terminal

| Feature | Claude Code | Codex | nailong |
|---|---|---|---|
| Esc to interrupt | yes | yes | have (also `/interrupt`, `/stop ID`) |
| Pause and resume running work in place | - | - | have (`/pause`, `/unpause`: the agent at its next step, commands frozen with SIGSTOP or Windows process suspend) |
| Persistent shell with state between commands | persistent bash tool | - | have (`shell_open`, `shell_run`) |
| Reused SSH session to a server | - | - | have (listed `ssh_hosts` only, key or agent login, shared connection on POSIX) |
| Task notes that survive compaction | todo / memory files | - | have (`notes_read`, `notes_write`, `/notes`) |
| Custom tools as files in a tools folder | - | - | have (`.nailong/tools/*.py`, hash-trusted for projects; compare [opencode-comparison.md](opencode-comparison.md)) |
| Skills as folders (SKILL.md, scripts, `/name`, allowed tools, manual-only, install) | yes | yes (`$name`, `$skill-installer`) | have (open layout; also reads `~/.claude/skills`, `~/.codex/skills`, `.claude/skills`) |
| Subagents as Markdown with a model each, parallel and background runs | yes | - | have (read-only ones run in the background) |
| Nested and local instruction files, rules folder, `@` imports | yes | partial | have |
| `mcp add/list/remove`, per-tool allow and deny | yes | `required` servers | have (no OAuth sign-in, by decision) |
| Scheduled tasks | routines, `/loop` | automations | have (`/schedule` + `--run-schedules`; the system scheduler fires it) |
| Browser control with your own logins (Manus Browser Operator) | Chrome extension | - | partial (`/mcp preset browser`: a separate Playwright browser, not your own profile) |
| Allow / ask / deny permission rules with patterns | yes | `.rules`, `execpolicy` | have (`[[rules]]`, last match wins; ask works in every mode) |
| Multi-file patch tool | - | `apply_patch` | have (`apply_patch`, all-or-nothing) |
| Formatter and linter run after edits | hooks | - | have (`format_on_edit`, `diagnostics_on_edit`, custom tables) |
| Command files with `$1`, `` !`cmd` ``, `model`, `agent` | yes | custom prompts | have |
| Run the agent as an HTTP service and attach to it | Agent SDK / remote control | `codex app-server`, `remote-control` | have (`--serve`, `--attach`; plan or accept-edits runs) |
| Rebindable keys and theme files | `keybindings.json`, themes | `/theme` | have (`[keybinds]`, theme JSON files) |
| Ask why the agent is doing something | - | - | have (`/why`: the step and the reasoning the model gave) |
| Type ahead / queue a message while it works | queued, sent after | Tab queues, Enter injects | have (Enter queues; `/broadcast` injects into the running agent) |
| Edit an earlier message and branch (Esc Esc) | rewind menu | edit previous and fork | missing (`/fork N`, `/rewind`) |
| Open the prompt in your editor | Ctrl+G | - | missing |
| Paste an image | Ctrl+V | yes | missing (`@image`, `--image`) |
| Full transcript / raw scrollback | Ctrl+O | Alt+R | partial (`/expand`) |
| Copy the last answer | `/copy` | Ctrl+O, `/copy` | missing |
| Greyed-out next-prompt suggestions | yes | - | partial (`/suggest` on request) |
| Side question that does not derail the task | `/btw` | `/side` | missing |
| Voice dictation | `/voice` | F8, `/voice` | missing |
| Vim editing mode | yes | `/vim` | have |
| Custom key bindings | `/keybindings` | `/keymap` | missing |
| Themes | `/theme` | `/theme` | partial (setting only) |
| Custom status line command | `statusLine` | `/statusline` | missing |
| Terminal title | - | `/title` | missing |
| Shortcut help with `?` | yes | - | missing (`/help`) |
| Background the running command with a key | Ctrl+B | `/ps`, `/stop` | partial (`/jobs`, `/kill`; `/ps` and `/stop` act on running agents) |
| Fast mode | `/fast` | `/fast` | missing |
| Set effort from a slash command | `/effort` | `/reasoning` | partial (flag and setting) |
| Persistent goal for a long task | `/goal` | `/goal` | missing |
| Session recap | `/recap` | `/recap` | missing |
| Usage and stats views | `/usage`, `/stats`, `/insights` | `/usage`, `/status` | partial (`/cost`, `/status`) |
| Import settings from another tool | `/import` | `/import` | missing |

## 5. The agent itself

| Feature | Claude Code | Codex | nailong |
|---|---|---|---|
| Plan mode, then carry out | yes | `/plan` | have |
| Streamed reasoning / thinking display | thinking toggle, summaries | reasoning summary setting | have |
| Auto-compaction | yes | `model_auto_compact_token_limit` | have |
| Rewind files | checkpoints | - | have |
| Rewind the *conversation*, or summarise from a point | rewind menu | - | missing |
| Subagents | Markdown files with frontmatter, per-agent model, tools, hooks, memory, worktree isolation, background | TOML files, per-agent model, thread limits | have (Markdown files or settings tables; a model per agent; read-only ones in parallel and in the background; no per-agent hooks, memory or worktree) |
| Built-in subagents | Explore, Plan, general-purpose | default, worker, explorer | partial (explorer and worker roles in `/plan`) |
| Parallel subagents | yes | yes | have (read-only subagents run side by side; team runs in `/plan` still go one task at a time) |
| Skills as folders with scripts and frontmatter | `SKILL.md`, `$ARGUMENTS`, injected commands, `allowed-tools`, `context: fork` | `SKILL.md`, `$name` | have (`SKILL.md` header, `$ARGUMENTS`, `$1`, bundled files, `allowed-tools`, manual-only; no `context: fork` or injected commands) |
| Custom prompts / commands | skills | `/prompts:<name>` | have |
| Plugins with a marketplace | yes | `codex plugin marketplace` | partial (local folders; marketplace by decision) |
| Hooks | 33 events, 5 handler types (command, http, mcp tool, prompt, agent), can allow, deny or rewrite | 14 events, trust by hash, `/hooks` | partial (6 events, command only, exit code only) |
| Hook that approves or denies a permission request | `PermissionRequest` | `PermissionRequest` | missing |
| MCP over stdio and HTTP | yes | yes | have |
| MCP management commands and scopes | `claude mcp add/list/remove`, three scopes | `codex mcp add/list/login` | have (`/mcp add/remove/preset`, settings file, `--mcp-config`; one user scope) |
| MCP sign-in (OAuth) | yes | yes | missing |
| Per-tool allow/deny lists and approval modes for MCP | permission rules | `enabled_tools`, `disabled_tools`, per-tool modes | have (`allow_tools`, `deny_tools`; no per-tool approval modes) |
| Run the agent as an MCP server | `claude mcp serve` | (app server) | have (`--mcp-serve`: file tools by permission mode; the SDK's harness server is separate) |
| Web search | WebSearch | cached or live search | have (a free scraper; weaker) |
| Fetch a web page | yes | - | have |
| Image input | yes | yes | have |
| Edit notebooks | yes | - | have (`notebook_edit`) |
| Agent asks *you* a question mid-run | yes | - | have (`ask_human_question`) |
| Recurring / scheduled tasks | `/loop`, `/schedule`, cron tools | automations (nav only) | have (`/schedule`, `--run-schedules`; fired by cron or Task Scheduler, not by a resident process) |
| Background sessions and a view of them | `--bg`, `claude agents`, `attach`, `logs`, `stop` | `codex agents`, cloud tasks | missing |
| Git worktree per session | `-w` | `--worktree` | have |
| Output styles | built-in and custom | - | missing |
| Models: aliases, plan with one model and execute with another | yes (`opusplan`) | `review_model` | missing |

## 6. Memory and instructions

| Feature | Claude Code | Codex | nailong |
|---|---|---|---|
| Project and user instruction files | `CLAUDE.md` | `AGENTS.md` | have (`NAILONG.md`, `AGENTS.md`, `CLAUDE.md`) |
| Files in subdirectories, loaded by location | yes | root-to-current directory chain | missing (project root and user file only) |
| Override and fallback file names | - | `AGENTS.override.md`, `project_doc_fallback_filenames` | missing |
| Imports with `@path` | yes (4 hops) | - | have (three levels, inside the project or settings folder) |
| Rules folder with path scoping | `.claude/rules/*.md` | `.rules` files (execution) | partial (`.nailong/rules/*.md` always loaded; no path scoping) |
| Private, untracked local file | `CLAUDE.local.md` | - | missing |
| The agent keeps its own notes between sessions | auto memory | `/memories` | partial (`/wiki` pages the agent writes and reads, `/kb` for documents; SDK semantic memory is still not used by the CLI) |
| Read PDF, Word, Excel files | yes (PDF, images) | - | have (`read_document`, `search_document`, `interpret_page`; PowerPoint read as slide text, tables and notes) |
| Bundled skills | bundled set | bundled set | have (8 built-ins, replaceable by name) |
| Size cap on instruction files | 200 lines of index | `project_doc_max_bytes` | have (20,000 characters each) |

## 7. Permissions and safety

| Feature | Claude Code | Codex | nailong |
|---|---|---|---|
| Permission modes | default, acceptEdits, plan, auto, dontAsk, bypass | approval policies plus sandbox modes | partial (no `dontAsk`) |
| Model-reviewed approvals | auto mode (classifier) | `auto_review` reviewer agent | missing (our `auto` mode is rule-based) |
| Allow, ask and deny rules with specifiers | yes | `.rules`, `execpolicy` | partial (allow and deny only; no "ask" list; no domain rules) |
| OS sandbox for commands | macOS, Linux, WSL2 | macOS, Linux, native Windows | partial (Linux, macOS, Docker; nothing native on Windows) |
| Network allow-list by domain inside the sandbox | yes | network off or on | missing (all or nothing) |
| Hide credentials from sandboxed commands | yes | - | missing |
| Workspace trust prompt | yes | `trust_level` | missing (project hooks and MCP are ignored instead) |
| Protected paths for the agent's file tools | yes | - | have |
| Tamper-evident audit trail | - | - | have (our strength) |

## 8. Observability and operations

| Feature | Claude Code | Codex | nailong |
|---|---|---|---|
| OpenTelemetry export | metrics, logs, traces by environment variable | `otel.*` | have (`otlp_endpoint`, `/telemetry`; spans, logs, metrics, audit entries and signed integrity records, sent as written; verified against an OpenTelemetry Collector 0.164.0) |
| Cost tracking | yes | credits and limits | have (needs your price table) |
| Session retention cleanup | `cleanupPeriodDays` | `history.persistence` | partial (`/cleanup` removes old runs' records; saved sessions are kept) |
| Turn-end notification | Notification hook | `notify` | have (terminal bell) |
| Commit attribution line | `attribution` setting | - | missing |

## 9. Automation and ecosystem

| Feature | Claude Code | Codex | nailong |
|---|---|---|---|
| GitHub Action | `claude-code-action`, `/install-github-app`, `@claude` | `codex-action`, `@codex review` | missing (`nailong -p` runs in CI) |
| Automatic PR review and auto-fix | `/autofix-pr` | PR reviews | missing |
| GitLab CI | yes | - | missing |
| Slack and Linear | - | in navigation | missing |
| Python and TypeScript SDKs | Agent SDK | TypeScript and Python | partial (Python SDK; MCP server for other hosts) |
| App server protocol for custom clients | - | `codex app-server` | partial (MCP HTTP service) |

## 10. Where we are ahead or different

- A tamper-evident, signed audit trail with Merkle checkpoints and a recorder (neither product
  documents one).
- An offline mode that refuses any setting that leaves your network, and prompted tools for models
  with no tool calling.
- Models can write new tools that are reviewed, tested, versioned and rolled back (`--forge`).
- A planner that splits a request and asks whether to run a team or one agent (`/plan`).
- Streamed reasoning that is kept for the next step but never stored.

## 11. Suggested order

Judged by how often the missing feature is felt in daily use, and how much of it is already in the
SDK.

1. **Expose what the SDK already has** (done 2026-11-05): OpenTelemetry export (now including the
   audit transcript and integrity records, sent as written), fallback models, the agent's
   question tool, notebook editing, retention cleanup, and `--mcp-serve`.
2. **Type ahead while the agent works** (done 2026-11-05: queue, `/why`, `/interrupt`, `/stop`,
   `/broadcast`, `/ps`). Left over: an editable earlier message.
3. **Hooks:** more events, decisions that allow, deny or rewrite, a `/hooks` view.
4. **Instruction files** (done 2026-11-05): parent and subdirectory files, `@` imports, a local
   untracked file, a rules folder. Left over: path scoping for rules.
5. **Subagents** (done 2026-11-05): Markdown definitions, a model per agent, parallel and
   background runs. Left over: per-agent hooks and worktree isolation.
6. **Skills as folders** (done 2026-11-05): the open `SKILL.md` layout, `/name` and `$name`.
7. **MCP** (done 2026-11-05): `mcp add/remove`, per-tool allow and deny, a browser preset. Left
   over: OAuth sign-in.
8. **Permissions:** an "ask" list, domain rules, `dontAsk`, a reviewer for auto mode.
9. **Conversation rewind** and background sessions.
10. **Output styles, a status-line command, key bindings.**
11. **A native Windows sandbox**, which matters because you develop on Windows.
