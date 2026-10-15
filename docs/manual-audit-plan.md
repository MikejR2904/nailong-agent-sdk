# Manual audit plan for `nailong_agent_sdk`

Status: `foundations/` is audited. Next: **Stage 1 - `observability/`**.

## How the order was chosen

Reading goes bottom-up so that every file you open only uses code you have already read.
The order comes from the real import graph (runtime imports, 147 files, no circular imports at
file level; the only circles are between folders and they are explained just below).

```
foundations  ->  observability  ->  memory  ->  specifications
             ->  tools (safety core)  ->  state  ->  tools (execution)
             ->  agent  ->  integrations  ->  mcp  ->  developer_tools / root / packaging
```

Why `tools` is split in two: `state/harness_coordinator.py` needs `tools/approvals.py`, while
`tools/registry.py`, `tools/elastic_requests.py` and `tools/core/definitions.py` need
`state/planning.py`, `state/elastic.py` and `state/graph_models.py`. Reading the approval and
policy files first, then `state`, then the rest of `tools`, avoids forward references.

Why `agent` comes late: it uses every other folder. `agent/orchestrator/` imports
`integrations/jev/architecture.py` and `integrations/*` import `agent/*`, so `integrations`
(small, optional-dependency bridges) is read right after `agent`.

## Size and effort (rough)

Estimate: about 300 lines/hour of careful reading plus 25% for notes and probes.

| Stage | Folder | Files | Lines | Tests (functions) | Hours |
|---|---|---:|---:|---:|---:|
| 0 | `foundations/` | 19 | 2,352 | 97 | done (18 files) |
| 1 | `observability/` + `foundations/run_fence.py` | 12 | 4,446 | 168 | 18.5 |
| 2 | `memory/` | 13 | 5,491 | 119 | 23 |
| 3 | `specifications/` | 7 | 1,591 | 40 | 6.5 |
| 4 | `tools/` safety core | 3 | 426 | (in 170) | 2 |
| 5 | `state/` | 15 | 6,262 | 245 | 26.1 |
| 6 | `tools/` execution | 17 | 3,757 | 180 | 15.7 |
| 7 | `agent/` | 24 | 9,770 | 380 | 40.7 |
| 8 | `integrations/` | 13 | 2,040 | 26 | 8.5 |
| 9 | `mcp/` | 18 | 3,593 | 176 | 15.0 |
| 10 | `developer_tools/`, root, packaging, CI, docs | 7 + config | 1,966 | 29 | 8.2 |
| 11 | cross-cutting passes | - | - | - | 9 |

About 175 hours in total, roughly seven weeks at five focused hours a day.

## Routine for every file

1. **Before reading:** open the file's section in `docs/reference/<folder>.md` (heading
   ``### `path/to/file.py` ``). Read "Role in the workflow" and "Algorithms & invariants". That is
   the claim you are checking. The "Called by" lists there are heuristic (about 200 differ from
   a fresh scan), so use search for real call sites.
2. **While reading, for each public function note:**
   - inputs: who controls them (model output, tool output, a file, an MCP caller, the host)?
   - invariants it relies on and the one it establishes;
   - side effects: files, SQLite, processes, network, threads;
   - failure modes: does the message name the exact field, status or exception?
   - concurrency: which lock or ordering makes it safe, and what happens on a crash between two writes?
3. **After reading:** open the matching tests, list behaviours with no test, and run the
   folder's tests: `.venv/Scripts/python.exe -m pytest tests/<folder> -q`.
4. **Record a finding** as: severity, `file:line`, expected (from the docs or common sense),
   actual, suggested fix. Triage them afterwards in one batch.
5. **Keep the checkout green when you annotate in place:** run
   `.venv/Scripts/ruff.exe format src` then `.venv/Scripts/ruff.exe check src` after each file
   (CI and `test_cli_quality_agrees_with_ruff_on_this_checkout` fail otherwise; long comment lines
   and a single space before an inline `#` were the usual causes). Or keep notes in a separate
   file and leave the source untouched.

A file is "done" when you can answer: what does it guarantee, what does it trust, what happens
if it is interrupted half-way, and which test would fail if the guarantee broke.

## Stage 0 - `foundations/` (done)

- [x] `foundations/` (18 files; `run_fence.py`, added afterwards, is read in Stage 1 with the
  stores that honour it)

## Stage 1 - `observability/` (the evidence layer; depends only on foundations)

