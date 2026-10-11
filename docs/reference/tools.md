# `tools/` - governed tool execution: what an agent may do and how it is done

Everything between a model's tool request and the real world. `tools.py` defines the `ToolExecutor` protocol `BaseAgent` calls. `registry.py` is the governed implementation: it checks the capability `policy.py` (role, capability, path containment, typed `approvals.py`) and then dispatches to the portable `core/` tools, to registered process commands run by `supervisor.py` (optionally inside the `sandbox.py` backends), or to host-supplied handlers. `artifacts.py` stores content-addressed drafts with write attribution. `tasks.py`, `delegation.py` and `worktrees.py` run background work and delegated sub-runs in isolated git worktrees.

| File | Lines | Role |
|---|---:|---|
| [`tools/__init__.py`](#tools__init__py---package-marker-for-governed-tool-execution) | 3 | package marker for governed tool execution |
| [`tools/approvals.py`](#toolsapprovalspy---typed-approval-gates-for-state-changing-actions) | 178 | typed approval gates for state-changing actions |
| [`tools/artifacts.py`](#toolsartifactspy---content-addressed-artifact-store-with-immutable-write-attribution) | 265 | content-addressed artifact store with immutable write attribution |
| [`tools/core/__init__.py`](#toolscore__init__py---public-surface-of-the-portable-core-tools) | 25 | public surface of the portable core tools |
| [`tools/core/definitions.py`](#toolscoredefinitionspy---typed-declarations-of-the-governed-core-tool-set) | 356 | typed declarations of the governed core tool set |
| [`tools/core/helpers.py`](#toolscorehelperspy---private-validation-http-and-isolated-regex-helpers-behind-the-core-tools) | 581 | private validation, HTTP and isolated-regex helpers behind the core tools |
| [`tools/core/regex_worker.py`](#toolscoreregex_workerpy---standalone-regex-worker-run-in-an-isolated-interpreter) | 38 | standalone regex worker run in an isolated interpreter |
| [`tools/core/services.py`](#toolscoreservicespy---portable-governed-tools-dispatcher-services-and-web-search) | 488 | portable governed tools: dispatcher, services and web search |
| [`tools/delegation.py`](#toolsdelegationpy---delegated-sub-runs-on-isolated-git-worktrees) | 128 | delegated sub-runs on isolated git worktrees |
| [`tools/elastic_requests.py`](#toolselastic_requestspy---agent-side-queue-and-tool-executor-for-elastic-spawn-requests) | 160 | agent-side queue and tool executor for elastic spawn requests |
| [`tools/policy.py`](#toolspolicypy---deny-by-default-capability-policy) | 176 | deny-by-default capability policy |
| [`tools/registry.py`](#toolsregistrypy---capability-bound-harness-tool-registry-and-its-baseagent-executor) | 327 | capability-bound harness tool registry and its BaseAgent executor |
| [`tools/sandbox.py`](#toolssandboxpy---pluggable-execution-backends-for-registered-command-templates) | 322 | pluggable execution backends for registered command templates |
| [`tools/sandbox_models.py`](#toolssandbox_modelspy---typed-configuration-for-sandbox-backends) | 59 | typed configuration for sandbox backends |
| [`tools/supervisor.py`](#toolssupervisorpy---registered-command-execution-with-timeout-bounded-output-and-process-tree-kill) | 491 | registered-command execution with timeout, bounded output and process-tree kill |
| [`tools/task_models.py`](#toolstask_modelspy---records-for-background-tasks) | 43 | records for background tasks |
| [`tools/tasks.py`](#toolstaskspy---background-task-lifecycle-start-poll-stop) | 258 | background task lifecycle: start, poll, stop |
| [`tools/tools.py`](#toolstoolspy---the-toolexecutor-protocol-and-two-deterministic-test-executors) | 73 | the ToolExecutor protocol and two deterministic test executors |
| [`tools/worktree_models.py`](#toolsworktree_modelspy---record-for-an-agents-git-worktree) | 20 | record for an agent's git worktree |
| [`tools/worktrees.py`](#toolsworktreespy---git-worktree-isolation-for-concurrent-agents) | 181 | git worktree isolation for concurrent agents |

---

### `tools/__init__.py` - package marker for governed tool execution

*3 lines · depends on: nothing in the package · used by: no other module (entry point or re-exported only) · not re-exported at the package root*

**Role in the workflow.** Docstring only.

---

### `tools/approvals.py` - typed approval gates for state-changing actions

*178 lines · depends on: `foundations/atomic_io.py`, `foundations/contracts.py` · used by: `agent/task_runner.py`, `state/harness_coordinator.py`, `tools/policy.py`, `tools/registry.py` · re-exported at the package root: 3 name(s)*

**Role in the workflow.** When `CapabilityPolicy` says a mutating or process capability needs approval, `HarnessToolExecutor` files a request here (a pending request for the same run, node and capability is reused) and returns a BLOCKED tool result carrying the approval id; a human or controller later answers it (MCP `submit_approval`) and `resume_run` re-opens the node. A registry bound to a path persists to `<run>.approvals.json`, so requests and decisions survive a restart.

**Contents**

- **class `ApprovalStatus`** *(enum; bases: StrEnum)* - pending, approved or rejected.
  - members: `PENDING`, `APPROVED`, `REJECTED`
- **class `ApprovalRequest`** *(pydantic model; bases: StrictModel)* - One request: id, run, node, capability, reason, status and the decision reason. · *Instantiated by:* `tools/approvals.py::ApprovalRegistry.request`
  - fields: `approval_id`, `run_id`, `node_id`, `capability`, `reason`, `status`, `decision_reason`
- **class `_PersistedApprovals`** *(pydantic model; bases: StrictModel)* - On-disk form of a registry: schema version `approvals-v1`, the next id counter and the requests.
  - fields: `schema_version`, `next_id`, `requests`
- **class `ApprovalRegistry`** *(class)* - In-memory owner of approval state as typed data rather than conversation text. · *Instantiated by:* `agent/task_runner.py::_RunToken.__init__`, `state/harness_coordinator.py::HarnessCoordinator._approval_registry`
  - `ApprovalRegistry.__init__(path: Path | None=None) -> None` - Starts empty with the id counter at 1; given a path it loads the persisted file (if present) and re-reads it whenever its modification time or size changes.
  - `ApprovalRegistry.request(run_id: str, node_id: str, capability: str, reason: str) -> ApprovalRequest` - Returns the existing pending request for the same run, node and capability, otherwise creates a pending `approval-N`; when persisted, the whole read-modify-write runs under a cross-process lock (`APPROVAL_LOCK_TIMEOUT`). · *Called by:* `agent/elastic_context.py::_child_entry`, `agent/elastic_context.py::render_elastic_instructions`, `agent/graph_agent_executor.py::GraphAgentExecutor.execute`, `openai_compatible/transport.py::UrlLibJsonTransport.post_json` (+52 more)
  - `ApprovalRegistry.submit(approval_id: str, approved: bool, reason: str | None=None) -> ApprovalRequest` - Records approve/reject exactly once; an unknown id raises, and an already-decided request raises naming the decision it already has. · *Called by:* `agent/task_runner.py::AgentTaskRunner._prepare_tools`, `agent/task_runner.py::AgentTaskRunner.decide_approval`, `state/harness_coordinator.py::HarnessCoordinator.submit_approval`
  - `ApprovalRegistry.get(approval_id: str) -> ApprovalRequest | None` - Returns a request by id (refreshing from disk first) or None. · *Called within this file by:* `tools/approvals.py::ApprovalRegistry.submit`
  - `ApprovalRegistry.find(run_id: str, node_id: str, capability: str) -> ApprovalRequest | None` - The latest request for a run, node and capability (whatever its status), or None. · *Called by:* `tools/registry.py::HarnessToolExecutor._approval_for`
  - `ApprovalRegistry.list(run_id: str | None=None) -> list[ApprovalRequest]` - All requests (optionally for one run) sorted by numeric approval id.
  - `ApprovalRegistry._matching(run_id: str, node_id: str, capability: str) -> list[ApprovalRequest]` - Requests for a run, node and capability in id order.
  - `ApprovalRegistry._transaction() -> Iterator[None]` *(contextmanager)* - Context manager for one mutation: holds the thread lock, and for a persisted registry also the file lock, reloads the file, runs the body and saves only if something changed. · *Called within this file by:* `tools/approvals.py::ApprovalRegistry.request`, `tools/approvals.py::ApprovalRegistry.submit`
  - `ApprovalRegistry._refresh() -> None` - Reloads the file when its modification time or size differs from the last read.
  - `ApprovalRegistry._load(path: Path) -> None` - Reads and validates the persisted file (retrying transient sharing violations) and replaces the in-memory requests and counter. · *Called within this file by:* `tools/approvals.py::ApprovalRegistry._refresh`, `tools/approvals.py::ApprovalRegistry._transaction`
  - `ApprovalRegistry._save(path: Path) -> None` - Writes the persisted form through a unique temporary file and `replace_atomic`, then records the new fingerprint.
- `_approval_order(request: ApprovalRequest) -> tuple[int, str]` - Sort key `(numeric suffix of the id, id)`.

---

### `tools/artifacts.py` - content-addressed artifact store with immutable write attribution

*265 lines · depends on: `foundations/atomic_io.py`, `foundations/contracts.py`, `foundations/paths.py`, `foundations/text.py` · used by: `agent/task_runner.py`, `tools/core/services.py`, `tools/registry.py` · re-exported at the package root: 3 name(s)*

**Role in the workflow.** `write_draft`/`edit_draft` write declared outputs through this store; `read_artifact`, `grep_artifact` and `diff_declared_artifacts` read them back by `sha256:` id. Each write also leaves an immutable occurrence record naming the run, node and task, which later proves that a draft was authored by the current task.

**Contents**

- **class `ArtifactRecord`** *(pydantic model; bases: StrictModel)* - Content identity (id, path, SHA-256, size, kind, manifest) plus the occurrence that produced the returned record. · *Instantiated by:* `tools/artifacts.py::ArtifactStore._register`
  - fields: `artifact_id`, `relative_path`, `sha256`, `size_bytes`, `kind`, `manifest`, `occurrence_id`
- **class `ArtifactWriteOccurrence`** *(pydantic model; bases: StrictModel)* - Immutable attribution of one write: occurrence id, artifact id, path, hash, size, kind, writer manifest and UTC time. · *Instantiated by:* `tools/artifacts.py::ArtifactStore._register`
  - fields: `occurrence_id`, `artifact_id`, `relative_path`, `sha256`, `size_bytes`, `kind`, `manifest`, `written_at_utc`
- **class `ArtifactStore`** *(class)* - Persists artifacts under one run root: output files, immutable content blobs, one manifest per content id and one occurrence record per write. · *Instantiated by:* `agent/task_runner.py::AgentTaskRunner._prepare_tools`
  - `ArtifactStore.__init__(run_root: Path) -> None` - Creates the run root and the `.agent-artifacts/{content,occurrences}` directories and an RLock.
  - `ArtifactStore.root() -> Path` *(property)* - Property: the resolved run root.
  - `ArtifactStore.write_text(relative_path: str, content: str, *, kind: str='draft', manifest: dict[str, Any] | None=None) -> ArtifactRecord` - Writes the UTF-8 content to the (root-contained) output path atomically, then registers it; returns the record. · *Called by:* `orchestrator/state_store.py::OrchestrationStateStore.save`, `orchestrator/state_store.py::OrchestrationStateStore.save_policy`, `developer_tools/catalog.py::write_public_api_catalog`, `foundations/benchmarks.py::write_pckp_benchmark_report` (+10 more)
  - `ArtifactStore.register_existing(relative_path: str, *, kind: str, manifest: dict[str, Any] | None=None) -> ArtifactRecord` - Registers a file that already exists below the root (error if it does not). · *No in-package callers (public API, entry point, or protocol hook).*
  - `ArtifactStore.read_text(artifact_id: str) -> str` - Reads from the immutable content blob (falling back to the output path for legacy records) and verifies the SHA-256 before returning text. · *Called by:* `developer_tools/validate.py::_load_structured_file`, `foundations/atomic_io.py::read_text_retrying`, `foundations/benchmarks.py::load_pckp_cases`, `memory/context_projection.py::FileToolResultJournal.handles_of` (+15 more)
  - `ArtifactStore.get(artifact_id: str) -> ArtifactRecord | None` - Loads the artifact manifest by id; an id that is not `sha256:<64 hex>` returns None without touching the disk. · *Called within this file by:* `tools/artifacts.py::ArtifactStore.occurrence_matches_task_draft`, `tools/artifacts.py::ArtifactStore.read_text`
  - `ArtifactStore.get_occurrence(occurrence_id: str) -> ArtifactWriteOccurrence | None` - Loads one occurrence record by `occ-<32 hex>` id, or None (a malformed id never reaches the file system). · *Called by:* `tools/artifacts.py::ArtifactStore.occurrence_matches_task_draft`
  - `ArtifactStore.occurrence_matches_task_draft(occurrence_id: str, artifact_id: str, *, run_id: str, node_id: str, task_id: str, declared_output_paths: tuple[str, ...]) -> bool` - True only if the occurrence is for that artifact, wrote a declared output path, and its manifest names the given run, node and task. · *Called by:* `tools/registry.py::HarnessToolExecutor._execute_registered`
  - `ArtifactStore.diff(base_artifact_id: str, draft_artifact_id: str) -> dict[str, Any]` - Unified diff of two artifacts' text (lines split on newline only), returned with both ids. · *Called by:* `core/services.py::CoreToolDispatcher._diff`, `tools/registry.py::HarnessToolExecutor._execute_registered`
  - `ArtifactStore._register(target: Path, relative_path: str, content: bytes, kind: str, manifest: dict[str, Any]) -> ArtifactRecord` - Hashes content, saves the blob once, saves the manifest on first registration, and always writes a new unique occurrence record. · *Called within this file by:* `tools/artifacts.py::ArtifactStore.register_existing`, `tools/artifacts.py::ArtifactStore.write_text`
  - `ArtifactStore._content_path(digest: str) -> Path` - Path of the immutable blob for a digest. · *Called by:* `tools/artifacts.py::ArtifactStore._register`, `tools/artifacts.py::ArtifactStore.read_text`
  - `ArtifactStore._manifest_path(artifact_id: str) -> Path` - Portable manifest filename for an artifact id (colon replaced). · *Called by:* `tools/artifacts.py::ArtifactStore._existing_manifest_path`, `tools/artifacts.py::ArtifactStore._register`
  - `ArtifactStore._existing_manifest_path(artifact_id: str) -> Path | None` - Finds a manifest under the portable name or the legacy colon name. · *Called by:* `tools/artifacts.py::ArtifactStore._register`, `tools/artifacts.py::ArtifactStore.get`
  - `ArtifactStore._resolve_relative(relative_path: str) -> Path` - Resolves a relative path under the run root, rejecting absolute, empty and root-escaping paths; containment is judged with `relative_to_base`, so the spelling Windows gives the resolved target does not matter. · *Called by:* `tools/artifacts.py::ArtifactStore.read_text`, `tools/artifacts.py::ArtifactStore.register_existing`, `tools/artifacts.py::ArtifactStore.write_text`
  - `ArtifactStore._atomic_write_bytes(target: Path, content: bytes) -> None` *(staticmethod)* - Writes bytes to a unique temporary file then replaces the target with retry. · *Called by:* `tools/artifacts.py::ArtifactStore._register`, `tools/artifacts.py::ArtifactStore.write_text`
- `_safe_name(value: str) -> str` - Filename-safe version of an id. · *Called within this file by:* `tools/artifacts.py::ArtifactStore._manifest_path`
- `_replace_with_retry(temporary: Path, target: Path, *, attempts: int=5) -> None` - Compatibility wrapper around `replace_atomic`. · *Called within this file by:* `tools/artifacts.py::ArtifactStore._atomic_write_bytes`

**Algorithms & invariants.** `artifact_id` is the content hash (deduplicating); attribution lives in the per-write occurrence so identical bytes written by different runs never overwrite each other's history.

---

### `tools/core/__init__.py` - public surface of the portable core tools

*25 lines · depends on: `tools/core/definitions.py`, `tools/core/services.py` · used by: `agent/task_runner.py`, `tools/registry.py` · not re-exported at the package root*

**Role in the workflow.** Re-exports `CoreToolDispatcher`, `CoreToolServices`, the search client types and `core_tool_definitions`.

---

### `tools/core/definitions.py` - typed declarations of the governed core tool set

*356 lines · depends on: `foundations/contracts.py`, `state/elastic.py` · used by: `tools/core/__init__.py` · re-exported at the package root: 1 name(s)*

**Role in the workflow.** A host opts into the core tools by copying these `ToolDefinition`s into `AgentDefinition.tools`; the model sees their names, descriptions and schemas.

**Contents**

- `core_tool_definitions() -> list[ToolDefinition]` - Returns the 18 declarations: read_file, glob, grep, write_draft, edit_draft, read_artifact, grep_artifact, diff_declared_artifacts, get_tool_result, web_fetch, render_pdf_page, web_search, sleep, ask_human_question, brief, notebook_edit, run_registered_command, request_elastic_node (an action tool that queues an exploration request; its optional `handoff` carries notes for the join that resumes the requester). · *Called by:* `agent/task_runner.py::AgentTaskRunner._builtin_definitions`
  - `core_tool_definitions.definition(name: str, description: str, schema: dict[str, Any], *, write: bool=False) -> ToolDefinition` - Builds one `ToolDefinition`: write tools are action/serial, all others exploratory/parallel-safe. · *Called by:* `base_agent/agent.py::BaseAgent.__init__`, `base_agent/agent.py::BaseAgent._accept_final_turn`, `base_agent/agent.py::BaseAgent._execute_tool_call`, `base_agent/agent.py::BaseAgent._record_executed_result` (+26 more)

---

### `tools/core/helpers.py` - private validation, HTTP and isolated-regex helpers behind the core tools

*581 lines · depends on: `foundations/hashing.py`, `foundations/text.py`, `foundations/version.py` · used by: `tools/core/services.py` · not re-exported at the package root*

**Role in the workflow.** Called only by `CoreToolDispatcher` (mostly through `asyncio.to_thread`) to implement grep, web_fetch, render_pdf_page and argument validation. Regex work runs in the standalone `regex_worker.py` script in a separate interpreter, and every fetch connects only to addresses it has validated as public.

**Contents**

- `_worker_interpreter() -> str` - The interpreter used for the regex worker: the base interpreter of a virtual environment when there is one, else `sys.executable`. · *Called within this file by:* `tools/core/helpers.py::_bounded_regex_search`
- `_bounded_regex_search(pattern: str, case_sensitive: bool, limit: int, documents: list[tuple[str, str]], timeout_seconds: float) -> dict[str, Any]` - Runs an untrusted regex in a separate interpreter (`-I -S`, the standalone `regex_worker.py`, which imports nothing from the SDK and so does not re-run the host's `__main__`), feeding the request as JSON on stdin and killing the child at the deadline; maps failures to `GREP_REGEX_TIMEOUT`, `GREP_REGEX_INVALID` and `GREP_REGEX_WORKER_FAILED` errors that carry the reason. · *Called by:* `core/services.py::CoreToolDispatcher._grep`
- `_http_fetch_hint(status_code: int) -> str` - Returns a ` Likely cause: ...` suffix for common HTTP statuses (401, 403, 404, 410, 429, 5xx) or an empty string. · *Called by:* `core/helpers.py::_fetch_pdf_bytes_with_redirects`, `core/helpers.py::_fetch_public_text`
- `_connect_pinned(addresses: tuple[str, ...], port: int, timeout: float | None, source_address: tuple[str, int] | None) -> socket.socket` - Connects to the first of the validated addresses that accepts, raising the last error (or one saying no address was available).
- **class `_PinnedHTTPConnection`** *(class; bases: http.client.HTTPConnection)* - HTTP connection that dials only `pinned_addresses`.
  - `_PinnedHTTPConnection.connect() -> None` - Uses `_connect_pinned` when addresses are pinned, else the default connect.
- **class `_PinnedHTTPSConnection`** *(class; bases: http.client.HTTPSConnection)* - HTTPS connection that dials only `pinned_addresses` and verifies TLS against the original host name.
  - `_PinnedHTTPSConnection.connect() -> None` - Connects to a pinned address, then wraps the socket with `server_hostname` set to the URL's host.
- **class `_FetchedBody`** *(dataclass)* - One fetch result: final URL, content type, charset, status, body bytes and whether the deadline cut the body short.
  - fields: `url`, `content_type`, `charset`, `status`, `body`, `deadline_reached`
- `_http_get_public(url: str, tool_name: str, *, byte_limit_for: Callable[[str], int], deadline_seconds: float) -> _FetchedBody` - Shared GET for web_fetch and render_pdf_page: for each of at most four redirect hops it validates the URL, pins the connection to the validated addresses, applies the remaining share of the total deadline, rejects non-2xx statuses with a likely-cause hint and reads the body up to the limit for its content type.
- `_read_until(response: http.client.HTTPResponse, sock: socket.socket | None, limit: int, end: float) -> tuple[bytes, bool]` - Reads the response body in slices of at most 5 s until the byte limit, the end of the body or the deadline; returns the bytes and whether the deadline was reached.
- `_fetch_public_text(url: str, max_chars: int, *, page: int=1, image_cache: dict[str, tuple[str, bytes]] | None=None, pdf_bytes_cache: dict[str, bytes] | None=None, de...` - web_fetch implementation: fetches through `_http_get_public` (SSRF-checked, pinned, at most four manual redirects, no proxy, one total deadline, default 30 s), parses PDFs page by page (cached bytes) and otherwise accepts only text/html/json/xml, returning bounded content marked untrusted (with `deadline_exceeded` when the body was cut short). · *Called by:* `core/services.py::CoreToolDispatcher._web_fetch`
  - `_fetch_public_text.byte_limit_for(content_type: str) -> int` - Body limit by content type: the PDF limit, four bytes per requested character for textual types, otherwise an error rejecting the type.
- `_to_png_bytes(pil_image: Any) -> bytes | None` - Normalizes a PIL image to PNG bytes (CMYK converted to RGB); returns None if it cannot be encoded. · *Called by:* `core/helpers.py::_parse_pdf_page`, `core/helpers.py::_render_pdf_page`
- `_fetch_pdf_bytes_with_redirects(url: str, deadline_seconds: float=_DEFAULT_FETCH_DEADLINE_SECONDS) -> bytes` - Downloads a PDF (max 20 MB, <= 4 redirects, SSRF-checked, within the total deadline) for page rendering; a non-PDF content type, an oversize file or a missed deadline raises. · *Called by:* `core/helpers.py::_render_pdf_page`
  - `_fetch_pdf_bytes_with_redirects.byte_limit_for(content_type: str) -> int` - The PDF byte limit, or an error naming a non-PDF content type.
- `_render_pdf_page(url: str, page: int, scale: float, pdf_bytes_cache: dict[str, bytes] | None, image_cache: dict[str, tuple[str, bytes]] | None) -> dict[str, Any]` - Renders one PDF page with pypdfium2 at a scale of 1 to 4, stores the PNG in the image cache under a `pdf:<urlhash>:p<N>:page` ref and returns the ref and page count.
- `_parse_pdf_page(raw: bytes, url: str, page: int, max_chars: int, image_cache: dict[str, tuple[str, bytes]] | None) -> dict[str, Any]` - Extracts text with pypdf from the requested page onward until the character budget is hit, lists each page's embedded images (refs cached as PNG) and reports `pages_included`, `next_page` and truncation. Pages after the first are included only when they fit whole; when the first page alone holds more than `max_chars`, the text is cut and the result says so (`truncated`, `text_truncated`, `omitted_chars`) instead of looking complete. · *Called by:* `core/helpers.py::_fetch_public_text`
- `_is_non_public_address(address: str) -> bool` - True for any address that is not globally routable (private, loopback, link-local, reserved, shared) or multicast, also when the address embeds such an IPv4 address (IPv4-mapped, 6to4 or NAT64 forms). · *Called by:* `tools/core/helpers.py::_assert_public_http_url`
- `_assert_public_http_url(url: str, tool_name: str='web_fetch') -> list[str]` - SSRF guard: requires an absolute http(s) URL, rejects localhost names, resolves the host and rejects every non-public address; returns the validated addresses so the caller can pin the connection to them. · *Called by:* `core/helpers.py::_fetch_pdf_bytes_with_redirects`, `core/helpers.py::_fetch_public_text`
- `_read_lines(path: Path, offset: int, limit: int, max_bytes: int) -> dict[str, Any]` - Reads a regular UTF-8 file under a byte limit and returns a window of numbered lines (split on newline only) plus a truncation flag. · *Called by:* `core/services.py::CoreToolDispatcher._dispatch`
- `_required_text(arguments: dict[str, Any], key: str) -> str` - Argument must be a non-empty string. · *Called by:* `core/services.py::CoreToolDispatcher._ask_human`, `core/services.py::CoreToolDispatcher._diff`, `core/services.py::CoreToolDispatcher._dispatch`, `core/services.py::CoreToolDispatcher._edit_draft` (+9 more)
- `_text_argument(arguments: dict[str, Any], key: str) -> str` - Like `_required_text` but accepts an empty string (for content and replacement text). · *Called by:* `tools/core/services.py::CoreToolDispatcher._edit_draft`, `tools/core/services.py::CoreToolDispatcher._notebook_edit`, `tools/core/services.py::CoreToolDispatcher._write_draft`
- `_nonnegative_int(value: Any, name: str) -> int` - Argument must be a non-negative integer (not a bool). · *Called by:* `core/helpers.py::_bounded_int`, `core/services.py::CoreToolDispatcher._dispatch`
- `_bounded_int(value: Any, name: str, lower: int, upper: int) -> int` - Non-negative integer within [lower, upper]. · *Called by:* `core/services.py::CoreToolDispatcher._dispatch`, `core/services.py::CoreToolDispatcher._grep`, `core/services.py::CoreToolDispatcher._notebook_edit`, `core/services.py::CoreToolDispatcher._read_result` (+3 more)
- `_leaves_run_root(pattern: str) -> bool` - True if a glob pattern is rooted, drive-qualified or contains `..` under either the POSIX or the Windows path convention, so a Windows-style pattern is refused on every platform with the same error. · *Called by:* `core/services.py::CoreToolDispatcher._glob`
- `_bounded_float(value: Any, name: str, lower: float, upper: float) -> float` - Number within [lower, upper] (not a bool). · *Called by:* `core/services.py::CoreToolDispatcher._dispatch`, `core/services.py::CoreToolDispatcher._render_pdf_page`
- `_strip_html(value: str) -> str` - Removes tags and unescapes entities. · *Called by:* `core/services.py::DuckDuckGoHtmlClient._search`

**Algorithms & invariants.** Every network helper names its tool in error text and appends a likely-cause hint for HTTP errors; every redirect hop is re-validated against the public-address rules (including IPv4 addresses embedded in IPv6 forms), and the connection is pinned to the validated addresses so a second DNS answer cannot redirect it. One total deadline bounds the connect, headers and body of a fetch.

*Module-level names:* `_MAX_PDF_BYTES`, `_HTTP_FETCH_STATUS_HINTS`

---

### `tools/core/regex_worker.py` - standalone regex worker run in an isolated interpreter

*38 lines · depends on: nothing in the package · used by: no other module (entry point or re-exported only) · not re-exported at the package root*

**Role in the workflow.** `_bounded_regex_search` starts this script as `python -I -S regex_worker.py` and sends one JSON request on stdin (pattern, case flag, match limit and the documents split into lines). It imports only the standard library and none of the SDK, so the child neither pays the SDK's import cost nor re-executes the host program's `__main__`, and the parent can kill it when the deadline passes.

**Contents**

- `main() -> None` - Reads the request, compiles the pattern (case-insensitive unless asked otherwise), searches every line of every document and writes one JSON result: the matches (path, 1-based line, text cut to 1,000 characters) and whether the limit stopped the search, or `{error: ...}` for an invalid pattern (the regex error text) or any other failure (`Type: message`). · *Called within this file by:* `core/regex_worker.py::<module>`
- `_emit(outcome: dict[str, object]) -> None` - Writes the outcome as JSON to stdout and flushes. · *Called within this file by:* `tools/core/regex_worker.py::main`

**Algorithms & invariants.** The pattern runs with Python's backtracking `re` engine, so a pathological pattern can run for ever; isolation in a separate process is what makes the deadline enforceable.

---

### `tools/core/services.py` - portable governed tools: dispatcher, services and web search

*488 lines · depends on: `foundations/contracts.py`, `foundations/detached.py`, `foundations/hashing.py`, `foundations/paths.py`, `foundations/text.py`, `foundations/version.py`, `memory/context_projection.py`, `tools/artifacts.py`, `tools/core/helpers.py`, `tools/policy.py` · used by: `tools/core/__init__.py` · re-exported at the package root: 4 name(s)*

**Role in the workflow.** `CoreToolDispatcher.execute(name, arguments)` is the implementation behind the core tool names. `HarnessToolExecutor` calls it after policy checks; hosts that skip the governed harness (such as the job-agent app) call it directly. Heavy work (grep scanning, web fetch, PDF render, search) runs in worker threads; small file operations run inline.

**Contents**

- **class `SearchResult`** *(dataclass)* - One web search hit: title, URL, snippet. · *Instantiated by:* `core/services.py::DuckDuckGoHtmlClient._search`
  - fields: `title`, `url`, `snippet`
- **class `WebSearchClient`** *(Protocol; bases: Protocol)* - Protocol for a pluggable search backend.
  - `WebSearchClient.search(query: str, limit: int) -> list[SearchResult]` *(async)* - Return up to `limit` results for a query.
- **class `DuckDuckGoHtmlClient`** *(class)* - Dependency-free default search client scraping DuckDuckGo's HTML endpoint; results are untrusted evidence. · *Instantiated by:* `agent/task_runner.py::AgentTaskRunner._prepare_tools`, `core/services.py::CoreToolDispatcher._web_search`
  - `DuckDuckGoHtmlClient.search(query: str, limit: int) -> list[SearchResult]` *(async)* - Runs the blocking search on a detached daemon thread (`run_detached`), so cancelling the run does not wait for the request. · *Called within this file by:* `core/services.py::DuckDuckGoHtmlClient._search`
  - `DuckDuckGoHtmlClient._search(query: str, limit: int) -> list[SearchResult]` *(staticmethod)* - POSTs the query, raises a clear error when the response is a rate-limit/bot challenge (so it is not mistaken for zero results), and parses titles, URLs and snippets with regexes. · *Called by:* `core/services.py::DuckDuckGoHtmlClient.search`
- **class `CoreToolServices`** *(dataclass)* - Dependencies and limits for the dispatcher: run root, artifact store, declared output paths, journal, search client, human responder, read/web/grep bounds and the PDF byte/image caches. · *Instantiated by:* `tools/registry.py::HarnessToolExecutor.__init__`
  - fields: `root`, `artifacts`, `declared_output_paths`, `result_journal`, `search_client`, `ask_human`, `max_read_bytes`, `max_web_chars`, `write_manifest`, `pdf_image_cache`, `pdf_bytes_cache`, `max_grep_files`, `max_grep_total_bytes`, `max_grep_seconds`, `max_fetch_seconds`, `read_scope`
  - `CoreToolServices.__post_init__() -> None` - Resolves the root, requires it to exist and validates that limits (including `max_fetch_seconds`) are positive and safe.
- **class `CoreToolDispatcher`** *(class)* - Executes one core tool by name; policy is applied by the caller. · *Instantiated by:* `tools/registry.py::HarnessToolExecutor.__init__`
  - `CoreToolDispatcher.__init__(services: CoreToolServices) -> None` - Stores the services.
  - `CoreToolDispatcher.execute(name: str, arguments: dict[str, Any]) -> ToolExecutionResult` *(async)* - Runs `_dispatch` and wraps any exception into a failed `ToolExecutionResult` with a `CORE_TOOL_EXECUTION_FAILED` failure naming the tool and exception type.
  - `CoreToolDispatcher._dispatch(name: str, arguments: dict[str, Any]) -> Any` *(async)* - Name-keyed if-chain mapping each tool name to its implementation; unknown names raise. · *Called within this file by:* `core/services.py::CoreToolDispatcher.execute`
  - `CoreToolDispatcher._path(relative_path: str) -> Path` - Resolves a run-root-relative path, rejecting absolute, escaping and credential paths; containment is judged with `relative_to_base`. · *Called by:* `agent/retention.py::_TombstoneAppender.__init__`, `agent/retention.py::_TombstoneAppender.record`, `integrations/_utils.py::assert_sanitized_interop_value`, `tools/approvals.py::ApprovalRegistry.__init__` (+5 more)
  - `CoreToolDispatcher._read_path(relative_path: str) -> Path` - Resolves a requested path for reading and raises, quoting it, when it is SDK-internal run state or outside the read scope. · *Called within this file by:* `tools/core/services.py::CoreToolDispatcher._dispatch`
  - `CoreToolDispatcher._is_readable(resolved: Path) -> bool` - False for anything whose first component starts with `.agent-` (telemetry, audit, run, journal and project-state directories) or, when a read scope is set, that lies outside every allowed path; paths are compared with `relative_to_base`.
  - `CoreToolDispatcher._glob(pattern: str, limit: int) -> list[str]` - Lists up to `limit` matching paths under the root, excluding credential paths, SDK-internal `.agent-*` state and anything outside the read scope; a rooted, drive-qualified or `..` pattern is refused naming the pattern (`_leaves_run_root`), and each match is reported relative to the root (`relative_to_base`). · *Called by:* `core/services.py::CoreToolDispatcher._dispatch`, `core/services.py::CoreToolDispatcher._grep_documents`
  - `CoreToolDispatcher._grep(arguments: dict[str, Any]) -> dict[str, Any]` *(async)* - Collects candidate files in a thread, then runs the regex search in the isolated child process. · *Called by:* `core/services.py::CoreToolDispatcher._dispatch`
  - `CoreToolDispatcher._grep_documents(file_glob: str) -> tuple[list[tuple[str, str]], bool]` - Reads matching UTF-8 files within the file-count and byte limits and reports whether the scan was truncated. · *Called by:* `core/services.py::CoreToolDispatcher._grep`
  - `CoreToolDispatcher._write_draft(arguments: dict[str, Any]) -> dict[str, Any]` - Writes a declared output path through the artifact store with the write manifest. · *Called by:* `core/services.py::CoreToolDispatcher._dispatch`
  - `CoreToolDispatcher._require_declared(path: str, label: str) -> None` - Raises naming the path and the declared output paths when a write targets a path the task did not declare.
  - `CoreToolDispatcher._edit_draft(arguments: dict[str, Any]) -> dict[str, Any]` - Exact-text replacement in a declared draft (an error naming the path if the text is not found, or occurs several times without `replace_all`), re-registered as a new artifact. · *Called by:* `core/services.py::CoreToolDispatcher._dispatch`
  - `CoreToolDispatcher._diff(arguments: dict[str, Any]) -> dict[str, Any]` - Unified diff of two artifact ids. · *Called by:* `core/services.py::CoreToolDispatcher._dispatch`
  - `CoreToolDispatcher._read_artifact(arguments: dict[str, Any]) -> dict[str, Any]` - Returns an artifact's text by id. · *Called by:* `core/services.py::CoreToolDispatcher._dispatch`
  - `CoreToolDispatcher._grep_artifact(arguments: dict[str, Any]) -> dict[str, Any]` - Returns the line numbers in an artifact containing a literal string. · *Called by:* `core/services.py::CoreToolDispatcher._dispatch`
  - `CoreToolDispatcher._read_result(arguments: dict[str, Any]) -> dict[str, Any]` - Reads a journal handle and returns its JSON truncated to `max_chars` with a content hash (the way the model retrieves compacted results). · *Called by:* `core/services.py::CoreToolDispatcher._dispatch`
  - `CoreToolDispatcher._web_fetch(arguments: dict[str, Any]) -> dict[str, Any]` *(async)* - Validates arguments and runs `_fetch_public_text` on a detached daemon thread (`run_detached`) with the PDF caches and the services' `max_fetch_seconds` deadline. · *Called by:* `core/services.py::CoreToolDispatcher._dispatch`
  - `CoreToolDispatcher._render_pdf_page(arguments: dict[str, Any]) -> dict[str, Any]` *(async)* - Validates arguments and runs the PDF page render on a detached daemon thread (`run_detached`). · *Called within this file by:* `core/services.py::CoreToolDispatcher._dispatch`
  - `CoreToolDispatcher._web_search(arguments: dict[str, Any]) -> dict[str, Any]` *(async)* - Uses the configured (or default DuckDuckGo) client and returns results marked untrusted. · *Called by:* `core/services.py::CoreToolDispatcher._dispatch`
  - `CoreToolDispatcher._ask_human(arguments: dict[str, Any]) -> dict[str, Any]` *(async)* - Awaits the configured human responder; error if none is configured. · *Called by:* `core/services.py::CoreToolDispatcher._dispatch`
  - `CoreToolDispatcher._notebook_edit(arguments: dict[str, Any]) -> dict[str, Any]` - Replaces or appends to one cell of a declared notebook JSON (a malformed notebook raises naming the path), extending the cell list as needed, and saves it as a new artifact. · *Called by:* `core/services.py::CoreToolDispatcher._dispatch`
  - `CoreToolDispatcher._load_notebook(path: str, target: Path) -> dict[str, Any]` *(staticmethod)* - Loads a notebook, or a fresh empty one when the file does not exist; invalid JSON or a non-list `cells` raises naming the path and the problem.

*Module-level names:* `HumanQuestionResponder`

---

### `tools/delegation.py` - delegated sub-runs on isolated git worktrees

*128 lines · depends on: `tools/task_models.py`, `tools/tasks.py`, `tools/worktree_models.py`, `tools/worktrees.py` · used by: no other module (entry point or re-exported only) · re-exported at the package root: 3 name(s)*

**Role in the workflow.** Composes the task manager and worktree manager so a host can hand off a bounded sub-run (typically another `BaseAgent.run`) without its file edits colliding with the caller's.

**Contents**

- **class `DelegatedRunContext`** *(dataclass)* - What a run factory receives: its isolated worktree, if any. · *Instantiated by:* `tools/delegation.py::SubagentCoordinator.delegate.run_with_cleanup`
  - fields: `worktree`
- **class `SubagentCoordinator`** *(class)* - Starts, polls, awaits and cancels delegated runs tracked in one shared `BackgroundTaskManager`.
  - `SubagentCoordinator.__init__(tasks: BackgroundTaskManager, *, worktrees: AgentWorktreeManager | None=None) -> None` - Stores the task manager, the optional worktree manager, an id list and a per-task map of cleanup warnings.
  - `SubagentCoordinator.delegate(description: str, run_factory: DelegatedRunFactory, *, repository_path: Path | None=None, worktree_slug: str | None=None, agent_id: str | None=Non...` *(async)* - Optionally creates a worktree for `worktree_slug`, then starts the factory's coroutine as an agent-run task, removing the worktree afterwards if requested; a cleanup failure never replaces the run's own outcome and is kept as a warning. · *No in-package callers (public API, entry point, or protocol hook).*
    - `SubagentCoordinator.delegate.cleanup() -> None` *(async)* - Removes the worktree when requested; any error is recorded as a warning naming the slug and the exception.
    - `SubagentCoordinator.delegate.run_with_cleanup() -> Any` *(async)* - Runs the factory, then `cleanup`, also when the run raised; returns the run's outcome. · *Called by:* `tools/delegation.py::SubagentCoordinator.delegate`
  - `SubagentCoordinator.cleanup_warnings(task_id: str) -> list[str]` - The cleanup problems recorded for a delegated task.
  - `SubagentCoordinator.get_delegation(task_id: str) -> TaskRecord | None` - The task record for an id. · *No in-package callers (public API, entry point, or protocol hook).*
  - `SubagentCoordinator.list_delegations(*, status: TaskStatus | None=None) -> list[TaskRecord]` - Only the delegations this coordinator started, optionally filtered by status. · *No in-package callers (public API, entry point, or protocol hook).*
  - `SubagentCoordinator.await_delegation(task_id: str, *, timeout: float | None=None) -> TaskRecord` *(async)* - Waits for a delegated task to finish (with optional timeout). · *No in-package callers (public API, entry point, or protocol hook).*
  - `SubagentCoordinator.cancel_delegation(task_id: str) -> TaskRecord` *(async)* - Stops a delegated task. · *No in-package callers (public API, entry point, or protocol hook).*

*Module-level names:* `DelegatedRunFactory`

---

### `tools/elastic_requests.py` - agent-side queue and tool executor for elastic spawn requests

*160 lines · depends on: `foundations/contracts.py`, `state/elastic.py`, `state/graph_models.py`, `tools/tools.py` · used by: `agent/graph_agent_executor.py` · re-exported at the package root: 3 name(s)*

**Role in the workflow.** How an agent asks for exploration. The `request_elastic_node` tool (declared in `core_tool_definitions`, granted to a profile through the `graph.elastic.request` capability) never touches the graph: it queues a typed request that `GraphAgentExecutor` attaches to the node's result when the run completes, so the controller, not the agent, decides what runs.

**Contents**

- **class `ElasticRequestRejected`** *(exception; bases: Exception)* - Raised by the queue with the refusal code and the message naming the numbers. · *Instantiated by:* `tools/elastic_requests.py::ElasticRequestBuffer.add`
  - `ElasticRequestRejected.__init__(problem: ElasticProblem) -> None` - Keeps the code and uses the problem's message.
- **class `ElasticRequestBuffer`** *(class)* - One node run's queue of elastic requests. `add` applies the same admission rules as the scheduler (duplicate id, visible dependencies, narrowing routing references, a per-task limit of 32) and checks the capacity the node was handed, counting the join each batch adds. With a reservation from the scheduler the check also counts what tasks running at the same time have queued, first come, first served. It refuses overflow unless the binding escalates it, and always refuses a request that no capacity grant could admit (a ceiling). The scheduler stays the authority at commit. · *Instantiated by:* `agent/graph_agent_executor.py::GraphAgentExecutor.execute`
  - `ElasticRequestBuffer.__init__(node: GraphNode, capacity: ElasticCapacity | None, visible_dependencies: Collection[str], *, limit: int=MAX_ELASTIC_REQUESTS_PER_RESULT, escalate_...` - Takes the node, the capacity from its execution context (None skips the capacity check), the dependencies it can see, the per-task limit, the escalate-overflow flag and the optional reservation handle.
  - `ElasticRequestBuffer.requests() -> list[ElasticSpawnRequest]` *(property)* - Property: a copy of the queued requests, in the order they were made. · *Called by:* `agent/graph_agent_executor.py::GraphAgentExecutor.execute`, `state/graph.py::StateGraph._apply_spawn_requests`, `state/graph.py::StateGraph._filtered_shared_state_for`, `state/graph.py::StateGraph.request_lateral_dependency` (+2 more)
  - `ElasticRequestBuffer.add(request: ElasticSpawnRequest) -> dict[str, Any]` - Queues one request and returns the receipt (request id, queued count, child depth, nodes remaining after the queue, whether a capacity decision will be needed and when it will run), or raises `ElasticRequestRejected` naming the code and numbers. With a reservation the whole queue is reserved first; an escalated overflow gives the hold back, because that batch waits for a controller decision. A request whose `handoff` would bring the queue's handoffs past 8,000 characters is refused with `ELASTIC_HANDOFF_LIMIT_REACHED`, naming the lengths. · *Called by:* `developer_tools/inspect.py::verify_project_evidence`, `foundations/contracts.py::ModelBinding.fallback_bindings_are_distinct`, `foundations/dependency_graph.py::deterministic_cycles`, `foundations/dependency_graph.py::reverse_reachable_nodes` (+20 more)
  - `ElasticRequestBuffer._remaining_after_queue() -> int | None` - Elastic nodes still free after this queue: the reservation's unreserved count, else the capacity's remaining nodes less the queued requests and the join; None without either. · *Called by:* `tools/elastic_requests.py::ElasticRequestBuffer.add`
  - `ElasticRequestBuffer._overflow(requested: int) -> ElasticProblem | None` - The problem with queueing `requested` requests, or None: asked of the reservation when there is one (which holds the capacity on success), otherwise checked against the capacity the node was handed. · *Called by:* `tools/elastic_requests.py::ElasticRequestBuffer.add`
- **class `ElasticRequestToolExecutor`** *(class)* - Tool executor that serves `request_elastic_node` from the queue and passes every other tool to the wrapped executor (or fails naming the tool when there is none). · *Instantiated by:* `agent/graph_agent_executor.py::GraphAgentExecutor.execute`
  - `ElasticRequestToolExecutor.__init__(inner: ToolExecutor | None, buffer: ElasticRequestBuffer) -> None` - Stores the wrapped executor and the queue.
  - `ElasticRequestToolExecutor.execute(tool: ToolDefinition, context: ToolInvocationContext) -> ToolExecutionResult` *(async)* - For `request_elastic_node`: validates the arguments into an `ElasticSpawnRequest` (failure `ELASTIC_REQUEST_INVALID` naming each field) and queues it (failure with the refusal code); success returns the receipt.

---

### `tools/policy.py` - deny-by-default capability policy

*176 lines · depends on: `foundations/contracts.py`, `foundations/paths.py`, `tools/approvals.py` · used by: `agent/orchestrator/models.py`, `agent/task_files.py`, `agent/task_runner.py`, `mcp/client_bridge.py`, `tools/core/services.py`, `tools/registry.py` · re-exported at the package root: 3 name(s)*

**Role in the workflow.** `HarnessToolExecutor.execute` asks `CapabilityPolicy.evaluate` before every governed tool call and either runs, blocks, or requests an approval based on the answer.

**Contents**

- `sensitive_pattern_for(path: Path | str) -> str | None` - The first built-in credential pattern that matches a path (compared case-insensitively on the forward-slash form, also with a trailing slash so a whole directory such as `.ssh` is caught), or None. The policy, the core read tools and the task file reader share it. · *Called by:* `agent/task_files.py::_entry`, `agent/task_files.py::_target`, `core/services.py::CoreToolDispatcher._glob`, `core/services.py::CoreToolDispatcher._path` (+1 more)
- **class `SideEffectClass`** *(enum; bases: StrEnum)* - read-only, mutating, process or destructive.
  - members: `READ_ONLY`, `MUTATING`, `PROCESS`, `DESTRUCTIVE`
- **class `CapabilityGrant`** *(pydantic model; bases: StrictModel)* - What one role may do: capability names and the paths it may touch. · *Instantiated by:* `agent/task_runner.py::AgentTaskRunner._prepare_tools`
  - fields: `role`, `capabilities`, `allowed_paths`
- **class `PolicyDecision`** *(pydantic model; bases: StrictModel)* - Allowed or not, with the reason, whether approval is required and the request if any. · *Instantiated by:* `tools/policy.py::CapabilityPolicy.evaluate`
  - fields: `allowed`, `reason`, `approval_required`, `approval_request`
- **class `CapabilityPolicy`** *(class)* - Checks sensitive paths, role grant, path containment and typed approval state in that order. · *Instantiated by:* `agent/task_runner.py::AgentTaskRunner._prepare_tools`
  - `CapabilityPolicy.__init__(grants: list[CapabilityGrant]) -> None` - Indexes grants by role.
  - `CapabilityPolicy.read_scope(role: str) -> tuple[str, ...]` - The paths a role may read: its grant's `allowed_paths` when the grant holds `filesystem.read`, otherwise none. · *Called by:* `tools/registry.py::HarnessToolExecutor.__init__`
  - `CapabilityPolicy.evaluate(*, role: str, capability: str, side_effect: SideEffectClass, run_root: Path, requested_paths: list[str]=(), approval: ApprovalRequest | None=None)...` - Order: deny built-in credential paths (no grant can override) -> role must hold the capability -> every requested path must be inside the grant's allowed paths -> read-only is allowed; anything else needs a matching, approved typed approval (missing, pending and rejected each have their own reason).
  - `CapabilityPolicy._sensitive_pattern_match(requested_path: str, run_root: Path) -> str | None` *(staticmethod)* - Resolves a requested path against the run root and returns `sensitive_pattern_for` of it. · *Called by:* `tools/policy.py::CapabilityPolicy.evaluate`
  - `CapabilityPolicy._path_is_allowed(requested_path: str, allowed_paths: list[str], run_root: Path) -> bool` *(staticmethod)* - True if the resolved path stays under the run root and under at least one allowed path (empty list means nothing is allowed); both tests use `relative_to_base`. · *Called by:* `tools/policy.py::CapabilityPolicy.evaluate`

**Algorithms & invariants.** `SENSITIVE_PATH_PATTERNS` (ssh keys, aws, gcloud, azure, gnupg, docker, kube, netrc, `.env*`, `.npmrc`, `.pypirc`, `.git-credentials`, `.pgpass`, `*.pem`, `*.p12`, `*.pfx`, private keys) is matched case-insensitively and checked before any grant is consulted.

*Module-level names:* `SENSITIVE_PATH_PATTERNS`

---

### `tools/registry.py` - capability-bound harness tool registry and its BaseAgent executor

*327 lines · depends on: `foundations/contracts.py`, `foundations/identifiers.py`, `memory/context_projection.py`, `state/elastic.py`, `state/planning.py`, `tools/approvals.py`, `tools/artifacts.py`, `tools/core/__init__.py`, `tools/policy.py`, `tools/supervisor.py`, `tools/tools.py` · used by: `agent/orchestrator/orchestrator.py`, `agent/task_runner.py`, `mcp/client_bridge.py` · re-exported at the package root: 5 name(s)*

**Role in the workflow.** The governed `ToolExecutor`: `BaseAgent` hands it every tool call; it applies policy and approvals, then runs a host handler, a core tool, a registered process command or a built-in spec/artifact reader. The registry is closed: no generic shell and no dynamically named tool.

**Contents**

- **class `HarnessExecutionContext`** *(dataclass)* - Non-prompt state for one scheduled node: run/node/role, plan task, run root, artifact store, policy, approvals, supervisor, spec snapshots, declared outputs, approval ids, journal, search client, human responder and PDF image cache. · *Instantiated by:* `agent/task_runner.py::AgentTaskRunner._prepare_tools`
  - fields: `run_id`, `node_id`, `role`, `plan_task`, `run_root`, `artifacts`, `policy`, `approvals`, `supervisor`, `spec_snapshots`, `declared_output_paths`, `approval_ids_by_capability`, `result_journal`, `search_client`, `ask_human`, `pdf_image_cache`
- **class `RegisteredTool`** *(dataclass)* - Name, capability, side-effect class and an optional command template name. · *Instantiated by:* `mcp/client_bridge.py::mcp_tools_as_extensions`, `tools/registry.py::HarnessToolRegistry.default_tools`
  - fields: `name`, `capability`, `side_effect`, `command_template`
- **class `HarnessToolRegistry`** *(class)* - Closed registry of tool names with optional custom handlers. · *Instantiated by:* `orchestrator/orchestrator.py::Orchestrator.__init__`, `agent/task_runner.py::AgentTaskRunner._prepare_tools`
  - `HarnessToolRegistry.__init__(tools: list[RegisteredTool] | None=None, *, custom_handlers: dict[str, HarnessToolHandler] | None=None) -> None` - Builds the name map (default tools if none are given), rejecting duplicates and handlers for unregistered tools.
  - `HarnessToolRegistry.default_tools() -> list[RegisteredTool]` *(staticmethod)* - The 19 standard declarations (spec/file/artifact/web tools, sleep, brief, human question, `request_elastic_node` with capability `graph.elastic.request` and `run_registered_command`). No domain-specific command is built in: a host declares its own process tools, such as a linter or a build step, through `with_extensions`. · *Called by:* `tools/registry.py::HarnessToolRegistry.__init__`, `tools/registry.py::HarnessToolRegistry.with_extensions`
  - `HarnessToolRegistry.with_extensions(extensions: list[RegisteredTool], *, handlers: dict[str, HarnessToolHandler]) -> HarnessToolRegistry` *(classmethod)* - Default tools plus host-defined extra tools and their handlers. · *Called by:* `agent/task_runner.py::AgentTaskRunner._prepare_tools`
  - `HarnessToolRegistry.resolve(name: str) -> RegisteredTool | None` - Looks up a registered tool by name.
  - `HarnessToolRegistry.names() -> tuple[str, ...]` - Sorted tuple of registered names. · *Called by:* `agent/task_runner.py::AgentTaskRunner._builtin_definitions`, `agent/task_runner.py::AgentTaskRunner._prepare_tools`, `mcp/client.py::McpClientManager.__init__`, `tools/sandbox.py::DockerSandbox._enforced_limit_names` (+1 more)
  - `HarnessToolRegistry.handler_for(name: str) -> HarnessToolHandler | None` - The custom handler registered for a name, if any. · *Called by:* `tools/registry.py::HarnessToolExecutor._execute_registered`
- **class `HarnessToolExecutor`** *(class; bases: ToolExecutor)* - Bridges BaseAgent tool calls into policy-governed typed harness actions. · *Instantiated by:* `agent/task_runner.py::AgentTaskRunner._prepare_tools`
  - `HarnessToolExecutor.__init__(registry: HarnessToolRegistry, context: HarnessExecutionContext) -> None` - Stores the registry and context and builds an internal `CoreToolDispatcher` from the context, limiting reads to the role's read scope.
  - `HarnessToolExecutor.execute(tool: ToolDefinition, invocation: ToolInvocationContext) -> ToolExecutionResult` *(async)* - Resolve the tool, collect requested write paths, look up any approval for the capability, evaluate policy; blocked decisions return BLOCKED (filing an approval request when one is needed); otherwise run the tool and convert exceptions into a failed result with `HARNESS_TOOL_EXECUTION_FAILED`. · *Called within this file by:* `tools/registry.py::HarnessToolExecutor._execute_registered`
  - `HarnessToolExecutor._approval_for(capability: str)` - The approval registered for a capability in this context, else the latest request this node filed for it, so a decision made after the block is found. · *Called by:* `tools/registry.py::HarnessToolExecutor.execute`
  - `HarnessToolExecutor._execute_registered(tool: RegisteredTool, arguments: dict[str, Any]) -> Any` *(async)* - Dispatch chain: custom handler -> `read_spec` (only the plan task's scope pointer) -> `request_elastic_node` (always an error here: it only works for a graph node run through `GraphAgentExecutor`) -> `run_registered_command` -> core tool names -> authorization-checked artifact read/grep/diff -> command-template process tools. · *Called by:* `tools/registry.py::HarnessToolExecutor.execute`
  - `HarnessToolExecutor._requested_paths(tool_name: str, arguments: dict[str, Any]) -> list[str]` *(staticmethod)* - Write-style tools declare their `path` argument for the path-containment check. · *Called by:* `tools/registry.py::HarnessToolExecutor.execute`
- `_process_failure_message(name: str, process: ProcessExecutionRecord) -> str` - `<code>: registered command "<name>" <what happened>` for a failed process (exit code, timeout, cancellation or resource limit). · *Called by:* `tools/registry.py::HarnessToolExecutor._execute_registered`
- `_string_argument(arguments: dict[str, Any], key: str) -> str` - Argument must be a non-empty string. · *Called by:* `tools/registry.py::HarnessToolExecutor._execute_registered`

**Algorithms & invariants.** `read_artifact` and `grep_artifact` check the plan-task authorization here and then run the implementation in `core/services.py`; `diff_declared_artifacts` has its own authorization (including occurrence proof) here and calls `ArtifactStore.diff`.

*Module-level names:* `HarnessToolHandler`

---

### `tools/sandbox.py` - pluggable execution backends for registered command templates

*322 lines · depends on: `tools/sandbox_models.py`, `tools/supervisor.py` · used by: `tools/tasks.py` · re-exported at the package root: 4 name(s)*

**Role in the workflow.** Selected by the host and used by `BackgroundTaskManager.start_command_task`: the native backend runs the template in a throwaway scratch directory with a scrubbed environment; the Docker backend runs it in an ephemeral network-isolated container.

**Contents**

- **class `DockerUnavailableError`** *(exception; bases: RuntimeError)* - Raised when the Docker binary cannot be found or run. · *Instantiated by:* `tools/sandbox.py::DockerSandbox._run_with_env_file`
- **class `SandboxBackend`** *(Protocol; bases: Protocol)* - Protocol: run one `CommandTemplate` and return a `ProcessExecutionRecord`.
  - `SandboxBackend.run(template: CommandTemplate, *, cwd: Path, environment: EnvironmentPolicy | None=None) -> ProcessExecutionRecord` *(async)* - Protocol method.
- **class `NativeSandbox`** *(class)* - Runs a template via `ProcessSupervisor` in a fresh scratch directory that is deleted afterwards.
  - `NativeSandbox.__init__(*, scratch_parent: Path | None=None) -> None` - Optionally fixes the parent directory for scratch folders.
  - `NativeSandbox.run(template: CommandTemplate, *, cwd: Path, environment: EnvironmentPolicy | None=None) -> ProcessExecutionRecord` *(async)* - Creates the scratch directory, resolves the environment allowlist (the platform's minimal `default_native_environment` when none is given, never the whole host environment), runs the supervisor there and always removes the scratch directory.
- **class `DockerSandbox`** *(class)* - Runs a template inside `docker run --rm` with the working directory bind-mounted at `/workspace`.
  - `DockerSandbox.__init__(options: DockerSandboxOptions, *, docker_binary: str='docker') -> None` - Stores the options and docker binary name.
  - `DockerSandbox.run(template: CommandTemplate, *, cwd: Path, environment: EnvironmentPolicy | None=None) -> ProcessExecutionRecord` *(async)* - Validates the cwd, writes an env file if an environment policy is given (values that cannot be written to one travel as inherited `-e NAME` variables), runs the container and always deletes the env file.
  - `DockerSandbox._run_with_env_file(template: CommandTemplate, cwd: Path, container_name: str, env_file: Path | None, inherited: dict[str, str]) -> ProcessExecutionRecord` *(async)* - Launches the docker CLI, captures bounded output, enforces the template timeout (stopping the container), stops the container and abandons the output reader on cancellation, classifies the exit (137 means resource limit) and builds the `ProcessExecutionRecord`. · *Called by:* `tools/sandbox.py::DockerSandbox.run`
  - `DockerSandbox._build_argv(template: CommandTemplate, cwd: Path, env_file: Path | None, container_name: str, inherited_names: Sequence[str]=()) -> list[str]` - Builds the docker command: network none, read-only root with tmpfs, memory/cpu limits, mounts, env file, `-e NAME` for inherited variables, image and the template command. · *Called by:* `tools/sandbox.py::DockerSandbox._run_with_env_file`
  - `DockerSandbox._write_env_file(environment: EnvironmentPolicy) -> Path` *(staticmethod)* - Writes the scrubbed environment to a private temp `--env-file` so values never appear on a process command line; a value containing a newline, carriage return or NUL is left out (it is passed as an inherited variable instead). Every name is checked before the file is created, and the file is removed if writing fails. · *Called by:* `tools/sandbox.py::DockerSandbox.run`
  - `DockerSandbox._fits_env_file(name: str, value: str) -> bool` *(staticmethod)* - Validates the variable name and returns whether the value can be written as one env-file line.
  - `DockerSandbox._check_variable_name(name: str) -> None` *(staticmethod)* - Raises naming the variable when its name is empty or contains whitespace, a control character or `=`.
  - `DockerSandbox._inherited_variables(environment: EnvironmentPolicy) -> dict[str, str]` *(staticmethod)* - The resolved environment entries that do not fit an env file, to be passed through the docker CLI's own environment.
  - `DockerSandbox._enforced_limit_names() -> list[str]` - Names of the container limits that were applied (memory, cpu). · *Called by:* `tools/sandbox.py::DockerSandbox._run_with_env_file`
  - `DockerSandbox._stop_container(process: asyncio.subprocess.Process, container_name: str) -> list[str]` *(async)* - `docker kill` the container and wait up to 5 s for the CLI to exit; if it does not, `docker rm --force`, kill the CLI process and wait once more; returns the termination path.
  - `DockerSandbox._kill_container(container_name: str) -> list[str]` *(async)* - Runs `docker kill` through `_docker_admin` and reports the outcome. · *Called by:* `tools/sandbox.py::DockerSandbox._run_with_env_file`
  - `DockerSandbox._docker_admin(container_name: str, verb: str, label: str, *flags: str) -> list[str]` *(async)* - Runs `docker <verb> [flags] <container>` with a 10 s timeout and returns `[label]` on success or `[label_FAILED]` when it cannot start, times out or exits non-zero.

*Module-level names:* `_CONTAINER_WORKDIR`, `_DOCKER_SIGKILL_EXIT_CODE`

---

### `tools/sandbox_models.py` - typed configuration for sandbox backends

*59 lines · depends on: `foundations/contracts.py` · used by: `tools/sandbox.py`, `tools/supervisor.py`, `tools/tasks.py` · re-exported at the package root: 3 name(s)*

**Role in the workflow.** Used by the supervisor templates, native sandbox and Docker sandbox to decide what the child process sees.

**Contents**

- **class `SandboxKind`** *(enum; bases: StrEnum)* - native or docker.
  - members: `NATIVE`, `DOCKER`
- **class `EnvironmentPolicy`** *(pydantic model; bases: StrictModel)* - An explicit environment allowlist plus literal overrides; the host environment is never inherited implicitly.
  - fields: `allowed_variable_names`, `literal_variables`
  - `EnvironmentPolicy.resolve() -> dict[str, str]` - Returns only the named host variables that exist, plus the literal overrides.
- `default_native_environment() -> EnvironmentPolicy` - Allowlist-only environment for the native sandbox: `SYSTEMROOT`, `WINDIR`, `PATH`, `PATHEXT`, `COMSPEC`, `TEMP`, `TMP` on Windows and `PATH`, `LANG`, `LC_ALL`, `TMPDIR` elsewhere. · *Called by:* `tools/sandbox.py::NativeSandbox.run`
- **class `DockerSandboxOptions`** *(pydantic model; bases: StrictModel)* - Container settings: image, network disabled (default), read-only root (default), memory/cpu limits and extra binds.
  - fields: `image`, `network_disabled`, `read_only_root`, `memory_bytes`, `cpu_limit`, `extra_binds`

---

### `tools/supervisor.py` - registered-command execution with timeout, bounded output and process-tree kill

*491 lines · depends on: `foundations/contracts.py`, `observability/telemetry_models.py`, `observability/telemetry_store.py`, `tools/sandbox_models.py` · used by: `agent/task_runner.py`, `tools/registry.py`, `tools/sandbox.py`, `tools/tasks.py` · re-exported at the package root: 8 name(s)*

**Role in the workflow.** `run_registered_command` and any process tool a host declares reach this supervisor. It never runs a raw string: only a host-registered `CommandTemplate`, with watchdog events recorded to telemetry.

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
- **class `ProcessSupervisor`** *(class)* - Runs only registered commands with bounded capture, a timeout and process-tree termination. · *Instantiated by:* `agent/task_runner.py::AgentTaskRunner._prepare_tools`, `tools/sandbox.py::NativeSandbox.run`
  - `ProcessSupervisor.__init__(templates: list[CommandTemplate]=(), *, telemetry: TelemetryStore | None=None, telemetry_context_factory: Callable[[str], TelemetryContext] | None...` - Indexes templates by name and stores telemetry wiring.
  - `ProcessSupervisor.template(name: str) -> CommandTemplate | None` - A registered template by name, or None. · *Called by:* `agent/task_runner.py::AgentTaskRunner.run`, `agent/task_runner.py::TaskRunOptions.options_are_consistent`, `tools/sandbox.py::DockerSandbox._build_argv`, `tools/sandbox.py::DockerSandbox._run_with_env_file` (+6 more)
  - `ProcessSupervisor.execute(template_name: str, *, cwd: Path, env: dict[str, str] | None=None) -> ProcessExecutionRecord` *(async)* - Validates the template and cwd, resolves the environment (call-site override, else the template policy, else inherit), and retries retryable idempotent failures with linear backoff.
  - `ProcessSupervisor._execute_once(template: CommandTemplate, *, cwd: Path, attempts: int, env: dict[str, str] | None=None) -> ProcessExecutionRecord` *(async)* - Spawns the process in its own group/session (an OS error at spawn is re-raised naming the template and command after a FAILED watchdog event), captures output concurrently, waits for exit by polling the return code with the timeout, terminates the group on timeout or cancellation, drains output for at most a second, classifies the exit and emits watchdog events. · *Called by:* `tools/supervisor.py::ProcessSupervisor.execute`
  - `ProcessSupervisor._terminate_process_group(process: asyncio.subprocess.Process) -> list[str]` *(async, staticmethod)* - POSIX: SIGTERM the group, wait 1 s, then SIGKILL; Windows: delegates to the taskkill helper; returns the termination path. · *Called by:* `tools/supervisor.py::ProcessSupervisor._execute_once`
- `_subprocess_launch_options(preexec: Callable[[], None] | None) -> dict[str, Any]` - Platform launch options: a new process group on Windows, a new session plus rlimit preexec on POSIX. · *Called by:* `tools/supervisor.py::ProcessSupervisor._execute_once`
- `_terminate_windows_process_tree(process: asyncio.subprocess.Process) -> list[str]` *(async)* - Runs `taskkill /T /F` on the process and always returns an auditable outcome string. · *Called by:* `tools/supervisor.py::ProcessSupervisor._terminate_process_group`
- **class `_OutputCapture`** *(class)* - Bounded reader of a process's combined output: keeps the first `limit` bytes in `retained` and counts every byte in `total`, so partial output survives even if the reader is abandoned.
  - `_OutputCapture.__init__(stream: asyncio.StreamReader | None, limit: int) -> None` - Stores the stream and limit and starts with empty buffers.
  - `_OutputCapture.run() -> None` *(async)* - Reads 64 KiB chunks until end of stream, retaining up to the limit.
- `_wait_for_exit(process: asyncio.subprocess.Process) -> None` *(async)* - Polls `returncode` (5 ms backing off to 50 ms) instead of `Process.wait`, which would also wait for every inherited pipe to close.
- `_drain_output(output_task: asyncio.Task[None], process: asyncio.subprocess.Process) -> list[str]` *(async)* - Waits up to a second for the reader to finish; otherwise abandons it and reports `OUTPUT_PIPE_HELD_BY_DESCENDANT` (a surviving child still holds the pipe).
- `_abandon_output(output_task: asyncio.Task[None], process: asyncio.subprocess.Process) -> None` *(async)* - Cancels the reader and closes the process transport so no pipe handle leaks.
- `_resource_preexec(limits: ResourceLimits) -> tuple[Callable[[], None] | None, list[str], list[str]]` - Builds the POSIX preexec function that applies rlimits and lists which limits were enforced or unsupported. · *Called by:* `tools/supervisor.py::ProcessSupervisor._execute_once`
  - `_resource_preexec.apply() -> None` - Child-side function that calls `setrlimit` for each requested limit. The CPU limit is soft at the requested seconds and hard one second later (never above the inherited hard limit), so a runaway child receives `SIGXCPU`, which is reported as resource-limited, before `SIGKILL`; the other limits use the same value for both. · *Called within this file by:* `tools/supervisor.py::_resource_preexec`
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

*258 lines · depends on: `foundations/logging.py`, `tools/sandbox.py`, `tools/sandbox_models.py`, `tools/supervisor.py`, `tools/task_models.py` · used by: `tools/delegation.py` · re-exported at the package root: 1 name(s)*

**Role in the workflow.** Runs a registered command through a sandbox backend, or tracks any host coroutine (usually `BaseAgent.run`), reporting every status change to an optional listener (so tasks can feed telemetry).

**Contents**

- **class `BackgroundTaskManager`** *(class)* - Owns each task's record and its `asyncio.Task`.
  - `BackgroundTaskManager.__init__(*, on_transition: TaskTransitionListener | None=None, max_retained_finished_tasks: int=1000) -> None` - Starts with no tasks and an optional transition listener; at most `max_retained_finished_tasks` (default 1,000, at least one) finished records are kept.
  - `BackgroundTaskManager.start_command_task(description: str, backend: SandboxBackend, template: CommandTemplate, *, cwd: Path, environment: EnvironmentPolicy | None=None) -> TaskRecord` - Registers a command task and starts `backend.run(template)` in the background; must run inside an event loop (`RuntimeError` otherwise). · *No in-package callers (public API, entry point, or protocol hook).*
  - `BackgroundTaskManager.start_agent_task(description: str, run: Awaitable[Any], *, summarize: Any=None) -> TaskRecord` - Registers an agent-run task and starts the supplied awaitable; the result is stored via `summarize` or when it is already a dict. Outside an event loop it closes the coroutine and raises `RuntimeError`. · *Called by:* `tools/delegation.py::SubagentCoordinator.delegate`
  - `BackgroundTaskManager.get_task(task_id: str) -> TaskRecord | None` - The record for an id, or None. · *Called by:* `tools/delegation.py::SubagentCoordinator.get_delegation`, `tools/delegation.py::SubagentCoordinator.list_delegations`
  - `BackgroundTaskManager.list_tasks(*, status: TaskStatus | None=None) -> list[TaskRecord]` - Records (optionally by status) in creation order. · *No in-package callers (public API, entry point, or protocol hook).*
  - `BackgroundTaskManager.stop_task(task_id: str) -> TaskRecord` *(async)* - Cancels a running task and marks it killed; a finished task is returned unchanged. If the caller is itself cancelled while waiting, the task is still marked killed and the caller's cancellation propagates. · *Called by:* `tools/delegation.py::SubagentCoordinator.cancel_delegation`
  - `BackgroundTaskManager.wait_for(task_id: str, *, timeout: float | None=None) -> TaskRecord` *(async)* - Waits, without cancelling the task, until it ends or the timeout elapses (raising `TimeoutError` naming the task) and returns its record. · *Called by:* `base_agent/agent.py::BaseAgent._await_with_watchdog`, `jev/decision.py::TypeSafeJevDecisionEvaluator._evaluate_with_bounded_attempts`, `tools/delegation.py::SubagentCoordinator.await_delegation`, `tools/sandbox.py::DockerSandbox._kill_container` (+4 more)
  - `BackgroundTaskManager._run_command(task_id: str, run: Awaitable[Any]) -> None` *(async)* - Awaits a command coroutine and records completed/failed from the process record or the exception (`Type: message`). · *Called by:* `tools/tasks.py::BackgroundTaskManager.start_command_task`
  - `BackgroundTaskManager._run_agent(task_id: str, run: Awaitable[Any], summarize: Any) -> None` *(async)* - Awaits an agent coroutine and records completed (with the summary) or failed (`Type: message`); a `summarize` that raises fails the task with `summarize raised ...`. · *Called by:* `tools/tasks.py::BackgroundTaskManager.start_agent_task`
  - `BackgroundTaskManager._allocate_id(kind: TaskKind) -> str` - `task-<kind>-<n>` ids. · *Called by:* `tools/tasks.py::BackgroundTaskManager.start_agent_task`, `tools/tasks.py::BackgroundTaskManager.start_command_task`
  - `BackgroundTaskManager._register(task_id: str, kind: TaskKind, description: str) -> TaskRecord` - Creates the RUNNING record and notifies the listener. · *Called within this file by:* `tools/tasks.py::BackgroundTaskManager.start_agent_task`, `tools/tasks.py::BackgroundTaskManager.start_command_task`
  - `BackgroundTaskManager._transition(task_id: str, status: TaskStatus, *, result: dict[str, Any] | None=None, error: str | None=None) -> TaskRecord` - Applies a new status with end time, result and error, drops the finished task from the running set, prunes the oldest finished records beyond the retention cap and notifies the listener. · *Called by:* `tools/tasks.py::BackgroundTaskManager._run_agent`, `tools/tasks.py::BackgroundTaskManager._run_command`, `tools/tasks.py::BackgroundTaskManager.stop_task`
  - `BackgroundTaskManager._prune_finished() -> None` - Deletes the oldest finished records beyond `max_retained_finished_tasks`. · *Called within this file by:* `tools/tasks.py::BackgroundTaskManager._transition`
  - `BackgroundTaskManager._notify(record: TaskRecord) -> None` - Calls the listener; an exception from it is logged, never propagated. · *Called by:* `tools/tasks.py::BackgroundTaskManager._register`, `tools/tasks.py::BackgroundTaskManager._transition`
  - `BackgroundTaskManager._require(task_id: str) -> TaskRecord` - The record or a `No task found` error. · *Called within this file by:* `tools/tasks.py::BackgroundTaskManager._transition`, `tools/tasks.py::BackgroundTaskManager.stop_task`, `tools/tasks.py::BackgroundTaskManager.wait_for`
- `_require_running_loop(operation: str) -> None` - Raises `RuntimeError` naming the operation when no asyncio loop is running. · *Called by:* `tools/tasks.py::BackgroundTaskManager.start_agent_task`, `tools/tasks.py::BackgroundTaskManager.start_command_task`
- `_now() -> str` - UTC ISO timestamp. · *Called within this file by:* `tools/tasks.py::BackgroundTaskManager._register`, `tools/tasks.py::BackgroundTaskManager._transition`

*Module-level names:* `TaskTransitionListener`, `_logger`

---

### `tools/tools.py` - the ToolExecutor protocol and two deterministic test executors

*73 lines · depends on: `foundations/contracts.py` · used by: `agent/base_agent/agent.py`, `agent/base_agent/types.py`, `agent/graph_agent_executor.py`, `agent/runtime.py`, `agent/task_runner.py`, `integrations/langchain.py`, `integrations/langgraph.py`, `tools/elastic_requests.py` (+1 more) · not re-exported at the package root*

**Role in the workflow.** `BaseAgent` depends only on `ToolExecutor`: any object with `async execute(tool, context)` can serve a run.

**Contents**

- **class `ToolInvocationContext`** *(dataclass)* - What an executor receives with each call: agent identity, task, iteration, the call and the `traceparent` of the call's span (None outside `BaseAgent`), for an executor that calls another service and wants to continue the trace. · *Instantiated by:* `base_agent/agent.py::BaseAgent._execute_tool_call`
  - fields: `agent_identity`, `task`, `iteration`, `call`, `trace_parent`
- **class `ToolExecutor`** *(Protocol; bases: Protocol)* - Protocol: `execute(tool, context)` returns a `ToolExecutionResult`.
  - `ToolExecutor.execute(tool: ToolDefinition, context: ToolInvocationContext) -> ToolExecutionResult` *(async)* - Protocol method.
- **class `InMemoryTaskToolExecutor`** *(class)* - Safe deterministic tools for protocol tests: `read_locked_interface` and `echo`; no filesystem or process. · *Instantiated by:* `agent/task_runner.py::AgentTaskRunner._prepare_tools`
  - `InMemoryTaskToolExecutor.__init__() -> None` - Starts with an empty call log.
  - `InMemoryTaskToolExecutor.execute(tool: ToolDefinition, context: ToolInvocationContext) -> ToolExecutionResult` *(async)* - Returns the locked interface or the arguments for the two known tools, otherwise a failure naming the tool.
- **class `RecordingToolExecutor`** *(dataclass)* - Test double that returns pre-configured results by tool name and records calls.
  - fields: `results_by_name`, `calls`
  - `RecordingToolExecutor.execute(tool: ToolDefinition, context: ToolInvocationContext) -> ToolExecutionResult` *(async)* - Logs the call and returns the configured result, or a failure naming the unconfigured tool.

---

### `tools/worktree_models.py` - record for an agent's git worktree

*20 lines · depends on: `foundations/contracts.py` · used by: `tools/delegation.py`, `tools/worktrees.py` · re-exported at the package root: 1 name(s)*

**Role in the workflow.** Returned by `AgentWorktreeManager`.

**Contents**

- **class `AgentWorktreeInfo`** *(pydantic model; bases: StrictModel)* - Slug, path, branch, base repository, creation time and optional agent id. · *Instantiated by:* `tools/worktrees.py::AgentWorktreeManager.create_worktree`
  - fields: `slug`, `path`, `branch`, `base_repository_path`, `created_at_utc`, `agent_id`

---

### `tools/worktrees.py` - git worktree isolation for concurrent agents

*181 lines · depends on: `tools/worktree_models.py` · used by: `tools/delegation.py` · re-exported at the package root: 2 name(s)*

**Role in the workflow.** Each concurrent agent gets its own checkout and branch so parallel edits cannot collide. The manager tracks only worktrees it created in this process.

**Contents**

- `validate_worktree_slug(slug: str) -> str` - Accepts a slug only if it is non-empty, at most 64 chars, relative, and every `/`-separated segment is letters, digits, dots, underscores or dashes (no `.` or `..`). · *Called by:* `tools/worktrees.py::AgentWorktreeManager.create_worktree`
- **class `AgentWorktreeManager`** *(class)* - Creates, lists and removes worktrees beneath one base directory.
  - `AgentWorktreeManager.__init__(base_dir: Path) -> None` - Stores the base directory, an empty tracking map and the per-slug locks.
  - `AgentWorktreeManager.create_worktree(repository_path: Path, slug: str, *, branch: str | None=None, agent_id: str | None=None) -> AgentWorktreeInfo` *(async)* - Validates the slug and serializes concurrent creates of the same slug; returns the tracked worktree if present, otherwise delegates to `_create_untracked`. · *Called by:* `tools/delegation.py::SubagentCoordinator.delegate`
  - `AgentWorktreeManager._create_untracked(repository_path: Path, slug: str, branch: str | None, agent_id: str | None) -> AgentWorktreeInfo` *(async)* - Refuses a slug that differs from a tracked one only by letter case and an existing untracked path, requires `repository_path` to be the repository root, then adds the worktree: a new branch from HEAD, or the existing branch of that name. · *Called within this file by:* `tools/worktrees.py::AgentWorktreeManager.create_worktree`
  - `AgentWorktreeManager.remove_worktree(slug: str) -> bool` *(async)* - Runs `git worktree remove --force` and untracks it; False if the slug is unknown. · *Called by:* `tools/delegation.py::SubagentCoordinator.delegate.cleanup`
  - `AgentWorktreeManager.get_worktree(slug: str) -> AgentWorktreeInfo | None` - The tracked worktree for a slug, or None. · *No in-package callers (public API, entry point, or protocol hook).*
  - `AgentWorktreeManager.list_worktrees() -> list[AgentWorktreeInfo]` - Tracked worktrees sorted by slug. · *No in-package callers (public API, entry point, or protocol hook).*
- `_require_repository_root(repository: Path) -> None` *(async)* - Raises `ValueError` naming both paths when git reports a different top-level directory than the one given, so a worktree is never created in an enclosing repository by accident. · *Called by:* `tools/worktrees.py::AgentWorktreeManager._create_untracked`
- `_flatten_slug(slug: str) -> str` - Replaces `/` with `+` to make a single directory name. · *Called by:* `tools/worktrees.py::AgentWorktreeManager.create_worktree`
- `_run_git(*args: str, cwd: Path | str) -> tuple[int, str, str]` *(async)* - Runs git asynchronously with prompts disabled and returns (exit code, stdout, stderr). · *Called by:* `tools/worktrees.py::AgentWorktreeManager.create_worktree`, `tools/worktrees.py::AgentWorktreeManager.remove_worktree`
- `_now() -> str` - UTC ISO timestamp. · *Called within this file by:* `tools/worktrees.py::AgentWorktreeManager.create_worktree`

*Module-level names:* `_VALID_SLUG_SEGMENT`, `_MAX_SLUG_LENGTH`
