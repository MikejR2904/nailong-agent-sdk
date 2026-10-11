# `specifications/` - from source documents to a gated, versioned, retrievable specification

The deterministic specification pipeline. `preprocessing.py` parses a manifest of source documents (text, PDF, DOCX, tables, diagrams, images...) into source-preserving `DocumentTree`s (every node carries a hash and a location). `retrieval.py` ranks references (lexical or Qdrant) that are always re-verified against local trees, and `evidence_graph.py` selects the required structural closure and packs optional evidence with the exact PCKP solver. A vector store only ranks references; it is never an authority. There is no gate, soft-lock or version lock in this package: whether a specification is approved or frozen is decided by the interface that calls the agent.

| File | Lines | Role |
|---|---:|---|
| [`specifications/__init__.py`](#specifications__init__py---package-marker-for-the-specification-pipeline) | 3 | package marker for the specification pipeline |
| [`specifications/documents.py`](#specificationsdocumentspy---manifest-document-node-and-source-locator-contracts) | 126 | manifest, document, node and source-locator contracts |
| [`specifications/evidence_graph.py`](#specificationsevidence_graphpy---source-preserving-evidence-graph-with-required-closure-and-bounded-packing) | 358 | source-preserving evidence graph with required closure and bounded packing |
| [`specifications/preprocessing.py`](#specificationspreprocessingpy---manifest-driven-source-preserving-specification-parsing) | 392 | manifest-driven, source-preserving specification parsing |
| [`specifications/retrieval.py`](#specificationsretrievalpy---provenance-grounded-candidate-retrieval-with-caches-and-optional-vector-backends) | 548 | provenance-grounded candidate retrieval with caches and optional vector backends |
| [`specifications/retrieval_models.py`](#specificationsretrieval_modelspy---retrieval-document-query-candidate-and-result-contracts) | 121 | retrieval document, query, candidate and result contracts |
| [`specifications/vision.py`](#specificationsvisionpy---vision-extraction-adapter-protocol-and-trivial-adapters) | 43 | vision-extraction adapter protocol and trivial adapters |

---

### `specifications/__init__.py` - package marker for the specification pipeline

*3 lines · depends on: nothing in the package · used by: no other module (entry point or re-exported only) · not re-exported at the package root*

**Role in the workflow.** Docstring only.

---

### `specifications/documents.py` - manifest, document, node and source-locator contracts

*126 lines · depends on: `foundations/contracts.py`, `foundations/identifiers.py` · used by: `agent/openai_compatible/vision.py`, `developer_tools/validate.py`, `specifications/evidence_graph.py`, `specifications/preprocessing.py`, `specifications/retrieval.py`, `specifications/retrieval_models.py`, `specifications/vision.py` · re-exported at the package root: 8 name(s)*

**Role in the workflow.** Shared vocabulary of the pipeline: every parsed node carries a `SourceRef` (document, path, file hash, format, location) so any later claim can be traced to exact source bytes.

**Contents**

  - members: `FUNCTIONAL`, `ARCHITECTURAL`, `INTERFACE`, `PPA`, `PDK`, `VERIFICATION`, `SAFETY_SECURITY`, `ASSUMPTIONS`
- **class `DocumentFormat`** *(enum; bases: StrEnum)* - The supported source formats (text, markdown, TeX, DOCX, PDF, CSV, XLSX, YAML, JSON, XML, diagram sources, SVG, draw.io, VSDX, PNG, JPEG).
  - members: `TXT`, `MD`, `TEX`, `DOCX`, `PDF`, `CSV`, `XLSX`, `YAML`, `JSON`, `XML`, `DOT`, `PLANTUML`, `MERMAID`, ... (+6)
- **class `SourceRef`** *(pydantic model; bases: StrictModel)* - Immutable locator: document id, relative path, SHA-256 of the file, format and a location string (for example `page:3;image:1`). · *Instantiated by:* `specifications/preprocessing.py::SpecificationPreprocessor.process_document.source`
  - fields: `document_id`, `relative_path`, `source_hash`, `format`, `location`
- **class `DocumentNodeKind`** *(enum; bases: StrEnum)* - text-block, table-block, image-node, structured-block or diagram-source.
  - members: `TEXT`, `TABLE`, `IMAGE`, `STRUCTURED`, `DIAGRAM`
- **class `VisionStatus`** *(enum; bases: StrEnum)* - not-required, pending, review-required or accepted for image nodes.
  - members: `NOT_REQUIRED`, `PENDING`, `REVIEW_REQUIRED`, `ACCEPTED`
- **class `DocumentNode`** *(pydantic model; bases: StrictModel)* - One ordered unit: id, kind, source ref, content, up to two neighbouring text snippets, vision status and any accepted resolved structure. · *Instantiated by:* `specifications/preprocessing.py::SpecificationPreprocessor._line_nodes`, `specifications/preprocessing.py::SpecificationPreprocessor._parse`, `specifications/preprocessing.py::SpecificationPreprocessor._table_nodes`
  - fields: `node_id`, `kind`, `source`, `content`, `surrounding_text`, `vision_status`, `resolved_structure`
- **class `DocumentTree`** *(pydantic model; bases: StrictModel)* - All nodes of one document with its category, title, format, path and file hash (`document-tree-v1`). · *Instantiated by:* `specifications/preprocessing.py::SpecificationPreprocessor.process_document`
  - fields: `schema_version`, `document_id`, `category`, `title`, `format`, `relative_path`, `source_hash`, `nodes`
- **class `SpecificationDocument`** *(pydantic model; bases: StrictModel)* - A manifest entry: id, title, format, relative path, category (a label the caller chooses) and declared dependencies.
  - fields: `id`, `title`, `format`, `path`, `category`, `dependencies`
  - `SpecificationDocument.path_is_relative(value: str) -> str` *(validator, classmethod)* - Validator: the path must be relative (no leading `/` or `\`, no drive letter or drive-relative form such as `C:x`) and contain no `..` segment with either separator; the error quotes the path.
- **class `SpecificationManifest`** *(pydantic model; bases: StrictModel)* - The list of documents to ingest (`specification-manifest-v1`).
  - fields: `schema_version`, `documents`
  - `SpecificationManifest.document_ids_are_unique() -> SpecificationManifest` *(validator)* - Validator: document ids are unique.

---

### `specifications/evidence_graph.py` - source-preserving evidence graph with required closure and bounded packing

*358 lines · depends on: `foundations/contracts.py`, `foundations/hashing.py`, `foundations/identifiers.py`, `foundations/optimization/__init__.py`, `specifications/documents.py` · used by: no other module (entry point or re-exported only) · re-exported at the package root: 12 name(s)*

**Role in the workflow.** Given a frozen typed graph, a set of target nodes and a token budget, the selector first keeps everything reachable through mandatory relation kinds, and only then spends the remaining budget on optional evidence chosen by the exact PCKP solver. It never creates relations from model output or semantic similarity.

**Contents**

- **class `EvidenceNodeKind`** *(enum; bases: StrEnum)* - requirement, interface, signal, acceptance, verification, dependency, source-span or decision.
  - members: `REQUIREMENT`, `INTERFACE`, `SIGNAL`, `ACCEPTANCE`, `VERIFICATION`, `DEPENDENCY`, `SOURCE_SPAN`, `DECISION`
- **class `EvidenceRelationKind`** *(enum; bases: StrEnum)* - requires, governs, interface-constraint, acceptance-criterion, verified-by, declared-dependency, asserted-by or advisory.
  - members: `REQUIRES`, `GOVERNS`, `INTERFACE_CONSTRAINT`, `ACCEPTANCE_CRITERION`, `VERIFIED_BY`, `DECLARED_DEPENDENCY`, `ASSERTED_BY`, `ADVISORY`
- **class `EvidenceNode`** *(pydantic model; bases: StrictModel)* - One exact evidence unit with source locator, token cost, authority tier and aliases.
  - fields: `node_id`, `kind`, `content`, `source`, `token_cost`, `authority_tier`, `aliases`
  - `EvidenceNode.aliases_are_unique() -> EvidenceNode` *(validator)* - Validator: aliases are unique.
- **class `EvidenceRelation`** *(pydantic model; bases: StrictModel)* - A source-backed directed relation: including `from` requires `to`.
  - fields: `from_node_id`, `to_node_id`, `kind`, `source`
  - `EvidenceRelation.endpoints_are_distinct() -> EvidenceRelation` *(validator)* - Validator: no self-relations.
- **class `EvidenceGraph`** *(pydantic model; bases: StrictModel)* - Frozen graph whose ids and source hashes are part of its identity.
  - fields: `snapshot_id`, `nodes`, `relations`
  - `EvidenceGraph.graph_is_well_formed() -> EvidenceGraph` *(validator)* - Validator: unique node ids and every relation endpoint exists.
  - `EvidenceGraph.content_hash() -> str` *(property)* - Property: SHA-256 of the canonical graph JSON. · *Called by:* `agent/retention.py::tombstoned_handles`, `developer_tools/inspect.py::verify_project_evidence`, `specifications/evidence_graph.py::StructuralContextSelector.select`, `state/project_state_engine.py::ProjectStateReducer.tool_transition`
- **class `EvidenceSelectionPolicy`** *(pydantic model; bases: StrictModel)* - Versioned traversal and scoring policy: mandatory relation kinds, per-kind utility, exact-target bonus, authority multiplier and lexical-overlap weight.
  - fields: `policy_id`, `mandatory_relation_kinds`, `node_kind_utility`, `exact_target_bonus`, `authority_multiplier`, `lexical_overlap_weight`
  - `EvidenceSelectionPolicy.relation_kinds_are_unique() -> EvidenceSelectionPolicy` *(validator)* - Validator: mandatory relation kinds are unique.
  - `EvidenceSelectionPolicy.policy_hash() -> str` *(property)* - Property: SHA-256 of the policy JSON recorded in every result. · *Called by:* `specifications/evidence_graph.py::StructuralContextSelector.select`
- **class `EvidenceSelectionRequest`** *(pydantic model; bases: StrictModel)* - Snapshot id, target ids (or aliases), token budget, task text and a strict flag.
  - fields: `snapshot_id`, `target_ids`, `token_budget`, `task_text`, `strict`
  - `EvidenceSelectionRequest.target_ids_are_unique() -> EvidenceSelectionRequest` *(validator)* - Validator: target ids are unique.
- **class `ClosureWitness`** *(pydantic model; bases: StrictModel)* - Proof that a node is in the mandatory closure: its root target and the relation path that reached it. · *Instantiated by:* `specifications/evidence_graph.py::StructuralContextSelector._mandatory_closure`
  - fields: `node_id`, `root_target_id`, `relation_path`
- **class `EvidenceOmission`** *(pydantic model; bases: StrictModel)* - A node left out and why. · *Instantiated by:* `specifications/evidence_graph.py::StructuralContextSelector.select`
  - fields: `node_id`, `reason`
- **class `EvidenceSelectionStatus`** *(enum; bases: StrEnum)* - selected, infeasible-required-closure or best-effort.
  - members: `SELECTED`, `INFEASIBLE_REQUIRED_CLOSURE`, `BEST_EFFORT`
- **class `EvidenceSelectionResult`** *(pydantic model; bases: StrictModel)* - Status, graph and policy hashes, selected nodes, mandatory/optional ids, cost, witnesses, omissions, diagnostics and the solver certificate. · *Instantiated by:* `specifications/evidence_graph.py::StructuralContextSelector.select`
  - fields: `status`, `graph_hash`, `policy_hash`, `selected_nodes`, `mandatory_node_ids`, `selected_optional_node_ids`, `token_cost`, `witnesses`, `omissions`, `diagnostics`, `solver`
- **class `StructuralContextSelector`** *(class)* - Computes the required closure and then packs optional evidence.
  - `StructuralContextSelector.select(graph: EvidenceGraph, request: EvidenceSelectionRequest, policy: EvidenceSelectionPolicy) -> EvidenceSelectionResult` - Checks the snapshot, resolves targets (honouring `request.strict`), computes the mandatory closure (infeasible if it alone exceeds the budget), then solves a PCKP over the remaining nodes with additive utility to fill the residual budget. · *No in-package callers (public API, entry point, or protocol hook).*
  - `StructuralContextSelector._resolve_targets(nodes: dict[str, EvidenceNode], requested: list[str], *, strict: bool=True) -> tuple[list[str], list[str]]` *(staticmethod)* - Maps requested ids or unique approved aliases to node ids. In strict mode an unknown or ambiguous target raises; otherwise it becomes an `(ignored)` diagnostic, and the call still raises when no target resolves at all. · *Called by:* `specifications/evidence_graph.py::StructuralContextSelector.select`
  - `StructuralContextSelector._mandatory_closure(graph: EvidenceGraph, targets: list[str], policy: EvidenceSelectionPolicy) -> tuple[set[str], list[ClosureWitness]]` *(staticmethod)* - Breadth-first walk along mandatory relation kinds from the targets, recording a witness path for every retained node. · *Called by:* `specifications/evidence_graph.py::StructuralContextSelector.select`
  - `StructuralContextSelector._optional_utility(node: EvidenceNode, target_ids: list[str], task_terms: set[str], policy: EvidenceSelectionPolicy) -> int` *(staticmethod)* - Static score: kind utility plus authority tier x multiplier plus term overlap with the task text x weight plus an exact-target bonus. · *Called by:* `specifications/evidence_graph.py::StructuralContextSelector.select`
- `_terms(value: str) -> set[str]` - Lowercase alphanumeric terms longer than one character. · *Called within this file by:* `specifications/evidence_graph.py::StructuralContextSelector._optional_utility`, `specifications/evidence_graph.py::StructuralContextSelector.select`

---

### `specifications/preprocessing.py` - manifest-driven, source-preserving specification parsing

*392 lines · depends on: `foundations/atomic_io.py`, `foundations/identifiers.py`, `foundations/json_limits.py`, `foundations/paths.py`, `foundations/text.py`, `specifications/documents.py`, `specifications/vision.py` · used by: `mcp/_shared.py`, `mcp/server.py`, `specifications/retrieval.py` · re-exported at the package root: 1 name(s)*

**Role in the workflow.** First stage of the pipeline: reads the manifest, parses each document into an ordered `DocumentTree`, optionally resolves images through a vision adapter, and can persist the processed trees. Exposed through the MCP specification tools.

**Contents**

- **class `SpecificationPreprocessor`** *(class)* - Safely parses manifests and source documents beneath one root. · *Instantiated by:* `mcp/server.py::create_mcp_server`
  - `SpecificationPreprocessor.__init__(specification_root: Path) -> None` - Resolves the specification root.
  - `SpecificationPreprocessor.load_manifest(relative_manifest_path: str='specification-manifest.yaml') -> SpecificationManifest` - Loads a YAML manifest and validates it; a manifest without a `documents` key raises, naming the file and its top-level keys. · *Called by:* `mcp/specification_tools.py::register_specification_tools.process_specification_manifest`
  - `SpecificationPreprocessor.process_manifest(manifest: SpecificationManifest) -> list[DocumentTree]` - Processes every manifest document in order. · *Called by:* `mcp/specification_tools.py::register_specification_tools.process_specification_manifest`
  - `SpecificationPreprocessor.process_document(document: SpecificationDocument) -> DocumentTree` - Reads the file, hashes it, parses it with a source-ref factory bound to that hash, adds neighbouring context and returns the tree; any parser failure is re-raised as a `ValueError` naming the document path and format. · *Called by:* `specifications/preprocessing.py::SpecificationPreprocessor.process_manifest`
    - `SpecificationPreprocessor.process_document.source(location: str) -> SourceRef` - Builds a `SourceRef` for a location inside the current document. · *Called by:* `base_agent/agent.py::BaseAgent._accept_final_turn`, `base_agent/agent.py::BaseAgent._await_with_watchdog`, `base_agent/agent.py::BaseAgent._execute_tool_call`, `base_agent/agent.py::BaseAgent._run` (+16 more)
  - `SpecificationPreprocessor.resolve_images(tree: DocumentTree, adapter: VisionAdapter | None=None, *, confidence_threshold: float=0.8, max_attempts: int=3) -> DocumentTree` *(async)* - For each image node asks the vision adapter up to `max_attempts` times; accepts the first proposal with confidence >= the threshold (0.8) and a non-empty structure, otherwise marks the node review-required. · *No in-package callers (public API, entry point, or protocol hook).*
  - `SpecificationPreprocessor.persist_tree(tree: DocumentTree) -> Path` - Atomically writes `processed/<file_safe_name(document id)>.document.yaml`. · *Called by:* `mcp/specification_tools.py::register_specification_tools.process_specification_manifest`
  - `SpecificationPreprocessor._parse(path: Path, format_: DocumentFormat, source) -> list[DocumentNode]` - Format dispatch: line nodes for text/code/diagram sources (UTF-8 BOM tolerated); per-page text plus image nodes for PDF; paragraphs, inline-image markers and tables for DOCX; tables for CSV/XLSX; one depth-bounded structured node for YAML/JSON; safe-parsed XML for XML/SVG/draw.io; page parts for VSDX (each part limited to 16 MiB and all parts to 64 MiB when decompressed); a single pending image node for PNG/JPEG. · *Called by:* `specifications/preprocessing.py::SpecificationPreprocessor.process_document`
  - `SpecificationPreprocessor._line_nodes(text: str, source, kind: DocumentNodeKind) -> list[DocumentNode]` *(staticmethod)* - One node per non-blank line, split on `\r\n`, `\r` or `\n` only (or a single empty node). · *Called by:* `specifications/preprocessing.py::SpecificationPreprocessor._parse`
  - `SpecificationPreprocessor._table_nodes(path: Path, format_: DocumentFormat, source) -> list[DocumentNode]` *(staticmethod)* - CSV (BOM tolerated, with the csv field-size limit raised to the file size for the call) as one table node; XLSX as one table node per sheet. · *Called by:* `specifications/preprocessing.py::SpecificationPreprocessor._parse`
  - `SpecificationPreprocessor._with_context(nodes: list[DocumentNode]) -> list[DocumentNode]` *(staticmethod)* - Attaches up to two neighbouring node texts as `surrounding_text`. · *Called by:* `specifications/preprocessing.py::SpecificationPreprocessor.process_document`
  - `SpecificationPreprocessor._resolve(relative_path: str) -> Path` - Resolves a path under the root and rejects escapes (`relative_to_base`). · *Called by:* `specifications/preprocessing.py::SpecificationPreprocessor.load_manifest`, `specifications/preprocessing.py::SpecificationPreprocessor.process_document`
- `keywords_from_task(text: str) -> set[str]` - Lowercase keyword set (letters first, length >= 3) used for matching. · *Called by:* `specifications/retrieval.py::DeterministicLexicalRetrievalIndex.search`

---

### `specifications/retrieval.py` - provenance-grounded candidate retrieval with caches and optional vector backends

*548 lines · depends on: `foundations/errors.py`, `observability/metrics.py`, `observability/telemetry_models.py`, `observability/telemetry_store.py`, `specifications/documents.py`, `specifications/preprocessing.py`, `specifications/retrieval_models.py` · used by: no other module (entry point or re-exported only) · re-exported at the package root: 10 name(s)*

**Role in the workflow.** `GroundedRetrievalService` indexes frozen trees, retrieves ranked references (cached, with a lexical fallback), and `resolve` re-verifies every candidate against local trees before any content can be exposed. Retrieval outcomes are logged digest-only to telemetry.

**Contents**

- **class `EmbeddingProvider`** *(Protocol; bases: Protocol)* - Host-owned embedding boundary.
  - `EmbeddingProvider.embed(text: str) -> list[float]` - Protocol method: text to vector.
- **class `RetrievalIndex`** *(Protocol; bases: Protocol)* - Candidate-ranking backend; never an authoritative source store.
  - `RetrievalIndex.backend_name() -> str` *(property)* - Protocol property: the backend identifier. · *Called by:* `specifications/retrieval.py::GroundedRetrievalService.retrieve`
  - `RetrievalIndex.upsert(documents: Iterable[RetrievalDocument]) -> None` - Protocol method: index documents.
  - `RetrievalIndex.search(query: RetrievalQuery) -> list[RetrievalCandidate]` - Protocol method: ranked candidates for a query.
- **class `RetrievalCache`** *(Protocol; bases: Protocol)* - Optional cache for bounded retrieval responses.
  - `RetrievalCache.get(key: str) -> RetrievalResult | None` - Protocol method: cached result or None.
  - `RetrievalCache.set(key: str, result: RetrievalResult, ttl_seconds: int) -> None` - Protocol method: store a result with a TTL.
- **class `InMemoryRetrievalCache`** *(class)* - Deterministic dictionary cache without wall-clock expiry.
  - `InMemoryRetrievalCache.__init__() -> None` - Starts empty.
  - `InMemoryRetrievalCache.get(key: str) -> RetrievalResult | None` - Returns the stored result or None.
  - `InMemoryRetrievalCache.set(key: str, result: RetrievalResult, ttl_seconds: int) -> None` - Stores a result; rejects a non-positive TTL.
- **class `RedisRetrievalCache`** *(class)* - Optional Redis cache storing only references and scores, never source text.
  - `RedisRetrievalCache.__init__(redis_url: str, *, namespace: str='agent-sdk:retrieval:') -> None` - Validates the URL and namespace, imports `redis` (a missing package raises naming `nailong-agent-sdk[redis-cache]`) and opens a client.
  - `RedisRetrievalCache.get(key: str) -> RetrievalResult | None` - Reads and validates a cached result.
  - `RedisRetrievalCache.set(key: str, result: RetrievalResult, ttl_seconds: int) -> None` - Stores the result JSON with an expiry.
- **class `DeterministicLexicalRetrievalIndex`** *(class)* - Offline fallback: ranks by the count of shared keywords.
  - fields: `backend_name`
  - `DeterministicLexicalRetrievalIndex.__init__() -> None` - Starts with no documents.
  - `DeterministicLexicalRetrievalIndex.upsert(documents: Iterable[RetrievalDocument]) -> None` - Stores documents by stable id.
  - `DeterministicLexicalRetrievalIndex.search(query: RetrievalQuery) -> list[RetrievalCandidate]` - Filters by snapshot and allowed categories, scores by keyword overlap, sorts by score then ids and returns the top `limit` candidates.
- **class `QdrantRetrievalIndex`** *(class)* - Optional Qdrant REST adapter with exact snapshot and category metadata filtering.
  - fields: `backend_name`
  - `QdrantRetrievalIndex.__init__(base_url: str, collection_name: str, embedding_provider: EmbeddingProvider, *, embedding_dimensions: int, timeout_seconds: float=5.0) -> None` - Validates URL, collection, dimensions and timeout.
  - `QdrantRetrievalIndex.upsert(documents: Iterable[RetrievalDocument]) -> None` - Embeds each document, checks dimensions and upserts points with deterministic UUID5 ids, in batches of 256 points per request.
  - `QdrantRetrievalIndex.search(query: RetrievalQuery) -> list[RetrievalCandidate]` - Embeds the query and searches with a snapshot and category filter, converting payloads to candidates and skipping malformed points.
  - `QdrantRetrievalIndex._ensure_collection() -> None` - Creates the collection (cosine) if it is missing (HTTP 404) and ensures keyword indexes on snapshot and category. · *Called by:* `specifications/retrieval.py::QdrantRetrievalIndex.search`, `specifications/retrieval.py::QdrantRetrievalIndex.upsert`
  - `QdrantRetrievalIndex._assert_dimensions(vector: list[float]) -> None` - Rejects vectors of the wrong size. · *Called by:* `specifications/retrieval.py::QdrantRetrievalIndex.search`, `specifications/retrieval.py::QdrantRetrievalIndex.upsert`
  - `QdrantRetrievalIndex._request(method: str, path: str, payload: dict[str, Any] | None=None) -> dict[str, Any]` - JSON HTTP call to Qdrant with a timeout. An HTTP error becomes a `RuntimeError` carrying the status and Qdrant's own redacted error text (cut to 300 characters); a transport failure, a timeout and a non-JSON body each raise `RuntimeError` naming the method, path and base URL. · *Called by:* `specifications/retrieval.py::QdrantRetrievalIndex._ensure_collection`, `specifications/retrieval.py::QdrantRetrievalIndex.search`, `specifications/retrieval.py::QdrantRetrievalIndex.upsert`
- **class `RetrievalTelemetrySink`** *(class)* - Writes digest-only retrieval outcomes to the telemetry ledger.
  - `RetrievalTelemetrySink.__init__(telemetry: TelemetryStore, context_factory: Callable[[RetrievalQuery], TelemetryContext]) -> None` - Stores the telemetry store and a context factory.
  - `RetrievalTelemetrySink.record(query: RetrievalQuery, result: RetrievalResult) -> None` - Emits `retrieval.completed` (status, candidate count, failure reason and cache warning) and the candidate-count and cache-hit metrics.
- **class `GroundedRetrievalService`** *(class)* - Retrieves ranked references, caches safely and resolves only local source facts.
  - `GroundedRetrievalService.__init__(index: RetrievalIndex, *, cache: RetrievalCache | None=None, cache_ttl_seconds: int=300, telemetry_sink: RetrievalTelemetrySink | None=None) -> None` - Stores the index, optional cache and TTL (>= 1) and sink.
  - `GroundedRetrievalService.index_snapshot(snapshot_id: str, trees: Iterable[DocumentTree]) -> int` - Builds retrieval documents from trees and upserts them; returns the count. · *No in-package callers (public API, entry point, or protocol hook).*
  - `GroundedRetrievalService.retrieve(query: RetrievalQuery, *, failure_mode: RetrievalFailureMode=RetrievalFailureMode.FALLBACK_LEXICAL) -> RetrievalResult` - Cache lookup by query digest, else search and validate; on backend failure either re-raise (REJECT) or return an UNAVAILABLE result carrying `Type: message`. A cache read or write failure never fails the request: it is reported in `RetrievalResult.cache_warning`. · *No in-package callers (public API, entry point, or protocol hook).*
  - `GroundedRetrievalService.resolve(result: RetrievalResult, trees: Iterable[DocumentTree]) -> ResolvedRetrieval` - Keeps a candidate only if a local node matches its document, node id and source hash with an identical source ref and category; others are listed as rejected.
  - `GroundedRetrievalService._record(query: RetrievalQuery, result: RetrievalResult) -> None` - Forwards to the telemetry sink if configured. · *Called within this file by:* `specifications/retrieval.py::GroundedRetrievalService.retrieve`
- `specification_retrieval_documents(snapshot_id: str, trees: Iterable[DocumentTree]) -> list[RetrievalDocument]` - One deterministic `RetrievalDocument` per node with non-empty text (`_node_text`), sorted by document and node id. · *Called by:* `specifications/retrieval.py::GroundedRetrievalService.index_snapshot`
- `_node_text(node: DocumentNode) -> str` - Indexable text of a node: its content, plus the JSON of its resolved structure when a vision result was accepted. · *Called by:* `specifications/retrieval.py::specification_retrieval_documents`
- `_validated_result(query: RetrievalQuery, result: RetrievalResult) -> RetrievalResult` - Checks the query digest, keeps candidates for the right snapshot and categories, sorts them and truncates to the limit. · *Called by:* `specifications/retrieval.py::GroundedRetrievalService.retrieve`
- `_read_error_body(error: HTTPError) -> str` - Best-effort decoded body of an `HTTPError` (empty string if unreadable).
- `_qdrant_error_detail(body: str) -> str` - `Qdrant said: <message>` from a JSON `status.error` (or the raw body), redacted with `redact_secrets`, whitespace-collapsed and cut to 300 characters; empty for an empty body.
- `_document_payload(document: RetrievalDocument) -> dict[str, Any]` - Qdrant payload for a document. · *Called by:* `specifications/retrieval.py::QdrantRetrievalIndex.upsert`
- `_candidate_from_qdrant_payload(point: Mapping[str, Any]) -> RetrievalCandidate | None` - Converts a Qdrant point to a candidate or None if malformed. · *Called by:* `specifications/retrieval.py::QdrantRetrievalIndex.search`

---

### `specifications/retrieval_models.py` - retrieval document, query, candidate and result contracts

*121 lines · depends on: `foundations/contracts.py`, `foundations/hashing.py`, `foundations/identifiers.py`, `specifications/documents.py` · used by: `specifications/retrieval.py` · re-exported at the package root: 7 name(s)*

**Role in the workflow.** The typed boundary between the untrusted ranking backend and the local, verified source trees.

**Contents**

- **class `RetrievalStatus`** *(enum; bases: StrEnum)* - retrieved, cache-hit or unavailable.
  - members: `RETRIEVED`, `CACHE_HIT`, `UNAVAILABLE`
- **class `RetrievalFailureMode`** *(enum; bases: StrEnum)* - fallback-lexical (default) or reject.
  - members: `FALLBACK_LEXICAL`, `REJECT`
- **class `RetrievalDocument`** *(pydantic model; bases: StrictModel)* - Indexable text with immutable source provenance from one frozen snapshot. · *Instantiated by:* `specifications/retrieval.py::specification_retrieval_documents`
  - fields: `snapshot_id`, `category`, `document_id`, `node_id`, `source`, `text`
  - `RetrievalDocument.node_belongs_to_source_document() -> RetrievalDocument` *(validator)* - Validator: `document_id` equals the source's document id.
  - `RetrievalDocument.stable_id() -> str` *(property)* - Property: digest of snapshot, category, document, node, source hash and location. · *Called by:* `specifications/retrieval.py::DeterministicLexicalRetrievalIndex.search`, `specifications/retrieval.py::DeterministicLexicalRetrievalIndex.upsert`, `specifications/retrieval.py::QdrantRetrievalIndex.upsert`, `specifications/retrieval.py::_document_payload`
- **class `RetrievalCandidate`** *(pydantic model; bases: StrictModel)* - A bounded rank/reference returned by an untrusted backend. · *Instantiated by:* `specifications/retrieval.py::DeterministicLexicalRetrievalIndex.search`, `specifications/retrieval.py::_candidate_from_qdrant_payload`
  - fields: `snapshot_id`, `category`, `document_id`, `node_id`, `source`, `score`, `backend_id`
  - `RetrievalCandidate.candidate_belongs_to_source_document() -> RetrievalCandidate` *(validator)* - Validator: `document_id` equals the source's document id.
- **class `RetrievalQuery`** *(pydantic model; bases: StrictModel)* - A bounded candidate request (limit 1-100); query text is never persisted in telemetry or cache keys.
  - fields: `snapshot_id`, `query_text`, `allowed_categories`, `limit`, `policy_id`
  - `RetrievalQuery.allowed_categories_are_unique() -> RetrievalQuery` *(validator)* - Validator: categories are unique.
  - `RetrievalQuery.query_digest() -> str` *(property)* - Property: digest of the full query, used as cache key and telemetry id. · *Called by:* `specifications/retrieval.py::GroundedRetrievalService.retrieve`, `specifications/retrieval.py::RetrievalTelemetrySink.record`, `specifications/retrieval.py::_validated_result`
- **class `RetrievalResult`** *(pydantic model; bases: StrictModel)* - Status, query digest, candidates, backend and failure reason. · *Instantiated by:* `specifications/retrieval.py::GroundedRetrievalService.retrieve`
  - fields: `status`, `query_digest`, `candidates`, `backend`, `failure_reason`, `cache_warning`
- **class `ResolvedRetrieval`** *(pydantic model; bases: StrictModel)* - Locally verified nodes plus the ids of rejected candidates. · *Instantiated by:* `specifications/retrieval.py::GroundedRetrievalService.resolve`
  - fields: `result`, `nodes`, `rejected_candidate_ids`

---

### `specifications/vision.py` - vision-extraction adapter protocol and trivial adapters

*43 lines · depends on: `foundations/contracts.py`, `specifications/documents.py` · used by: `agent/openai_compatible/vision.py`, `specifications/preprocessing.py` · re-exported at the package root: 4 name(s)*

**Role in the workflow.** `SpecificationPreprocessor.resolve_images` calls a `VisionAdapter` for image and diagram nodes; the real model-backed adapter is `OpenAICompatibleVisionAdapter` in the agent package.

**Contents**

- **class `VisionProposal`** *(pydantic model; bases: StrictModel)* - A model's proposal: confidence in [0, 1], an extracted structure and error strings. · *Instantiated by:* `specifications/vision.py::ScriptedVisionAdapter.extract`, `specifications/vision.py::UnconfiguredVisionAdapter.extract`
  - fields: `confidence`, `structure`, `errors`
- **class `VisionAdapter`** *(Protocol; bases: Protocol)* - Protocol for anything that can propose a structure for an image node.
  - `VisionAdapter.extract(node: DocumentNode) -> VisionProposal` *(async)* - Protocol method.
- **class `UnconfiguredVisionAdapter`** *(class)* - Default adapter: always returns confidence 0 and an error saying none is configured. · *Instantiated by:* `specifications/preprocessing.py::SpecificationPreprocessor.resolve_images`
  - `UnconfiguredVisionAdapter.extract(node: DocumentNode) -> VisionProposal` *(async)* - Returns the zero-confidence proposal.
- **class `ScriptedVisionAdapter`** *(class)* - Test adapter that returns pre-supplied proposals in order.
  - `ScriptedVisionAdapter.__init__(proposals: list[VisionProposal]) -> None` - Copies the proposal list.
  - `ScriptedVisionAdapter.extract(node: DocumentNode) -> VisionProposal` *(async)* - Pops the next proposal, or a zero-confidence one when exhausted.
