# Threat model for the nailong CLI

What the CLI protects, from whom, how, and which test shows it. Written 2026-11-06 from the code.
`apps/nailong_cli/tests/test_security_eval.py` and the tests named below are the evidence; a control
with no test is marked.

## Assets

1. Files outside the project, and secrets on the machine (keys, tokens, `.env`, cloud credentials).
2. The user's authority: what the agent may do without asking.
3. The project's authoritative knowledge (wiki pages, accepted discoveries) and its audit trail.
4. The user's money (model spend) and machine (CPU, disk, network).

## Who or what is untrusted

| Source | Why |
|---|---|
| Document and web content the agent reads | anyone may have written it, including instructions aimed at the agent |
| A cloned project's own files (`.nailong/config.toml`, rules, agents, skills, tools) | it came from someone else |
| The model's output | it can be wrong or steered by the above |
| Subagents | they run with the model's judgment, not the user's |
| Other processes on the machine writing the same state | lock and journal races |

Trusted: the user's own settings file and keys, the program's code, and (to the extent they set it
up) the user's hooks and MCP servers.

## Boundaries and controls

| Boundary | Control | Evidence |
|---|---|---|
| Project folder | every file tool resolves the path and refuses anything outside the project or added folders, including through symbolic links | `test_a_symlink_inside_the_project_...`, `test_paths_that_climb_out_...` |
| Secrets in the project | credential-style paths (`.env`, keys, `.aws/credentials`, `credentials.json`, `*.key`, ...) are refused by the file tools, the document reader, the wiki and discoveries | `test_credential_style_files_are_never_read_...` |
| Wiki names | page names are a closed pattern; no `..`, spaces or absolute paths | `test_wiki_page_names_cannot_walk_out_...` |
| Hostile files | Office packages are checked for size, part count and compression ratio before any parser; wrong-type and truncated files fail with an error; files over 80 MB are refused | `test_an_office_file_that_unpacks_...`, `test_a_high_ratio_zip_...`, `test_files_with_the_wrong_contents_...` |
| Network | `fetch_document` and `web_fetch` refuse private, loopback, link-local and metadata addresses and non-HTTP schemes, and re-check redirects; both ask for approval; web is off until turned on | `test_fetch_document_refuses_private_addresses_...` |
| Authority | approvals come only from the mode, rules and the user; text in a document, a skill or a tool result cannot grant anything. A skill's `allowed-tools` only narrows what the user's own `/skill` may do | `test_instructions_inside_a_document_...` |
| A cloned project | hooks, MCP servers, `ssh_hosts`, `hook_env`, `offline`, formatters, diagnostics, language servers, the OTLP endpoint and `allow` rules are ignored from project settings; custom tools in a project need a hash trust | `test_a_poisoned_project_...`, `test_settings_for_hooks_...` |
| Hooks | run with a short environment (no API keys unless listed in `hook_env`); `pre_tool` failure blocks unless the hook is marked `warn` | `test_hook_policy.py` |
| Subagents | cannot write authoritative memory or decide discoveries; background ones run only what their user-level `allow` list pre-approves | `test_a_subagent_cannot_write_...`, `test_agent_files.py` |
| Shared state | writes are locked across processes, atomic, revisioned; a dead writer's lock is released at once; edits to one file are claimed per task | `test_multiprocess.py`, `test_plan_durability.py` |
| Audit trail | each run's event log is hash-chained; an edited or removed line is found | `test_events.py`, `--health` |
| Spend | `max_budget_usd` stops the next turn; retries are bounded | `test_cost*`, `test_plan_durability.py` |

## Findings from the evaluation

Fixed while writing it:
1. `credentials.json`, `client_secret*.json`, `service-account*.json`, `*.key`, `*.keystore`, `*.jks` and
   `.vault-token` were not on the SDK's denied-path list; they are now.
2. Office files had no limit on unpacked size; a 300 MB zip bomb of a few hundred KB would have been
   handed to the parser. Now refused first.
3. A writer killed while holding a state lock blocked the next writer for 60 seconds; the lock now
   records its holder and is taken over as soon as the holder is gone. On Windows a waiter reading the
   lock could also make the holder's release fail; release now retries.

## Residual risks (not mitigated, or not tested)

- **Commands are not isolated by default.** Without Docker, bubblewrap or Seatbelt a command runs with
  the user's rights. The CLI says so in `/sandbox`. The container and OS sandboxes were not exercised
  here (no Docker daemon running; Linux isolation not run), so their strength is unevaluated.
- **Prompt injection is bounded by approvals, not prevented.** An injected instruction can still
  change what the model asks for. In `bypass` mode, or with broad allow rules, nothing asks. A model
  that is steered can also write misleading text into a wiki page or a discovery; discoveries need a
  person's approval, wiki pages written by the main agent do not.
- **Hooks and MCP servers you configure are trusted code.** `hook_env` is the only filter on what a
  hook sees.
- **PDF and image parsing** use third-party libraries (pypdf, pypdfium2, python-docx, openpyxl) on
  untrusted files. Size limits help; there is no process isolation or CPU time limit for parsing.
- **Event logs prove order and tampering after the fact, not truth.** A compromised process can write
  false events with a valid chain. An independent recorder with signed checkpoints (the SDK's
  `RecorderMirror`) is the answer, and the CLI does not wire one itself.
- **Local state files are readable by anyone with the user's rights**, including the knowledge index
  and event payloads (clipped, but they hold tool names and topics).
- **Live providers were not tested.** Whether a real model respects the tool, approval and skill
  contracts under adversarial input is unmeasured; `tests/live` is the place to start.
