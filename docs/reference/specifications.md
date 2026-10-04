# `specifications/` - from source documents to a gated, versioned, retrievable specification

The deterministic specification pipeline of the EDA framework. `preprocessing.py` parses a manifest of source documents (text, PDF, DOCX, tables, diagrams, images...) into source-preserving `DocumentTree`s (every node carries a hash and a location). Hosts turn those into a `UnifiedSpecification`; `gate.py` runs the deterministic **Gate 1** checks (absence, traceability, verifiability, admitted semantic findings) and the designer soft-lock; `git_versioning.py` records an approved lock as a Git tag plus a structured snapshot and classifies the next change as major/minor/patch. `retrieval.py` ranks references (lexical or Qdrant) that are always re-verified against local trees, and `evidence_graph.py` selects the required structural closure and packs optional evidence with the exact PCKP solver. A vector store only ranks references; it is never an authority.

| File | Lines | Role |
|---|---:|---|
| [`specifications/__init__.py`](#specifications__init__py---package-marker-for-the-specification-pipeline) | 3 | package marker for the specification pipeline |
| [`specifications/documents.py`](#specificationsdocumentspy---manifest-document-node-and-source-locator-contracts) | 136 | manifest, document, node and source-locator contracts |
| [`specifications/evidence_graph.py`](#specificationsevidence_graphpy---source-preserving-evidence-graph-with-required-closure-and-bounded-packing) | 361 | source-preserving evidence graph with required closure and bounded packing |
| [`specifications/gate.py`](#specificationsgatepy---gate-1-deterministic-checks-soft-lock-decision-and-artifact-persistence) | 413 | Gate 1 deterministic checks, soft-lock decision and artifact persistence |
| [`specifications/gate_models.py`](#specificationsgate_modelspy---gate-1-requirement-gap-and-version-metadata-contracts) | 167 | Gate 1 requirement, gap and version-metadata contracts |
| [`specifications/git_models.py`](#specificationsgit_modelspy---git-backed-version-and-variant-worktree-contracts) | 120 | Git-backed version and variant-worktree contracts |
| [`specifications/git_versioning.py`](#specificationsgit_versioningpy---local-only-git-adapter-and-the-specification-version-lock-service) | 504 | local-only Git adapter and the specification version-lock service |
| [`specifications/preprocessing.py`](#specificationspreprocessingpy---manifest-driven-source-preserving-specification-parsing) | 420 | manifest-driven, source-preserving specification parsing |
| [`specifications/retrieval.py`](#specificationsretrievalpy---provenance-grounded-candidate-retrieval-with-caches-and-optional-vector-backends) | 548 | provenance-grounded candidate retrieval with caches and optional vector backends |
| [`specifications/retrieval_models.py`](#specificationsretrieval_modelspy---retrieval-document-query-candidate-and-result-contracts) | 131 | retrieval document, query, candidate and result contracts |
| [`specifications/vision.py`](#specificationsvisionpy---vision-extraction-adapter-protocol-and-trivial-adapters) | 43 | vision-extraction adapter protocol and trivial adapters |

---

### `specifications/__init__.py` - package marker for the specification pipeline

*3 lines · depends on: nothing in the package · used by: no other module (entry point or re-exported only) · not re-exported at the package root*

**Role in the workflow.** Docstring only.

---

### `specifications/documents.py` - manifest, document, node and source-locator contracts

*136 lines · depends on: `foundations/contracts.py` · used by: `agent/openai_compatible/semantic_gap.py`, `agent/openai_compatible/vision.py`, `developer_tools/validate.py`, `mcp/specification_tools.py`, `memory/context_selection.py`, `specifications/evidence_graph.py`, `specifications/gate.py`, `specifications/gate_models.py` (+4 more) · re-exported at the package root: 8 name(s)*

**Role in the workflow.** Shared vocabulary of the pipeline: every parsed node carries a `SourceRef` (document, path, file hash, format, location) so any later claim can be traced to exact source bytes.

**Contents**

- **class `SpecificationCategory`** *(enum; bases: StrEnum)* - functional, architectural, interface, PPA, PDK, verification, safety-security or assumptions. · *Instantiated by:* `mcp/specification_tools.py::register_specification_tools.validate_gate_one`
  - members: `FUNCTIONAL`, `ARCHITECTURAL`, `INTERFACE`, `PPA`, `PDK`, `VERIFICATION`, `SAFETY_SECURITY`, `ASSUMPTIONS`
- **class `DocumentFormat`** *(enum; bases: StrEnum)* - The supported source formats (text, markdown, TeX, DOCX, PDF, CSV, XLSX, YAML, JSON, XML, SystemRDL, SDC, UPF, diagram sources, SVG, draw.io, VSDX, PNG, JPEG).
  - members: `TXT`, `MD`, `TEX`, `DOCX`, `PDF`, `CSV`, `XLSX`, `YAML`, `JSON`, `XML`, `SYSTEMRDL`, `SDC`, `UPF`, `DOT`, `PLANTUML`, `WAVEDROM`, ... (+7)
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
- **class `SpecificationDocument`** *(pydantic model; bases: StrictModel)* - A manifest entry: id, title, format, relative path, category and declared dependencies.
  - fields: `id`, `title`, `format`, `path`, `category`, `dependencies`
  - `SpecificationDocument.path_is_relative(value: str) -> str` *(validator, classmethod)* - Validator: the path must be relative (no leading `/` or `\`, no drive letter or drive-relative form such as `C:x`) and contain no `..` segment with either separator; the error quotes the path.
- **class `SpecificationManifest`** *(pydantic model; bases: StrictModel)* - The list of documents to ingest (`specification-manifest-v1`).
  - fields: `schema_version`, `documents`
  - `SpecificationManifest.document_ids_are_unique() -> SpecificationManifest` *(validator)* - Validator: document ids are unique.

---

### `specifications/evidence_graph.py` - source-preserving evidence graph with required closure and bounded packing

*361 lines · depends on: `foundations/contracts.py`, `foundations/optimization/__init__.py`, `specifications/documents.py` · used by: no other module (entry point or re-exported only) · re-exported at the package root: 12 name(s)*

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
  - `EvidenceGraph.content_hash() -> str` *(property)* - Property: SHA-256 of the canonical graph JSON. · *Called by:* `specifications/evidence_graph.py::StructuralContextSelector.select`, `state/project_state_engine.py::ProjectStateReducer.tool_transition`
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
  - `StructuralContextSelector.select(graph: EvidenceGraph, request: EvidenceSelectionRequest, policy: EvidenceSelectionPolicy) -> EvidenceSelectionResult` - Checks the snapshot, resolves targets (honouring `request.strict`), computes the mandatory closure (infeasible if it alone exceeds the budget), then solves a PCKP over the remaining nodes with additive utility to fill the residual budget.
  - `StructuralContextSelector._resolve_targets(nodes: dict[str, EvidenceNode], requested: list[str], *, strict: bool=True) -> tuple[list[str], list[str]]` *(staticmethod)* - Maps requested ids or unique approved aliases to node ids. In strict mode an unknown or ambiguous target raises; otherwise it becomes an `(ignored)` diagnostic, and the call still raises when no target resolves at all. · *Called by:* `specifications/evidence_graph.py::StructuralContextSelector.select`
  - `StructuralContextSelector._mandatory_closure(graph: EvidenceGraph, targets: list[str], policy: EvidenceSelectionPolicy) -> tuple[set[str], list[ClosureWitness]]` *(staticmethod)* - Breadth-first walk along mandatory relation kinds from the targets, recording a witness path for every retained node. · *Called by:* `specifications/evidence_graph.py::StructuralContextSelector.select`
  - `StructuralContextSelector._optional_utility(node: EvidenceNode, target_ids: list[str], task_terms: set[str], policy: EvidenceSelectionPolicy) -> int` *(staticmethod)* - Static score: kind utility plus authority tier x multiplier plus term overlap with the task text x weight plus an exact-target bonus. · *Called by:* `specifications/evidence_graph.py::StructuralContextSelector.select`
- `_terms(value: str) -> set[str]` - Lowercase alphanumeric terms longer than one character. · *Called within this file by:* `specifications/evidence_graph.py::StructuralContextSelector._optional_utility`, `specifications/evidence_graph.py::StructuralContextSelector.select`

---

### `specifications/gate.py` - Gate 1 deterministic checks, soft-lock decision and artifact persistence

*413 lines · depends on: `foundations/atomic_io.py`, `foundations/dependency_graph.py`, `specifications/documents.py`, `specifications/gate_models.py` · used by: `mcp/_shared.py`, `mcp/server.py` · re-exported at the package root: 2 name(s)*

**Role in the workflow.** After a host assembles a `UnifiedSpecification`, `validate` produces the dependency graph and a gap report; admitted semantic findings are merged in; the designer then soft-locks via `soft_lock`; `Gate1ArtifactStore` writes the YAML artifacts; `git_versioning.py` can turn the soft-lock into a Git version lock. Exposed through the MCP `validate_gate_one` and `soft_lock_specification` tools.

**Contents**

- **class `SpecificationGate`** *(class)* - Performs only deterministic Gate 1 checks and keeps explicit flags for anything that needs a model. · *Instantiated by:* `mcp/server.py::create_mcp_server`
  - `SpecificationGate.validate(specification: UnifiedSpecification, *, required_categories: set[SpecificationCategory], semantic_findings: Sequence[SemanticGapFinding]=()) -> tu...` - Reports missing required categories (CRITICAL), empty requirement text (CRITICAL), requirements without acceptance checks (IMPORTANT), image nodes still pending or awaiting review (IMPORTANT, `vision-resolution-presence`), dependencies on missing requirements (CRITICAL, with blast radius from reverse reachability) and dependency cycles (IMPORTANT); then admits semantic findings and returns the graph and `GapReport`.
  - `SpecificationGate.admit_semantic_findings(specification: UnifiedSpecification, graph: DependencyGraph, findings: Sequence[SemanticGapFinding]) -> tuple[list[Gap], list[SemanticGapAdmission]]` - Mechanically accepts a host-proposed semantic finding only if its id is unique, every cited requirement exists, and every cited source reference is bound to those requirements; accepted ones become gaps (marked model-analysis-required) with a blast radius, rejected ones record the reason. · *Called by:* `specifications/gate.py::SpecificationGate.validate`
  - `SpecificationGate.soft_lock(specification: UnifiedSpecification, report: GapReport, metadata: VersionMetadata, *, user_approved: bool, proceed_with_gaps: bool=False) -> SoftL...` - Requires designer approval; if gaps remain it also requires `proceed_with_gaps`; on success returns metadata marked soft-locked (and override-with-gaps when applicable). · *Called by:* `mcp/specification_tools.py::register_specification_tools.soft_lock_specification`
- `_source_ref_key(source: SourceRef) -> tuple[str, str, str, str, str]` - The full immutable identity tuple of a source reference. · *Called by:* `specifications/gate.py::SpecificationGate.admit_semantic_findings`
- `classify_version_change(previous: UnifiedSpecification | None, current: UnifiedSpecification) -> tuple[VersionChangeKind, list[str]]` - Requirement-level preview of the bump: initial baseline is major, removed or changed (text, category, fields) requirements are major, added ones minor, otherwise patch. The graph-aware version is in `git_versioning.py`. · *No in-package callers (public API, entry point, or protocol hook).*
- **class `Gate1ArtifactStore`** *(class)* - Writes Gate 1 outputs under the approved specification root. · *Instantiated by:* `mcp/server.py::create_mcp_server`
  - `Gate1ArtifactStore.__init__(specification_root: Path) -> None` - Resolves and creates the specification root.
  - `Gate1ArtifactStore.persist(specification: UnifiedSpecification, dependency_graph: DependencyGraph, gap_report: GapReport, metadata: VersionMetadata, plans: list[dict[str, An...` - Stages `unified-specification.yaml`, `dependency-graph.yaml`, `gap-report.yaml` (with summary), `version-metadata.yaml` and any `plans/plan-N.yaml` to unique temporary files first, renames them all into place only if every payload was written, then deletes plan files numbered beyond the current plans.
  - `Gate1ArtifactStore._stage(relative: str, payload: dict[str, Any]) -> Path` - Dumps one payload as YAML to a unique temporary file next to its target and returns that path without publishing it. · *Called by:* `specifications/gate.py::Gate1ArtifactStore.persist`
  - `Gate1ArtifactStore._remove_stale_plans(retained_count: int) -> None` - Deletes `plans/plan-N.yaml` files whose number exceeds `retained_count`, so a re-lock with fewer plans leaves no stale ones. · *Called by:* `specifications/gate.py::Gate1ArtifactStore.persist`

**Algorithms & invariants.** Gate 1 verifies absence, traceability and verifiability; it deliberately does not decide whether prose is ambiguous, and a semantic finding can never be CRITICAL.

---

### `specifications/gate_models.py` - Gate 1 requirement, gap and version-metadata contracts

*167 lines · depends on: `foundations/contracts.py`, `specifications/documents.py` · used by: `agent/openai_compatible/semantic_gap.py`, `mcp/git_tools.py`, `mcp/specification_tools.py`, `specifications/gate.py`, `specifications/git_models.py`, `specifications/git_versioning.py` · re-exported at the package root: 9 name(s)*

**Role in the workflow.** The typed data that flows through `gate.py`, the Git lock service and the MCP specification tools.

**Contents**

- **class `GapType`** *(enum; bases: StrEnum)* - absence, traceability, inconsistency, ambiguity, verifiability or unstated assumption.
  - members: `ABSENCE`, `TRACEABILITY`, `INCONSISTENCY`, `AMBIGUITY`, `VERIFIABILITY`, `UNSTATED_ASSUMPTION`
- **class `GapSeverity`** *(enum; bases: StrEnum)* - CRITICAL, IMPORTANT or OPTIONAL.
  - members: `CRITICAL`, `IMPORTANT`, `OPTIONAL`
- **class `RequirementEntry`** *(pydantic model; bases: StrictModel)* - One requirement: id, category, text, source refs, dependencies, acceptance checks and free-form fields.
  - fields: `id`, `category`, `text`, `source_refs`, `dependencies`, `acceptance_checks`, `fields`
- **class `UnifiedSpecification`** *(pydantic model; bases: StrictModel)* - Version, all document trees and all requirements.
  - fields: `schema_version`, `version`, `documents`, `requirements`
  - `UnifiedSpecification.requirement_ids_are_unique() -> UnifiedSpecification` *(validator)* - Validator: requirement ids are unique.
- **class `DependencyEdge`** *(pydantic model; bases: StrictModel)* - One dependency edge between requirement ids. · *Instantiated by:* `specifications/gate.py::SpecificationGate.validate`
  - fields: `source_id`, `target_id`
- **class `DependencyGraph`** *(pydantic model; bases: StrictModel)* - Sorted requirement ids and the declared dependency edges. · *Instantiated by:* `specifications/gate.py::SpecificationGate.validate`
  - fields: `schema_version`, `nodes`, `edges`
- **class `Gap`** *(pydantic model; bases: StrictModel)* - One finding: type, locations, description, categories, suggested fix, severity, source, optional blast radius and a model-analysis flag. · *Instantiated by:* `specifications/gate.py::SpecificationGate.admit_semantic_findings`, `specifications/gate.py::SpecificationGate.validate`
  - fields: `type`, `locations`, `description`, `categories_touched`, `suggested_fix`, `severity`, `source`, `blast_radius`, `model_analysis_required`
- **class `SemanticGapFinding`** *(pydantic model; bases: StrictModel)* - An untrusted semantic finding proposed by a host analysis component, with receipt digest and provider/model identity. · *Instantiated by:* `openai_compatible/semantic_gap.py::OpenAICompatibleSemanticGapAnalyzer.analyze`
  - fields: `schema_version`, `finding_id`, `type`, `requirement_ids`, `source_refs`, `description`, `suggested_fix`, `severity`, `analysis_provider`, `analysis_model`, `analysis_receipt_digest`
  - `SemanticGapFinding.semantic_findings_are_bounded_and_noncritical() -> SemanticGapFinding` *(validator)* - Validator: only semantic gap types, unique requirement ids, inconsistency cites at least two requirements, and never CRITICAL.
- **class `SemanticGapAdmission`** *(pydantic model; bases: StrictModel)* - Whether a semantic finding was admitted, and why. · *Instantiated by:* `specifications/gate.py::SpecificationGate.admit_semantic_findings`
  - fields: `finding_id`, `accepted`, `reason`
- **class `GapReport`** *(pydantic model; bases: StrictModel)* - All gaps plus the semantic admission outcomes for one document version. · *Instantiated by:* `specifications/gate.py::SpecificationGate.validate`
  - fields: `schema_version`, `document_version`, `gaps`, `semantic_admissions`
  - `GapReport.summary() -> dict[str, int]` - Counts of total/critical/important/optional gaps and accepted/rejected semantic findings. · *Called by:* `base_agent/agent.py::BaseAgent._record_executed_result`, `base_agent/agent.py::BaseAgent._terminate`, `jev/exploration.py::JevExplorationAdvisor.prioritize`, `mcp/specification_tools.py::register_specification_tools.validate_gate_one` (+9 more)
- **class `VersionChangeKind`** *(enum; bases: StrEnum)* - major, minor or patch.
  - members: `MAJOR`, `MINOR`, `PATCH`
- **class `VersionMetadata`** *(pydantic model; bases: StrictModel)* - Version, change kind, rationale, specification hash, and soft-lock/override flags.
  - fields: `schema_version`, `version`, `change_kind`, `rationale`, `unified_specification_hash`, `soft_locked`, `user_override_with_gaps`
- **class `SoftLockDecision`** *(pydantic model; bases: StrictModel)* - Accepted or not, with warnings and the stamped metadata. · *Instantiated by:* `specifications/gate.py::SpecificationGate.soft_lock`
  - fields: `accepted`, `warnings`, `metadata`

---

### `specifications/git_models.py` - Git-backed version and variant-worktree contracts

*120 lines · depends on: `foundations/contracts.py`, `specifications/gate_models.py` · used by: `mcp/git_tools.py`, `specifications/git_versioning.py` · re-exported at the package root: 8 name(s)*

**Role in the workflow.** Records produced and consumed by `git_versioning.py` (locks, snapshots, classifications, approvals).

**Contents**

- **class `VersionBump`** *(enum; bases: StrEnum)* - major, minor or patch.
  - members: `MAJOR`, `MINOR`, `PATCH`
- **class `StructuralSpecificationDiff`** *(pydantic model; bases: StrictModel)* - Added/removed/modified requirement ids, which fields changed and added/removed dependency edges. · *Instantiated by:* `specifications/git_versioning.py::structural_specification_diff`
  - fields: `added_requirement_ids`, `removed_requirement_ids`, `modified_requirement_ids`, `changed_requirement_fields`, `added_dependency_edges`, `removed_dependency_edges`
  - `StructuralSpecificationDiff.has_breaking_change() -> bool` *(property)* - Property: true when requirements were removed or modified or edges were added or removed. · *Called by:* `specifications/git_versioning.py::SpecificationVersionService.classify`
- **class `SpecificationSnapshotRecord`** *(pydantic model; bases: StrictModel)* - Content-addressed specification and graph retained with a lock so the next version can be diffed. · *Instantiated by:* `specifications/git_versioning.py::SpecificationVersionService._persist_snapshot`
  - fields: `schema_version`, `version`, `tag_name`, `specification`, `dependency_graph`, `specification_digest`, `dependency_graph_digest`
- **class `GitApproval`** *(pydantic model; bases: StrictModel)* - A recorded approval decision: approved flag, approver, reason, action name, id and time.
  - fields: `approved`, `approver_id`, `reason`, `action`, `approval_id`, `at_utc`
- **class `GitRepositoryState`** *(pydantic model; bases: StrictModel)* - Root, HEAD commit, tree id, branch, cleanliness and version tags. · *Instantiated by:* `specifications/git_versioning.py::GitRepositoryAdapter.state`
  - fields: `repository_root`, `head_commit`, `tree_id`, `branch`, `clean`, `tags`
- **class `VersionClassification`** *(pydantic model; bases: StrictModel)* - Recommended bump, rationale, changed paths, previous tag and the structural diff. · *Instantiated by:* `specifications/git_versioning.py::SpecificationVersionService.classify`
  - fields: `recommended_bump`, `rationale`, `changed_paths`, `previous_tag`, `structural_diff`
- **class `SpecificationLockRecord`** *(pydantic model; bases: StrictModel)* - The full lock: version, tag, commit/tree/tag ids, digests, metadata, classification, approval and snapshot digest. · *Instantiated by:* `specifications/git_versioning.py::SpecificationVersionService.create_lock`
  - fields: `schema_version`, `version`, `tag_name`, `repository_root`, `head_commit`, `tree_id`, `tag_object_id`, `specification_digest`, `version_metadata`, `classification`, `approval`, `created_at_utc`, `gap_report_hash`, `dependency_graph_hash`, `snapshot_digest`
- **class `VariantWorktreeRecord`** *(pydantic model; bases: StrictModel)* - A variant worktree: name, path, branch, commits, specification tag, purpose and approval. · *Instantiated by:* `specifications/git_versioning.py::SpecificationVersionService.create_variant_worktree`
  - fields: `schema_version`, `name`, `path`, `branch`, `head_commit`, `specification_tag`, `base_ref`, `base_commit`, `purpose`, `approval`, `created_at_utc`
- **class `GitCommandError`** *(dataclass, exception; bases: RuntimeError)* - A failed git invocation carrying the command, stdout and stderr. · *Instantiated by:* `specifications/git_versioning.py::GitRepositoryAdapter._git`
  - fields: `command`, `stdout`, `stderr`
  - `GitCommandError.__str__() -> str` - Formats `Git command failed: git <args>: <stderr or stdout>`.

---

### `specifications/git_versioning.py` - local-only Git adapter and the specification version-lock service

*504 lines · depends on: `foundations/atomic_io.py`, `specifications/gate_models.py`, `specifications/git_models.py` · used by: `mcp/_shared.py`, `mcp/server.py` · re-exported at the package root: 2 name(s)*

**Role in the workflow.** Turns an approved Gate 1 soft-lock into a Git annotated tag plus a structured snapshot, and later classifies the next candidate version by comparing it with the previous snapshot. Exposed through the MCP git tools.

**Contents**

- **class `GitRepositoryAdapter`** *(class)* - Repository-scoped git commands only; no generic shell, remotes or destructive operations. · *Instantiated by:* `mcp/_shared.py::McpContext.repository_for`, `specifications/git_versioning.py::SpecificationVersionService.create_variant_worktree`
  - `GitRepositoryAdapter.__init__(repository_root: Path) -> None` - Resolves the repository root.
  - `GitRepositoryAdapter.root() -> Path` *(property)* - Property: the resolved root.
  - `GitRepositoryAdapter.state() -> GitRepositoryState` - Verifies the configured root is the true top level, then returns HEAD, tree, branch, cleanliness (the `.agent-*` runtime directories are excluded from the status check) and the `vMAJOR.MINOR.PATCH` tags in version order; other `v*` tags are ignored. · *Called by:* `base_agent/agent.py::BaseAgent._apply_tool_outcome_to_project_state`, `openai_compatible/chat.py::OpenAICompatibleAgentModel._continuation_messages`, `openai_compatible/chat.py::OpenAICompatibleAgentModel.accept_tool_results`, `optimization/solvers.py::_prune_dominated` (+37 more)
  - `GitRepositoryAdapter.diff_names(base_ref: str | None=None) -> list[str]` - `git diff --name-status` (optionally from a base ref to HEAD). · *Called by:* `specifications/git_versioning.py::SpecificationVersionService.classify`
  - `GitRepositoryAdapter.tag_exists(tag_name: str) -> bool` - Exact lookup with `rev-parse --verify --quiet refs/tags/<name>`, so glob characters in a tag name never match other tags. · *Called by:* `specifications/git_versioning.py::SpecificationVersionService.create_lock`, `specifications/git_versioning.py::SpecificationVersionService.create_variant_worktree`
  - `GitRepositoryAdapter.tag_message(tag_name: str) -> str` - The annotated tag's message body (`%(contents)`), stripped. · *Called by:* `specifications/git_versioning.py::SpecificationVersionService.create_lock`
  - `GitRepositoryAdapter.create_annotated_tag(tag_name: str, message: str) -> None` - Creates an annotated tag with a message. · *Called by:* `specifications/git_versioning.py::SpecificationVersionService.create_lock`
  - `GitRepositoryAdapter.delete_tag(tag_name: str) -> None` - Deletes a tag (used to roll back a failed lock). · *Called by:* `specifications/git_versioning.py::SpecificationVersionService.create_lock`
  - `GitRepositoryAdapter.tag_object_id(tag_name: str) -> str` - The tag object id. · *Called by:* `specifications/git_versioning.py::SpecificationVersionService.create_lock`
  - `GitRepositoryAdapter.resolve_commit(ref: str) -> str` - Resolves a non-option ref to a commit id (rejects empty or `-` refs). · *Called by:* `specifications/git_versioning.py::SpecificationVersionService.create_variant_worktree`
  - `GitRepositoryAdapter.create_worktree(path: Path, branch: str, base_ref: str) -> None` - `git worktree add -b <branch> <path> <base>`.
  - `GitRepositoryAdapter.remove_worktree(path: Path) -> None` - `git worktree remove --force <path>`. · *Called by:* `specifications/git_versioning.py::SpecificationVersionService.create_variant_worktree`
  - `GitRepositoryAdapter.delete_branch(branch: str) -> None` - `git branch -D <branch>`. · *Called by:* `specifications/git_versioning.py::SpecificationVersionService.create_variant_worktree`
  - `GitRepositoryAdapter.worktrees() -> list[dict[str, str]]` - Parses `git worktree list --porcelain` into dictionaries. · *Called by:* `tools/delegation.py::SubagentCoordinator.__init__`
  - `GitRepositoryAdapter._run(*arguments: str) -> tuple[int, str, str]` - Runs git with `core.quotepath=false` and prompts disabled and returns (return code, stdout, stderr) decoded as UTF-8 with replacement, independent of the machine's locale. · *Called by:* `specifications/git_versioning.py::GitRepositoryAdapter._git`, `specifications/git_versioning.py::GitRepositoryAdapter.tag_exists`
  - `GitRepositoryAdapter._git(*arguments: str, check: bool=True) -> str` - Runs git through `_run` and raises `GitCommandError` on a non-zero status when `check` is set; returns stdout. · *Called by:* `specifications/git_versioning.py::GitRepositoryAdapter.create_annotated_tag`, `specifications/git_versioning.py::GitRepositoryAdapter.create_worktree`, `specifications/git_versioning.py::GitRepositoryAdapter.delete_tag`, `specifications/git_versioning.py::GitRepositoryAdapter.diff_names` (+5 more)
- **class `SpecificationVersionService`** *(class)* - Classifies, verifies and locally tags a complete approved specification state under `<root>/.agent-git-locks`. · *Instantiated by:* `mcp/server.py::create_mcp_server`
  - `SpecificationVersionService.__init__(lock_root: Path) -> None` - Creates the lock and snapshot directories.
  - `SpecificationVersionService.classify(repository: GitRepositoryAdapter, version: str, specification: UnifiedSpecification, dependency_graph: DependencyGraph) -> VersionClassification` - Validates the semver, finds the previous tag and its snapshot, diffs structure and dependency edges, and recommends MAJOR (breaking or first baseline), MINOR (additions only) or PATCH. · *Called by:* `mcp/git_tools.py::register_git_tools.classify_specification_version`, `specifications/git_versioning.py::SpecificationVersionService.create_lock`
  - `SpecificationVersionService._snapshot_path(tag_name: str) -> Path` - Snapshot file path for a `vMAJOR.MINOR.PATCH` tag; any other name raises, quoting it. · *Called by:* `specifications/git_versioning.py::SpecificationVersionService._load_snapshot`, `specifications/git_versioning.py::SpecificationVersionService._persist_snapshot`, `specifications/git_versioning.py::SpecificationVersionService.create_lock`
  - `SpecificationVersionService._load_snapshot(tag_name: str) -> SpecificationSnapshotRecord` - Loads a persisted snapshot and re-verifies the specification and graph digests. · *Called by:* `specifications/git_versioning.py::SpecificationVersionService.classify`
  - `SpecificationVersionService._persist_snapshot(*, tag_name: str, version: str, specification: UnifiedSpecification, dependency_graph: DependencyGraph, specification_digest: str, dependency_grap...` - Writes a snapshot record atomically. · *Called by:* `specifications/git_versioning.py::SpecificationVersionService.create_lock`
  - `SpecificationVersionService.create_lock(repository: GitRepositoryAdapter, specification: UnifiedSpecification, dependency_graph: DependencyGraph, gap_report: GapReport, metadata: Version...` - After checking approval, soft-lock metadata, a clean repo, a free tag, matching change kind, a valid bump and the specification hash, creates the annotated tag, persists the snapshot and lock record, and deletes the tag and files again if anything fails. A tag left by an interrupted earlier attempt (same message and commit, no lock record) is removed and the lock proceeds; any other existing tag raises. · *Called by:* `mcp/git_tools.py::register_git_tools.create_specification_git_lock`
  - `SpecificationVersionService.create_variant_worktree(repository: GitRepositoryAdapter, *, name: str, branch: str, base_ref: str, specification_tag: str, purpose: str, approval: GitApproval) -> Varian...` - With an approved decision, validates names, requires `base_ref` to resolve to the same commit as an existing specification tag, creates a worktree and writes its record; if recording fails the worktree is removed and the branch deleted before the error propagates.
- `structural_specification_diff(previous: UnifiedSpecification, previous_graph: DependencyGraph, current: UnifiedSpecification, current_graph: DependencyGraph) -> StructuralSpeci...` - Exact comparison of requirement ids, requirement text/category/fields, and dependency edges (source locators and acceptance checks are outside the breaking surface). · *Called by:* `specifications/git_versioning.py::SpecificationVersionService.classify`
- `_major_rationale(diff: StructuralSpecificationDiff) -> list[str]` - Human-readable reasons for a MAJOR classification. · *Called by:* `specifications/git_versioning.py::SpecificationVersionService.classify`
- `_parse_semver(value: str) -> tuple[int, int, int]` - Parses `MAJOR.MINOR.PATCH` (no prerelease) into integers or raises. · *Called by:* `specifications/git_versioning.py::SpecificationVersionService.classify`, `specifications/git_versioning.py::SpecificationVersionService.create_lock`
- `_satisfies_bump(previous: tuple[int, int, int], requested: tuple[int, int, int], required: VersionBump) -> bool` - Whether the requested version advances the previous one by the required bump (major resets minor/patch, minor resets patch). · *Called by:* `specifications/git_versioning.py::SpecificationVersionService.create_lock`
- `_sha256(value: Any) -> str` - SHA-256 of canonical JSON. · *Called within this file by:* `specifications/git_versioning.py::SpecificationVersionService._load_snapshot`, `specifications/git_versioning.py::SpecificationVersionService.create_lock`
- `_atomic_json(target: Path, value: dict[str, Any]) -> None` - Writes JSON to a unique temporary file and replaces the target atomically. · *Called by:* `specifications/git_versioning.py::SpecificationVersionService._persist_snapshot`, `specifications/git_versioning.py::SpecificationVersionService.create_lock`, `specifications/git_versioning.py::SpecificationVersionService.create_variant_worktree`

*Module-level names:* `_SEMVER`, `_SAFE_BRANCH`

---

### `specifications/preprocessing.py` - manifest-driven, source-preserving specification parsing

*420 lines · depends on: `foundations/atomic_io.py`, `foundations/identifiers.py`, `foundations/json_limits.py`, `foundations/text.py`, `specifications/documents.py`, `specifications/vision.py` · used by: `mcp/_shared.py`, `mcp/server.py`, `memory/context_selection.py`, `specifications/retrieval.py` · re-exported at the package root: 1 name(s)*

**Role in the workflow.** First stage of the pipeline: reads the manifest, parses each document into an ordered `DocumentTree`, optionally resolves images through a vision adapter, and can persist the processed trees. Exposed through the MCP specification tools.

**Contents**

- **class `SpecificationPreprocessor`** *(class)* - Safely parses manifests and source documents beneath one root. · *Instantiated by:* `mcp/server.py::create_mcp_server`
  - `SpecificationPreprocessor.__init__(specification_root: Path) -> None` - Resolves the specification root.
  - `SpecificationPreprocessor.load_manifest(relative_manifest_path: str='specification-manifest.yaml') -> SpecificationManifest` - Loads a YAML manifest (converting the legacy per-category layout when there is no `documents` key) and validates it. · *Called by:* `mcp/specification_tools.py::register_specification_tools.process_specification_manifest`
  - `SpecificationPreprocessor.process_manifest(manifest: SpecificationManifest) -> list[DocumentTree]` - Processes every manifest document in order. · *Called by:* `mcp/specification_tools.py::register_specification_tools.process_specification_manifest`
  - `SpecificationPreprocessor.process_document(document: SpecificationDocument) -> DocumentTree` - Reads the file, hashes it, parses it with a source-ref factory bound to that hash, adds neighbouring context and returns the tree; any parser failure is re-raised as a `ValueError` naming the document path and format. · *Called by:* `specifications/preprocessing.py::SpecificationPreprocessor.process_manifest`
    - `SpecificationPreprocessor.process_document.source(location: str) -> SourceRef` - Builds a `SourceRef` for a location inside the current document. · *Called by:* `openai_compatible/semantic_gap.py::OpenAICompatibleSemanticGapAnalyzer.analyze`, `openai_compatible/vision.py::OpenAICompatibleVisionAdapter.extract`, `openai_compatible/vision.py::SourceVerifiedImageLoader.__call__`, `memory/context_selection.py::TaskAwareContextSelector.select` (+16 more)
  - `SpecificationPreprocessor.resolve_images(tree: DocumentTree, adapter: VisionAdapter | None=None, *, confidence_threshold: float=0.8, max_attempts: int=3) -> DocumentTree` *(async)* - For each image node asks the vision adapter up to `max_attempts` times; accepts the first proposal with confidence >= the threshold (0.8) and a non-empty structure, otherwise marks the node review-required. · *No in-package callers (public API, entry point, or protocol hook).*
  - `SpecificationPreprocessor.persist_tree(tree: DocumentTree) -> Path` - Atomically writes `processed/<file_safe_name(document id)>.document.yaml`. · *Called by:* `mcp/specification_tools.py::register_specification_tools.process_specification_manifest`
  - `SpecificationPreprocessor._parse(path: Path, format_: DocumentFormat, source) -> list[DocumentNode]` - Format dispatch: line nodes for text/code/diagram sources (UTF-8 BOM tolerated); per-page text plus image nodes for PDF; paragraphs, inline-image markers and tables for DOCX; tables for CSV/XLSX; one depth-bounded structured node for YAML/JSON; safe-parsed XML for XML/SVG/draw.io; page parts for VSDX (each part limited to 16 MiB and all parts to 64 MiB when decompressed); a single pending image node for PNG/JPEG. · *Called by:* `specifications/preprocessing.py::SpecificationPreprocessor.process_document`
  - `SpecificationPreprocessor._line_nodes(text: str, source, kind: DocumentNodeKind) -> list[DocumentNode]` *(staticmethod)* - One node per non-blank line, split on `\r\n`, `\r` or `\n` only (or a single empty node). · *Called by:* `specifications/preprocessing.py::SpecificationPreprocessor._parse`
  - `SpecificationPreprocessor._table_nodes(path: Path, format_: DocumentFormat, source) -> list[DocumentNode]` *(staticmethod)* - CSV (BOM tolerated, with the csv field-size limit raised to the file size for the call) as one table node; XLSX as one table node per sheet. · *Called by:* `specifications/preprocessing.py::SpecificationPreprocessor._parse`
  - `SpecificationPreprocessor._with_context(nodes: list[DocumentNode]) -> list[DocumentNode]` *(staticmethod)* - Attaches up to two neighbouring node texts as `surrounding_text`. · *Called by:* `specifications/preprocessing.py::SpecificationPreprocessor.process_document`
  - `SpecificationPreprocessor._resolve(relative_path: str) -> Path` - Resolves a path under the root and rejects escapes. · *Called by:* `specifications/preprocessing.py::SpecificationPreprocessor.load_manifest`, `specifications/preprocessing.py::SpecificationPreprocessor.process_document`
  - `SpecificationPreprocessor._legacy_manifest_to_documents(payload: dict[str, Any]) -> dict[str, Any]` *(staticmethod)* - Flattens the old category-keyed manifest layout into a `documents` list. · *Called by:* `specifications/preprocessing.py::SpecificationPreprocessor.load_manifest`
- `keywords_from_task(text: str) -> set[str]` - Lowercase keyword set (letters first, length >= 3) used for matching. · *Called by:* `memory/context_selection.py::TaskAwareContextSelector.select`, `specifications/retrieval.py::DeterministicLexicalRetrievalIndex.search`

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
  - `RedisRetrievalCache.__init__(redis_url: str, *, namespace: str='agent-sdk:retrieval:') -> None` - Validates the URL and namespace, imports `redis` (the install hint it prints still names the old package `agent-design-agent-sdk[redis-cache]`) and opens a client.
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

*131 lines · depends on: `foundations/contracts.py`, `specifications/documents.py` · used by: `specifications/retrieval.py` · re-exported at the package root: 7 name(s)*

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
- `_canonical_digest(value: Any) -> str` - SHA-256 of canonical JSON. · *Called by:* `specifications/retrieval_models.py::RetrievalDocument.stable_id`, `specifications/retrieval_models.py::RetrievalQuery.query_digest`

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
