# `developer_tools/` - read-only maintainer utilities and the `nailong-agent-sdk-dev` command

Five deterministic helpers behind one CLI: generate the public API catalogue from `__all__`, validate a JSON or YAML contract file, verify and summarise a run's evidence chains without writing anything, cross-check a project's recorded tool-result hashes against the result journal, and run the closed Ruff quality check. None of them runs a model or a tool. The CLI prints JSON and exits 0 on success, 1 when a verdict field of the result (`valid`, `passed`, `telemetry_chain_valid`, `audit_chain_valid`, `verified`) is false, 2 for a command-line usage error and 3 when the input could not be read or the check could not run (a one-line message on stderr, no traceback).

| File | Lines | Role |
|---|---:|---|
| [`developer_tools/__init__.py`](#developer_tools__init__py---public-surface-of-the-developer-utilities) | 56 | public surface of the developer utilities |
| [`developer_tools/catalog.py`](#developer_toolscatalogpy---public-api-catalogue-generation) | 88 | public API catalogue generation |
| [`developer_tools/cli.py`](#developer_toolsclipy---the-nailong-agent-sdk-dev-command-line) | 518 | the `nailong-agent-sdk-dev` command line |
| [`developer_tools/diagnose.py`](#developer_toolsdiagnosepy---where-a-run-first-went-wrong-what-ended-it-and-what-it-held-up) | 682 | where a run first went wrong, what ended it and what it held up |
| [`developer_tools/inspect.py`](#developer_toolsinspectpy---read-only-run-evidence-inspection) | 211 | read-only run evidence inspection |
| [`developer_tools/quality.py`](#developer_toolsqualitypy---closed-ruff-quality-check) | 92 | closed Ruff quality check |
| [`developer_tools/validate.py`](#developer_toolsvalidatepy---contract-file-validation) | 97 | contract-file validation |

---

### `developer_tools/__init__.py` - public surface of the developer utilities

*56 lines · depends on: `developer_tools/catalog.py`, `developer_tools/diagnose.py`, `developer_tools/inspect.py`, `developer_tools/quality.py`, `developer_tools/validate.py` · used by: no other module (entry point or re-exported only) · not re-exported at the package root*

**Role in the workflow.** Re-exports the catalogue, validation, inspection, diagnosis, evidence verification and quality functions and report types.

---

### `developer_tools/catalog.py` - public API catalogue generation

*88 lines · depends on: `foundations/atomic_io.py`, `foundations/contracts.py` · used by: `developer_tools/__init__.py`, `developer_tools/cli.py` · not re-exported at the package root*

**Role in the workflow.** Lists every name in `nailong_agent_sdk.__all__` with its kind, defining module and first documentation line; used to detect accidental API changes.

**Contents**

- **class `PublicApiSymbol`** *(pydantic model; bases: StrictModel)* - One exported name: kind, module and first docstring line. · *Instantiated by:* `developer_tools/catalog.py::_describe_symbol`
  - fields: `name`, `kind`, `module`, `documentation`
- **class `PublicApiCatalog`** *(pydantic model; bases: StrictModel)* - Versioned inventory with the package name and version. · *Instantiated by:* `developer_tools/catalog.py::build_public_api_catalog`
  - fields: `schema_version`, `package_name`, `package_version`, `symbols`
- `build_public_api_catalog() -> PublicApiCatalog` - Imports the package and describes each `__all__` name in sorted order. · *Called by:* `developer_tools/catalog.py::write_public_api_catalog`, `developer_tools/cli.py::_catalog`
- `write_public_api_catalog(path: Path) -> PublicApiCatalog` - Writes the catalogue as indented sorted JSON through a temporary file and returns it. · *Called by:* `developer_tools/cli.py::_catalog`
- `_describe_symbol(name: str, value: object) -> PublicApiSymbol` - Classifies a value as class, function, module or its type name. · *Called by:* `developer_tools/catalog.py::build_public_api_catalog`
- `_package_version() -> str` - Installed version of `nailong-agent-sdk`, or `uninstalled-source-tree`. · *Called by:* `developer_tools/catalog.py::build_public_api_catalog`

---

### `developer_tools/cli.py` - the `nailong-agent-sdk-dev` command line

*518 lines · depends on: `agent/retention.py`, `agent/retention_inventory.py`, `developer_tools/catalog.py`, `developer_tools/diagnose.py`, `developer_tools/inspect.py`, `developer_tools/quality.py`, `developer_tools/validate.py`, `foundations/errors.py`, `mcp/authority_matrix.py`, `observability/audit_log.py`, `observability/otlp.py`, `observability/run_checkpoints.py`, `observability/telemetry_store.py`, `observability/trace_context.py`, `state/run_state_store.py` · used by: no other module (entry point or re-exported only) · not re-exported at the package root*

**Role in the workflow.** Subcommands `catalog`, `validate`, `inspect-run`, `verify-evidence` and `quality`; each prints its report as JSON. Exit status: 0 success, 1 the artifact was inspected and is invalid, failed or has a broken chain, 2 usage error (argparse), 3 operational error. `prune-runs` is the one command that can change a run root, and only with `--apply`. `diagnose-run` reads a run's evidence and says where it first went wrong; like `inspect-run` it changes nothing.

**Contents**

- `main(argv: list[str] | None=None) -> int` - Parses arguments and runs the subcommand. An operational error (`OSError`, `ValueError`, `yaml.YAMLError`, `sqlite3.Error` or a missing Ruff) prints `nailong-agent-sdk-dev <command>: <ExceptionType>: <message>` to stderr and returns 3; otherwise it prints the result as JSON and returns 1 if any verdict field is false, else 0. · *Called within this file by:* `developer_tools/cli.py::<module>`
- `_parser() -> argparse.ArgumentParser` - Builds the argument parser (subcommands `catalog`, `validate`, `inspect-run`, `diagnose-run`, `verify-evidence`, `prune-runs`, `export-otlp`, `quality`; the epilog documents the exit statuses). Also `inventory`, `anchor-tombstones`, `verify-tombstones`, `vacuum-telemetry`, `authority-matrix` (no arguments), `checkpoint-run` and `verify-checkpoints` (both take `--key-file`); `prune-runs` accepts `--anchor`. · *Called by:* `developer_tools/cli.py::main`
- `_catalog(arguments: argparse.Namespace) -> object` - Handler for `catalog`, writing a file when `--output` is given. · *Called by:* `developer_tools/cli.py::main`
- `_validate(arguments: argparse.Namespace) -> object` - Handler for `validate`. · *Called by:* `developer_tools/cli.py::main`
- `_inspect_run(arguments: argparse.Namespace) -> object` - Handler for `inspect-run`. · *Called by:* `developer_tools/cli.py::main`
- `_diagnose_run(arguments: argparse.Namespace) -> object` - Handler for `diagnose-run <run_root> <run_id>`: the `RunDiagnosis` of a telemetry or graph run id; exit status 1 when `ok` is false, 3 for an unknown id or an unreadable root. · *Called by:* `developer_tools/cli.py::main`
- `_verify_evidence(arguments: argparse.Namespace) -> object` - Handler for `verify-evidence`. · *Called by:* `developer_tools/cli.py::main`
- `_prune_runs(arguments: argparse.Namespace) -> object` - `prune-runs <run_root> --older-than-seconds N [--keep-most-recent K] [--max-runs M] [--include-unfinished] [--export-reports] [--apply]`: builds a `RetentionPolicy` and calls `prune_run_root`. Without `--apply` it is a dry run that writes nothing; the JSON is the `RetentionReport`, and the exit status is 1 when it lists a problem (a broken chain), 3 for an invalid policy, a missing run root or a failure to prune. `--anchor <file>` checks the tombstone log against that anchor before an applied pass and refreshes it afterwards. · *Called by:* `developer_tools/cli.py::main`
- `_inventory(arguments: argparse.Namespace) -> object` - `inventory <run_root> --older-than-seconds N [--keep-most-recent K] [--max-runs M] [--include-unfinished] [--max-items N]`: reports every persistent resource class and what the policy would do with each; writes nothing. · *Called by:* `developer_tools/cli.py::main`
- `_anchor_tombstones(arguments: argparse.Namespace) -> object` - `anchor-tombstones <run_root> <destination>`: writes the tombstone log's length and head hash to a file outside the run root; refuses a destination inside it or a log the existing anchor no longer matches. · *Called by:* `developer_tools/cli.py::main`
- `_verify_tombstones(arguments: argparse.Namespace) -> object` - `verify-tombstones <run_root> <anchor>`: prints the verdict; exit 1 for `truncated`, `diverged`, `chain-broken` or `unreadable`. · *Called by:* `developer_tools/cli.py::main`
- `_vacuum_telemetry(arguments: argparse.Namespace) -> object` - `vacuum-telemetry <run_root> [--apply]`: reports the telemetry database's free pages and reclaimable bytes; with `--apply` runs `VACUUM` (refused while a prune is fenced or without the free space) and checks integrity afterwards. · *Called by:* `developer_tools/cli.py::main`
- `_export_otlp(arguments: argparse.Namespace) -> object` - `export-otlp <run_root> [--endpoint URL] [--run-id ID]... [--graph-run ID]... [--trace-id ID]... [--signals traces,logs,metrics] [--header NAME=VALUE]... [--timeout-seconds S] [--gzip] [--dry-run]`: opens the ledger read-only, resolves `--graph-run` and `--trace-id` to every run that recorded an event under that trace, and sends each run's spans, log records and metric points to the collector. The endpoint and headers default to `OTEL_EXPORTER_OTLP_ENDPOINT` and `OTEL_EXPORTER_OTLP_HEADERS`; `--dry-run` reports what would be sent. It never writes to the run root. With `--graph-run`, node dependencies from the run record are exported as span links. · *Called by:* `developer_tools/cli.py::main`
- `_graph_dependencies(run_root: Path, graph_run_ids: list[str]) -> dict[str, dict[str, list[str]]]` - For each `--graph-run` that has a run record, maps every node to its declared dependencies plus the parents of its enabled edges, loading the record through `RunStateStore` (so its integrity is verified); empty when the run root has no run store or the id is unknown, and it never creates one. · *Called by:* `developer_tools/cli.py::_export_otlp`
- `_signals(value: str) -> list[OtlpSignal]` - Parses `--signals` into the subset of traces, logs and metrics in send order; an unknown or empty list raises `ValueError` naming the option and the value. · *Called by:* `developer_tools/cli.py::_export_otlp`
- `_headers(items: list[str]) -> dict[str, str]` - Parses `NAME=VALUE` items (the `--header` option and the `OTEL_EXPORTER_OTLP_HEADERS` variable, whose values are percent-decoded); an item without `=` or a name raises `ValueError` naming it. · *Called by:* `developer_tools/cli.py::_export_otlp`
- `_authority_matrix() -> object` - Prints `authority_matrix()` (imported on use, so the other commands do not load the MCP layer). · *Called by:* `developer_tools/cli.py::main`
- `_checkpoint_signer(arguments: argparse.Namespace) -> HmacSha256Signer` - The `HmacSha256Signer` built from `--key-file` (its stripped text) and `--signer-id`; a key under 32 bytes is refused naming the length, never the key. · *Called by:* `developer_tools/cli.py::_checkpoint_run`, `developer_tools/cli.py::_verify_checkpoints`
- `_checkpoint_run(arguments: argparse.Namespace) -> object` - Opens the run root read-only and appends a signed checkpoint of the run's heads to the destination directory (`export_run_head_checkpoint`), consulting the run root's tombstones so a run id used again after a prune starts a new series. · *Called by:* `developer_tools/cli.py::main`
- `_verify_checkpoints(arguments: argparse.Namespace) -> object` - Checks a run against its checkpoint file (or the directory that holds it) and the terminal cross-link, consulting the run root's tombstones (a pruned run is `pruned`); exit status 1 when the report is not `ok`. · *Called by:* `developer_tools/cli.py::main`
- `_quality(arguments: argparse.Namespace) -> object` - Handler for `quality`. · *Called by:* `developer_tools/cli.py::main`
- `_exit_status(result: object) -> int` - 1 if any of `valid`, `passed`, `telemetry_chain_valid`, `audit_chain_valid`, `verified` is False on the result, else 0. · *Called by:* `developer_tools/cli.py::main`
- `_to_json(value: object) -> object` - Converts a pydantic result to JSON-compatible data. · *Called by:* `developer_tools/cli.py::main`

*Module-level names:* `_VERDICT_FIELDS`

---

### `developer_tools/diagnose.py` - where a run first went wrong, what ended it and what it held up

*682 lines · depends on: `agent/retention.py`, `foundations/contracts.py`, `foundations/errors.py`, `observability/audit_log.py`, `observability/run_checkpoints.py`, `observability/telemetry_models.py`, `observability/telemetry_store.py`, `state/graph.py`, `state/graph_models.py`, `state/run_state_store.py` · used by: `developer_tools/__init__.py`, `developer_tools/cli.py` · not re-exported at the package root*

**Role in the workflow.** A failed run leaves its evidence in three places: telemetry events that name what happened, an audit transcript and, for a graph run, a run record whose blocked nodes name only their direct parent. `diagnose_run(run_root, run_id)` reads them without writing and answers: what ended the run (`terminal`), what went wrong first (`first_problem`, `first_error`), whether the evidence can be trusted (`integrity`: both chains and the terminal link) and, for a graph run, which nodes stopped it for their own reason (`graph.roots`) and which nodes each one held up (`blocked_descendants`), with the terminal finding of the agent run behind each root. The id is a telemetry run id or a graph run id (a graph run's controller events are recorded under it). `nailong-agent-sdk-dev diagnose-run` prints the model and exits 1 when `ok` is false.

**Contents**

- **class `DiagnosisFinding`** *(pydantic model; bases: StrictModel)* - One recorded event that says something did not go as intended: the telemetry sequence and event type, the iteration, `error` or `warning`, the typed code when the record carried one, a message of at most 600 characters, the subject (a tool or a gate), the run status for a terminal finding and a few bounded details (tool call id, verifier id, output digest). · *Instantiated by:* `developer_tools/diagnose.py::_checkpoint_failed`, `developer_tools/diagnose.py::_controller_cancelled`, `developer_tools/diagnose.py::_graph_executed`, `developer_tools/diagnose.py::_listener_failed` (+9 more)
  - fields: `sequence`, `event_type`, `iteration`, `severity`, `code`, `message`, `subject`, `status`, `terminal`, `details`
- **class `IntegrityDiagnosis`** *(pydantic model; bases: StrictModel)* - Whether the evidence of the run can be trusted: event and entry counts, the validity and first break of each chain and the state and sentence of the terminal cross-link. · *Instantiated by:* `developer_tools/diagnose.py::_integrity`
  - fields: `telemetry_events`, `audit_entries`, `telemetry_chain_valid`, `audit_chain_valid`, `telemetry_chain_failure`, `audit_chain_failure`, `terminal_link`, `terminal_link_detail`
  - `IntegrityDiagnosis.intact() -> bool` *(property)* - Property: both chains verify and the terminal link is not `broken`; an `absent` link (a run written before runs cross-linked their heads) is not a failure.
- **class `GraphRootFailure`** *(pydantic model; bases: StrictModel)* - A node that stopped a graph run for its own reason: its status, reason, failure code and diagnostics, the graph event that set the status, the agent run behind it, that run's terminal finding and first problem when its telemetry is present, and the nodes it blocked (at most 100 listed, the rest counted). · *Instantiated by:* `developer_tools/diagnose.py::_root_failure`
  - fields: `node_id`, `status`, `reason`, `failure_code`, `diagnostics`, `graph_event_sequence`, `agent_run_id`, `agent_terminal`, `agent_first_problem`, `blocked_descendants`, `blocked_descendants_omitted`
- **class `GraphDiagnosis`** *(pydantic model; bases: StrictModel)* - The graph half of a diagnosis: node count, status counts, the roots (at most 50), the nodes that never finished (at most 50 listed) and the nodes blocked by a dependency that no root explains, which should be none. · *Instantiated by:* `developer_tools/diagnose.py::_graph_diagnosis`
  - fields: `run_id`, `node_count`, `status_counts`, `roots`, `roots_omitted`, `unfinished`, `unfinished_omitted`, `blocked_without_root`
- **class `RunDiagnosis`** *(pydantic model; bases: StrictModel)* - The answer: `verdict` (`completed`, `failed`, `blocked`, `cancelled` or `incomplete`), `ok` (completed and the evidence intact), one `summary` sentence, the `terminal` finding that ended the run, the `first_problem` of any severity and the `first_error`, up to 50 `findings` in sequence order, the integrity of the evidence and, for a graph run, the graph diagnosis or the reason its record could not be read. · *Instantiated by:* `developer_tools/diagnose.py::_diagnose`
  - fields: `schema_version`, `run_id`, `verdict`, `ok`, `summary`, `terminal`, `first_problem`, `first_error`, `findings`, `findings_omitted`, `integrity`, `graph`, `graph_record_error`
- `diagnose_run(run_root: Path, run_id: str) -> RunDiagnosis` - Requires a run root that is a directory and holds a telemetry database or the run's graph record (a `ValueError` naming the root, the database and the id otherwise), opens telemetry, audit and the run store read-only and diagnoses a telemetry run id or a graph run id. An id with neither events nor a record raises, naming the retention tombstone when one pruned it. · *Called by:* `developer_tools/cli.py::_diagnose_run`
- `_diagnose(run_root: Path, run_id: str, telemetry: TelemetryStore | None, audit: AuditTranscriptStore | None, runs: RunStateStore | None) -> RunDiagnosis` - Reads the run's events, loads its graph record, scans the events into findings, checks the chains and the terminal link when events exist, builds the graph diagnosis and the verdict and finally the summary sentence. · *Called by:* `developer_tools/diagnose.py::diagnose_run`
- `_unknown_run_message(run_root: Path, run_id: str) -> str` - `Run "<id>" is unknown: ...` naming both places looked in and both id kinds accepted, or, when a tombstone names the run, that it was pruned, when and at which head. · *Called within this file by:* `developer_tools/diagnose.py::_diagnose`
- `_load_graph(runs: RunStateStore | None, run_id: str) -> tuple[StateGraph | None, str | None]` - Loads the run record and rebuilds its `StateGraph`; a record that cannot be read, fails its integrity check or does not rebuild comes back as `ExceptionType: message` instead of an exception. · *Called by:* `developer_tools/diagnose.py::_diagnose`
- `_integrity(telemetry: TelemetryStore, audit: AuditTranscriptStore, run_id: str, event_count: int) -> IntegrityDiagnosis` - Both chain checks, the audit entry count and the terminal link as an `IntegrityDiagnosis`. · *Called by:* `developer_tools/diagnose.py::_diagnose`
- **class `_Scan`** *(dataclass)* - What one pass over a run's events found: the findings, the terminal finding and the final run status (None when no terminal event was recorded). · *Instantiated by:* `developer_tools/diagnose.py::_scan`
  - fields: `findings`, `terminal`, `status`
- `_scan(events: list[TelemetryEvent]) -> _Scan` - Walks the events once in sequence order, giving each event type to its extractor in `_EXTRACTORS`; a `run.terminated` finding is dropped when `agent.terminated` already says the same. · *Called by:* `developer_tools/diagnose.py::_diagnose`, `developer_tools/diagnose.py::_root_failure`
- `_details(event: TelemetryEvent) -> dict[str, Any]` - The `details` mapping of an agent lifecycle payload, empty when absent. · *Called by:* `developer_tools/diagnose.py::_listener_failed`, `developer_tools/diagnose.py::_model_cancelled`, `developer_tools/diagnose.py::_output_rejected`, `developer_tools/diagnose.py::_retrying` (+4 more)
- `_iteration(event: TelemetryEvent) -> int | None` - The iteration number of a payload, or None. · *Called by:* `developer_tools/diagnose.py::_listener_failed`, `developer_tools/diagnose.py::_model_cancelled`, `developer_tools/diagnose.py::_output_rejected`, `developer_tools/diagnose.py::_retrying` (+3 more)
- `_text(value: object, fallback: str) -> str` - A message cut to 600 characters, or the fallback when the value is not a non-empty string. · *Called by:* `developer_tools/diagnose.py::_checkpoint_failed`, `developer_tools/diagnose.py::_controller_cancelled`, `developer_tools/diagnose.py::_listener_failed`, `developer_tools/diagnose.py::_output_rejected` (+5 more)
- `_code(value: object) -> str | None` - The value when it is a non-empty string, else None. · *Called by:* `developer_tools/diagnose.py::_checkpoint_failed`, `developer_tools/diagnose.py::_listener_failed`, `developer_tools/diagnose.py::_output_rejected`, `developer_tools/diagnose.py::_retrying` (+3 more)
- `_terminated(event: TelemetryEvent) -> list[DiagnosisFinding]` - `agent.terminated`: nothing for `completed`; otherwise the terminal finding with the status, reason and failure code (a cancelled run is a warning). · *Called by:* `developer_tools/diagnose.py::<module>`
- `_run_terminated(event: TelemetryEvent) -> list[DiagnosisFinding]` - `run.terminated` of the task runner: a terminal finding from its status and reason, used when the agent recorded no terminal event of its own. · *Called by:* `developer_tools/diagnose.py::<module>`
- `_verification(event: TelemetryEvent) -> list[DiagnosisFinding]` - `agent.verification-completed` with `passed` false: an error finding with the gate id, the failure code, the reason and the verifier id, kind and output digest. · *Called by:* `developer_tools/diagnose.py::<module>`
- `_output_rejected(event: TelemetryEvent) -> list[DiagnosisFinding]` - `agent.output-rejected`: a warning with the schema code and the reason. · *Called by:* `developer_tools/diagnose.py::<module>`
- `_tool_outcomes(event: TelemetryEvent) -> list[DiagnosisFinding]` - `agent.tool-batch-completed`: one finding per call that did not succeed, with the outcome's failure code and bounded message, the tool as subject and the call id in the details; a failed call is an error and a blocked one a warning. · *Called by:* `developer_tools/diagnose.py::<module>`
- `_retrying(event: TelemetryEvent) -> list[DiagnosisFinding]` - `agent.model-turn-retrying`: a warning naming the provider error code, the attempt and the delay. · *Called by:* `developer_tools/diagnose.py::<module>`
- `_model_cancelled(event: TelemetryEvent) -> list[DiagnosisFinding]` - `agent.model-turn-cancelled`: a warning carrying the scalar fields of the cancellation outcome. · *Called by:* `developer_tools/diagnose.py::<module>`
- `_listener_failed(event: TelemetryEvent) -> list[DiagnosisFinding]` - `agent.stream-listener-failed`: a warning with the exception type and message. · *Called by:* `developer_tools/diagnose.py::<module>`
- `_checkpoint_failed(event: TelemetryEvent) -> list[DiagnosisFinding]` - `run.checkpoint-failed`: a warning with the error code or type and the message. · *Called by:* `developer_tools/diagnose.py::<module>`
- `_graph_executed(event: TelemetryEvent) -> list[DiagnosisFinding]` - `graph.executed` that is not `completed`: an error naming the failed and the blocked node ids. · *Called by:* `developer_tools/diagnose.py::<module>`
- `_stage_failure(event: TelemetryEvent) -> list[DiagnosisFinding]` - `controller.stage-failure`: an error carrying the controller's reason. · *Called by:* `developer_tools/diagnose.py::<module>`
- `_controller_cancelled(event: TelemetryEvent) -> list[DiagnosisFinding]` - `controller.cancelled`: a warning with the reason. · *Called by:* `developer_tools/diagnose.py::<module>`
- `_provenance(event: TelemetryEvent) -> list[DiagnosisFinding]` - `provenance.checked` with status `rejected`: an error. · *Called by:* `developer_tools/diagnose.py::<module>`
- `_agent_verdict(status: str | None) -> Verdict` - `incomplete` with no terminal event, `completed`, `blocked` or `cancelled` for those statuses and `failed` for any other. · *Called by:* `developer_tools/diagnose.py::_diagnose`
- `_graph_diagnosis(run_id: str, graph: StateGraph, telemetry: TelemetryStore | None) -> GraphDiagnosis` - Rebuilds the statuses, takes as roots the nodes that failed, were cancelled or are blocked by their own result, orders them by the last graph event that set their status, caps them, lists the unfinished nodes and the dependency-blocked nodes that have no root cause. · *Called by:* `developer_tools/diagnose.py::_diagnose`
- `_root_failure(node_id: str, graph: StateGraph, status: GraphNodeStatus, last_event: dict[tuple[str, str], int], telemetry: TelemetryStore | None) -> GraphRootFa...` - One root: its result, its blocked descendants (capped) and, through the `agent-run:` diagnostic and that run's telemetry, the agent's terminal finding and first problem. · *Called by:* `developer_tools/diagnose.py::_graph_diagnosis`
- `_graph_verdict(graph: GraphDiagnosis) -> Verdict` - `failed` when any node failed, else `cancelled`, `blocked`, `incomplete` (nodes still unfinished) or `completed`. · *Called by:* `developer_tools/diagnose.py::_diagnose`
- `_summary(run_id: str, verdict: Verdict, terminal: DiagnosisFinding | None, first_problem: DiagnosisFinding | None, first_error: DiagnosisFinding | None, gr...` - One bounded sentence: the evidence problem first when it is not intact, then what ended the run or the first root and what it held up, then the first error, or a recoverable problem of a completed run, when it differs. · *Called within this file by:* `developer_tools/diagnose.py::_diagnose`
- `_code_suffix(finding: DiagnosisFinding) -> str` - ` (CODE)` for a finding that has a code, else nothing. · *Called by:* `developer_tools/diagnose.py::_summary`
- `_graph_sentence(run_id: str, verdict: Verdict, graph: GraphDiagnosis) -> str` - The first root with its status, code and reason, how many other nodes stopped independently and how many nodes were blocked downstream. · *Called by:* `developer_tools/diagnose.py::_summary`

**Algorithms & invariants.** A finding is produced only for the event types in `_EXTRACTORS`; everything else is background. Errors are a failed terminal event, a rejected verification, a failed tool call, a graph execution that failed or blocked, a stage failure and a rejected provenance contract; warnings are a cancelled run, a blocked tool call, a rejected candidate output, a retried or cancelled model turn, a failed stream listener, a failed checkpoint and a cancelled controller. A recovered failure is still a finding: a run can be `completed` and `ok` with a `first_error` from an earlier iteration. The verdict comes from the graph record when there is one (failed, then cancelled, blocked, incomplete, completed) and from the terminal event otherwise; `ok` also needs both chains to verify and a terminal link that is not broken. The reason of a tool call that never ran (bad arguments, a hook denial) is in the ledger only because `tool-batch-completed` carries each failed outcome's code and a message of at most 300 characters; a run recorded before that has the status alone. Output is bounded: 50 findings, 50 roots, 100 listed descendants per root and 50 unfinished nodes, each with the count left out. Nothing is written: telemetry and audit open read-only, `RunStateStore(read_only=True)` creates no directory, and the only files a read can add are SQLite's own `-wal` and `-shm` side files, as `inspect_run` does.

---

### `developer_tools/inspect.py` - read-only run evidence inspection

*211 lines · depends on: `agent/retention.py`, `foundations/contracts.py`, `foundations/errors.py`, `memory/context_projection.py`, `observability/audit_log.py`, `observability/telemetry_store.py`, `state/project_state_store.py` · used by: `developer_tools/__init__.py`, `developer_tools/cli.py` · not re-exported at the package root*

**Role in the workflow.** Verifies the telemetry and audit chains of one run and summarises event types, statuses and metric availability from every event; `verify_project_evidence` cross-checks a project's recorded tool-result hashes against the result journal. Both open the stores read-only, so a mistyped path creates nothing.

**Contents**

- **class `RunInspection`** *(pydantic model; bases: StrictModel)* - Chain validity flags with the first failure of each chain, event and metric counts, breakdowns, audit entry count and an evidence report with event hashes. · *Instantiated by:* `developer_tools/inspect.py::inspect_run`
  - fields: `schema_version`, `run_id`, `telemetry_chain_valid`, `audit_chain_valid`, `telemetry_chain_failure`, `audit_chain_failure`, `event_count`, `metric_count`, `event_types`, `statuses`, `metric_availability`, `audit_entry_count`, `report`
- **class `EvidenceMismatch`** *(pydantic model; bases: StrictModel)* - One recorded tool-result handle that disagrees with the journal: the evidence id, the project-state revision that recorded it, the recorded hash, the hash the journal file produces (None if unreadable) and a reason naming both. · *Instantiated by:* `developer_tools/inspect.py::verify_project_evidence`
  - fields: `evidence_id`, `revision`, `recorded_hash`, `journal_hash`, `reason`
- **class `EvidenceVerification`** *(pydantic model; bases: StrictModel)* - Project id, how many distinct tool-result evidence records were checked, `verified` (true when none mismatched) the mismatches and how many handles were accepted through a retention tombstone (`pruned`). · *Instantiated by:* `developer_tools/inspect.py::verify_project_evidence`
  - fields: `schema_version`, `project_id`, `checked`, `verified`, `mismatches`, `pruned`
- `verify_project_evidence(run_root: Path, project_id: str) -> EvidenceVerification` - Opens the project-state store read-only (an unknown project or run root raises), walks every event's `tool-result-handle` evidence once per distinct (id, hash) and recomputes each handle's hash from `.agent-tool-results/` with `journal_content_hash`; a missing, corrupt or different file becomes an `EvidenceMismatch`. Detects journal files overwritten after the evidence was recorded. A handle whose journal file is gone is accepted when a retention tombstone names it with the hash the project state recorded (counted in `pruned`); it is a mismatch when the hashes differ, when no tombstone names it or when the tombstone chain is broken (the reason says so). · *Called by:* `developer_tools/cli.py::_verify_evidence`
- `inspect_run(run_root: Path, run_id: str) -> RunInspection` - Requires an existing run root with a telemetry database (otherwise a `ValueError` naming the root or database), opens the stores read-only, streams every event of the run (page by page), reads all metrics and counts every audit entry, verifies both chains and builds the summary; an unknown run raises. A root without audit logs reports zero entries and gets no audit directory. · *Called by:* `developer_tools/cli.py::_inspect_run`
- `_unknown_run_message(run_root: Path, run_id: str) -> str` - `Telemetry run "<id>" is unknown.`, or, when a retention tombstone names the run, that it was pruned, when, by which tombstone, and how many events it held and the hash they ended at. · *Called within this file by:* `developer_tools/inspect.py::inspect_run`
- `_counts(values: Iterable[str]) -> dict[str, int]` - Sorted occurrence counts of an iterable of strings. · *Called by:* `developer_tools/inspect.py::inspect_run`
- `_sorted(counts: Counter[str]) -> dict[str, int]` - A counter as a dict sorted by key. · *Called by:* `developer_tools/inspect.py::_counts`, `developer_tools/inspect.py::inspect_run`

---

### `developer_tools/quality.py` - closed Ruff quality check

*92 lines · depends on: `foundations/contracts.py` · used by: `developer_tools/__init__.py`, `developer_tools/cli.py` · not re-exported at the package root*

**Role in the workflow.** Runs `ruff check` (F401, F811, E, F, I, UP) and optionally `ruff format --check` over the checkout's `src`, `tests`, `examples` and `scripts` directories.

**Contents**

- **class `RuffUnavailableError`** *(exception; bases: RuntimeError)* - Ruff is not on PATH; the CLI reports it as an operational error. · *Instantiated by:* `developer_tools/quality.py::check_source_quality`
- **class `QualityCheckReport`** *(pydantic model; bases: StrictModel)* - Checked paths, pass flag, return code, diagnostics text and whether the formatter ran. · *Instantiated by:* `developer_tools/quality.py::check_source_quality`
  - fields: `schema_version`, `checkout_root`, `checked_paths`, `passed`, `return_code`, `diagnostics`, `formatter_checked`
- `check_source_quality(checkout_root: Path, *, check_format: bool=True) -> QualityCheckReport` - Requires `ruff` on PATH (`RuffUnavailableError` otherwise) and a checkout containing `src/nailong_agent_sdk`; runs the commands in order and stops at the first failure, decoding Ruff's output as UTF-8 with replacement so a non-ASCII diagnostic is reported faithfully on any locale. · *Called by:* `developer_tools/cli.py::_quality`
- `_checked_paths(root: Path) -> list[str]` - The existing standard directories; raises quoting the root when the package directory is absent. · *Called by:* `developer_tools/quality.py::check_source_quality`

---

### `developer_tools/validate.py` - contract-file validation

*97 lines · depends on: `foundations/contracts.py`, `specifications/documents.py`, `state/planning.py` · used by: `developer_tools/__init__.py`, `developer_tools/cli.py` · not re-exported at the package root*

**Role in the workflow.** `validate` checks local JSON or YAML files against the SDK's pydantic contracts, without running anything.

**Contents**

- **class `ValidationIssue`** *(pydantic model; bases: StrictModel)* - One failing field path, message and error type. · *Instantiated by:* `developer_tools/validate.py::validate_contract_file`
  - fields: `path`, `message`, `error_type`
- **class `ValidationReport`** *(pydantic model; bases: StrictModel)* - Artifact type, path, validity and the issues. · *Instantiated by:* `developer_tools/validate.py::validate_contract_file`
  - fields: `schema_version`, `artifact_type`, `path`, `valid`, `issues`
- `supported_contract_types() -> tuple[str, ...]` - Sorted names: `agent-definition`, `plan`, `scoped-task`, `specification-manifest`. · *Called by:* `developer_tools/cli.py::_parser`, `developer_tools/validate.py::validate_contract_file`
- `validate_contract_file(path: Path, artifact_type: str) -> ValidationReport` - Loads the file and validates it against the chosen contract, returning field-level issues; an unsupported type, a missing file, a wrong suffix or content that is not valid JSON/YAML raises a message naming the file. · *Called by:* `developer_tools/cli.py::_validate`
- `_load_structured_file(path: Path) -> Any` - Reads `.json`, `.yaml` or `.yml` (a UTF-8 byte-order mark is accepted); a missing file, another suffix or a parse error raises, quoting the path and the parser's message. · *Called by:* `developer_tools/validate.py::validate_contract_file`

*Module-level names:* `_CONTRACTS`
