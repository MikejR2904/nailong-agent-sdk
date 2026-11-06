# Where things live

Start from the symptom, open the file named, and you are in the right place. Each file has one job.

## Conventions

- One responsibility per module. A package folder (`recorder/`, `evidence_profile/`) groups files that share one subject.
- An error code's prefix names the module that raises it (`RECORDER_*` comes from `observability/recorder/`). `tests/test_error_code_ownership.py` fails when a module raises another module's prefix.
- Each verdict has one producer. The check that decides it is the only place to change it.
- Every definition is described in `docs/reference/`; `tests/test_reference_docs.py` keeps that in step with the code.

## Using the SDK without assembling it

`nailong_agent_sdk.starter` is the short path: `Agent`, `@tool`, `core_tools`, `openai_chat`. It only composes the parts below, so a problem in a run is still found in the layer that owns it.

| You want to | Look in |
| --- | --- |
| Change how a function becomes a tool, or its schema | `starter/tools.py` |
| Change who may call what, or when approval is asked | `starter/toolbox.py` |
| Offer different built-in file tools | `starter/core_tools.py` |
| Change how a model is named or reached | `starter/models.py` |
| Change what every provider is sent (system text, task context, tools) | `agent/provider_context.py` |
| Change how Claude requests or responses are handled | `agent/anthropic_messages/messages.py` (headers and URL in `endpoint.py`) |
| Change how Chat Completions providers are handled | `agent/openai_compatible/chat.py` |
| Retry rules, status hints, redirects | `agent/openai_compatible/transport.py` |
| Change what a run returns, or how the definition and task are built | `starter/agent.py` |
| Change hook, check or skill behaviour | `starter/hooks.py`, `starter/checks.py`, `starter/skills.py` |
| Change MCP tool naming, approval or connection | `starter/mcp_tools.py` (the client itself is `mcp/client.py`) |
| Change what the coding tools may write or run | `starter/dev_tools.py` (the process machinery is `tools/supervisor.py`, `tools/sandbox.py`) |
| Change what a sandboxed command can reach | `tools/os_sandbox.py` (bubblewrap, Seatbelt), `tools/sandbox.py` (Docker, native) |
| Decide where an interactive CLI belongs and how it should behave | `docs/interactive-cli.md` |
| Learn to use the CLI (install, modes, sessions, hooks, scripts) | `docs/cli-guide.md` |
| See what Claude Code and Codex have that we do not, and the suggested order to close it | `docs/feature-gap-analysis.md` (the shorter list of decisions is `docs/cli-parity.md`) |
| Change how a request is split into tasks, or the team-or-one-agent suggestion | `starter/planner.py` (`Planner`, `compile_draft`, `choose_architecture`); the CLI side is `apps/nailong_cli/nailong_cli/plan_command.py` |
| Build a team of agents (supervisor, fan-out, governed plan with approval and recovery) | `docs/multi-agent-tutorial.md`, `examples/multi_agent_starter.py`, `examples/multi_agent_pipeline.py` |
| Use a model you host yourself, or keep a run inside your network | `docs/local-models.md`, `apps/nailong_cli/nailong_cli/network.py` (offline check), `starter/models.py` (`openai_chat`, shortcuts) |
| Change resume, fork or the history the model gets | `starter/sessions.py` (storage and chain in `starter/session_store.py`, `starter/chain_log.py`) |
| Change how model-written tools are proposed, approved, tested or rolled back | `starter/forge.py` (execution in `starter/forge_execution.py`, the reviewer note in `starter/forge_report.py`, the subprocess script `starter/forge_runner.py`) |
| Change how a Claude stream is read | `agent/anthropic_messages/stream.py` |

## A bug: where does it come from?

| Symptom | Look in |
| --- | --- |
| An audit or telemetry chain does not verify | `observability/audit_log.py`, `observability/telemetry_store.py` |
| Hashes differ between writer and verifier | `foundations/jcs.py` (canonical form), then the ledger's schema tag |
| A signature is rejected, or a key is "unknown" or "revoked" | `observability/keyring.py`, `observability/signed_records.py` |
| A signer or HMAC/Ed25519 mismatch | `observability/signers.py`, `observability/ed25519.py` |
| A tree head or inclusion proof fails | `observability/merkle.py`, `observability/transparency.py` |
| A run-head checkpoint is missing or does not match | `observability/run_checkpoints.py`, `agent/run_checkpointer.py` |
| Checkpoints are not written where expected | `observability/sinks.py` |
| The last telemetry event and last audit entry disagree | `observability/terminal_link.py` |
| A timestamp is rejected | `observability/timestamp.py` |
| Event times run backwards | `observability/clock_check.py` |
| A payload was cut short | `observability/payload_bounds.py` |
| A witness refuses or does not answer | `observability/witness.py` |
| The recorder refuses a request or an append | `observability/recorder/protocol.py` (wire format), `core.py` (what it accepts) |
| The recorder cannot be reached, TLS fails, a read-back is cut | `recorder/client.py`, `recorder/tls.py`, `recorder/server.py` |
| The recorder and a host ledger disagree | `recorder/compare.py`, `recorder/mirror.py` |
| One slow recorder stalls other runs | `recorder/client.py` (connection pool), `recorder/core.py` (per-run gates) |
| A profile fails to load or build | `agent/evidence_profile/settings.py`, `build.py` |
| A profile verdict is wrong | `agent/evidence_profile/checks.py`, one function per check |
| The profile object behaves oddly | `agent/evidence_profile/profile.py` |

## A feature: where does it go?

| You want to | Add it to |
| --- | --- |
| Verify one more thing about a finished run | a function in `agent/evidence_profile/checks.py`, then add it to `CHECKS` |
| Add a setting to the evidence profile | `agent/evidence_profile/settings.py`, then read it in `build.py` |
| Keep checkpoints somewhere new | a class beside `FileCheckpointSink` in `observability/sinks.py` |
| Sign with a new algorithm | `observability/signers.py`; resolve its keys in `keyring.py` |
| Add a recorder command | handler in `recorder/server.py`, logic in `recorder/core.py`, call in `recorder/client.py`, wire names in `recorder/protocol.py` |
| Add a new kind of ledger record | the ledger's module, then `observability/signed_records.py` so it is checked the same way |

When you add a file, add its entry under `docs/reference/` and run the documentation tests.
