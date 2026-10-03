# `developer_tools/` - read-only maintainer utilities and the `nailong-agent-sdk-dev` command

Four deterministic helpers behind one CLI: generate the public API catalogue from `__all__`, validate a JSON or YAML contract file, verify and summarise a run's evidence chains, and run the closed Ruff quality check. None of them runs a model or a tool. The CLI prints JSON and exits 1 when any verdict field of the result (`valid`, `passed`, `telemetry_chain_valid`, `audit_chain_valid`) is false, else 0.

| File | Lines | Role |
|---|---:|---|
| [`developer_tools/__init__.py`](#developer_tools__init__py---public-surface-of-the-developer-utilities) | 33 | public surface of the developer utilities |
| [`developer_tools/catalog.py`](#developer_toolscatalogpy---public-api-catalogue-generation) | 87 | public API catalogue generation |
| [`developer_tools/cli.py`](#developer_toolsclipy---the-nailong-agent-sdk-dev-command-line) | 98 | the `nailong-agent-sdk-dev` command line |
| [`developer_tools/inspect.py`](#developer_toolsinspectpy---read-only-run-evidence-inspection) | 84 | read-only run evidence inspection |
| [`developer_tools/quality.py`](#developer_toolsqualitypy---closed-ruff-quality-check) | 85 | closed Ruff quality check |
| [`developer_tools/validate.py`](#developer_toolsvalidatepy---contract-file-validation) | 92 | contract-file validation |

---

### `developer_tools/__init__.py` - public surface of the developer utilities

*33 lines · depends on: `developer_tools/catalog.py`, `developer_tools/inspect.py`, `developer_tools/quality.py`, `developer_tools/validate.py` · used by: no other module (entry point or re-exported only) · not re-exported at the package root*

**Role in the workflow.** Re-exports the catalogue, validation, inspection and quality functions and report types.

---

### `developer_tools/catalog.py` - public API catalogue generation

*87 lines · depends on: `foundations/contracts.py` · used by: `developer_tools/__init__.py`, `developer_tools/cli.py` · not re-exported at the package root*

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

*98 lines · depends on: `developer_tools/catalog.py`, `developer_tools/inspect.py`, `developer_tools/quality.py`, `developer_tools/validate.py` · used by: no other module (entry point or re-exported only) · not re-exported at the package root*

**Role in the workflow.** Subcommands `catalog`, `validate`, `inspect-run` and `quality`; each prints its report as JSON.

**Contents**

- `main(argv: list[str] | None=None) -> int` - Parses arguments, runs the subcommand, prints the result as JSON and returns 1 if any verdict field (`valid`, `passed`, `telemetry_chain_valid`, `audit_chain_valid`) is false, else 0. · *Called within this file by:* `developer_tools/cli.py::<module>`
- `_parser() -> argparse.ArgumentParser` - Builds the argument parser. · *Called by:* `developer_tools/cli.py::main`
- `_catalog(arguments: argparse.Namespace) -> object` - Handler for `catalog`, writing a file when `--output` is given. · *Called by:* `developer_tools/cli.py::main`
- `_validate(arguments: argparse.Namespace) -> object` - Handler for `validate`. · *Called by:* `developer_tools/cli.py::main`
- `_inspect_run(arguments: argparse.Namespace) -> object` - Handler for `inspect-run`. · *Called by:* `developer_tools/cli.py::main`
- `_quality(arguments: argparse.Namespace) -> object` - Handler for `quality`. · *Called by:* `developer_tools/cli.py::main`
- `_exit_status(result: object) -> int` - 1 if any of `valid`, `passed`, `telemetry_chain_valid`, `audit_chain_valid` is False on the result, else 0. · *Called by:* `developer_tools/cli.py::main`
- `_to_json(value: object) -> object` - Converts a pydantic result to JSON-compatible data. · *Called by:* `developer_tools/cli.py::main`

*Module-level names:* `_VERDICT_FIELDS`

---

### `developer_tools/inspect.py` - read-only run evidence inspection

*84 lines · depends on: `foundations/contracts.py`, `observability/audit_log.py`, `observability/telemetry_store.py` · used by: `developer_tools/__init__.py`, `developer_tools/cli.py` · not re-exported at the package root*

**Role in the workflow.** Verifies the telemetry and audit chains of one run and summarises event types, statuses and metric availability.

**Contents**

- **class `RunInspection`** *(pydantic model; bases: StrictModel)* - Chain validity flags with the first failure of each chain, event and metric counts, breakdowns, audit entry count and an evidence report with event hashes. · *Instantiated by:* `developer_tools/inspect.py::inspect_run`
  - fields: `schema_version`, `run_id`, `telemetry_chain_valid`, `audit_chain_valid`, `telemetry_chain_failure`, `audit_chain_failure`, `event_count`, `metric_count`, `event_types`, `statuses`, `metric_availability`, `audit_entry_count`, `report`
- `inspect_run(run_root: Path, run_id: str) -> RunInspection` - Opens the stores, reads up to 1,000 events, all metrics and up to 10,000 audit entries, verifies both chains and builds the summary; an unknown run raises. The counts are capped at those limits without saying so, and opening the stores creates them if the root had none. · *Called by:* `developer_tools/cli.py::_inspect_run`
- `_counts(values: Iterable[str]) -> dict[str, int]` - Sorted occurrence counts. · *Called by:* `developer_tools/inspect.py::inspect_run`

---

### `developer_tools/quality.py` - closed Ruff quality check

*85 lines · depends on: `foundations/contracts.py` · used by: `developer_tools/__init__.py`, `developer_tools/cli.py` · not re-exported at the package root*

**Role in the workflow.** Runs `ruff check` (F401, F811, E, F, I, UP) and optionally `ruff format --check` over the checkout's `src`, `tests`, `examples` and `scripts` directories.

**Contents**

- **class `QualityCheckReport`** *(pydantic model; bases: StrictModel)* - Checked paths, pass flag, return code, diagnostics text and whether the formatter ran. · *Instantiated by:* `developer_tools/quality.py::check_source_quality`
  - fields: `schema_version`, `checkout_root`, `checked_paths`, `passed`, `return_code`, `diagnostics`, `formatter_checked`
- `check_source_quality(checkout_root: Path, *, check_format: bool=True) -> QualityCheckReport` - Requires `ruff` on PATH and a checkout containing `src/nailong_agent_sdk`; runs the commands in order and stops at the first failure. · *Called by:* `developer_tools/cli.py::_quality`
- `_checked_paths(root: Path) -> list[str]` - The existing standard directories, raising if the package directory is absent. · *Called by:* `developer_tools/quality.py::check_source_quality`

---

### `developer_tools/validate.py` - contract-file validation

*92 lines · depends on: `foundations/contracts.py`, `specifications/documents.py`, `state/planning.py` · used by: `developer_tools/__init__.py`, `developer_tools/cli.py` · not re-exported at the package root*

**Role in the workflow.** `validate` checks local JSON or YAML files against the SDK's pydantic contracts, without running anything.

**Contents**

- **class `ValidationIssue`** *(pydantic model; bases: StrictModel)* - One failing field path, message and error type. · *Instantiated by:* `developer_tools/validate.py::validate_contract_file`
  - fields: `path`, `message`, `error_type`
- **class `ValidationReport`** *(pydantic model; bases: StrictModel)* - Artifact type, path, validity and the issues. · *Instantiated by:* `developer_tools/validate.py::validate_contract_file`
  - fields: `schema_version`, `artifact_type`, `path`, `valid`, `issues`
- `supported_contract_types() -> tuple[str, ...]` - Sorted names: `agent-definition`, `plan`, `scoped-task`, `specification-manifest`. · *Called by:* `developer_tools/cli.py::_parser`, `developer_tools/validate.py::validate_contract_file`
- `validate_contract_file(path: Path, artifact_type: str) -> ValidationReport` - Loads the file and validates it against the chosen contract, returning field-level issues; an unsupported type or unreadable file raises. · *Called by:* `developer_tools/cli.py::_validate`
- `_load_structured_file(path: Path) -> Any` - Reads `.json`, `.yaml` or `.yml`; a missing file or another suffix raises. · *Called by:* `developer_tools/validate.py::validate_contract_file`

*Module-level names:* `_CONTRACTS`

