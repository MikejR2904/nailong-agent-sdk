# `developer_tools/` - read-only maintainer utilities and the `nailong-agent-sdk-dev` command

Five deterministic helpers behind one CLI: generate the public API catalogue from `__all__`, validate a JSON or YAML contract file, verify and summarise a run's evidence chains without writing anything, cross-check a project's recorded tool-result hashes against the result journal, and run the closed Ruff quality check. None of them runs a model or a tool. The CLI prints JSON and exits 0 on success, 1 when a verdict field of the result (`valid`, `passed`, `telemetry_chain_valid`, `audit_chain_valid`, `verified`) is false, 2 for a command-line usage error and 3 when the input could not be read or the check could not run (a one-line message on stderr, no traceback).

| File | Lines | Role |
|---|---:|---|
| [`developer_tools/__init__.py`](#developer_tools__init__py---public-surface-of-the-developer-utilities) | 42 | public surface of the developer utilities |
| [`developer_tools/catalog.py`](#developer_toolscatalogpy---public-api-catalogue-generation) | 88 | public API catalogue generation |
| [`developer_tools/cli.py`](#developer_toolsclipy---the-nailong-agent-sdk-dev-command-line) | 181 | the `nailong-agent-sdk-dev` command line |
| [`developer_tools/inspect.py`](#developer_toolsinspectpy---read-only-run-evidence-inspection) | 211 | read-only run evidence inspection |
| [`developer_tools/quality.py`](#developer_toolsqualitypy---closed-ruff-quality-check) | 92 | closed Ruff quality check |
| [`developer_tools/validate.py`](#developer_toolsvalidatepy---contract-file-validation) | 97 | contract-file validation |

---

### `developer_tools/__init__.py` - public surface of the developer utilities

*42 lines · depends on: `developer_tools/catalog.py`, `developer_tools/inspect.py`, `developer_tools/quality.py`, `developer_tools/validate.py` · used by: no other module (entry point or re-exported only) · not re-exported at the package root*

**Role in the workflow.** Re-exports the catalogue, validation, inspection, evidence verification and quality functions and report types.

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

*181 lines · depends on: `agent/retention.py`, `developer_tools/catalog.py`, `developer_tools/inspect.py`, `developer_tools/quality.py`, `developer_tools/validate.py`, `foundations/errors.py` · used by: no other module (entry point or re-exported only) · not re-exported at the package root*

**Role in the workflow.** Subcommands `catalog`, `validate`, `inspect-run`, `verify-evidence` and `quality`; each prints its report as JSON. Exit status: 0 success, 1 the artifact was inspected and is invalid, failed or has a broken chain, 2 usage error (argparse), 3 operational error. `prune-runs` is the one command that can change a run root, and only with `--apply`.

**Contents**

- `main(argv: list[str] | None=None) -> int` - Parses arguments and runs the subcommand. An operational error (`OSError`, `ValueError`, `yaml.YAMLError`, `sqlite3.Error` or a missing Ruff) prints `nailong-agent-sdk-dev <command>: <ExceptionType>: <message>` to stderr and returns 3; otherwise it prints the result as JSON and returns 1 if any verdict field is false, else 0. · *Called within this file by:* `developer_tools/cli.py::<module>`
- `_parser() -> argparse.ArgumentParser` - Builds the argument parser (subcommands `catalog`, `validate`, `inspect-run`, `verify-evidence`, `quality`; the epilog documents the exit statuses). · *Called by:* `developer_tools/cli.py::main`
- `_catalog(arguments: argparse.Namespace) -> object` - Handler for `catalog`, writing a file when `--output` is given. · *Called by:* `developer_tools/cli.py::main`
- `_validate(arguments: argparse.Namespace) -> object` - Handler for `validate`. · *Called by:* `developer_tools/cli.py::main`
- `_inspect_run(arguments: argparse.Namespace) -> object` - Handler for `inspect-run`. · *Called by:* `developer_tools/cli.py::main`
- `_verify_evidence(arguments: argparse.Namespace) -> object` - Handler for `verify-evidence`. · *Called by:* `developer_tools/cli.py::main`
- `_prune_runs(arguments: argparse.Namespace) -> object` - `prune-runs <run_root> --older-than-seconds N [--keep-most-recent K] [--max-runs M] [--include-unfinished] [--export-reports] [--apply]`: builds a `RetentionPolicy` and calls `prune_run_root`. Without `--apply` it is a dry run that writes nothing; the JSON is the `RetentionReport`, and the exit status is 1 when it lists a problem (a broken chain), 3 for an invalid policy, a missing run root or a failure to prune. · *Called by:* `developer_tools/cli.py::main`
- `_quality(arguments: argparse.Namespace) -> object` - Handler for `quality`. · *Called by:* `developer_tools/cli.py::main`
- `_exit_status(result: object) -> int` - 1 if any of `valid`, `passed`, `telemetry_chain_valid`, `audit_chain_valid`, `verified` is False on the result, else 0. · *Called by:* `developer_tools/cli.py::main`
- `_to_json(value: object) -> object` - Converts a pydantic result to JSON-compatible data. · *Called by:* `developer_tools/cli.py::main`

*Module-level names:* `_VERDICT_FIELDS`

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
- `_unknown_run_message(run_root: Path, run_id: str) -> str` - `Telemetry run "<id>" is unknown.`, or, when a retention tombstone names the run, that it was pruned, when, by which tombstone, and how many events it held and the hash they ended at. · *Called by:* `developer_tools/inspect.py::inspect_run`
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
