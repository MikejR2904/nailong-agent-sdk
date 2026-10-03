# `tools/` - governed tool execution: what an agent may do and how it is done

Everything between a model's tool request and the real world. `tools.py` defines the `ToolExecutor` protocol `BaseAgent` calls. `registry.py` is the governed implementation: it checks the capability `policy.py` (role, capability, path containment, typed `approvals.py`) and then dispatches to the portable `core/` tools, to registered process commands run by `supervisor.py` (optionally inside the `sandbox.py` backends), or to host-supplied handlers. `artifacts.py` stores content-addressed drafts with write attribution. `tasks.py`, `delegation.py` and `worktrees.py` run background work and delegated sub-runs in isolated git worktrees.

| File | Lines | Role |
|---|---:|---|
| [`tools/__init__.py`](#tools__init__py---package-marker-for-governed-tool-execution) | 3 | package marker for governed tool execution |
| [`tools/approvals.py`](#toolsapprovalspy---typed-approval-gates-for-state-changing-actions) | 73 | typed approval gates for state-changing actions |
| [`tools/artifacts.py`](#toolsartifactspy---content-addressed-artifact-store-with-immutable-write-attribution) | 255 | content-addressed artifact store with immutable write attribution |
| [`tools/core/__init__.py`](#toolscore__init__py---public-surface-of-the-portable-core-tools) | 25 | public surface of the portable core tools |
| [`tools/core/definitions.py`](#toolscoredefinitionspy---typed-declarations-of-the-governed-core-tool-set) | 288 | typed declarations of the governed core tool set |
| [`tools/core/helpers.py`](#toolscorehelperspy---private-validation-http-and-isolated-regex-helpers-behind-the-core-tools) | 436 | private validation, HTTP and isolated-regex helpers behind the core tools |
| [`tools/core/services.py`](#toolscoreservicespy---portable-governed-tools-dispatcher-services-and-web-search) | 437 | portable governed tools: dispatcher, services and web search |
| [`tools/delegation.py`](#toolsdelegationpy---delegated-sub-runs-on-isolated-git-worktrees) | 113 | delegated sub-runs on isolated git worktrees |
| [`tools/policy.py`](#toolspolicypy---deny-by-default-capability-policy) | 153 | deny-by-default capability policy |
| [`tools/registry.py`](#toolsregistrypy---capability-bound-harness-tool-registry-and-its-baseagent-executor) | 323 | capability-bound harness tool registry and its BaseAgent executor |
| [`tools/sandbox.py`](#toolssandboxpy---pluggable-execution-backends-for-registered-command-templates) | 247 | pluggable execution backends for registered command templates |
| [`tools/sandbox_models.py`](#toolssandbox_modelspy---typed-configuration-for-sandbox-backends) | 50 | typed configuration for sandbox backends |
| [`tools/supervisor.py`](#toolssupervisorpy---registered-command-execution-with-timeout-bounded-output-and-process-tree-kill) | 437 | registered-command execution with timeout, bounded output and process-tree kill |
| [`tools/task_models.py`](#toolstask_modelspy---records-for-background-tasks) | 43 | records for background tasks |
| [`tools/tasks.py`](#toolstaskspy---background-task-lifecycle-start-poll-stop) | 208 | background task lifecycle: start, poll, stop |
| [`tools/tools.py`](#toolstoolspy---the-toolexecutor-protocol-and-two-deterministic-test-executors) | 72 | the ToolExecutor protocol and two deterministic test executors |
| [`tools/worktree_models.py`](#toolsworktree_modelspy---record-for-an-agents-git-worktree) | 25 | record for an agent's git worktree |
| [`tools/worktrees.py`](#toolsworktreespy---git-worktree-isolation-for-concurrent-agents) | 145 | git worktree isolation for concurrent agents |

---

### `tools/__init__.py` - package marker for governed tool execution

*3 lines · depends on: nothing in the package · used by: no other module (entry point or re-exported only) · not re-exported at the package root*

**Role in the workflow.** Docstring only.

---

### `tools/approvals.py` - typed approval gates for state-changing actions

*73 lines · depends on: `foundations/contracts.py` · used by: `state/harness_coordinator.py`, `tools/policy.py`, `tools/registry.py` · re-exported at the package root: 3 name(s)*

**Role in the workflow.** When `CapabilityPolicy` says a mutating or process capability needs approval, `HarnessToolExecutor` files a request here and returns a BLOCKED tool result carrying the approval id; a human or controller later answers it (MCP `submit_approval`) and the run is resumed.

**Contents**

- **class `ApprovalStatus`** *(enum; bases: StrEnum)* - pending, approved or rejected.
  - members: `PENDING`, `APPROVED`, `REJECTED`
- **class `ApprovalRequest`** *(pydantic model; bases: StrictModel)* - One request: id, run, node, capability, reason, status and the decision reason. · *Instantiated by:* `tools/approvals.py::ApprovalRegistry.request`
  - fields: `approval_id`, `run_id`, `node_id`, `capability`, `reason`, `status`, `decision_reason`
- **class `ApprovalRegistry`** *(class)* - In-memory owner of approval state as typed data rather than conversation text. · *Instantiated by:* `state/harness_coordinator.py::HarnessCoordinator.get_run_state`, `state/harness_coordinator.py::HarnessCoordinator.start_run`
  - `ApprovalRegistry.__init__() -> None` - Starts empty with the id counter at 1.
  - `ApprovalRegistry.request(run_id: str, node_id: str, capability: str, reason: str) -> ApprovalRequest` - Creates a pending `approval-N` request for a run/node/capability. · *Called by:* `openai_compatible/transport.py::UrlLibJsonTransport.post_json`, `orchestrator/orchestrator.py::Orchestrator._assign_workers`, `orchestrator/orchestrator.py::Orchestrator._build_bindings`, `orchestrator/orchestrator.py::Orchestrator._emit` (+30 more)
  - `ApprovalRegistry.submit(approval_id: str, approved: bool, reason: str | None=None) -> ApprovalRequest` - Records approve/reject exactly once; unknown ids and already-decided requests raise. · *Called by:* `state/harness_coordinator.py::HarnessCoordinator.submit_approval`
  - `ApprovalRegistry.get(approval_id: str) -> ApprovalRequest | None` - Returns a request by id or None. · *Called within this file by:* `tools/approvals.py::ApprovalRegistry.submit`
  - `ApprovalRegistry.list(run_id: str | None=None) -> list[ApprovalRequest]` - All requests (optionally for one run) sorted by id.

---

### `tools/artifacts.py` - content-addressed artifact store with immutable write attribution

*255 lines · depends on: `foundations/atomic_io.py`, `foundations/contracts.py` · used by: `tools/core/services.py`, `tools/registry.py` · re-exported at the package root: 3 name(s)*

**Role in the workflow.** `write_draft`/`edit_draft` write declared outputs through this store; `read_artifact`, `grep_artifact` and `diff_declared_artifacts` read them back by `sha256:` id. Each write also leaves an immutable occurrence record naming the run, node and task, which later proves that a draft was authored by the current task.

**Contents**

- **class `ArtifactRecord`** *(pydantic model; bases: StrictModel)* - Content identity (id, path, SHA-256, size, kind, manifest) plus the occurrence that produced the returned record. · *Instantiated by:* `tools/artifacts.py::ArtifactStore._register`
  - fields: `artifact_id`, `relative_path`, `sha256`, `size_bytes`, `kind`, `manifest`, `occurrence_id`
- **class `ArtifactWriteOccurrence`** *(pydantic model; bases: StrictModel)* - Immutable attribution of one write: occurrence id, artifact id, path, hash, size, kind, writer manifest and UTC time. · *Instantiated by:* `tools/artifacts.py::ArtifactStore._register`
  - fields: `occurrence_id`, `artifact_id`, `relative_path`, `sha256`, `size_bytes`, `kind`, `manifest`, `written_at_utc`
- **class `ArtifactStore`** *(class)* - Persists artifacts under one run root: output files, immutable content blobs, one manifest per content id and one occurrence record per write.
  - `ArtifactStore.__init__(run_root: Path) -> None` - Creates the run root and the `.agent-artifacts/{content,occurrences}` directories and an RLock.
  - `ArtifactStore.root() -> Path` *(property)* - Property: the resolved run root.
  - `ArtifactStore.write_text(relative_path: str, content: str, *, kind: str='draft', manifest: dict[str, Any] | None=None) -> ArtifactRecord` - Writes the UTF-8 content to the (root-contained) output path atomically, then registers it; returns the record. · *Called by:* `orchestrator/state_store.py::OrchestrationStateStore.save`, `orchestrator/state_store.py::OrchestrationStateStore.save_policy`, `developer_tools/catalog.py::write_public_api_catalog`, `foundations/benchmarks.py::write_pckp_benchmark_report` (+11 more)
  - `ArtifactStore.register_existing(relative_path: str, *, kind: str, manifest: dict[str, Any] | None=None) -> ArtifactRecord` - Registers a file that already exists below the root (error if it does not). · *No in-package callers (public API, entry point, or protocol hook).*
  - `ArtifactStore.read_text(artifact_id: str) -> str` - Reads from the immutable content blob (falling back to the output path for legacy records) and verifies the SHA-256 before returning text. · *Called by:* `orchestrator/state_store.py::OrchestrationStateStore.load`, `orchestrator/state_store.py::OrchestrationStateStore.load_policy`, `orchestrator/state_store.py::OrchestrationStateStore.save_policy`, `developer_tools/validate.py::_load_structured_file` (+24 more)
  - `ArtifactStore.get(artifact_id: str) -> ArtifactRecord | None` - Loads the artifact manifest by id, or None. · *Called within this file by:* `tools/artifacts.py::ArtifactStore.occurrence_matches_task_draft`, `tools/artifacts.py::ArtifactStore.read_text`
  - `ArtifactStore.get_occurrence(occurrence_id: str) -> ArtifactWriteOccurrence | None` - Loads one occurrence record, or None. · *Called by:* `tools/artifacts.py::ArtifactStore.occurrence_matches_task_draft`
  - `ArtifactStore.occurrence_matches_task_draft(occurrence_id: str, artifact_id: str, *, run_id: str, node_id: str, task_id: str, declared_output_paths: tuple[str, ...]) -> bool` - True only if the occurrence is for that artifact, wrote a declared output path, and its manifest names the given run, node and task. · *Called by:* `tools/registry.py::HarnessToolExecutor._execute_registered`
  - `ArtifactStore.diff(base_artifact_id: str, draft_artifact_id: str) -> dict[str, Any]` - Unified diff of two artifacts' text, returned with both ids. · *Called by:* `specifications/git_versioning.py::SpecificationVersionService.classify`, `specifications/git_versioning.py::_major_rationale`, `core/services.py::CoreToolDispatcher._diff`, `tools/registry.py::HarnessToolExecutor._execute_registered`
  - `ArtifactStore._register(target: Path, relative_path: str, content: bytes, kind: str, manifest: dict[str, Any]) -> ArtifactRecord` - Hashes content, saves the blob once, saves the manifest on first registration, and always writes a new unique occurrence record. · *Called within this file by:* `tools/artifacts.py::ArtifactStore.register_existing`, `tools/artifacts.py::ArtifactStore.write_text`
  - `ArtifactStore._content_path(digest: str) -> Path` - Path of the immutable blob for a digest. · *Called by:* `tools/artifacts.py::ArtifactStore._register`, `tools/artifacts.py::ArtifactStore.read_text`
  - `ArtifactStore._manifest_path(artifact_id: str) -> Path` - Portable manifest filename for an artifact id (colon replaced). · *Called by:* `tools/artifacts.py::ArtifactStore._existing_manifest_path`, `tools/artifacts.py::ArtifactStore._register`
  - `ArtifactStore._existing_manifest_path(artifact_id: str) -> Path | None` - Finds a manifest under the portable name or the legacy colon name. · *Called by:* `tools/artifacts.py::ArtifactStore._register`, `tools/artifacts.py::ArtifactStore.get`
  - `ArtifactStore._resolve_relative(relative_path: str) -> Path` - Resolves a relative path under the run root, rejecting absolute, empty and root-escaping paths. · *Called by:* `tools/artifacts.py::ArtifactStore.read_text`, `tools/artifacts.py::ArtifactStore.register_existing`, `tools/artifacts.py::ArtifactStore.write_text`
  - `ArtifactStore._atomic_write_bytes(target: Path, content: bytes) -> None` *(staticmethod)* - Writes bytes to a temp file then replaces the target with retry. · *Called by:* `tools/artifacts.py::ArtifactStore._register`, `tools/artifacts.py::ArtifactStore.write_text`
- `_safe_name(value: str) -> str` - Filename-safe version of an id. · *Called within this file by:* `tools/artifacts.py::ArtifactStore._manifest_path`
- `_replace_with_retry(temporary: Path, target: Path, *, attempts: int=5) -> None` - Compatibility wrapper around `replace_atomic`. · *Called within this file by:* `tools/artifacts.py::ArtifactStore._atomic_write_bytes`

**Algorithms & invariants.** `artifact_id` is the content hash (deduplicating); attribution lives in the per-write occurrence so identical bytes written by different runs never overwrite each other's history.

---

### `tools/core/__init__.py` - public surface of the portable core tools

*25 lines · depends on: `tools/core/definitions.py`, `tools/core/services.py` · used by: `tools/registry.py` · not re-exported at the package root*

**Role in the workflow.** Re-exports `CoreToolDispatcher`, `CoreToolServices`, the search client types and `core_tool_definitions`.

---

### `tools/core/definitions.py` - typed declarations of the governed core tool set

*288 lines · depends on: `foundations/contracts.py` · used by: `tools/core/__init__.py` · re-exported at the package root: 1 name(s)*

**Role in the workflow.** A host opts into the core tools by copying these `ToolDefinition`s into `AgentDefinition.tools`; the model sees their names, descriptions and schemas.

**Contents**

- `core_tool_definitions() -> list[ToolDefinition]` - Returns the 17 declarations: read_file, glob, grep, write_draft, edit_draft, read_artifact, grep_artifact, diff_declared_artifacts, get_tool_result, web_fetch, render_pdf_page, web_search, sleep, ask_human_question, brief, notebook_edit, run_registered_command. · *No in-package callers (public API, entry point, or protocol hook).*
  - `core_tool_definitions.definition(name: str, description: str, schema: dict[str, Any], *, write: bool=False) -> ToolDefinition` - Builds one `ToolDefinition`: write tools are action/serial, all others exploratory/parallel-safe. · *Called by:* `base_agent/agent.py::BaseAgent.__init__`, `base_agent/agent.py::BaseAgent._accept_final_turn`, `base_agent/agent.py::BaseAgent._execute_tool_call`, `base_agent/agent.py::BaseAgent._record_executed_result` (+23 more)

---

### `tools/core/helpers.py` - private validation, HTTP and isolated-regex helpers behind the core tools

*436 lines · depends on: nothing in the package · used by: `tools/core/services.py` · not re-exported at the package root*

**Role in the workflow.** Called only by `CoreToolDispatcher` (mostly through `asyncio.to_thread`) to implement grep, web_fetch, render_pdf_page and argument validation.

**Contents**

- `_bounded_regex_search(pattern: str, case_sensitive: bool, limit: int, documents: list[tuple[str, str]], timeout_seconds: float) -> dict[str, Any]` - Runs an untrusted regex in a killable spawned child process with a deadline (terminate then kill), so catastrophic backtracking cannot hang the host; maps failures to `GREP_REGEX_*` errors. · *Called by:* `core/services.py::CoreToolDispatcher._grep`
- `_regex_search_worker(results: multiprocessing.Queue[dict[str, Any]], pattern: str, case_sensitive: bool, limit: int, documents: list[tuple[str, str]]) -> None` - Child-process entry point: compiles the pattern, scans documents line by line and returns up to `limit` matches (path, line, first 1,000 chars) or the compile error. · *Called by:* `core/helpers.py::_bounded_regex_search`
- `_http_fetch_hint(status_code: int) -> str` - Returns a ` Likely cause: ...` suffix for common HTTP statuses (401, 403, 404, 410, 429, 5xx) or an empty string. · *Called by:* `core/helpers.py::_fetch_pdf_bytes_with_redirects`, `core/helpers.py::_fetch_public_text`
- `_fetch_public_text(url: str, max_chars: int, *, page: int=1, image_cache: dict[str, tuple[str, bytes]] | None=None, pdf_bytes_cache: dict[str, bytes] | None=None) ->...` - web_fetch implementation: validates every URL, follows at most four manual redirects with no proxy and a 30 s timeout, parses PDFs page by page (cached bytes) and otherwise accepts only text/html/json/xml, returning bounded content marked untrusted. · *Called by:* `core/services.py::CoreToolDispatcher._web_fetch`
- `_to_png_bytes(pil_image: Any) -> bytes | None` - Normalizes a PIL image to PNG bytes (CMYK converted to RGB); returns None if it cannot be encoded. · *Called by:* `core/helpers.py::_parse_pdf_page`, `core/helpers.py::_render_pdf_page`
- `_fetch_pdf_bytes_with_redirects(url: str) -> bytes` - Downloads a PDF (max 20 MB, <= 4 redirects, SSRF-checked) for page rendering. · *Called by:* `core/helpers.py::_render_pdf_page`
- `_render_pdf_page(url: str, page: int, scale: float, pdf_bytes_cache: dict[str, bytes] | None, image_cache: dict[str, tuple[str, bytes]] | None) -> dict[str, Any]` - Renders one PDF page with pypdfium2 at a scale of 1 to 4, stores the PNG in the image cache under a `pdf:<urlhash>:p<N>:page` ref and returns the ref and page count.
- `_parse_pdf_page(raw: bytes, url: str, page: int, max_chars: int, image_cache: dict[str, tuple[str, bytes]] | None) -> dict[str, Any]` - Extracts text with pypdf from the requested page onward until the character budget is hit, lists each page's embedded images (refs cached as PNG) and reports `pages_included`, `next_page` and truncation. · *Called by:* `core/helpers.py::_fetch_public_text`
- **class `_NoRedirect`** *(class; bases: urllib.request.HTTPRedirectHandler)* - urllib redirect handler that disables automatic redirects so each hop is re-validated. · *Instantiated by:* `core/helpers.py::_fetch_pdf_bytes_with_redirects`, `core/helpers.py::_fetch_public_text`
  - `_NoRedirect.redirect_request(req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> None` - Returns None, refusing the automatic redirect.
- `_assert_public_http_url(url: str, tool_name: str='web_fetch') -> None` - SSRF guard: requires an absolute http(s) URL, rejects localhost names, resolves the host and rejects private, loopback, link-local, multicast, reserved or unspecified addresses. · *Called by:* `core/helpers.py::_fetch_pdf_bytes_with_redirects`, `core/helpers.py::_fetch_public_text`
- `_read_lines(path: Path, offset: int, limit: int, max_bytes: int) -> dict[str, Any]` - Reads a regular UTF-8 file under a byte limit and returns a window of numbered lines plus a truncation flag. · *Called by:* `core/services.py::CoreToolDispatcher._dispatch`
- `_required_text(arguments: dict[str, Any], key: str) -> str` - Argument must be a non-empty string. · *Called by:* `core/services.py::CoreToolDispatcher._ask_human`, `core/services.py::CoreToolDispatcher._diff`, `core/services.py::CoreToolDispatcher._dispatch`, `core/services.py::CoreToolDispatcher._edit_draft` (+9 more)
- `_nonnegative_int(value: Any, name: str) -> int` - Argument must be a non-negative integer (not a bool). · *Called by:* `core/helpers.py::_bounded_int`, `core/services.py::CoreToolDispatcher._dispatch`
- `_bounded_int(value: Any, name: str, lower: int, upper: int) -> int` - Non-negative integer within [lower, upper]. · *Called by:* `core/services.py::CoreToolDispatcher._dispatch`, `core/services.py::CoreToolDispatcher._grep`, `core/services.py::CoreToolDispatcher._notebook_edit`, `core/services.py::CoreToolDispatcher._read_result` (+3 more)
- `_bounded_float(value: Any, name: str, lower: float, upper: float) -> float` - Number within [lower, upper] (not a bool). · *Called by:* `core/services.py::CoreToolDispatcher._dispatch`, `core/services.py::CoreToolDispatcher._render_pdf_page`
- `_strip_html(value: str) -> str` - Removes tags and unescapes entities. · *Called by:* `core/services.py::DuckDuckGoHtmlClient._search`
- `_sha256(value: str) -> str` - SHA-256 hex of a string. · *Called within this file by:* `core/helpers.py::_parse_pdf_page`, `core/helpers.py::_render_pdf_page`

**Algorithms & invariants.** Every network helper names its tool in error text and appends a likely-cause hint for HTTP errors; every redirect hop is re-validated against the public-address rules.

*Module-level names:* `_MAX_PDF_BYTES`, `_HTTP_FETCH_STATUS_HINTS`

---

### `tools/core/services.py` - portable governed tools: dispatcher, services and web search

*437 lines · depends on: `foundations/contracts.py`, `memory/context_projection.py`, `tools/artifacts.py`, `tools/core/helpers.py`, `tools/policy.py` · used by: `tools/core/__init__.py` · re-exported at the package root: 4 name(s)*

**Role in the workflow.** `CoreToolDispatcher.execute(name, arguments)` is the implementation behind the core tool names. `HarnessToolExecutor` calls it after policy checks; hosts that skip the governed harness (such as the job-agent app) call it directly. Heavy work (grep scanning, web fetch, PDF render, search) runs in worker threads; small file operations run inline.

**Contents**

- `_matches_sensitive_pattern(path: Path) -> bool` - True if a path matches a built-in credential-location deny pattern. · *Called by:* `core/services.py::CoreToolDispatcher._glob`, `core/services.py::CoreToolDispatcher._path`
- **class `SearchResult`** *(dataclass)* - One web search hit: title, URL, snippet. · *Instantiated by:* `core/services.py::DuckDuckGoHtmlClient._search`
  - fields: `title`, `url`, `snippet`
- **class `WebSearchClient`** *(Protocol; bases: Protocol)* - Protocol for a pluggable search backend.
  - `WebSearchClient.search(query: str, limit: int) -> list[SearchResult]` *(async)* - Return up to `limit` results for a query.
- **class `DuckDuckGoHtmlClient`** *(class)* - Dependency-free default search client scraping DuckDuckGo's HTML endpoint; results are untrusted evidence. · *Instantiated by:* `core/services.py::CoreToolDispatcher._web_search`
  - `DuckDuckGoHtmlClient.search(query: str, limit: int) -> list[SearchResult]` *(async)* - Runs the blocking search in a worker thread. · *Called within this file by:* `core/services.py::DuckDuckGoHtmlClient._search`
  - `DuckDuckGoHtmlClient._search(query: str, limit: int) -> list[SearchResult]` *(staticmethod)* - POSTs the query, raises a clear error when the response is a rate-limit/bot challenge (so it is not mistaken for zero results), and parses titles, URLs and snippets with regexes. · *Called by:* `core/services.py::DuckDuckGoHtmlClient.search`
- **class `CoreToolServices`** *(dataclass)* - Dependencies and limits for the dispatcher: run root, artifact store, declared output paths, journal, search client, human responder, read/web/grep bounds and the PDF byte/image caches. · *Instantiated by:* `tools/registry.py::HarnessToolExecutor.__init__`
  - fields: `root`, `artifacts`, `declared_output_paths`, `result_journal`, `search_client`, `ask_human`, `max_read_bytes`, `max_web_chars`, `write_manifest`, `pdf_image_cache`, `pdf_bytes_cache`, `max_grep_files`, `max_grep_total_bytes`, `max_grep_seconds`
  - `CoreToolServices.__post_init__() -> None` - Resolves the root, requires it to exist and validates that limits are positive and safe.
- **class `CoreToolDispatcher`** *(class)* - Executes one core tool by name; policy is applied by the caller. · *Instantiated by:* `tools/registry.py::HarnessToolExecutor.__init__`
  - `CoreToolDispatcher.__init__(services: CoreToolServices) -> None` - Stores the services.
  - `CoreToolDispatcher.execute(name: str, arguments: dict[str, Any]) -> ToolExecutionResult` *(async)* - Runs `_dispatch` and wraps any exception into a failed `ToolExecutionResult` with a `CORE_TOOL_EXECUTION_FAILED` failure naming the tool and exception type.
  - `CoreToolDispatcher._dispatch(name: str, arguments: dict[str, Any]) -> Any` *(async)* - Name-keyed if-chain mapping each tool name to its implementation; unknown names raise. · *Called by:* `core/services.py::CoreToolDispatcher.execute`
  - `CoreToolDispatcher._path(relative_path: str) -> Path` - Resolves a run-root-relative path, rejecting absolute, escaping and credential paths. · *Called by:* `core/services.py::CoreToolDispatcher._dispatch`, `core/services.py::CoreToolDispatcher._edit_draft`, `core/services.py::CoreToolDispatcher._grep_documents`, `core/services.py::CoreToolDispatcher._notebook_edit`
  - `CoreToolDispatcher._glob(pattern: str, limit: int) -> list[str]` - Lists up to `limit` matching paths under the root, excluding escapes and credential paths. · *Called by:* `core/services.py::CoreToolDispatcher._dispatch`, `core/services.py::CoreToolDispatcher._grep_documents`
  - `CoreToolDispatcher._grep(arguments: dict[str, Any]) -> dict[str, Any]` *(async)* - Collects candidate files in a thread, then runs the regex search in the isolated child process. · *Called by:* `core/services.py::CoreToolDispatcher._dispatch`
  - `CoreToolDispatcher._grep_documents(file_glob: str) -> tuple[list[tuple[str, str]], bool]` - Reads matching UTF-8 files within the file-count and byte limits and reports whether the scan was truncated. · *Called by:* `core/services.py::CoreToolDispatcher._grep`
  - `CoreToolDispatcher._write_draft(arguments: dict[str, Any]) -> dict[str, Any]` - Writes a declared output path through the artifact store with the write manifest. · *Called by:* `core/services.py::CoreToolDispatcher._dispatch`
  - `CoreToolDispatcher._edit_draft(arguments: dict[str, Any]) -> dict[str, Any]` - Exact-text replacement in a declared draft (error if not found or ambiguous without `replace_all`), re-registered as a new artifact. · *Called by:* `core/services.py::CoreToolDispatcher._dispatch`
  - `CoreToolDispatcher._diff(arguments: dict[str, Any]) -> dict[str, Any]` - Unified diff of two artifact ids. · *Called by:* `core/services.py::CoreToolDispatcher._dispatch`
  - `CoreToolDispatcher._read_artifact(arguments: dict[str, Any]) -> dict[str, Any]` - Returns an artifact's text by id. · *Called by:* `core/services.py::CoreToolDispatcher._dispatch`
  - `CoreToolDispatcher._grep_artifact(arguments: dict[str, Any]) -> dict[str, Any]` - Returns the line numbers in an artifact containing a literal string. · *Called by:* `core/services.py::CoreToolDispatcher._dispatch`
  - `CoreToolDispatcher._read_result(arguments: dict[str, Any]) -> dict[str, Any]` - Reads a journal handle and returns its JSON truncated to `max_chars` with a content hash (the way the model retrieves compacted results). · *Called by:* `core/services.py::CoreToolDispatcher._dispatch`
  - `CoreToolDispatcher._web_fetch(arguments: dict[str, Any]) -> dict[str, Any]` *(async)* - Validates arguments and runs `_fetch_public_text` in a worker thread with the PDF caches. · *Called by:* `core/services.py::CoreToolDispatcher._dispatch`
  - `CoreToolDispatcher._render_pdf_page(arguments: dict[str, Any]) -> dict[str, Any]` *(async)* - Validates arguments and runs the PDF page render in a worker thread. · *Called within this file by:* `core/services.py::CoreToolDispatcher._dispatch`
  - `CoreToolDispatcher._web_search(arguments: dict[str, Any]) -> dict[str, Any]` *(async)* - Uses the configured (or default DuckDuckGo) client and returns results marked untrusted. · *Called by:* `core/services.py::CoreToolDispatcher._dispatch`
  - `CoreToolDispatcher._ask_human(arguments: dict[str, Any]) -> dict[str, Any]` *(async)* - Awaits the configured human responder; error if none is configured. · *Called by:* `core/services.py::CoreToolDispatcher._dispatch`
  - `CoreToolDispatcher._notebook_edit(arguments: dict[str, Any]) -> dict[str, Any]` - Replaces or appends to one cell of a declared notebook JSON, extending the cell list as needed, and saves it as a new artifact. · *Called by:* `core/services.py::CoreToolDispatcher._dispatch`

*Module-level names:* `HumanQuestionResponder`

---

### `tools/delegation.py` - delegated sub-runs on isolated git worktrees

*113 lines · depends on: `tools/task_models.py`, `tools/tasks.py`, `tools/worktree_models.py`, `tools/worktrees.py` · used by: no other module (entry point or re-exported only) · re-exported at the package root: 3 name(s)*

**Role in the workflow.** Composes the task manager and worktree manager so a host can hand off a bounded sub-run (typically another `BaseAgent.run`) without its file edits colliding with the caller's.

**Contents**

- **class `DelegatedRunContext`** *(dataclass)* - What a run factory receives: its isolated worktree, if any. · *Instantiated by:* `tools/delegation.py::SubagentCoordinator.delegate.run_with_cleanup`
  - fields: `worktree`
- **class `SubagentCoordinator`** *(class)* - Starts, polls, awaits and cancels delegated runs tracked in one shared `BackgroundTaskManager`.
  - `SubagentCoordinator.__init__(tasks: BackgroundTaskManager, *, worktrees: AgentWorktreeManager | None=None) -> None` - Stores the task manager and optional worktree manager and an id list.
  - `SubagentCoordinator.delegate(description: str, run_factory: DelegatedRunFactory, *, repository_path: Path | None=None, worktree_slug: str | None=None, agent_id: str | None=Non...` *(async)* - Optionally creates a worktree for `worktree_slug`, then starts the factory's coroutine as an agent-run task, removing the worktree afterwards if requested. · *No in-package callers (public API, entry point, or protocol hook).*
    - `SubagentCoordinator.delegate.run_with_cleanup() -> Any` *(async)* - Runs the factory and, in `finally`, removes the worktree when asked. · *Called by:* `tools/delegation.py::SubagentCoordinator.delegate`
  - `SubagentCoordinator.get_delegation(task_id: str) -> TaskRecord | None` - The task record for an id. · *No in-package callers (public API, entry point, or protocol hook).*
  - `SubagentCoordinator.list_delegations(*, status: TaskStatus | None=None) -> list[TaskRecord]` - Only the delegations this coordinator started, optionally filtered by status. · *No in-package callers (public API, entry point, or protocol hook).*
  - `SubagentCoordinator.await_delegation(task_id: str, *, timeout: float | None=None) -> TaskRecord` *(async)* - Waits for a delegated task to finish (with optional timeout). · *No in-package callers (public API, entry point, or protocol hook).*
  - `SubagentCoordinator.cancel_delegation(task_id: str) -> TaskRecord` *(async)* - Stops a delegated task. · *No in-package callers (public API, entry point, or protocol hook).*

*Module-level names:* `DelegatedRunFactory`

---

### `tools/policy.py` - deny-by-default capability policy

*153 lines · depends on: `foundations/contracts.py`, `tools/approvals.py` · used by: `agent/orchestrator/models.py`, `mcp/client_bridge.py`, `tools/core/services.py`, `tools/registry.py` · re-exported at the package root: 3 name(s)*

**Role in the workflow.** `HarnessToolExecutor.execute` asks `CapabilityPolicy.evaluate` before every governed tool call and either runs, blocks, or requests an approval based on the answer.

**Contents**

- **class `SideEffectClass`** *(enum; bases: StrEnum)* - read-only, mutating, process or destructive.
  - members: `READ_ONLY`, `MUTATING`, `PROCESS`, `DESTRUCTIVE`
- **class `CapabilityGrant`** *(pydantic model; bases: StrictModel)* - What one role may do: capability names and the paths it may touch.
  - fields: `role`, `capabilities`, `allowed_paths`
- **class `PolicyDecision`** *(pydantic model; bases: StrictModel)* - Allowed or not, with the reason, whether approval is required and the request if any. · *Instantiated by:* `tools/policy.py::CapabilityPolicy.evaluate`
  - fields: `allowed`, `reason`, `approval_required`, `approval_request`
- **class `CapabilityPolicy`** *(class)* - Checks sensitive paths, role grant, path containment and typed approval state in that order.
  - `CapabilityPolicy.__init__(grants: list[CapabilityGrant]) -> None` - Indexes grants by role.
  - `CapabilityPolicy.evaluate(*, role: str, capability: str, side_effect: SideEffectClass, run_root: Path, requested_paths: list[str]=(), approval: ApprovalRequest | None=None)...` - Order: deny built-in credential paths (no grant can override) -> role must hold the capability -> every requested path must be inside the grant's allowed paths -> read-only is allowed; anything else needs a matching, approved typed approval (missing, pending and rejected each have their own reason).
  - `CapabilityPolicy._sensitive_pattern_match(requested_path: str, run_root: Path) -> str | None` *(staticmethod)* - Resolves a requested path against the run root and returns the matching built-in deny pattern (also checks a trailing-slash form so whole-directory requests are caught). · *Called by:* `tools/policy.py::CapabilityPolicy.evaluate`
  - `CapabilityPolicy._path_is_allowed(requested_path: str, allowed_paths: list[str], run_root: Path) -> bool` *(staticmethod)* - True if the resolved path stays under the run root and under at least one allowed path (empty list means nothing is allowed). · *Called by:* `tools/policy.py::CapabilityPolicy.evaluate`

**Algorithms & invariants.** `SENSITIVE_PATH_PATTERNS` (ssh, aws, gcloud, azure, gnupg, docker, kube, netrc, private keys) is checked before any grant is consulted.

*Module-level names:* `SENSITIVE_PATH_PATTERNS`

---

### `tools/registry.py` - capability-bound harness tool registry and its BaseAgent executor

*323 lines · depends on: `foundations/contracts.py`, `memory/context_projection.py`, `state/planning.py`, `tools/approvals.py`, `tools/artifacts.py`, `tools/core/__init__.py`, `tools/policy.py`, `tools/supervisor.py`, `tools/tools.py` · used by: `agent/orchestrator/orchestrator.py`, `mcp/client_bridge.py` · re-exported at the package root: 5 name(s)*

**Role in the workflow.** The governed `ToolExecutor`: `BaseAgent` hands it every tool call; it applies policy and approvals, then runs a host handler, a core tool, a registered process command or a built-in spec/artifact reader. The registry is closed: no generic shell and no dynamically named tool.

**Contents**

- **class `HarnessExecutionContext`** *(dataclass)* - Non-prompt state for one scheduled node: run/node/role, plan task, run root, artifact store, policy, approvals, supervisor, spec snapshots, declared outputs, approval ids, journal, search client, human responder and PDF image cache.
  - fields: `run_id`, `node_id`, `role`, `plan_task`, `run_root`, `artifacts`, `policy`, `approvals`, `supervisor`, `spec_snapshots`, `declared_output_paths`, `approval_ids_by_capability`, `result_journal`, `search_client`, `ask_human`, `pdf_image_cache`
- **class `RegisteredTool`** *(dataclass)* - Name, capability, side-effect class and an optional command template name. · *Instantiated by:* `mcp/client_bridge.py::mcp_tools_as_extensions`, `tools/registry.py::HarnessToolRegistry.default_tools`
  - fields: `name`, `capability`, `side_effect`, `command_template`
- **class `HarnessToolRegistry`** *(class)* - Closed registry of tool names with optional custom handlers. · *Instantiated by:* `orchestrator/orchestrator.py::Orchestrator.__init__`
  - `HarnessToolRegistry.__init__(tools: list[RegisteredTool] | None=None, *, custom_handlers: dict[str, HarnessToolHandler] | None=None) -> None` - Builds the name map (default tools if none are given), rejecting duplicates and handlers for unregistered tools.
  - `HarnessToolRegistry.default_tools() -> list[RegisteredTool]` *(staticmethod)* - The 22 standard declarations (spec/file/artifact/web tools, sleep, brief, human question, `run_registered_command`, and the Verilator/Yosys/OpenROAD/OpenSTA process tools). · *Called by:* `tools/registry.py::HarnessToolRegistry.__init__`, `tools/registry.py::HarnessToolRegistry.with_extensions`
  - `HarnessToolRegistry.with_extensions(extensions: list[RegisteredTool], *, handlers: dict[str, HarnessToolHandler]) -> HarnessToolRegistry` *(classmethod)* - Default tools plus host-defined extra tools and their handlers. · *No in-package callers (public API, entry point, or protocol hook).*
  - `HarnessToolRegistry.resolve(name: str) -> RegisteredTool | None` - Looks up a registered tool by name.
  - `HarnessToolRegistry.names() -> tuple[str, ...]` - Sorted tuple of registered names. · *Called by:* `mcp/client.py::McpClientManager.__init__`, `tools/sandbox.py::DockerSandbox._enforced_limit_names`
  - `HarnessToolRegistry.handler_for(name: str) -> HarnessToolHandler | None` - The custom handler registered for a name, if any. · *Called by:* `tools/registry.py::HarnessToolExecutor._execute_registered`
- **class `HarnessToolExecutor`** *(class; bases: ToolExecutor)* - Bridges BaseAgent tool calls into policy-governed typed harness actions.
  - `HarnessToolExecutor.__init__(registry: HarnessToolRegistry, context: HarnessExecutionContext) -> None` - Stores the registry and context and builds an internal `CoreToolDispatcher` from the context.
  - `HarnessToolExecutor.execute(tool: ToolDefinition, invocation: ToolInvocationContext) -> ToolExecutionResult` *(async)* - Resolve the tool, collect requested write paths, look up any approval for the capability, evaluate policy; blocked decisions return BLOCKED (filing an approval request when one is needed); otherwise run the tool and convert exceptions into a failed result with `HARNESS_TOOL_EXECUTION_FAILED`. · *Called within this file by:* `tools/registry.py::HarnessToolExecutor._execute_registered`
  - `HarnessToolExecutor._approval_for(capability: str)` - The approval registered for a capability in this context, if any. · *Called by:* `tools/registry.py::HarnessToolExecutor.execute`
  - `HarnessToolExecutor._execute_registered(tool: RegisteredTool, arguments: dict[str, Any]) -> Any` *(async)* - Dispatch chain: custom handler -> `read_spec` (only the plan task's scope pointer) -> `run_registered_command` -> core tool names -> authorization-checked artifact read/grep/diff -> command-template process tools. · *Called by:* `tools/registry.py::HarnessToolExecutor.execute`
  - `HarnessToolExecutor._requested_paths(tool_name: str, arguments: dict[str, Any]) -> list[str]` *(staticmethod)* - Write-style tools declare their `path` argument for the path-containment check. · *Called by:* `tools/registry.py::HarnessToolExecutor.execute`
- `_string_argument(arguments: dict[str, Any], key: str) -> str` - Argument must be a non-empty string. · *Called by:* `tools/registry.py::HarnessToolExecutor._execute_registered`

**Algorithms & invariants.** Note that `read_artifact`, `grep_artifact` and `diff_declared_artifacts` are implemented here with plan-task authorization checks, separately from the unauthorized versions in `core/services.py`.

*Module-level names:* `HarnessToolHandler`

---

### `tools/sandbox.py` - pluggable execution backends for registered command templates

*247 lines · depends on: `tools/sandbox_models.py`, `tools/supervisor.py` · used by: `tools/tasks.py` · re-exported at the package root: 4 name(s)*

**Role in the workflow.** Selected by the host and used by `BackgroundTaskManager.start_command_task`: the native backend runs the template in a throwaway scratch directory with a scrubbed environment; the Docker backend runs it in an ephemeral network-isolated container.

**Contents**

- **class `DockerUnavailableError`** *(exception; bases: RuntimeError)* - Raised when the Docker binary cannot be found or run. · *Instantiated by:* `tools/sandbox.py::DockerSandbox._run_with_env_file`
- **class `SandboxBackend`** *(Protocol; bases: Protocol)* - Protocol: run one `CommandTemplate` and return a `ProcessExecutionRecord`.
  - `SandboxBackend.run(template: CommandTemplate, *, cwd: Path, environment: EnvironmentPolicy | None=None) -> ProcessExecutionRecord` *(async)* - Protocol method.
- **class `NativeSandbox`** *(class)* - Runs a template via `ProcessSupervisor` in a fresh scratch directory that is deleted afterwards.
  - `NativeSandbox.__init__(*, scratch_parent: Path | None=None) -> None` - Optionally fixes the parent directory for scratch folders.
  - `NativeSandbox.run(template: CommandTemplate, *, cwd: Path, environment: EnvironmentPolicy | None=None) -> ProcessExecutionRecord` *(async)* - Creates the scratch directory, resolves the environment allowlist, runs the supervisor there and always removes the scratch directory.
- **class `DockerSandbox`** *(class)* - Runs a template inside `docker run --rm` with the working directory bind-mounted at `/workspace`.
  - `DockerSandbox.__init__(options: DockerSandboxOptions, *, docker_binary: str='docker') -> None` - Stores the options and docker binary name.
  - `DockerSandbox.run(template: CommandTemplate, *, cwd: Path, environment: EnvironmentPolicy | None=None) -> ProcessExecutionRecord` *(async)* - Validates the cwd, writes an env file if an environment policy is given, runs the container and always deletes the env file.
  - `DockerSandbox._run_with_env_file(template: CommandTemplate, cwd: Path, container_name: str, env_file: Path | None) -> ProcessExecutionRecord` *(async)* - Launches the docker CLI, captures bounded output, enforces the template timeout (killing the container), classifies the exit (137 means resource limit) and builds the `ProcessExecutionRecord`. · *Called by:* `tools/sandbox.py::DockerSandbox.run`
  - `DockerSandbox._build_argv(template: CommandTemplate, cwd: Path, env_file: Path | None, container_name: str) -> list[str]` - Builds the docker command: network none, read-only root with tmpfs, memory/cpu limits, mounts, env file, image and the template command. · *Called by:* `tools/sandbox.py::DockerSandbox._run_with_env_file`
  - `DockerSandbox._write_env_file(environment: EnvironmentPolicy) -> Path` *(staticmethod)* - Writes the scrubbed environment to a private temp `--env-file` so values never appear on a process command line. · *Called by:* `tools/sandbox.py::DockerSandbox.run`
  - `DockerSandbox._enforced_limit_names() -> list[str]` - Names of the container limits that were applied (memory, cpu). · *Called by:* `tools/sandbox.py::DockerSandbox._run_with_env_file`
  - `DockerSandbox._kill_container(container_name: str) -> list[str]` *(async)* - Runs `docker kill` with a 10 s timeout and reports the outcome. · *Called by:* `tools/sandbox.py::DockerSandbox._run_with_env_file`

*Module-level names:* `_CONTAINER_WORKDIR`, `_DOCKER_SIGKILL_EXIT_CODE`

---

### `tools/sandbox_models.py` - typed configuration for sandbox backends

*50 lines · depends on: `foundations/contracts.py` · used by: `tools/sandbox.py`, `tools/supervisor.py`, `tools/tasks.py` · re-exported at the package root: 3 name(s)*

**Role in the workflow.** Used by the supervisor templates, native sandbox and Docker sandbox to decide what the child process sees.

**Contents**

- **class `SandboxKind`** *(enum; bases: StrEnum)* - native or docker.
  - members: `NATIVE`, `DOCKER`
- **class `EnvironmentPolicy`** *(pydantic model; bases: StrictModel)* - An explicit environment allowlist plus literal overrides; the host environment is never inherited implicitly.
  - fields: `allowed_variable_names`, `literal_variables`
  - `EnvironmentPolicy.resolve() -> dict[str, str]` - Returns only the named host variables that exist, plus the literal overrides.
- **class `DockerSandboxOptions`** *(pydantic model; bases: StrictModel)* - Container settings: image, network disabled (default), read-only root (default), memory/cpu limits and extra binds.
  - fields: `image`, `network_disabled`, `read_only_root`, `memory_bytes`, `cpu_limit`, `extra_binds`

---

### `tools/supervisor.py` - registered-command execution with timeout, bounded output and process-tree kill

*437 lines · depends on: `foundations/contracts.py`, `observability/telemetry_models.py`, `observability/telemetry_store.py`, `tools/sandbox_models.py` · used by: `tools/registry.py`, `tools/sandbox.py`, `tools/tasks.py` · re-exported at the package root: 8 name(s)*

**Role in the workflow.** `run_registered_command` and the EDA process tools reach this supervisor. It never runs a raw string: only a host-registered `CommandTemplate`, with watchdog events recorded to telemetry.

**Contents**

- **class `ProcessExitKind`** *(enum; bases: StrEnum)* - succeeded, exit-nonzero, timed-out, cancelled or resource-limit.
  - members: `SUCCEEDED`, `EXIT_NONZERO`, `TIMED_OUT`, `CANCELLED`, `RESOURCE_LIMIT`
- **class `WatchdogState`** *(enum; bases: StrEnum)* - Lifecycle states emitted as `watchdog.<state>` telemetry events.
  - members: `REGISTERED`, `STARTED`, `TERMINATING`, `KILLED`, `COMPLETED`, `FAILED`, `TIMED_OUT`, `CANCELLED`, `RESOURCE_LIMITED`
- **class `ResourceLimits`** *(pydantic model; bases: StrictModel)* - Optional CPU seconds, address space and file size limits (applied as POSIX rlimits where supported).
  - fields: `cpu_seconds`, `max_address_space_bytes`, `max_file_bytes`
- **class `RetryPolicy`** *(pydantic model; bases: StrictModel)* - Attempts (1-5), retryable exit kinds, backoff and an `idempotent` flag required before any retry.
  - fields: `max_attempts`, `retryable_exit_kinds`, `base_backoff_seconds`, `idempotent`
- **class `CommandTemplate`** *(pydantic model; bases: StrictModel)* - A host-declared command: name, argv, timeout, output cap, resource limits, retry policy and an optional environment policy (unset inherits the host environment).
  - fields: `name`, `command`, `timeout_seconds`, `max_output_bytes`, `resource_limits`, `retry_policy`, `environment`
- **class `ProcessExecutionRecord`** *(pydantic model; bases: StrictModel)* - Full outcome: timing, pid, return code, signal, exit kind, error code, captured output with byte counts, termination path and enforced/unsupported limits. · *Instantiated by:* `tools/sandbox.py::DockerSandbox._run_with_env_file`, `tools/supervisor.py::ProcessSupervisor._execute_once`
  - fields: `template_name`, `command`, `started_at_utc`, `ended_at_utc`, `duration_ns`, `pid`, `process_group_id`, `return_code`, `exit_signal`, `exit_kind`, `error_code`, `timed_out`, `cancelled`, `resource_limited`, `output`, `output_bytes_total`, ... (+6)
  - `ProcessExecutionRecord.successful() -> bool` *(property)* - Property: the exit kind is `succeeded`. · *Called by:* `tools/registry.py::HarnessToolExecutor._execute_registered`, `tools/supervisor.py::ProcessSupervisor.execute`, `tools/tasks.py::BackgroundTaskManager._run_command`
- **class `WatchdogController`** *(class)* - Emits structured watchdog state changes; the supervisor performs the actual kill. · *Instantiated by:* `tools/supervisor.py::ProcessSupervisor._execute_once`
  - `WatchdogController.__init__(telemetry: TelemetryStore | None=None, telemetry_context: TelemetryContext | None=None) -> None` - Stores the optional telemetry store and context.
  - `WatchdogController.emit(state: WatchdogState, *, payload: dict[str, Any] | None=None) -> None` - Records a `watchdog.<state>` telemetry event (no-op without telemetry).
- **class `ProcessSupervisor`** *(class)* - Runs only registered commands with bounded capture, a timeout and process-tree termination. · *Instantiated by:* `tools/sandbox.py::NativeSandbox.run`
  - `ProcessSupervisor.__init__(templates: list[CommandTemplate]=(), *, telemetry: TelemetryStore | None=None, telemetry_context_factory: Callable[[str], TelemetryContext] | None...` - Indexes templates by name and stores telemetry wiring.
  - `ProcessSupervisor.template(name: str) -> CommandTemplate | None` - A registered template by name, or None. · *Called by:* `tools/sandbox.py::DockerSandbox._build_argv`, `tools/sandbox.py::DockerSandbox._run_with_env_file`, `tools/sandbox.py::DockerSandbox.run`, `tools/sandbox.py::NativeSandbox.run` (+4 more)
  - `ProcessSupervisor.execute(template_name: str, *, cwd: Path, env: dict[str, str] | None=None) -> ProcessExecutionRecord` *(async)* - Validates the template and cwd, resolves the environment (call-site override, else the template policy, else inherit), and retries retryable idempotent failures with linear backoff.
  - `ProcessSupervisor._execute_once(template: CommandTemplate, *, cwd: Path, attempts: int, env: dict[str, str] | None=None) -> ProcessExecutionRecord` *(async)* - Spawns the process in its own group/session, captures output concurrently, waits with the timeout, terminates the group on timeout or cancellation, classifies the exit and emits watchdog events. · *Called by:* `tools/supervisor.py::ProcessSupervisor.execute`
  - `ProcessSupervisor._terminate_process_group(process: asyncio.subprocess.Process) -> list[str]` *(async, staticmethod)* - POSIX: SIGTERM the group, wait 1 s, then SIGKILL; Windows: delegates to the taskkill helper; returns the termination path. · *Called by:* `tools/supervisor.py::ProcessSupervisor._execute_once`
- `_subprocess_launch_options(preexec: Callable[[], None] | None) -> dict[str, Any]` - Platform launch options: a new process group on Windows, a new session plus rlimit preexec on POSIX. · *Called by:* `tools/supervisor.py::ProcessSupervisor._execute_once`
- `_terminate_windows_process_tree(process: asyncio.subprocess.Process) -> list[str]` *(async)* - Runs `taskkill /T /F` on the process and always returns an auditable outcome string. · *Called by:* `tools/supervisor.py::ProcessSupervisor._terminate_process_group`
- `_capture_bounded_output(stream: asyncio.StreamReader | None, limit: int) -> tuple[bytes, int]` *(async)* - Reads the stream to the end but retains only the first `limit` bytes, returning the retained bytes and the true total. · *Called by:* `tools/sandbox.py::DockerSandbox._run_with_env_file`, `tools/supervisor.py::ProcessSupervisor._execute_once`
- `_resource_preexec(limits: ResourceLimits) -> tuple[Callable[[], None] | None, list[str], list[str]]` - Builds the POSIX preexec function that applies rlimits and lists which limits were enforced or unsupported. · *Called by:* `tools/supervisor.py::ProcessSupervisor._execute_once`
  - `_resource_preexec.apply() -> None` - Child-side function that calls `setrlimit` for each requested limit. · *Called within this file by:* `tools/supervisor.py::_resource_preexec`
- `_signal_name(return_code: int | None) -> str | None` - Names the signal behind a negative return code. · *Called by:* `tools/supervisor.py::ProcessSupervisor._execute_once`
- `_is_resource_signal(return_code: int | None) -> bool` - True for SIGXCPU/SIGXFSZ (CPU or file-size limit exceeded). · *Called by:* `tools/supervisor.py::ProcessSupervisor._execute_once`

---

### `tools/task_models.py` - records for background tasks

*43 lines · depends on: `foundations/contracts.py` · used by: `tools/delegation.py`, `tools/tasks.py` · re-exported at the package root: 3 name(s)*

**Role in the workflow.** The pollable handle returned by `BackgroundTaskManager` and `SubagentCoordinator`.

**Contents**

- **class `TaskKind`** *(enum; bases: StrEnum)* - command-template or agent-run.
  - members: `COMMAND_TEMPLATE`, `AGENT_RUN`
- **class `TaskStatus`** *(enum; bases: StrEnum)* - running, completed, failed or killed.
  - members: `RUNNING`, `COMPLETED`, `FAILED`, `KILLED`
- **class `TaskRecord`** *(pydantic model; bases: StrictModel)* - Task id, kind, description, status, timestamps, result payload (set only when terminal) and error text. · *Instantiated by:* `tools/tasks.py::BackgroundTaskManager._register`
  - fields: `task_id`, `kind`, `description`, `status`, `created_at_utc`, `started_at_utc`, `ended_at_utc`, `result`, `error`

---

### `tools/tasks.py` - background task lifecycle: start, poll, stop

*208 lines · depends on: `foundations/logging.py`, `tools/sandbox.py`, `tools/sandbox_models.py`, `tools/supervisor.py`, `tools/task_models.py` · used by: `tools/delegation.py` · re-exported at the package root: 1 name(s)*

**Role in the workflow.** Runs a registered command through a sandbox backend, or tracks any host coroutine (usually `BaseAgent.run`), reporting every status change to an optional listener (so tasks can feed telemetry).

**Contents**

- **class `BackgroundTaskManager`** *(class)* - Owns each task's record and its `asyncio.Task`.
  - `BackgroundTaskManager.__init__(*, on_transition: TaskTransitionListener | None=None) -> None` - Starts with no tasks and an optional transition listener.
  - `BackgroundTaskManager.start_command_task(description: str, backend: SandboxBackend, template: CommandTemplate, *, cwd: Path, environment: EnvironmentPolicy | None=None) -> TaskRecord` - Registers a command task and starts `backend.run(template)` in the background. · *No in-package callers (public API, entry point, or protocol hook).*
  - `BackgroundTaskManager.start_agent_task(description: str, run: Awaitable[Any], *, summarize: Any=None) -> TaskRecord` - Registers an agent-run task and starts the supplied awaitable; the result is stored via `summarize` or when it is already a dict. · *Called by:* `tools/delegation.py::SubagentCoordinator.delegate`
  - `BackgroundTaskManager.get_task(task_id: str) -> TaskRecord | None` - The record for an id, or None. · *Called by:* `tools/delegation.py::SubagentCoordinator.get_delegation`, `tools/delegation.py::SubagentCoordinator.list_delegations`
  - `BackgroundTaskManager.list_tasks(*, status: TaskStatus | None=None) -> list[TaskRecord]` - Records (optionally by status) in creation order. · *No in-package callers (public API, entry point, or protocol hook).*
  - `BackgroundTaskManager.stop_task(task_id: str) -> TaskRecord` *(async)* - Cancels a running task and marks it killed; a finished task is returned unchanged. · *Called by:* `tools/delegation.py::SubagentCoordinator.cancel_delegation`
  - `BackgroundTaskManager.wait_for(task_id: str, *, timeout: float | None=None) -> TaskRecord` *(async)* - Waits (shielded) until the task ends or the timeout elapses and returns its record. · *Called by:* `base_agent/agent.py::BaseAgent._await_with_watchdog`, `jev/decision.py::TypeSafeJevDecisionEvaluator._evaluate_with_bounded_attempts`, `tools/delegation.py::SubagentCoordinator.await_delegation`, `tools/sandbox.py::DockerSandbox._kill_container` (+4 more)
  - `BackgroundTaskManager._run_command(task_id: str, run: Awaitable[Any]) -> None` *(async)* - Awaits a command coroutine and records completed/failed from the process record or the exception (`Type: message`). · *Called by:* `tools/tasks.py::BackgroundTaskManager.start_command_task`
  - `BackgroundTaskManager._run_agent(task_id: str, run: Awaitable[Any], summarize: Any) -> None` *(async)* - Awaits an agent coroutine and records completed (with the summary) or failed (`Type: message`). · *Called by:* `tools/tasks.py::BackgroundTaskManager.start_agent_task`
  - `BackgroundTaskManager._allocate_id(kind: TaskKind) -> str` - `task-<kind>-<n>` ids. · *Called by:* `tools/tasks.py::BackgroundTaskManager.start_agent_task`, `tools/tasks.py::BackgroundTaskManager.start_command_task`
  - `BackgroundTaskManager._register(task_id: str, kind: TaskKind, description: str) -> TaskRecord` - Creates the RUNNING record and notifies the listener. · *Called within this file by:* `tools/tasks.py::BackgroundTaskManager.start_agent_task`, `tools/tasks.py::BackgroundTaskManager.start_command_task`
  - `BackgroundTaskManager._transition(task_id: str, status: TaskStatus, *, result: dict[str, Any] | None=None, error: str | None=None) -> TaskRecord` - Applies a new status with end time, result and error and notifies the listener. · *Called by:* `tools/tasks.py::BackgroundTaskManager._run_agent`, `tools/tasks.py::BackgroundTaskManager._run_command`, `tools/tasks.py::BackgroundTaskManager.stop_task`
  - `BackgroundTaskManager._notify(record: TaskRecord) -> None` - Calls the listener; an exception from it is logged, never propagated. · *Called by:* `tools/tasks.py::BackgroundTaskManager._register`, `tools/tasks.py::BackgroundTaskManager._transition`
  - `BackgroundTaskManager._require(task_id: str) -> TaskRecord` - The record or a `No task found` error. · *Called within this file by:* `tools/tasks.py::BackgroundTaskManager._transition`, `tools/tasks.py::BackgroundTaskManager.stop_task`, `tools/tasks.py::BackgroundTaskManager.wait_for`
- `_now() -> str` - UTC ISO timestamp. · *Called within this file by:* `tools/tasks.py::BackgroundTaskManager._register`, `tools/tasks.py::BackgroundTaskManager._transition`

*Module-level names:* `TaskTransitionListener`, `_logger`

---

### `tools/tools.py` - the ToolExecutor protocol and two deterministic test executors

*72 lines · depends on: `foundations/contracts.py` · used by: `agent/base_agent/agent.py`, `agent/base_agent/types.py`, `agent/graph_agent_executor.py`, `agent/runtime.py`, `integrations/langchain.py`, `integrations/langgraph.py`, `mcp/agent_tools.py`, `tools/registry.py` · not re-exported at the package root*

**Role in the workflow.** `BaseAgent` depends only on `ToolExecutor`: any object with `async execute(tool, context)` can serve a run.

**Contents**

- **class `ToolInvocationContext`** *(dataclass)* - What an executor receives with each call: agent identity, task, iteration and the call. · *Instantiated by:* `base_agent/agent.py::BaseAgent._execute_tool_call`
  - fields: `agent_identity`, `task`, `iteration`, `call`
- **class `ToolExecutor`** *(Protocol; bases: Protocol)* - Protocol: `execute(tool, context)` returns a `ToolExecutionResult`.
  - `ToolExecutor.execute(tool: ToolDefinition, context: ToolInvocationContext) -> ToolExecutionResult` *(async)* - Protocol method.
- **class `InMemoryTaskToolExecutor`** *(class)* - Safe deterministic tools for protocol tests: `read_locked_interface` and `echo`; no filesystem or process. · *Instantiated by:* `mcp/agent_tools.py::register_agent_tools.run_agent_task`
  - `InMemoryTaskToolExecutor.__init__() -> None` - Starts with an empty call log.
  - `InMemoryTaskToolExecutor.execute(tool: ToolDefinition, context: ToolInvocationContext) -> ToolExecutionResult` *(async)* - Returns the locked interface or the arguments for the two known tools, otherwise a failure naming the tool.
- **class `RecordingToolExecutor`** *(dataclass)* - Test double that returns pre-configured results by tool name and records calls.
  - fields: `results_by_name`, `calls`
  - `RecordingToolExecutor.execute(tool: ToolDefinition, context: ToolInvocationContext) -> ToolExecutionResult` *(async)* - Logs the call and returns the configured result, or a failure naming the unconfigured tool.

---

### `tools/worktree_models.py` - record for an agent's git worktree

*25 lines · depends on: `foundations/contracts.py` · used by: `tools/delegation.py`, `tools/worktrees.py` · re-exported at the package root: 1 name(s)*

**Role in the workflow.** Returned by `AgentWorktreeManager`; distinct from the specification variant worktrees in `specifications/git_models.py`.

**Contents**

- **class `AgentWorktreeInfo`** *(pydantic model; bases: StrictModel)* - Slug, path, branch, base repository, creation time and optional agent id. · *Instantiated by:* `tools/worktrees.py::AgentWorktreeManager.create_worktree`
  - fields: `slug`, `path`, `branch`, `base_repository_path`, `created_at_utc`, `agent_id`

---

### `tools/worktrees.py` - git worktree isolation for concurrent agents

*145 lines · depends on: `tools/worktree_models.py` · used by: `tools/delegation.py` · re-exported at the package root: 2 name(s)*

**Role in the workflow.** Each concurrent agent gets its own checkout and branch so parallel edits cannot collide. The manager tracks only worktrees it created in this process.

**Contents**

- `validate_worktree_slug(slug: str) -> str` - Accepts a slug only if it is non-empty, at most 64 chars, relative, and every `/`-separated segment is letters, digits, dots, underscores or dashes (no `.` or `..`). · *Called by:* `tools/worktrees.py::AgentWorktreeManager.create_worktree`
- **class `AgentWorktreeManager`** *(class)* - Creates, lists and removes worktrees beneath one base directory.
  - `AgentWorktreeManager.__init__(base_dir: Path) -> None` - Stores the base directory and an empty tracking map.
  - `AgentWorktreeManager.create_worktree(repository_path: Path, slug: str, *, branch: str | None=None, agent_id: str | None=None) -> AgentWorktreeInfo` *(async)* - Validates the slug, returns the tracked worktree if present, refuses an untracked existing path, and runs `git worktree add -B <branch> <path> HEAD`.
  - `AgentWorktreeManager.remove_worktree(slug: str) -> bool` *(async)* - Runs `git worktree remove --force` and untracks it; False if the slug is unknown. · *Called by:* `tools/delegation.py::SubagentCoordinator.delegate.run_with_cleanup`
  - `AgentWorktreeManager.get_worktree(slug: str) -> AgentWorktreeInfo | None` - The tracked worktree for a slug, or None. · *No in-package callers (public API, entry point, or protocol hook).*
  - `AgentWorktreeManager.list_worktrees() -> list[AgentWorktreeInfo]` - Tracked worktrees sorted by slug. · *No in-package callers (public API, entry point, or protocol hook).*
- `_flatten_slug(slug: str) -> str` - Replaces `/` with `+` to make a single directory name. · *Called by:* `tools/worktrees.py::AgentWorktreeManager.create_worktree`
- `_run_git(*args: str, cwd: Path | str) -> tuple[int, str, str]` *(async)* - Runs git asynchronously with prompts disabled and returns (exit code, stdout, stderr). · *Called by:* `tools/worktrees.py::AgentWorktreeManager.create_worktree`, `tools/worktrees.py::AgentWorktreeManager.remove_worktree`
- `_now() -> str` - UTC ISO timestamp. · *Called within this file by:* `tools/worktrees.py::AgentWorktreeManager.create_worktree`

*Module-level names:* `_VALID_SLUG_SEGMENT`, `_MAX_SLUG_LENGTH`

