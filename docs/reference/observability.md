# `observability/` - tamper-evident records of what a run did

Three independent records of a run, all written outside the model's context: the **telemetry ledger** (SQLite, one hash chain per run, plus metric observations), the **audit transcript** (append-only JSONL per run, hash chained, fsynced per entry, with a readable markdown rendering) and the **profiler** (timed spans for harness phases). `BaseAgent` writes all three; the MCP tools and `developer_tools/inspect.py` read and verify them. Every durable payload is passed through the secret redactor and rejects hidden-reasoning keys before it is hashed.

| File | Lines | Role |
|---|---:|---|
| [`observability/__init__.py`](#observability__init__py---package-marker-for-the-observability-layer) | 3 | package marker for the observability layer |
| [`observability/audit_log.py`](#observabilityaudit_logpy---append-only-hash-chained-per-run-jsonl-audit-transcript) | 388 | append-only, hash-chained, per-run JSONL audit transcript |
| [`observability/metric_definitions.py`](#observabilitymetric_definitionspy---the-sdks-standard-metric-catalogue) | 481 | the SDK's standard metric catalogue |
| [`observability/metrics.py`](#observabilitymetricspy---record-metric-values-against-the-standard-catalogue) | 131 | record metric values against the standard catalogue |
| [`observability/profiler.py`](#observabilityprofilerpy---privacy-conscious-timing-profile-for-one-agent-run) | 318 | privacy-conscious timing profile for one agent run |
| [`observability/telemetry_helpers.py`](#observabilitytelemetry_helperspy---tiny-constructors-for-timestamps-and-metric-observations-and-the-shared-hash-chain-checker) | 99 | tiny constructors for timestamps and metric observations, and the shared hash-chain checker |
| [`observability/telemetry_models.py`](#observabilitytelemetry_modelspy---telemetry-event-context-actor-and-metric-contracts) | 129 | telemetry event, context, actor and metric contracts |
| [`observability/telemetry_store.py`](#observabilitytelemetry_storepy---durable-telemetry-ledger-sqlite-events-with-a-per-run-hash-chain-plus-metrics) | 495 | durable telemetry ledger: SQLite events with a per-run hash chain, plus metrics |

---

### `observability/__init__.py` - package marker for the observability layer

*3 lines · depends on: nothing in the package · used by: no other module (entry point or re-exported only) · not re-exported at the package root*

**Role in the workflow.** Docstring only; callers import the deep modules.

---

### `observability/audit_log.py` - append-only, hash-chained, per-run JSONL audit transcript

*388 lines · depends on: `foundations/atomic_io.py`, `foundations/contracts.py`, `foundations/errors.py`, `foundations/identifiers.py`, `foundations/text.py`, `observability/telemetry_helpers.py`, `observability/telemetry_models.py` · used by: `agent/base_agent/agent.py`, `agent/runtime.py`, `developer_tools/inspect.py`, `mcp/_shared.py`, `mcp/server.py` · re-exported at the package root: 2 name(s)*

**Role in the workflow.** `BaseAgent` appends an entry for every lifecycle event, the received task, each model turn, each tool result and the final profile. Reviewers read it through the MCP `get_audit_log` / `render_audit_transcript` tools or `developer_tools/inspect.py`, which also verify the chain. It never feeds back into the model's context.

**Contents**

- **class `AuditLogEntry`** *(pydantic model; bases: StrictModel)* - One transcript line: schema version, 1-based sequence, event type, UTC time, run/task ids, iteration, bounded payload, previous hash and its own integrity hash. · *Instantiated by:* `observability/audit_log.py::AuditTranscriptStore.append`
  - fields: `schema_version`, `sequence`, `event_type`, `occurred_at_utc`, `run_id`, `task_id`, `iteration`, `payload`, `previous_hash`, `integrity_hash`
  - `AuditLogEntry.identifiers_are_well_formed(value: str | None, info: ValidationInfo) -> str | None` *(validator, classmethod)* - Validator: `event_type`, `run_id` and `task_id` contain no unpaired surrogate (which UTF-8 cannot encode), naming the field and code point.
  - `AuditLogEntry.safe_payload(payload: dict[str, Any]) -> dict[str, Any]` *(validator, classmethod)* - Validator: refuses a payload containing a hidden-reasoning key.
- **class `AuditTranscriptStore`** *(class)* - Owns `<root>/.agent-audit-logs/<run>.jsonl` files: bounded redacted appends, paging, boundary snapshots, chain verification and markdown rendering. · *Instantiated by:* `agent/runtime.py::AgentRuntimeServices.open`, `developer_tools/inspect.py::inspect_run`, `mcp/server.py::create_mcp_server`
  - fields: `_append_locks`, `_append_locks_guard`
  - `AuditTranscriptStore.__init__(root: Path, *, max_payload_chars: int=8192, max_open_handles: int=32, read_only: bool=False) -> None` - Creates the log directory (not when `read_only`, which creates nothing and makes `append` and `render_markdown` raise); validates `max_payload_chars` (>= 256) and `max_open_handles` (>= 1, default 32); sets up the store lock and the LRU of open file handles.
  - `AuditTranscriptStore._require_writable(operation: str) -> None` - Raises `RuntimeError` naming the store root and the operation when the store was opened `read_only`.
  - `AuditTranscriptStore._handle_for(run_id: str) -> IO[bytes]` - Returns a cached append handle for a run, evicting and closing the least recently used one when the cache is full (so more than 32 concurrently active runs reopen files on every append). · *Called by:* `observability/audit_log.py::AuditTranscriptStore.append`
  - `AuditTranscriptStore.append(run_id: str, event_type: str, payload: dict[str, Any], *, task_id: str | None=None, iteration: int | None=None) -> AuditLogEntry` - One atomic transaction under a thread lock plus a cross-process `exclusive_file_lock` (`AUDIT_LOCK_TIMEOUT`): read the last entry from the end of the file, require that it belongs to the same run, redact and size-bound the payload, compute the entry hash, write the JSON line, flush and `fsync`.
  - `AuditTranscriptStore.list_entries(run_id: str, *, limit: int=1000, through_sequence: int | None=None) -> list[AuditLogEntry]` - Streams entries through `iter_entries` and stops as soon as `limit` (1 to 10,000) are collected or `through_sequence` is passed, so it never reads past what it returns. · *Called by:* `developer_tools/inspect.py::inspect_run`, `mcp/telemetry_tools.py::register_telemetry_tools.get_audit_log`, `observability/audit_log.py::AuditTranscriptStore.render_markdown`
  - `AuditTranscriptStore.snapshot_sequence(run_id: str) -> int` - Highest persisted sequence for a run (read from the tail); the fixed boundary used for verification and rendering. · *Called by:* `observability/audit_log.py::AuditTranscriptStore.chain_break`, `observability/audit_log.py::AuditTranscriptStore.iter_entries`, `observability/audit_log.py::AuditTranscriptStore.render_markdown`
  - `AuditTranscriptStore.iter_entries(run_id: str, *, through_sequence: int | None=None) -> Iterator[AuditLogEntry]` - Streams entries in order up to a boundary without loading the file at once, checking that each belongs to the requested run; a missing file yields nothing. · *Called by:* `observability/audit_log.py::AuditTranscriptStore.chain_break`, `observability/audit_log.py::AuditTranscriptStore.entry_count`, `observability/audit_log.py::AuditTranscriptStore.render_markdown`
  - `AuditTranscriptStore.entry_count(run_id: str, *, through_sequence: int | None=None) -> int` - Counts entries through a boundary. · *Called by:* `developer_tools/inspect.py::inspect_run`, `state/run_state_store.py::RunStateStore._counts_for`, `state/run_state_store.py::RunStateStore.load`, `state/run_state_store.py::RunStateStore.save` (+1 more)
  - `AuditTranscriptStore.verify(run_id: str) -> bool` - True if `chain_break` finds nothing from entry 1 to the current boundary.
  - `AuditTranscriptStore.chain_break(run_id: str) -> ChainBreak | None` - Checks the chain from entry 1 to the current boundary and returns a `ChainBreak` naming the first bad sequence and why (content altered, predecessor missing or reordered, or an unreadable line), or None. · *Called within this file by:* `observability/audit_log.py::AuditTranscriptStore.verify`
  - `AuditTranscriptStore._verify_entries(entries: Iterator[AuditLogEntry]) -> bool` *(staticmethod)* - True if `_entries_chain_break` finds no break in a stream of entries. · *No in-package callers (public API, entry point, or protocol hook).*
  - `AuditTranscriptStore.render_markdown(run_id: str) -> Path` - Writes `<run>.transcript.md`: integrity status (with the first integrity failure when there is one), the number of entries verified (those before the first break) and the number rendered, then one section per entry with a pretty-printed payload; the file is replaced atomically through a unique temporary file. Raises on a read-only store. · *Called by:* `mcp/telemetry_tools.py::register_telemetry_tools.render_audit_transcript`
  - `AuditTranscriptStore._tail(path: Path) -> tuple[str | None, int]` - Reads the last entry (`_read_tail_entry`) and returns (last hash, last sequence), or (None, 0) for a missing or empty file. · *Called by:* `observability/audit_log.py::AuditTranscriptStore.snapshot_sequence`
  - `AuditTranscriptStore._jsonl_path(run_id: str) -> Path` - Maps a run id to `<file_safe_name(run_id)>.jsonl`; the mapping is injective, so two run ids never share a transcript. · *Called by:* `observability/audit_log.py::AuditTranscriptStore._append_transaction`, `observability/audit_log.py::AuditTranscriptStore._handle_for`, `observability/audit_log.py::AuditTranscriptStore.iter_entries`, `observability/audit_log.py::AuditTranscriptStore.list_entries` (+1 more)
  - `AuditTranscriptStore.close() -> None` - Closes all cached append handles (needed before deleting the root on Windows). · *Called within this file by:* `observability/audit_log.py::AuditTranscriptStore._handle_for`
  - `AuditTranscriptStore._append_transaction(run_id: str) -> Iterator[None]` *(contextmanager)* - Context manager that holds the in-process lock and an `exclusive_file_lock` on `<run>.lock` (timeout code `AUDIT_LOCK_TIMEOUT`) around one append. · *Called by:* `observability/audit_log.py::AuditTranscriptStore.append`
  - `AuditTranscriptStore._local_append_lock(path: Path) -> Iterator[None]` *(classmethod, contextmanager)* - Class-level registry of one `RLock` per log path so separate store instances in one process serialize on the same file. · *Called by:* `observability/audit_log.py::AuditTranscriptStore._append_transaction`
- `_bound_and_redact(value: Any, max_chars: int) -> dict[str, Any]` - Redacts secrets, then keeps the payload if its JSON fits `max_chars`; otherwise replaces it with a truncation record (hash, original size, preview). · *Called by:* `observability/audit_log.py::AuditTranscriptStore.append`
- `_last_line(handle: IO[bytes], end: int) -> bytes` - Reads backwards from `end` in 64 KiB chunks and returns the last non-blank line of the file, however long it is.
- `_read_tail_entry(handle: IO[bytes], end: int, label: str) -> AuditLogEntry | None` - Parses the last line as an `AuditLogEntry`, or returns None for an empty file; an invalid last line raises `AUDIT_TRANSCRIPT_CORRUPT` naming the transcript and the validation error, so nothing is appended after damage.
- `_require_transcript_owner(entry: AuditLogEntry | None, run_id: str, label: str) -> None` - Raises `AUDIT_TRANSCRIPT_RUN_MISMATCH` naming both run ids when an entry's run id differs from the requested one.
- `_verified_entry_count(entries: Iterator[AuditLogEntry], failure: ChainBreak | None) -> int` - Number of entries that precede the first integrity failure (all of them when the chain is intact); counting stops at an unreadable line. · *Called by:* `observability/audit_log.py::AuditTranscriptStore.render_markdown`
- `_canonical_json(value: Any) -> str` - Sorted-key compact JSON used for hashing and size checks. · *Called within this file by:* `observability/audit_log.py::_bound_and_redact`, `observability/audit_log.py::_hash`
- `_hash(value: Any) -> str` - SHA-256 hex of the canonical JSON of a value. · *Called within this file by:* `observability/audit_log.py::AuditTranscriptStore.append`, `observability/audit_log.py::_entries_chain_break`
- `_entries_chain_break(entries: Iterator[AuditLogEntry]) -> ChainBreak | None` - Runs the shared chain checker over audit entries (previous-hash field `previous_hash`, hash recomputed with `integrity_hash` blanked). · *Called by:* `observability/audit_log.py::AuditTranscriptStore._verify_entries`, `observability/audit_log.py::AuditTranscriptStore.chain_break`, `observability/audit_log.py::AuditTranscriptStore.render_markdown`
- `_atomic_write(path: Path, content: str) -> None` - Writes text to a unique temporary file, then `replace_atomic`s it into place. · *Called within this file by:* `observability/audit_log.py::AuditTranscriptStore.render_markdown`

**Algorithms & invariants.** Each entry hashes its own canonical JSON with `integrity_hash` blank and stores the previous entry's hash, so editing, deleting or reordering any entry breaks verification of everything after it. Per-entry `fsync` makes entries durable but is synchronous work done on whichever thread calls `append`. A transcript file belongs to exactly one run id (`file_safe_name` is injective); an entry of another run raises `AUDIT_TRANSCRIPT_RUN_MISMATCH`, and an unreadable last line raises `AUDIT_TRANSCRIPT_CORRUPT` instead of being skipped. Reads are streamed and stop at the requested boundary or limit.

*Module-level names:* `_LOCK_TIMEOUT_SECONDS`, `_LOCK_POLL_SECONDS`

---

### `observability/metric_definitions.py` - the SDK's standard metric catalogue

*481 lines · depends on: `observability/telemetry_models.py` · used by: `observability/metrics.py` · re-exported at the package root: 1 name(s)*

**Role in the workflow.** `register_standard_metric_definitions` loads this table into the telemetry store at the start of every run so each recorded observation has a formula, unit, aggregation rule and missing-data rule that `create_run_report` can apply.

**Contents**

- `_definition(metric_id: str, name: str, unit: str, direction: str, formula: str, **kwargs: Any) -> MetricDefinition` - Small factory that builds a `MetricDefinition` from positional id/name/unit/direction/formula plus keyword fields. · *Called by:* `observability/metric_definitions.py::<module>`

**Algorithms & invariants.** `STANDARD_METRIC_DEFINITIONS` is a tuple of definitions grouped by prefix: `agent.*` (model-turn, rejection, tool, verification and escalation counts, wall/CPU duration), `context.*` (estimated input tokens, working budget, deadlocks, PASK and exact-PCKP compaction counts and certificates), `controller.*` (repair attempts, escalations), `model.*` (provider-reported token usage; explicitly unavailable until an adapter reports it, never estimated), `research.*` (evaluation inputs such as PPA drift or DRC/LVS clean rate; unavailable until evidence exists), `retrieval.*` and `tool.*`.

*Module-level names:* `STANDARD_METRIC_DEFINITIONS`

---

### `observability/metrics.py` - record metric values against the standard catalogue

*131 lines · depends on: `observability/metric_definitions.py`, `observability/telemetry_helpers.py`, `observability/telemetry_models.py`, `observability/telemetry_store.py` · used by: `agent/base_agent/agent.py`, `mcp/server.py`, `specifications/retrieval.py`, `state/controller_runtime.py` · re-exported at the package root: 3 name(s)*

**Role in the workflow.** Thin recording layer used by `BaseAgent` (per-turn context metrics, provider usage, tool durations, end-of-run counters) and by integrations that want to publish their own values.

**Contents**

- `register_standard_metric_definitions(store: TelemetryStore) -> None` - Upserts every definition from `STANDARD_METRIC_DEFINITIONS` into the telemetry store. · *Called by:* `base_agent/agent.py::BaseAgent.run`, `mcp/server.py::create_mcp_server`, `state/controller_runtime.py::ControllerRuntime.__init__`
- `record_metric_value(store: TelemetryStore, context: TelemetryContext, metric_id: str, value: float, unit: str, *, source_event_id: str | None=None, details: dict[str,...` - Records an available numeric observation for the run in `context`, optionally linked to a source event and annotated with details. · *Called by:* `base_agent/agent.py::BaseAgent._record_provider_usage`, `base_agent/agent.py::BaseAgent._terminate`, `base_agent/agent.py::BaseAgent.run`, `observability/metrics.py::record_terminal_agent_metrics` (+2 more)
- `record_metric_unavailable(store: TelemetryStore, context: TelemetryContext, metric_id: str, unit: str, reason: str, *, source_event_id: str | None=None, details: dict[str, ...` - Records an explicit *unavailable* observation with the reason, so a missing measurement is never silently read as zero. · *Called by:* `base_agent/agent.py::BaseAgent._record_provider_usage`, `base_agent/agent.py::BaseAgent._terminate`, `base_agent/agent.py::BaseAgent.run`, `observability/metrics.py::record_terminal_agent_metrics`
- `record_terminal_agent_metrics(store: TelemetryStore, context: TelemetryContext, events: Iterable[TelemetryEvent], profile: dict[str, Any], *, completed: bool, terminal_reason: ...` - At run end, reduces the run's telemetry events into stable counters (turn attempts, recovery iterations, output rejections, tool attempts/failures/blocks, verification failures, escalations, deadlocks) and records wall and CPU duration from the profile, or an unavailable observation if the profiler did not finish. · *Called by:* `base_agent/agent.py::BaseAgent._terminate`

---

### `observability/profiler.py` - privacy-conscious timing profile for one agent run

*318 lines · depends on: `foundations/atomic_io.py`, `foundations/contracts.py` · used by: `agent/base_agent/agent.py`, `agent/runtime.py` · re-exported at the package root: 7 name(s)*

**Role in the workflow.** `BaseAgent` owns the lifecycle: `begin_run`, a span around every context projection, model turn, tool call and verification, then `finish_run`. The result is attached to `AgentResult.profile`, summarized into telemetry, and its hash is audited. It records durations and statuses only, never prompts, reasoning or raw tool payloads.

**Contents**

- **class `ProfileSpanKind`** *(enum; bases: StrEnum)* - Phase categories: run, context-projection, model-turn, tool, verification.
  - members: `RUN`, `CONTEXT_PROJECTION`, `MODEL_TURN`, `TOOL`, `VERIFICATION`
- **class `ProfileSpanStatus`** *(enum; bases: StrEnum)* - Span outcomes: completed, failed, blocked, cancelled, timed-out.
  - members: `COMPLETED`, `FAILED`, `BLOCKED`, `CANCELLED`, `TIMED_OUT`
- **class `ProfileSpan`** *(pydantic model; bases: StrictModel)* - One finished span: ids, kind, name, UTC start, wall and CPU nanoseconds, status and bounded attributes. · *Instantiated by:* `observability/profiler.py::AgentRunProfiler.finish_span`
  - fields: `span_id`, `parent_span_id`, `kind`, `name`, `started_at_utc`, `duration_ns`, `cpu_duration_ns`, `status`, `attributes`
  - `ProfileSpan.attributes_are_bounded_and_safe(value: dict[str, Any]) -> dict[str, Any]` *(validator, classmethod)* - Validator: no hidden-reasoning keys and attributes must serialize to at most 4,096 characters.
- **class `ProfilePhaseSummary`** *(pydantic model; bases: StrictModel)* - Per-kind totals: counts by status plus total wall and CPU nanoseconds. · *Instantiated by:* `observability/profiler.py::_summaries`
  - fields: `kind`, `count`, `completed_count`, `failed_count`, `blocked_count`, `cancelled_count`, `timed_out_count`, `duration_ns`, `cpu_duration_ns`
- **class `AgentRunProfile`** *(pydantic model; bases: StrictModel)* - The complete, hash-sealed profile (`agent-run-profile-v1`): run identity, timing, spans and per-kind summaries.
  - fields: `schema_version`, `run_id`, `task_id`, `agent_identity`, `started_at_utc`, `finished_at_utc`, `status`, `wall_duration_ns`, `process_cpu_duration_ns`, `spans`, `summaries`, `integrity_hash`
- **class `ProfileSpanHandle`** *(dataclass)* - Opaque token returned when a span starts and passed back to finish it. · *Instantiated by:* `observability/profiler.py::AgentRunProfiler.start_span`
  - fields: `span_id`
- **class `_ActiveSpan`** *(dataclass)* - Internal record of a span that has started but not finished (start clocks and attributes). · *Instantiated by:* `observability/profiler.py::AgentRunProfiler.start_span`
  - fields: `handle`, `parent_span_id`, `kind`, `name`, `started_at_utc`, `started_monotonic_ns`, `started_cpu_ns`, `attributes`
- **class `AgentRunProfiler`** *(class)* - Thread-safe profiler for exactly one invocation; a second `begin_run` raises, which is why a `BaseAgent` instance can run only once. · *Instantiated by:* `base_agent/agent.py::BaseAgent.__init__`, `agent/runtime.py::AgentRuntimeServices.create_agent`
  - `AgentRunProfiler.__init__() -> None` - Initializes empty state and the re-entrant lock.
  - `AgentRunProfiler.begin_run(run_id: str, task_id: str, agent_identity: str) -> ProfileSpanHandle` - Records run identity and start clocks and opens the root `agent-run` span; rejects a second run. · *Called by:* `base_agent/agent.py::BaseAgent.run`
  - `AgentRunProfiler.start_span(kind: ProfileSpanKind, name: str, *, attributes: dict[str, Any] | None=None, parent_span_id: str | None=None) -> ProfileSpanHandle` - Validates attributes (safe, <= 4,096 chars), allocates `span-N` and records start wall/CPU clocks. · *Called by:* `base_agent/agent.py::BaseAgent._accept_final_turn`, `base_agent/agent.py::BaseAgent._execute_profiled_tool_call`, `base_agent/agent.py::BaseAgent.run`, `observability/profiler.py::AgentRunProfiler.begin_run`
  - `AgentRunProfiler.finish_span(handle: ProfileSpanHandle, status: ProfileSpanStatus, *, attributes: dict[str, Any] | None=None) -> ProfileSpan` - Closes an active span with a status and merged attributes and appends the finished `ProfileSpan`. · *Called by:* `base_agent/agent.py::BaseAgent._accept_final_turn`, `base_agent/agent.py::BaseAgent._execute_profiled_tool_call`, `base_agent/agent.py::BaseAgent._terminate`, `base_agent/agent.py::BaseAgent.run` (+1 more)
  - `AgentRunProfiler.finish_run(status: ProfileSpanStatus) -> AgentRunProfile` - Force-closes any open span as cancelled, stamps the end time and status, and freezes the final profile (idempotent). · *Called by:* `base_agent/agent.py::BaseAgent._terminate`
  - `AgentRunProfiler.snapshot() -> AgentRunProfile` - Builds the current `AgentRunProfile` (durations only once finished) and seals it with a SHA-256 integrity hash. · *Called within this file by:* `observability/profiler.py::AgentRunProfiler.finish_run`, `observability/profiler.py::AgentRunProfiler.write_json`
  - `AgentRunProfiler.write_json(destination: Path) -> Path` - Atomically writes a finished profile to a path chosen by the host; requires `finish_run` first. · *No in-package callers (public API, entry point, or protocol hook).*
- `_summaries(spans: list[ProfileSpan]) -> list[ProfilePhaseSummary]` - Folds spans into per-kind `ProfilePhaseSummary` rows sorted by kind. · *Called by:* `observability/profiler.py::AgentRunProfiler.snapshot`
- `_assert_profile_safe(value: Any) -> None` - Recursively rejects attribute keys reserved for hidden reasoning. · *Called by:* `observability/profiler.py::AgentRunProfiler.start_span`, `observability/profiler.py::ProfileSpan.attributes_are_bounded_and_safe`
- `_canonical_json(value: Any) -> str` - Sorted-key compact JSON for hashing. · *Called within this file by:* `observability/profiler.py::AgentRunProfiler.start_span`, `observability/profiler.py::ProfileSpan.attributes_are_bounded_and_safe`, `observability/profiler.py::_hash`
- `_hash(value: Any) -> str` - SHA-256 of the canonical JSON. · *Called within this file by:* `observability/profiler.py::AgentRunProfiler.snapshot`
- `_utc_now() -> str` - Current UTC time as an ISO-8601 string. · *Called by:* `observability/profiler.py::AgentRunProfiler.begin_run`, `observability/profiler.py::AgentRunProfiler.finish_run`, `observability/profiler.py::AgentRunProfiler.start_span`

---

### `observability/telemetry_helpers.py` - tiny constructors for timestamps and metric observations, and the shared hash-chain checker

*99 lines · depends on: `observability/telemetry_models.py` · used by: `observability/audit_log.py`, `observability/metrics.py`, `observability/telemetry_store.py` · re-exported at the package root: 1 name(s)*

**Role in the workflow.** Used by `metrics.py` so every observation is built the same way.

**Contents**

- `utc_now() -> str` - Current UTC time as an ISO-8601 string. · *Called by:* `observability/telemetry_helpers.py::metric_observation`
- `first_chain_break(records: Iterable[Any], *, noun: str, previous_attribute: str, expected_hash: Callable[[Any], str]) -> ChainBreak | None` - Walks records in order and returns a `ChainBreak` for the first problem: an unreadable record, a stored hash that differs from the recomputed one (content altered), or a previous-hash link that does not equal the preceding record's hash (a record removed, reordered or replaced); None if the chain is intact. Used by both the telemetry and audit stores. · *Called by:* `observability/audit_log.py::_entries_chain_break`, `observability/telemetry_store.py::_events_chain_break`
- `_short(value: str | None) -> str` - First 12 characters of a hash, or `none`, for readable failure messages. · *Called by:* `observability/telemetry_helpers.py::first_chain_break`
- `metric_observation(metric_id: str, run_id: str, *, unit: str, value: float | None, unavailable_reason: str | None=None, source_event_id: str | None=None, source_arti...` - Builds a `MetricObservation`; availability is AVAILABLE when a value is given and UNAVAILABLE otherwise (then `unavailable_reason` should explain why). · *Called by:* `observability/metrics.py::record_metric_unavailable`, `observability/metrics.py::record_metric_value`

---

### `observability/telemetry_models.py` - telemetry event, context, actor and metric contracts

*129 lines · depends on: `foundations/contracts.py`, `foundations/errors.py` · used by: `agent/base_agent/agent.py`, `agent/orchestrator/orchestrator.py`, `integrations/jev/receipts.py`, `integrations/receipts.py`, `mcp/agent_tools.py`, `mcp/telemetry_tools.py`, `observability/audit_log.py`, `observability/metric_definitions.py` (+6 more) · re-exported at the package root: 10 name(s)*

**Role in the workflow.** The typed shapes persisted by `TelemetryStore`. They deliberately carry structured outcomes, timings, authority, links and hashes only; hidden reasoning is rejected at validation.

**Contents**

- **class `TelemetryAuthority`** *(enum; bases: StrEnum)* - Who produced the fact: deterministic code, model-mediated, human, tool or system.
  - members: `DETERMINISTIC`, `MODEL_MEDIATED`, `HUMAN`, `TOOL`, `SYSTEM`
- **class `TelemetrySeverity`** *(enum; bases: StrEnum)* - debug, info, warning or error.
  - members: `DEBUG`, `INFO`, `WARNING`, `ERROR`
- **class `MetricAvailability`** *(enum; bases: StrEnum)* - Whether a metric observation carries a value or is explicitly unavailable.
  - members: `AVAILABLE`, `UNAVAILABLE`
- **class `TelemetryActor`** *(pydantic model; bases: StrictModel)* - The emitter: a kind (for example `agent`), an identifier and an optional role. · *Instantiated by:* `base_agent/agent.py::BaseAgent._terminate`, `base_agent/agent.py::BaseAgent._run.emit`, `orchestrator/orchestrator.py::Orchestrator._emit`, `jev/receipts.py::TelemetryJevReceiptSink.__call__` (+5 more)
  - fields: `kind`, `identifier`, `role`
- **class `TelemetryContext`** *(pydantic model; bases: StrictModel)* - Correlation ids for an event (project, experiment, cohort, run, attempt, controller, node, task, agent, trace/span, stage, environment); `run_id` is mandatory. · *Instantiated by:* `base_agent/agent.py::BaseAgent._terminate`, `base_agent/agent.py::BaseAgent.run`, `orchestrator/orchestrator.py::Orchestrator._emit`, `mcp/agent_tools.py::register_agent_tools.run_agent_task` (+1 more)
  - fields: `project_id`, `experiment_id`, `cohort_id`, `run_id`, `attempt_id`, `controller_id`, `node_id`, `task_id`, `agent_id`, `trace_id`, `span_id`, `parent_span_id`, `stage`, `environment_id`
- **class `TelemetryEvent`** *(pydantic model; bases: StrictModel)* - One ledger event: id, per-run sequence, type, UTC and monotonic times, context, actor, authority, status, severity, links, payload and its place in the hash chain. · *Instantiated by:* `observability/telemetry_store.py::TelemetryStore.emit`
  - fields: `schema_version`, `event_id`, `sequence`, `event_type`, `occurred_at_utc`, `occurred_at_monotonic_ns`, `context`, `actor`, `authority`, `status`, `severity`, `links`, `payload`, `previous_event_hash`, `integrity_hash`
  - `TelemetryEvent.reject_hidden_reasoning(value: dict[str, Any]) -> dict[str, Any]` *(validator, classmethod)* - Validator: payload and links must not contain hidden-reasoning keys.
- **class `MetricDefinition`** *(pydantic model; bases: StrictModel)* - Catalogue entry: id, name, unit, direction, formula, optional numerator/denominator, aggregation, missing-data rule and source. · *Instantiated by:* `observability/metric_definitions.py::_definition`
  - fields: `metric_id`, `name`, `unit`, `direction`, `formula`, `numerator`, `denominator`, `aggregation`, `missing_data_rule`, `source_description`, `schema_version`
- **class `MetricObservation`** *(pydantic model; bases: StrictModel)* - One measured (or unavailable) value for a run with provenance (source event/artifact, parser version) and context. · *Instantiated by:* `observability/telemetry_helpers.py::metric_observation`
  - fields: `observation_id`, `metric_id`, `run_id`, `value`, `unit`, `availability`, `unavailable_reason`, `source_event_id`, `source_artifact_id`, `parser_version`, `context`, `observed_at_utc`
- **class `TelemetryRunSummary`** *(pydantic model; bases: StrictModel)* - Per-run roll-up for listings: event count, first/last time and counts by status and event type. · *Instantiated by:* `observability/telemetry_store.py::TelemetryStore.list_runs`
  - fields: `run_id`, `event_count`, `started_at`, `last_event_at`, `statuses`, `event_types`
- **class `ChainBreak`** *(pydantic model; bases: StrictModel)* - Where and why an integrity chain fails: the sequence number, a kind (content-hash-mismatch, previous-hash-mismatch or unreadable-entry) and a message naming both hashes. · *Instantiated by:* `observability/telemetry_helpers.py::first_chain_break`
  - fields: `sequence`, `kind`, `message`

---

### `observability/telemetry_store.py` - durable telemetry ledger: SQLite events with a per-run hash chain, plus metrics

*495 lines · depends on: `foundations/atomic_io.py`, `foundations/errors.py`, `foundations/identifiers.py`, `observability/telemetry_helpers.py`, `observability/telemetry_models.py` · used by: `agent/base_agent/agent.py`, `agent/orchestrator/orchestrator.py`, `agent/runtime.py`, `developer_tools/inspect.py`, `integrations/jev/receipts.py`, `integrations/receipts.py`, `mcp/_shared.py`, `mcp/server.py` (+4 more) · re-exported at the package root: 1 name(s)*

**Role in the workflow.** Every `BaseAgent` lifecycle event becomes an `agent.<type>` telemetry event; metrics are recorded beside them. The MCP telemetry tools and `developer_tools/inspect.py` page the events, verify the chain and build run reports from this store.

**Contents**

- **class `TelemetryStore`** *(class)* - Append-only SQLite store (`.agent-telemetry/telemetry.sqlite3`, WAL mode) holding events, metric observations and metric definitions. · *Instantiated by:* `orchestrator/orchestrator.py::Orchestrator.__init__`, `agent/runtime.py::AgentRuntimeServices.open`, `developer_tools/inspect.py::inspect_run`, `mcp/server.py::create_mcp_server`
  - `TelemetryStore.__init__(root: Path, *, read_only: bool=False) -> None` - Creates the directory and one shared connection (guarded by an RLock), enables WAL and foreign keys, and creates the schema; the WAL switch and the schema creation retry for up to ten seconds while another process holds the database lock, so processes that open one run root together all start. With `read_only=True` it requires the database to exist (`FileNotFoundError` naming it), opens it with `mode=ro`, and creates no directory and runs no setup.
  - `TelemetryStore.close() -> None` - Closes the connection (needed before deleting the root on Windows).
  - `TelemetryStore.root() -> Path` *(property)* - Property: the `.agent-telemetry` directory. · *Called within this file by:* `observability/telemetry_store.py::TelemetryStore.__init__`
  - `TelemetryStore.database_path() -> Path` *(property)* - Property: the SQLite file path. · *No in-package callers (public API, entry point, or protocol hook).*
  - `TelemetryStore.emit(event_type: str, context: TelemetryContext, *, actor: TelemetryActor, authority: TelemetryAuthority, status: str, severity: TelemetrySeverity=Tele...` - Builds a `TelemetryEvent` with UTC and monotonic timestamps from the given context/actor/authority/payload and appends it.
  - `TelemetryStore.append(event: TelemetryEvent) -> TelemetryEvent` - In one `BEGIN IMMEDIATE` transaction: read the run's last (sequence, hash), assign the next sequence, redact payload and links, hash the event with its predecessor and insert it. · *Called within this file by:* `observability/telemetry_store.py::TelemetryStore.emit`, `observability/telemetry_store.py::TelemetryStore.list_runs`
  - `TelemetryStore.record_metric(observation: MetricObservation) -> MetricObservation` - Validates availability rules (available needs a value, unavailable needs a reason) and inserts the observation. · *Called by:* `mcp/telemetry_tools.py::register_telemetry_tools.record_metric_observation`, `observability/metrics.py::record_metric_unavailable`, `observability/metrics.py::record_metric_value`
  - `TelemetryStore.register_metric_definition(definition: MetricDefinition) -> MetricDefinition` - Upserts a metric definition by id.
  - `TelemetryStore.list_metric_definitions() -> list[MetricDefinition]` - All definitions ordered by id. · *Called within this file by:* `observability/telemetry_store.py::TelemetryStore.create_run_report`
  - `TelemetryStore.list_events(run_id: str, *, limit: int=250, after_sequence: int=0, through_sequence: int | None=None) -> list[TelemetryEvent]` - One page (1 to 1,000) of a run's events after a sequence and optionally through a boundary. · *Called by:* `base_agent/agent.py::BaseAgent._terminate`, `developer_tools/inspect.py::inspect_run`, `mcp/telemetry_tools.py::register_telemetry_tools.get_telemetry_events`, `observability/telemetry_store.py::TelemetryStore.iter_events`
  - `TelemetryStore.run_snapshot_sequence(run_id: str) -> int` - Highest sequence for a run; the boundary for verification and reports. · *Called by:* `observability/telemetry_store.py::TelemetryStore.chain_break`, `observability/telemetry_store.py::TelemetryStore.create_run_report`, `observability/telemetry_store.py::TelemetryStore.iter_events`
  - `TelemetryStore.iter_events(run_id: str, *, page_size: int=1000, through_sequence: int | None=None, after_sequence: int=0) -> Iterator[TelemetryEvent]` - Streams a run's events page by page, from `after_sequence` through a fixed boundary. · *Called by:* `observability/telemetry_store.py::TelemetryStore.chain_break`, `observability/telemetry_store.py::TelemetryStore.create_run_report`
  - `TelemetryStore.list_metrics(run_id: str) -> list[MetricObservation]` - All metric observations for a run ordered by observation time. · *Called by:* `developer_tools/inspect.py::inspect_run`, `mcp/telemetry_tools.py::register_telemetry_tools.get_telemetry_metrics`, `observability/telemetry_store.py::TelemetryStore.create_run_report`
  - `TelemetryStore.list_runs(*, limit: int=100) -> list[TelemetryRunSummary]` - The most recently active runs with event counts, time span and status/type histograms, all derived from each event's hashed `event_json` (`json_extract`) rather than from the denormalized columns, so a forged column cannot change what is reported. · *Called by:* `mcp/telemetry_tools.py::register_telemetry_tools.list_telemetry_runs`
  - `TelemetryStore.verify_run_chain(run_id: str) -> bool` - True if `chain_break` finds nothing through the run's current boundary. · *No in-package callers (public API, entry point, or protocol hook).*
  - `TelemetryStore.chain_break(run_id: str) -> ChainBreak | None` - Checks the run's chain through its current boundary and returns a `ChainBreak` naming the first bad sequence and why, or None. · *Called within this file by:* `observability/telemetry_store.py::TelemetryStore.verify_run_chain`
  - `TelemetryStore._verify_events(events: Iterable[TelemetryEvent]) -> bool` *(staticmethod)* - True if `_events_chain_break` finds no break in a stream of events. · *No in-package callers (public API, entry point, or protocol hook).*
  - `TelemetryStore.create_run_report(run_id: str) -> dict[str, Any]` - Writes `reports/<file_safe_name(run)>.run-report.json`: counts by status and type, watchdog interventions, all metrics, per-metric summaries by registered aggregation, chain validity (with the failure when broken), the number of events verified (those before the first break) and every event hash; covers the whole run, not a page. · *Called by:* `mcp/telemetry_tools.py::register_telemetry_tools.create_telemetry_report`
  - `TelemetryStore._initialize() -> None` - Creates the events, metric_observations and metric_definitions tables and their indexes if absent. · *Called by:* `observability/telemetry_store.py::TelemetryStore.__init__`
- `_retry_when_locked(operation: Callable[[], T]) -> T` - Runs an operation again, with a doubling delay (5 ms up to 100 ms), for up to ten seconds while SQLite reports the database as locked; any other error, or the deadline, raises. · *Called by:* `observability/telemetry_store.py::TelemetryStore.__init__`
- `_canonical_json(value: Any) -> str` - Sorted-key compact ASCII JSON used for hashing and storage. · *Called within this file by:* `observability/telemetry_store.py::TelemetryStore.append`, `observability/telemetry_store.py::TelemetryStore.record_metric`, `observability/telemetry_store.py::_canonical_hash`
- `_canonical_hash(value: Any) -> str` - SHA-256 of the canonical JSON. · *Called by:* `observability/telemetry_store.py::TelemetryStore.append`, `observability/telemetry_store.py::_events_chain_break`
- `_events_chain_break(events: Iterable[TelemetryEvent]) -> ChainBreak | None` - Runs the shared chain checker over telemetry events (previous-hash field `previous_event_hash`, hash recomputed with `integrity_hash` blanked). · *Called by:* `observability/telemetry_store.py::TelemetryStore._verify_events`, `observability/telemetry_store.py::TelemetryStore.chain_break`, `observability/telemetry_store.py::TelemetryStore.create_run_report`
- `_count(values: Iterable[str]) -> dict[str, int]` - Counts occurrences of each string into a dict. · *Called by:* `observability/telemetry_store.py::TelemetryStore.create_run_report`
- `_summarize_metrics(observations: Iterable[MetricObservation], definitions: Iterable[MetricDefinition]) -> dict[str, dict[str, Any]]` - Groups observations by metric and aggregates available values (sum, mean, min, max, last) according to the registered definition; unregistered metrics and missing data yield None rather than zero. · *Called by:* `observability/telemetry_store.py::TelemetryStore.create_run_report`
- `_atomic_write(target: Path, content: bytes) -> None` - Writes bytes to a unique temporary file and `replace_atomic`s it. · *Called within this file by:* `observability/telemetry_store.py::TelemetryStore.create_run_report`

**Algorithms & invariants.** Integrity hash = SHA-256 of the event's canonical JSON with `integrity_hash` blank, which includes `previous_event_hash`; `UNIQUE(run_id, sequence)` prevents two writers from claiming the same slot. All writes serialize on one connection, and each append commits (one fsync under SQLite's default synchronous mode).