Run: `.venv/Scripts/python.exe -m pytest tests/observability tests/developer_tools -q`

- [ ] `trace_context.py` (181) - W3C trace/span ids, `traceparent`, derived graph-run ids, the process resource;
  read first, `telemetry_models.py` and `profiler.py` import it
- [ ] `telemetry_models.py` (239) - event, context, actor, metric contracts, maintenance reports
- [ ] `telemetry_helpers.py` (99) - `first_chain_break`, the one chain verifier
- [ ] `foundations/run_fence.py` (139) - the per-run write fence retention raises; read before
  the two stores that honour it
- [ ] `audit_log.py` (463)
- [ ] `telemetry_store.py` (768) - includes `maintenance_report`, `vacuum` and the `event_types` read filter
- [ ] `run_checkpoints.py` (864) - signed run-head checkpoints kept outside the run root: the signer and the sink seams, the verdicts (including `pruned`, decided from retention's tombstone through a lookup the caller passes) and the terminal cross-link; read after both stores
- [ ] `otlp.py` (829) - read-side OTLP/HTTP JSON export; read after the store, it only reads the ledger
- [ ] `metric_definitions.py` (400) - mostly a declarative table; skim, check ids and formulas once
- [ ] `metrics.py` (131)
- [ ] `profiler.py` (330)

Verify:
- Which fields enter each `integrity_hash`; blanked-then-hashed; `previous_*` link; chains are per
  run id; what `first_chain_break` can and cannot see (tail truncation, whole-run deletion).
- Redaction happens at the persistence boundary for payload and links; identifiers rely on
  validators instead (`assert_well_formed_text`, `Field(min_length=1)`).
- Append atomicity. Audit: thread lock, per-path lock, cross-process file lock, `fsync`, tail
  read, ownership guard. Telemetry: `BEGIN IMMEDIATE`, WAL, retry while locked.
- Read-only mode creates nothing; `prune_run` / `footprint` guards (`expected_last_sequence`).
- Run report: streaming, verified prefix, `evidence_head_hash`; metrics never synthesise a value.
- Fence and maintenance: `append` and `record_metric` wait for the run's fence and re-check it
  inside their transaction; `quiesce` takes the lock once; `vacuum` refuses while any run is
  fenced or without free space for the rewrite, checkpoints the WAL and reports
  `integrity_check`; `maintenance_report` writes nothing.
- Profiler `integrity_hash` is a single digest, not a chain.
- Run-head checkpoints: both chains are verified before their heads are signed, each with one chain check and one pass; the telemetry head comes from the event JSON, not the denormalized column; a sink inside the run root is refused; an existing record is verified first so a run cannot be re-checkpointed into agreement; `truncated` (fewer than recorded), `diverged` (a different hash at the recorded position, which a resealed chain still gives), `advanced` (grew) and `pruned` (the run is gone and retention's tombstone holds the checkpoint's heads, or is longer and says so) are different findings. A tombstone older than the checkpoint cannot account for it, and a run id used again after a prune is a new incarnation whose counts are not compared with the old one's. `BaseAgent._terminate` writes `audit_head` into `agent.profile-completed` and a separate `terminal-link` audit entry carrying `telemetry_head`, so a small audit payload bound cannot cut the link; an older run has the head inside `profile-completed` or neither and reports the link as `absent`, not `broken`.
- `event_types` filters on `json_extract(event_json, '$.event_type')`, so a forged column cannot hide an event; at most 64 names; a bare string and an empty list are refused.
- OTLP export: nothing runs on the write path; ids are hex strings (not base64), 64-bit values are
  decimal strings and enums are integers; retries follow 429/502/503/504 only; a redirect is
  refused; credentials in headers are scrubbed from errors; a graph run is exported as one
  `invoke_workflow` span plus the nodes' own spans; a span whose parent is not in the export
  keeps its `parentSpanId` and is marked `nailong.parent_span_exported=false`.
- Trace context: ids are validated when a `TelemetryContext` is built or parsed and skipped
  for stored rows (`TelemetryEvent.parse_stored`); the `agent.profile-completed` payload
  carries the trace id, the resource and at most 512 spans; `traceparent` is parsed
  strictly and an unparseable header from outside is ignored, never fatal.

## Stage 2 - `memory/`

Run: `.venv/Scripts/python.exe -m pytest tests/memory tests/agent/test_context_guards.py tests/agent/test_semantic_context.py -q`

- [ ] `episode_models.py` (630) - records and policies first, then the semantic contracts
  (summary, draft, model-facing view, lifecycle and references, verification record,
  summarizer / claim judge / summary judge / archive protocols, policy)
- [ ] `episode_archive.py` (290) - content-addressed snapshots of episodes and of summaries;
  read before the compactor
- [ ] `entailment_screen.py` (117) - the lexical screen; read before the compactor that calls it
- [ ] `summary_ledger.py` (192) - where each summary lives (resident, superseded, archived)
- [ ] `summarization_scheduler.py` (190) - the bounded background queue and its accounting
- [ ] `memory_snapshot.py` (251) - the generation snapshot and `snapshot_problems`
- [ ] `episode_scoring.py` (77)
- [ ] `episodes.py` (97) - includes `retained_limit`
- [ ] `semantic_compaction.py` (1,133) - grouping, `ExtractiveEpisodeSummarizer`,
  `SemanticCompactor`, its deterministic checks and its optional stages (fallback summarizer,
  screen, claim judge, summary judge); read after the archive and before the store that
  commits what it returns
- [ ] `episode_store.py` (1,872) - four sittings: in-memory store and compaction strategies; the
  synchronous semantic pass (commit, consolidation, eviction, `rehydrate`, `CompactionGuard`);
  the background pass, summary archiving and ceilings; snapshots and the file store
- [ ] `context.py` (51)
- [ ] `context_projection.py` (588) - projector (summary selection and stub dedupe), journal
  (handles, run index, `.high-water`, the run fence and lock)

Verify: budgets are character estimates (chars/4), compaction is deterministic and what it drops
(stubs keep handle ids), checkpoint hash and file format, journal handle claiming by exclusive
create, never reusing a handle number, previews and truncation flags, what the model sees versus
what only the journal holds.

Semantic compaction (opt-in; a store without a `SemanticCompactor` must behave exactly as before):

- Order of effects per group: archive every original, read each entry back and compare it with
  the snapshot, call the summarizer, run the deterministic checks, run the judge, then commit
  under the commit lock. No payload is released on any other path except the explicit
  `release-after-archive` policy, and then only for originals that are archived.
- Which check would catch each failure: unsupported claim (`claims-grounded`), missing
  evidence (`sources-archived-and-intact`, evidence length), contradictory observations kept
  apart (`_conflict_issues`), dropped failure (`mandatory-outcomes-covered`), invented number
  or identifier (`identifiers-supported`), size (`compression`), citations outside the group.
- A crash or exception at any point leaves the record with its payload or fully linked to its
  summary and archive entry, never half of each; `CompactionGuard.abandon` stops commits after a
  cancelled or timed-out run.
- Archive ids are the SHA-256 of the snapshot; `get` re-hashes; `FileEpisodeArchive` fsyncs,
  renames and reads back before `put` returns. A restored episode is dropped again only while
  its archive entry reads back and verifies (`_archive_intact`).
- Groups come from `depends_on` edges only; `unrelated_batch_size` batches without asserting a
  relationship. Groups below `compression_floor_tokens` are skipped, not rejected.
- Consolidation keeps children for provenance, never exceeds `max_level` and carries every child
  unresolved issue; eviction never removes a record a live episode depends on.
- What the model receives: `ModelContext.semantic_summaries` (views without evidence excerpts,
  at most 16 source ids and handles each beside the counts), stubs of covered episodes left
  out, `omitted_summary_count`; the chat adapter adds the keys only when there are summaries.
  Summaries with unresolved issues rank ahead of equally relevant ones.

Summary lifecycle, persistence and background work (all opt-in):

- A summary is `active`, `superseded` or `archived`; the ledger keeps a reference for each; an
  archived summary is read back and compared with the reference's integrity hash; a summary
  that covers a live episode is pinned and never archived by a ceiling.
- A snapshot is one file with its own hash; `restore` adopts the newest generation that
  verifies and names every generation it rejected; archives are written before the snapshot
  that refers to them, so a crash leaves the previous generation whole.
- Verification is recorded per stage with its verifier and policy hash; a judge runs only
  after the deterministic checks passed and can only veto; a deterministic pass is never
  reported as entailment.
- Background jobs capture snapshots of their records; a commit re-checks that the sources are
  still compacted, unchanged and unsummarized; a job past its timeout is abandoned and
  counted; `close_background` drops what is queued and ignores what returns later.

## Stage 3 - `specifications/`

Run: `.venv/Scripts/python.exe -m pytest tests/specifications -q`

- [ ] `documents.py` (126)
- [ ] `vision.py` (43)
- [ ] `retrieval_models.py` (121)
- [ ] `evidence_graph.py` (358)
- [ ] `preprocessing.py` (392)
- [ ] `retrieval.py` (548) - caches, optional Qdrant / Redis backends

Verify: size, nesting and decompression bounds on every parser; deterministic ids and hashes;
provenance fields survive every transform; cache failure is tolerated, not fatal; optional
backends fail with an install hint; image bytes are loaded only through the verified loader.

## Stage 4 - `tools/` safety core

Run: `.venv/Scripts/python.exe -m pytest tests/tools/test_core_tools_policy.py tests/tools/test_core_tools_files.py tests/tools/test_core_tools_drafts.py tests/tools/test_core_tools_commands.py -q`

- [ ] `tools/tools.py` (73) - executor protocol, in-memory executor
- [ ] `tools/approvals.py` (188)
- [ ] `tools/policy.py` (176)

Verify: policy order is sensitive path, role grant, path scope, side effect, approval; default is
deny; a rejected or pending approval has its own reason; approvals persist under a lock.

## Stage 5 - `state/`

Run: `.venv/Scripts/python.exe -m pytest tests/state -q` (a few minutes; the scaling and
multi-process tests are the slow ones)

5a. Data and rules
- [ ] `shared_state.py` (240)
- [ ] `elastic.py` (353)
- [ ] `graph_models.py` (228)
- [ ] `orchestration_models.py` (110)
- [ ] `coordination_records.py` (42)
- [ ] `planning.py` (496) - plan validation, proofs, cycle detection
- [ ] `project_state_models.py` (363) - `state_hash`, event hash chain
- [ ] `project_state_engine.py` (533) - reducer authority rules, projector budgets

5b. Stores
- [ ] `project_state_store.py` (252)
- [ ] `run_state_store.py` (435) - snapshot + history sidecar, prefix replay, fingerprint conflicts
- [ ] `orchestration.py` (453) - controller state machine and its store

5c. Engines
- [ ] `graph.py` (1,471) - three sittings (see "Splitting the big files")
- [ ] `harness_coordinator.py` (372)
- [ ] `controller_runtime.py` (911) - note the write order across stores and `reconcile`

Verify: every validator names the field; who may set which authority in the reducer (the tables `TRANSITION_AUTHORITY` and `QUESTION_RESOLUTION_AUTHORITY`; `tool-outcome` and `agent-result` only the harness, `question-opened` every actor); a `DecisionSource` reaches the controller event and the telemetry event's authority class; record
locks held across read-modify-write and never across an await; a failed save leaves no cached
state ahead of disk; elastic accounting (joins counted, ceilings, reservations); crash recovery
(`recover_interrupted`) only replays what is declared safe.

## Stage 6 - `tools/` execution

Run: `.venv/Scripts/python.exe -m pytest tests/tools -q` (about 2 minutes, Docker tests skip)

- [ ] `artifacts.py` (265)
- [ ] `core/helpers.py` (581) - HTTP fetch, SSRF checks, PDF, regex helpers
- [ ] `core/services.py` (488) - path containment, read scope, sensitive paths, caches
- [ ] `core/definitions.py` (356)
- [ ] `core/regex_worker.py` (38)
- [ ] `supervisor.py` (491)
- [ ] `sandbox_models.py` (59), `sandbox.py` (322)
- [ ] `task_models.py` (43), `tasks.py` (258)
- [ ] `worktree_models.py` (20), `worktrees.py` (187)
- [ ] `delegation.py` (128)
- [ ] `registry.py` (333) - the dispatch chain and the approval flow
- [ ] `elastic_requests.py` (160)

Verify: no path escapes the run root (symlinks, extended-length Windows spellings, `.agent-*`);
`web_fetch` pins validated public addresses and refuses redirects to private ones; the regex
worker is isolated and time-boxed; process execution uses a minimal environment, process
groups, timeouts and output caps; secrets never reach logs or telemetry.

## Stage 7 - `agent/`

Run: `.venv/Scripts/python.exe -m pytest tests/agent -q` (a few minutes)

- [ ] `model.py` (418) - model contract, streaming events, failover, cancellation outcome
- [ ] `verification.py` (474) - decisions may declare a verifier id, an authority class and bounded evidence; `AllOfVerificationGate` and `register_all_of`; declared metadata is checked when a gate is registered or a composite is built
- [ ] `openai_compatible/transport.py` (537) - redirects refused, secret scrubbing, error
  classes, the blocking and the async transport
- [ ] `openai_compatible/chat.py` (746) - includes the provider-cancel hook and the
  cancellation outcome
- [ ] `openai_compatible/embeddings.py` (68), `vision.py` (163)
- [ ] `base_agent/types.py` (99)
- [ ] `base_agent/agent.py` (2,456) - five sittings (see below)
- [ ] `runtime.py` (194) - the services; the opt-in checkpointer, `checkpoint_after_run` (worker thread; a failure is recorded, never raised) and the audit payload bound
- [ ] `run_checkpointer.py` (71) - the signer and sink a runtime uses; `require_outside`, `export` and `check` bind the tombstone lookup of the run root
- [ ] `elastic_context.py` (105)
- [ ] `graph_agent_executor.py` (298)
- [ ] `orchestrator/models.py` (180), `state_store.py` (119)
- [ ] `orchestrator/orchestrator.py` (1,004)
- [ ] `task_runner.py` (694) - options validation, approval wait, workspace, default transport
- [ ] `retention.py` (666) - tombstones, the fence and prune order, crash resume, anchor wrappers, `pruned_heads_lookup` (the tombstone heads the checkpoint verdicts read)
- [ ] `retention_anchor.py` (179) - anchors of the tombstone head; read after `retention.py`
- [ ] `retention_inventory.py` (980) - read-only inventory of every resource class
- [ ] `task_files.py` (213)

Verify: model output is untrusted at every step; the loop guards (deadline, cancellation,
context deadlock) fire before a model call; tool calls only run when declared; project state is
updated before the run is reported terminal; cancellation interrupts the awaited operation and
the terminate path is the only exit.

Verification: a gate that declares nothing is recorded as before; the declared class is the host's claim, not something the SDK verifies and is refused when it is not a `TelemetryAuthority`, at registration and at composite construction rather than after members ran; evidence is JSON, at most 4,096 characters, no NaN or infinity, and a composite keeps each member's own evidence while the whole fits; a member that raises is named by index and verifier; `verification-completed` carries the gate id and the digest of the output on every path, including the failures.

Pruning: a run is fenced before it is examined; every writer re-checks the fence inside its
own critical section; an interrupted prune keeps the fence until an applied pass finishes
it; the anchor is checked before a pass and refreshed after it. Cancellation:
`ModelCancellationOutcome` never claims more than its three enumerated levels and reports
`confirmed` only from a host hook.

## Stage 8 - `integrations/`

Run: `.venv/Scripts/python.exe -m pytest tests/integrations -q`

- [ ] `_utils.py` (152), `contracts.py` (98), `receipts.py` (65)
- [ ] `jev/models.py` (171), `jev/receipts.py` (57)
- [ ] `jev/decision.py` (297), `jev/advisory.py` (143)
- [ ] `jev/architecture.py` (164), `jev/exploration.py` (149)
- [ ] `langchain.py` (266), `langgraph.py` (308)

Verify: sanitiser covers keys and string values; envelope validation; the router refuses
`escalate`; a missing optional dependency raises an install hint, not an `ImportError` traceback;
result digests use `canonical_hash`.

## Stage 9 - `mcp/`

Run: `.venv/Scripts/python.exe -m pytest tests/mcp -q` (spawns real servers; a few minutes)

- [ ] `authority.py` (445) - principals, operation classes, digests and the decision gate;
  read before the files that use it
- [ ] `security.py` (443) - tokens, hosts, origins, TLS, refused-request body handling, the audit payload bound and the checkpoint signer and directory (the key is read from a file and is never printed)
- [ ] `arguments.py` (116) - the argument guard and the Python-only capability table
- [ ] `client_types.py` (90), `client.py` (550), `client_bridge.py` (94)
- [ ] `_shared.py` (156) - tool offloading, `state_lock`, operation classes
- [ ] `server.py` (205)
- [ ] `tool_groups.py` (27), `authority_matrix.py` (118) - the groups registered once; the reducer tables, the credential policies and the tool classes in one export
- [ ] Tool groups: `run_tools.py` (107), `specification_tools.py` (40), `orchestration_tools.py` (156),
  `project_state_tools.py` (274), `controller_tools.py` (340), `agent_tools.py` (217),
  `telemetry_tools.py` (212)

Verify: constant-time token comparison, nothing runs before authentication; each tool is a thin
wrapper that returns the `{ok, ...}` envelope; no tool lets a caller widen authority; the client
owns each connection in one task and closes it there; external tool output is marked untrusted.
The credential, never the caller, decides the principal and with it the operation class; a
decision carries the digest it reviewed and its issue time, and its `authorized` event is
written before the operation; an argument a tool does not define is refused by name.
A decision tool passes `receipt.source()` to the operation, so the approval request, the controller and elastic events, the orchestration event and the project-state action name the principal; `authority_matrix()` is read from the code that enforces each rule.

## Stage 10 - developer tools, root, packaging

- [ ] `developer_tools/` (1,035): `inspect.py`, `cli.py` (now also `authority-matrix`, `checkpoint-run` and `verify-checkpoints`, both reading retention's tombstones), `catalog.py`, `quality.py`, `validate.py`
- [ ] `__init__.py` (931): `_EXPORTS` matches the `TYPE_CHECKING` block
- [ ] `pyproject.toml`, `.github/workflows/ci.yml`
- [ ] `docs/reference/README.md`: each behavioural claim is true; "Still open" is still open

## Splitting the big files

`agent/base_agent/agent.py` (2,410 lines; the ranges below were taken at 2,193 lines, before the
cancellation outcome work, so find the boundaries with `grep -n "    def "`; the order of the
sittings is unchanged):
1. `__init__` (118-175), `run`, `_clear_active_run` and the start of `_run` (200-313): setup,
   the `emit` closure (telemetry plus audit), run-started and context-assembled events.
2. `_run` lines 315-563: per-iteration guards, context projection, budget guards.
3. `_run` lines 564-805: model call (watchdog, retries, cancellation), `_normalize_model_response`
   (913-939), dispatch of blocked / final / tool turns.
4. `_run` lines 806-911 and `_accept_final_turn` (941-1131), `_execute_tool_batch` (1133-1302),
   `_execute_profiled_tool_call`, `_execute_tool_call` (1343-1534).
5. Helpers and ending: consumed-episode checks, `_record_*`, provider results, watchdog,
   retry delay, stream listener and provider usage (1537-1843), `_terminate` (1845-2012),
   project-state recording (2014-2124), module-level helpers (2127-2193).

`state/graph.py` (1,463 lines):
1. Lines 81-317: construction, snapshot / `from_snapshot`, runnable waves, `reopen_blocked`.
2. Lines 319-586: execution context, `recover_interrupted`, `mark_*`, `execute`, shared values,
   lateral dependencies.
3. Lines 587-1023: elastic (`grant` / `decline`, `_apply_spawn_requests`, joins, held batches).
4. Lines 1025-1463: routing index, discovery routing, validation, blocking, status changes.

`state/controller_runtime.py` (873): lifecycle (108-216), results and shared state (219-340),
execution and recovery (342-551), question / blocker / artifact status (567-678), private
helpers and `_emit` (680-873).

`agent/orchestrator/orchestrator.py` (989): prepare / submit / approve (152-313), dispatch and
recovery (315-534), bindings and elastic bindings (544-807), module helpers (822-989).

## Stage 11 - cross-cutting passes (grep-driven, after the folders)

1. **Trust boundaries:** list every place external text enters (model output, tool output, web
   content, MCP servers, files, environment). For each, find where it is bounded, redacted and
   typed before it is stored or shown to a model.
2. **Write paths:** every `write_text`, `open(..., "w")`, `os.replace`, `sqlite3.connect`. Is it
   atomic, locked and flushed, and is a half-written file detected on load?
3. **Integrity chains:** audit, telemetry, tombstones, project state, run history, controller
   events, tool-result hashes. Confirm each is verified on load or on request.
4. **Errors:** every `raise` and `AgentSdkError(...)`: code is stable, message names the field,
   status or exception, and no secret is interpolated.
5. **Concurrency:** `asyncio.to_thread`, `run_detached`, `threading`, locks, `await` while holding
   a lock (there should be none).
6. **Dead code and duplication:** public functions with no references, near-copies of helpers.
7. **Public surface:** `__all__` / `_EXPORTS`, the MCP tool list, the docs claims.

## Notes you may want to keep

- `docs/reference/<folder>.md` mirrors `src/` one section per file; it is checked by
  `tests/test_reference_docs.py`, so a function you find that is missing from it is a doc bug.
- When you doubt a behaviour, a short probe script against the real classes is usually faster
  than reasoning; ask and a probe plus a regression test can be written.
