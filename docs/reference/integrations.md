# `integrations/` - optional, authority-preserving bridges to external frameworks and evaluators

Nothing in this package is imported by the core runtime. Each adapter loads its optional dependency lazily (`require_optional_module`) and keeps one rule: the SDK owns authority, evidence and durable state, and only a bounded, sanitised projection plus digests cross the boundary. `contracts.py` and `_utils.py` define that boundary (an `InteropRunEnvelope`, `InteropReceipt`, the key-name sanitiser and the digest helper); `jev/` wraps the TypeSafe Jev evaluator as read-only advice (verification, exploration ordering, single-to-multi routing lift) that local policy may accept, reject or escalate; `langchain.py` and `langgraph.py` run SDK agents inside those frameworks or run a framework graph inside an SDK graph node.

| File | Lines | Role |
|---|---:|---|
| [`integrations/__init__.py`](#integrations__init__py---public-surface-of-the-integrations-package) | 113 | public surface of the integrations package |
| [`integrations/_utils.py`](#integrations_utilspy---private-sanitiser-digest-and-optional-import-helpers) | 126 | private sanitiser, digest and optional-import helpers |
| [`integrations/contracts.py`](#integrationscontractspy---framework-neutral-interop-contracts) | 147 | framework-neutral interop contracts |
| [`integrations/jev/__init__.py`](#integrationsjev__init__py---public-surface-of-the-jev-package) | 59 | public surface of the Jev package |
| [`integrations/jev/advisory.py`](#integrationsjevadvisorypy---verification-gate-that-adds-a-non-authoritative-jev-signal) | 107 | verification gate that adds a non-authoritative Jev signal |
| [`integrations/jev/architecture.py`](#integrationsjevarchitecturepy---monotonic-single-to-multi-routing-advice) | 151 | monotonic single-to-multi routing advice |
| [`integrations/jev/decision.py`](#integrationsjevdecisionpy---optional-typesafe-jev-evaluator) | 296 | optional TypeSafe Jev evaluator |
| [`integrations/jev/exploration.py`](#integrationsjevexplorationpy---optional-prioritisation-of-an-already-approved-candidate-set) | 139 | optional prioritisation of an already-approved candidate set |
| [`integrations/jev/models.py`](#integrationsjevmodelspy---jev-question-answer-request-result-and-receipt-contracts) | 163 | Jev question, answer, request, result and receipt contracts |
| [`integrations/jev/receipts.py`](#integrationsjevreceiptspy---receipt-sinks-for-jev-evaluations) | 57 | receipt sinks for Jev evaluations |
| [`integrations/langchain.py`](#integrationslangchainpy---optional-langchain-adapters) | 247 | optional LangChain adapters |
| [`integrations/langgraph.py`](#integrationslanggraphpy---optional-langgraph-adapters) | 298 | optional LangGraph adapters |
| [`integrations/receipts.py`](#integrationsreceiptspy---receipt-sinks-for-external-operations) | 65 | receipt sinks for external operations |

---

### `integrations/__init__.py` - public surface of the integrations package

*113 lines · depends on: `integrations/contracts.py`, `integrations/jev/__init__.py`, `integrations/langchain.py`, `integrations/langgraph.py`, `integrations/receipts.py` · used by: no other module (entry point or re-exported only) · not re-exported at the package root*

**Role in the workflow.** Re-exports the contracts, the Jev advisory classes, the LangChain and LangGraph adapters and the receipt sinks; importing it never imports LangChain, LangGraph or TypeSafe.

---

### `integrations/_utils.py` - private sanitiser, digest and optional-import helpers

*126 lines · depends on: nothing in the package · used by: `integrations/contracts.py`, `integrations/jev/decision.py`, `integrations/jev/models.py`, `integrations/langchain.py`, `integrations/langgraph.py` · not re-exported at the package root*

**Role in the workflow.** Every contract validator and every receipt digest in this package calls these.

**Contents**

- `_canonical_key(value: Any) -> str` - Normalises a key (camelCase, kebab, spaces, case) to lowercase snake form so variants of a forbidden name cannot slip through. · *Called by:* `integrations/_utils.py::assert_sanitized_interop_value`
- `assert_sanitized_interop_value(value: Any, *, _depth: int=0) -> None` - Rejects values unsafe for a framework boundary: non-JSON types (tuples and non-dict mappings are rejected), nesting deeper than 16, lists over 256 items, objects over 128 entries, keys over 256 characters, strings over 16,384 characters, and any dictionary key that is a credential or transcript name (token, secret, password, message, messages, approval, audit, capability and similar, including collapsed variants). Only keys are inspected, never string values. · *Called by:* `integrations/_utils.py::canonical_digest`, `integrations/contracts.py::ExternalDecisionRequest.state_is_safe`, `integrations/contracts.py::ExternalDecisionResult.answers_are_safe`, `integrations/contracts.py::InteropRunEnvelope.projection_is_safe` (+5 more)
- **class `OptionalDependencyError`** *(exception; bases: RuntimeError)* - `RuntimeError` subclass raised when an optional dependency is missing; its message names the module and the exact `nailong-agent-sdk[<extra>]` install hint, and Jev reports it verbatim. · *Instantiated by:* `integrations/_utils.py::require_optional_module`
- `content_digest(value: Any) -> str` - SHA-256 over canonical JSON (sorted keys, no spaces, ASCII, `default=str`) without the key sanitiser; for hashing content that is not itself exported, such as an agent's output or a question spec. · *Called by:* `integrations/_utils.py::canonical_digest`, `jev/decision.py::_normalize_jev_response`, `jev/models.py::JevQuestionSpec.digest`, `integrations/langgraph.py::LangGraphSdkNode.__call__`
- `canonical_digest(value: Any) -> str` - Sanitises, then hashes with `content_digest`. Use it only for values that really cross the boundary; hashing a value whose keys include a forbidden name raises. · *Called by:* `integrations/contracts.py::InteropRunEnvelope.projection_digest_matches_projection`, `jev/models.py::JevDecisionRequest.state_digest`, `integrations/langgraph.py::LangGraphSdkNodeBinding.binding_digest`
- `require_optional_module(module_name: str, extra_name: str) -> Any` - Imports an optional dependency or raises `OptionalDependencyError` naming the module and the install hint `nailong-agent-sdk[<extra>]`. · *Called by:* `jev/decision.py::TypeSafeJevDecisionEvaluator._request_once`, `integrations/langchain.py::LangChainSdkRunnable.as_runnable`, `integrations/langchain.py::LangChainSdkToolFacade.as_tool`, `integrations/langgraph.py::build_langgraph_state_graph`

*Module-level names:* `_FORBIDDEN_KEYS`, `_FORBIDDEN_COLLAPSED_KEYS`, `_MAX_INTEROP_DEPTH`, `_MAX_INTEROP_LIST_ITEMS`, `_MAX_INTEROP_MAPPING_ENTRIES`, `_MAX_INTEROP_STRING_CHARS`

---

### `integrations/contracts.py` - framework-neutral interop contracts

*147 lines · depends on: `foundations/contracts.py`, `integrations/_utils.py` · used by: `integrations/__init__.py`, `integrations/jev/advisory.py`, `integrations/jev/architecture.py`, `integrations/jev/decision.py`, `integrations/jev/exploration.py`, `integrations/jev/models.py`, `integrations/jev/receipts.py`, `integrations/langchain.py` (+2 more) · re-exported at the package root: 4 name(s)*

**Role in the workflow.** `InteropRunEnvelope` carries sanitised state into a framework node, `InteropReceipt` records what an external operation did, and `ExternalDecisionProvider` is the read-only evaluator protocol the Jev classes consume.

**Contents**

- **class `InteropFailureMode`** *(enum; bases: StrEnum)* - What a host wants when an optional external call is unavailable: escalate, reject or fall back to the deterministic result.
  - members: `ESCALATE`, `REJECT`, `FALLBACK_DETERMINISTIC`
- **class `InteropOperationStatus`** *(enum; bases: StrEnum)* - succeeded, rejected, escalated, unavailable or failed.
  - members: `SUCCEEDED`, `REJECTED`, `ESCALATED`, `UNAVAILABLE`, `FAILED`
- **class `InteropRunEnvelope`** *(pydantic model; bases: StrictModel)* - Sanitised state crossing a boundary: run, task and graph node ids, external thread id, remaining turn budget, state reference, projection and its digest.
  - fields: `schema_version`, `run_id`, `task_id`, `graph_node_id`, `external_thread_id`, `remaining_turn_budget`, `state_reference`, `projection`, `projection_digest`, `ledger_event_hash`
  - `InteropRunEnvelope.projection_is_safe(projection: dict[str, Any]) -> dict[str, Any]` *(validator, classmethod)* - Validator: the projection passes the sanitiser.
  - `InteropRunEnvelope.projection_digest_matches_projection() -> InteropRunEnvelope` *(validator)* - Validator: fills the digest when absent and rejects a wrong caller-supplied digest.
- **class `InteropReceipt`** *(pydantic model; bases: StrictModel)* - Digest-only evidence record of an external operation: provider, operation, status, run id, projection and result digests, model, request and checkpoint ids, duration, retries and a detail code. · *Instantiated by:* `integrations/langgraph.py::LangGraphSdkNode._emit_receipt`
  - fields: `schema_version`, `provider`, `operation`, `status`, `run_id`, `projection_digest`, `result_digest`, `provider_model`, `provider_request_id`, `external_checkpoint_id`, `external_parent_checkpoint_id`, `duration_ms`, `retry_count`, `detail_code`
- **class `ExternalDecisionRequest`** *(pydantic model; bases: StrictModel)* - Bounded evaluator request: purpose, run id, sanitised state with digest, question spec id, version and digest, model and a deadline of at most 300 seconds.
  - fields: `schema_version`, `purpose`, `run_id`, `state`, `state_digest`, `question_spec_id`, `question_spec_version`, `question_spec_digest`, `model`, `deadline_seconds`
  - `ExternalDecisionRequest.state_is_safe(state: dict[str, Any]) -> dict[str, Any]` *(validator, classmethod)* - Validator: the state passes the sanitiser.
- **class `ExternalDecisionResult`** *(pydantic model; bases: StrictModel)* - Normalised evaluator result: status, model, request id, answers, response digest, token counts, duration, retries and an unavailable reason.
  - fields: `schema_version`, `status`, `model`, `provider_request_id`, `answers`, `response_digest`, `input_tokens`, `output_tokens`, `duration_ms`, `retry_count`, `unavailable_reason`
  - `ExternalDecisionResult.answers_are_safe(answers: dict[str, Any]) -> dict[str, Any]` *(validator, classmethod)* - Validator: the answers pass the sanitiser.
- **class `ExternalDecisionProvider`** *(Protocol; bases: Protocol)* - Read-only evaluator protocol; implementations never receive authority objects.
  - `ExternalDecisionProvider.evaluate(request: ExternalDecisionRequest) -> ExternalDecisionResult` *(async)* - Evaluate one request and return a result.

**Algorithms & invariants.** `SanitizedStateProjector` and `AsyncSanitizedStateProjector` are callable type aliases defined here.

*Module-level names:* `SanitizedStateProjector`, `AsyncSanitizedStateProjector`

---

### `integrations/jev/__init__.py` - public surface of the Jev package

*59 lines · depends on: `integrations/jev/advisory.py`, `integrations/jev/architecture.py`, `integrations/jev/decision.py`, `integrations/jev/exploration.py`, `integrations/jev/models.py`, `integrations/jev/receipts.py` · used by: `integrations/__init__.py` · not re-exported at the package root*

**Role in the workflow.** Re-exports the Jev models, evaluator, advisory gate, architecture router, exploration advisor and receipt sinks. Jev is a typed evaluator, never an executor or an authority.

---

### `integrations/jev/advisory.py` - verification gate that adds a non-authoritative Jev signal

*107 lines · depends on: `agent/verification.py`, `foundations/contracts.py`, `integrations/contracts.py`, `integrations/jev/models.py` · used by: `integrations/jev/__init__.py` · re-exported at the package root: 2 name(s)*

**Role in the workflow.** Registered as a verification gate: the deterministic gate runs first and a failure is final; only after it passes is Jev asked, and Jev's answer can lower confidence but never accept a locally failed output.

**Contents**

- **class `JevAdvisoryPolicy`** *(pydantic model; bases: StrictModel)* - Fixed policy: the noul question id, the minimum probability, the unavailable mode and a version.
  - fields: `question_id`, `minimum_noul`, `on_unavailable`, `policy_version`
- **class `JevAdvisoryVerificationGate`** *(class)* - Composes a deterministic gate with Jev.
  - `JevAdvisoryVerificationGate.__init__(deterministic_gate: VerificationGate, evaluator: ExternalDecisionProvider, request_factory: JevRequestFactory, policy: JevAdvisoryPolicy) -> None` - Stores the gate, evaluator, request factory and policy.
  - `JevAdvisoryVerificationGate.verify(context: VerificationContext) -> VerificationReturn` *(async)* - Runs the deterministic gate, returns its failure, otherwise builds a request, evaluates it and applies the policy.
  - `JevAdvisoryVerificationGate._apply_policy(result: ExternalDecisionResult) -> VerificationDecision` - Unavailable results follow the failure mode; the named question must be a noul answer; passes when the probability meets the minimum. · *Called by:* `jev/advisory.py::JevAdvisoryVerificationGate.verify`
- `_unavailable_decision(mode: InteropFailureMode, reason: str | None) -> VerificationDecision` - Fallback accepts, escalate rejects with `Escalation required`, reject rejects, each naming the reason. · *Called by:* `jev/advisory.py::JevAdvisoryVerificationGate._apply_policy`

*Module-level names:* `JevRequestFactory`

---

### `integrations/jev/architecture.py` - monotonic single-to-multi routing advice

*151 lines · depends on: `foundations/contracts.py`, `integrations/contracts.py`, `integrations/jev/models.py` · used by: `agent/orchestrator/models.py`, `agent/orchestrator/orchestrator.py`, `integrations/jev/__init__.py` · re-exported at the package root: 3 name(s)*

**Role in the workflow.** `Orchestrator.prepare` calls `advise` after the deterministic route; Jev may lift single-agent to multi-agent when its choice confidence meets the minimum and can never lower a multi-agent route.

**Contents**

- **class `JevArchitectureRoutingPolicy`** *(pydantic model; bases: StrictModel)* - Model, minimum confidence (default 0.8), unavailable mode and version.
  - fields: `model`, `minimum_confidence`, `on_unavailable`, `policy_version`
- **class `JevArchitectureAdvice`** *(pydantic model; bases: StrictModel)* - Receipt-backed advice: deterministic and chosen architecture, whether the deterministic fallback was used, a reason and the Jev result. · *Instantiated by:* `jev/architecture.py::JevArchitectureRouter._unavailable_advice`, `jev/architecture.py::JevArchitectureRouter.advise`
  - fields: `deterministic_architecture`, `architecture`, `used_deterministic_fallback`, `reason`, `result`
- **class `JevArchitectureRouter`** *(class)* - Maps a typed Jev answer to a route under the local policy.
  - `JevArchitectureRouter.__init__(evaluator: ExternalDecisionProvider, policy: JevArchitectureRoutingPolicy) -> None` - Stores the evaluator and policy.
  - `JevArchitectureRouter.advise(*, run_id: str, deterministic_architecture: Literal['single-agent', 'multi-agent'], state: dict[str, Any], deadline_seconds: float=20) -> JevArchi...` *(async)* - Returns a no-op advice (no Jev call) for a deterministic multi-agent route; otherwise asks a fixed single-versus-multi choice question and lifts only on a high-confidence multi-agent answer. · *Called by:* `orchestrator/orchestrator.py::Orchestrator.prepare`
  - `JevArchitectureRouter._unavailable_advice(result: JevDecisionResult, detail: str | None=None) -> JevArchitectureAdvice` - Raises `RuntimeError` in reject mode; any other mode, escalate included, keeps the single-agent route with the reason. · *Called by:* `jev/architecture.py::JevArchitectureRouter.advise`

---

### `integrations/jev/decision.py` - optional TypeSafe Jev evaluator

*296 lines · depends on: `integrations/_utils.py`, `integrations/contracts.py`, `integrations/jev/models.py`, `integrations/jev/receipts.py` · used by: `integrations/jev/__init__.py` · re-exported at the package root: 1 name(s)*

**Role in the workflow.** `evaluate` runs up to `max_attempts` bounded calls to `typesafe_sdk`, normalises the response into typed answers checked against the question spec, records a digest-only receipt and returns the result; any failure becomes an UNAVAILABLE result whose reason is only `provider-<exception type>`.

**Contents**

- **class `JevResponseError`** *(exception; bases: ValueError)* - `ValueError` subclass for problems the SDK itself detects in a provider response; its message is safe to record. · *Instantiated by:* `jev/decision.py::_to_mapping`, `jev/decision.py::_validate_answers_match_spec`
- **class `TypeSafeJevDecisionEvaluator`** *(class)* - Concrete provider using `typesafe-sdk` only when selected; construction performs no call and infers no credentials.
  - `TypeSafeJevDecisionEvaluator.__init__(*, receipt_sink: JevReceiptSink, policy_version: str='jev-advisory-v1', client_factory: Callable[[], Any] | None=None) -> None` - Stores the receipt sink, policy version and optional client factory.
  - `TypeSafeJevDecisionEvaluator.evaluate(request: JevDecisionRequest) -> JevDecisionResult` *(async)* - Times the evaluation, converts any exception into an UNAVAILABLE result with a bounded reason, records a receipt and returns the result.
  - `TypeSafeJevDecisionEvaluator._evaluate_with_bounded_attempts(request: JevDecisionRequest) -> JevDecisionResult` *(async)* - Retries immediately up to `max_attempts`, each attempt under the request deadline, and re-raises the last error. · *Called by:* `jev/decision.py::TypeSafeJevDecisionEvaluator.evaluate`
  - `TypeSafeJevDecisionEvaluator._request_once(request: JevDecisionRequest) -> Any` *(async)* - Lazily imports `typesafe_sdk`, builds the client (async-context-managed if supported) and calls `system_one` with the state, questions and model. · *Called by:* `jev/decision.py::TypeSafeJevDecisionEvaluator._evaluate_with_bounded_attempts`
  - `TypeSafeJevDecisionEvaluator._record_receipt(request: JevDecisionRequest, result: JevDecisionResult, *, policy_outcome: str) -> None` *(async)* - Builds the receipt and passes it to the sink (awaiting it if needed). · *Called by:* `jev/decision.py::TypeSafeJevDecisionEvaluator.evaluate`
- `_make_typesafe_questions(sdk: Any, spec: JevQuestionSpec) -> dict[str, Any]` - Converts the question spec into the SDK's Noul, Choice and Score objects. · *Called by:* `jev/decision.py::TypeSafeJevDecisionEvaluator._request_once`
- `_normalize_jev_response(response: Any, request: JevDecisionRequest, *, duration_ms: float, retry_count: int) -> JevDecisionResult` - Reads answers from `answers` or the grouped `nouls`, `choices`, `scores` collections, validates them, checks them against the spec and computes the response digest and token counts. · *Called by:* `jev/decision.py::TypeSafeJevDecisionEvaluator._evaluate_with_bounded_attempts`
- `_to_mapping(value: Any) -> Mapping[str, Any]` - Accepts a mapping, a pydantic-style object or any object with attributes; anything else raises `JevResponseError` naming the response type. · *Called by:* `jev/decision.py::_grouped_answers`, `jev/decision.py::_normalize_jev_response`
- `_grouped_answers(payload: Mapping[str, Any]) -> dict[str, Any]` - Flattens the grouped answer collections, filling in each answer's type. · *Called by:* `jev/decision.py::_normalize_jev_response`
- `_validate_answers_match_spec(answers: Mapping[str, JevAnswer], spec: JevQuestionSpec) -> None` - Requires the same question ids, matching answer types and, for choices, a valid choice and probabilities for exactly the declared options; each failure raises `JevResponseError` naming the spec, question and options. · *Called by:* `jev/decision.py::_normalize_jev_response`
- `_safe_provider_error(error: Exception) -> str` - Returns `provider-<exception class>`; the text after a colon is added only for errors whose messages are safe by construction: the SDK's own `JevResponseError` and `OptionalDependencyError`, a timeout (names the deadline), and pydantic validation errors (field paths and error codes, never the offending values). Any other exception contributes only its class name. · *Called by:* `jev/decision.py::TypeSafeJevDecisionEvaluator.evaluate`
- `_duration_ms(started: float) -> float` - Elapsed milliseconds. · *Called within this file by:* `jev/decision.py::TypeSafeJevDecisionEvaluator.evaluate`
- `_string_or_none(value: Any) -> str | None` - A non-empty string or None. · *Called by:* `jev/decision.py::_normalize_jev_response`
- `_nonnegative_int_or_none(value: Any) -> int | None` - A non-negative integer or None. · *Called by:* `jev/decision.py::_normalize_jev_response`

---

### `integrations/jev/exploration.py` - optional prioritisation of an already-approved candidate set

*139 lines · depends on: `foundations/contracts.py`, `integrations/contracts.py`, `integrations/jev/models.py` · used by: `integrations/jev/__init__.py` · re-exported at the package root: 2 name(s)*

**Role in the workflow.** A host passes candidates it already authorised; Jev picks one; the result cannot create nodes or change state, and failure falls back to the best deterministic rank.

**Contents**

- **class `ExplorationCandidate`** *(pydantic model; bases: StrictModel)* - A redacted candidate: id, summary, deterministic rank and provenance reference.
  - fields: `candidate_id`, `summary`, `deterministic_rank`, `provenance_ref`
- **class `JevExplorationAdvice`** *(pydantic model; bases: StrictModel)* - The selected candidate id (or None), whether the deterministic fallback was used and the Jev result. · *Instantiated by:* `jev/exploration.py::JevExplorationAdvisor.prioritize`
  - fields: `selected_candidate_id`, `used_deterministic_fallback`, `result`
- **class `JevExplorationAdvisor`** *(class)* - Prioritises a bounded candidate set with a Jev choice question.
  - `JevExplorationAdvisor.__init__(evaluator: ExternalDecisionProvider, *, model: str, max_candidates: int=64, on_unavailable: InteropFailureMode=InteropFailureMode.FALLBACK_DETERMI...` - Stores the evaluator, model, candidate limit and unavailable mode.
  - `JevExplorationAdvisor.prioritize(run_id: str, candidates: Sequence[ExplorationCandidate], *, instructions: str, deadline_seconds: float=20) -> JevExplorationAdvice` *(async)* - Rejects fewer than two candidates or more than the bound (messages give the counts), orders candidates by rank then id, asks a choice question over their ids, and returns Jev's pick if it names a known candidate; otherwise rejects or falls back to the first candidate. · *No in-package callers (public API, entry point, or protocol hook).*

---

### `integrations/jev/models.py` - Jev question, answer, request, result and receipt contracts

*163 lines · depends on: `foundations/contracts.py`, `integrations/_utils.py`, `integrations/contracts.py` · used by: `integrations/jev/__init__.py`, `integrations/jev/advisory.py`, `integrations/jev/architecture.py`, `integrations/jev/decision.py`, `integrations/jev/exploration.py`, `integrations/jev/receipts.py` · re-exported at the package root: 5 name(s)*

**Role in the workflow.** A host fixes a `JevQuestionSpec` per purpose; the evaluator sends a `JevDecisionRequest` and validates the response into typed answers; a `JevDecisionReceipt` records the outcome.

**Contents**

- **class `JevQuestionKind`** *(enum; bases: StrEnum)* - noul, choice or score.
  - members: `NOUL`, `CHOICE`, `SCORE`
- **class `JevNoulQuestion`** *(pydantic model; bases: StrictModel)* - A yes/no probability question with optional true and false criteria.
  - fields: `type`, `instructions`, `criteria`
- **class `JevChoiceQuestion`** *(pydantic model; bases: StrictModel)* - A choice among 2-255 labelled options. · *Instantiated by:* `jev/architecture.py::JevArchitectureRouter.advise`, `jev/exploration.py::JevExplorationAdvisor.prioritize`
  - fields: `type`, `instructions`, `criteria`
- **class `JevScoreQuestion`** *(pydantic model; bases: StrictModel)* - A score with 2-10 criteria.
  - fields: `type`, `instructions`, `criteria`
- **class `JevQuestionSpec`** *(pydantic model; bases: StrictModel)* - Host-owned, versioned set of up to 64 questions for one purpose. · *Instantiated by:* `jev/architecture.py::JevArchitectureRouter.advise`, `jev/exploration.py::JevExplorationAdvisor.prioritize`
  - fields: `spec_id`, `version`, `questions`
  - `JevQuestionSpec.question_ids_are_nonempty(questions: dict[str, JevQuestion]) -> dict[str, JevQuestion]` *(validator, classmethod)* - Validator: question ids are non-blank.
  - `JevQuestionSpec.digest() -> str` *(property)* - Property: content digest of the spec (option labels are not sanitised). · *Called by:* `jev/decision.py::_normalize_jev_response`, `jev/models.py::JevDecisionResult.unavailable`, `observability/telemetry_store.py::TelemetryStore.append`, `specifications/preprocessing.py::SpecificationPreprocessor.process_document` (+6 more)
- **class `JevDecisionRequest`** *(pydantic model; bases: StrictModel)* - One bounded, redacted evaluation request: purpose, run id, sanitised state, question spec, model, deadline and up to three attempts. · *Instantiated by:* `jev/architecture.py::JevArchitectureRouter.advise`, `jev/exploration.py::JevExplorationAdvisor.prioritize`
  - fields: `schema_version`, `purpose`, `run_id`, `state`, `question_spec`, `model`, `deadline_seconds`, `max_attempts`
  - `JevDecisionRequest.state_is_redacted_json(state: dict[str, Any]) -> dict[str, Any]` *(validator, classmethod)* - Validator: the state passes the sanitiser.
  - `JevDecisionRequest.state_digest() -> str` *(property)* - Property: digest of the state. · *Called by:* `jev/decision.py::TypeSafeJevDecisionEvaluator._record_receipt`, `jev/decision.py::_normalize_jev_response`, `jev/models.py::JevDecisionResult.unavailable`
- **class `JevNoulAnswer`** *(pydantic model; bases: StrictModel)* - A probability in [0, 1].
  - fields: `type`, `noul`
- **class `JevChoiceAnswer`** *(pydantic model; bases: StrictModel)* - A chosen option, option probabilities and a confidence.
  - fields: `type`, `choice`, `probabilities`, `confidence`
- **class `JevScoreAnswer`** *(pydantic model; bases: StrictModel)* - A score with legend, probabilities and a confidence.
  - fields: `type`, `score`, `legend`, `probabilities`, `confidence`
- **class `JevDecisionResult`** *(pydantic model; bases: StrictModel)* - Typed result without the submitted state or question text: status, answers, digests, token counts, duration, retries and unavailable reason. · *Instantiated by:* `jev/decision.py::_normalize_jev_response`
  - fields: `schema_version`, `status`, `model`, `provider_request_id`, `answers`, `state_digest`, `question_spec_digest`, `response_digest`, `input_tokens`, `output_tokens`, `duration_ms`, `retry_count`, `unavailable_reason`
  - `JevDecisionResult.unavailable(request: JevDecisionRequest, reason: str, *, duration_ms: float) -> JevDecisionResult` *(classmethod)* - Builds an UNAVAILABLE result carrying the request's digests and a reason. · *Called by:* `jev/decision.py::TypeSafeJevDecisionEvaluator.evaluate`
- **class `JevDecisionReceipt`** *(class; bases: InteropReceipt)* - Interop receipt extended with the question spec identity, policy version and outcome and token counts. · *Instantiated by:* `jev/decision.py::TypeSafeJevDecisionEvaluator._record_receipt`
  - fields: `schema_version`, `question_spec_id`, `question_spec_version`, `question_spec_digest`, `policy_version`, `policy_outcome`, `input_tokens`, `output_tokens`

**Algorithms & invariants.** `JevDecisionResult` is not a subclass of `ExternalDecisionResult`; the consumers rely on their shared `status`, `answers` and `unavailable_reason` attributes.

*Module-level names:* `JevQuestion`, `JevAnswer`, `_JEV_ANSWER_ADAPTER`

---

### `integrations/jev/receipts.py` - receipt sinks for Jev evaluations

*57 lines · depends on: `integrations/contracts.py`, `integrations/jev/models.py`, `observability/telemetry_models.py`, `observability/telemetry_store.py` · used by: `integrations/jev/__init__.py`, `integrations/jev/decision.py` · re-exported at the package root: 2 name(s)*

**Role in the workflow.** `TypeSafeJevDecisionEvaluator` calls the sink once per evaluation; the telemetry sink records `interop.jev.decision` without the submitted state or questions.

**Contents**

- **class `InMemoryJevReceiptStore`** *(dataclass)* - Test and development sink.
  - fields: `receipts`
  - `InMemoryJevReceiptStore.__call__(receipt: JevDecisionReceipt) -> None` - Appends a receipt.
- **class `TelemetryJevReceiptSink`** *(class)* - Emits receipt metadata only.
  - `TelemetryJevReceiptSink.__init__(telemetry: TelemetryStore, context_factory: Callable[[JevDecisionReceipt], TelemetryContext]) -> None` - Stores the telemetry store and a context factory.
  - `TelemetryJevReceiptSink.__call__(receipt: JevDecisionReceipt) -> None` - Emits an evaluator-actor, tool-authority event (INFO when succeeded, WARNING otherwise).

*Module-level names:* `JevReceiptSink`

---

### `integrations/langchain.py` - optional LangChain adapters

*247 lines · depends on: `agent/base_agent/__init__.py`, `agent/model.py`, `foundations/contracts.py`, `integrations/_utils.py`, `integrations/contracts.py`, `tools/tools.py` · used by: `integrations/__init__.py` · re-exported at the package root: 4 name(s)*

**Role in the workflow.** A LangChain Runnable can serve as the SDK model, an SDK run can be exposed as a Runnable, and an SDK tool can be exposed as a LangChain tool that re-enters the host's governed `ToolExecutor`. LangChain is never the security monitor.

**Contents**

- **class `AsyncLangChainRunnable`** *(Protocol; bases: Protocol)* - Structural protocol for a Runnable or chat model.
  - `AsyncLangChainRunnable.ainvoke(input: Any, config: Mapping[str, Any] | None=None) -> Any` *(async)* - Invoke asynchronously with input and optional config.
- **class `LangChainAgentModelAdapter`** *(class)* - Uses an injected Runnable as an SDK model adapter, with a host prompt projector and typed turn parser; never infers a tool call from prose.
  - `LangChainAgentModelAdapter.__init__(runnable: AsyncLangChainRunnable, prompt_projector: LangChainPromptProjector, turn_parser: LangChainTurnParser, *, config_factory: Callable[[Model...` - Stores the runnable, projector, parser and optional config factory.
  - `LangChainAgentModelAdapter.next_turn(context: ModelContext) -> AgentTurn | ModelTurnResponse` *(async)* - Projects the prompt, checks it with the chat-pair sanitiser, sanitises the config, invokes the runnable, parses and validates the turn.
- **class `LangChainSdkRunnable`** *(class)* - Exposes a host-built SDK agent as an async Runnable that hands the framework the redacted run outcome only.
  - `LangChainSdkRunnable.__init__(agent_factory: LangChainAgentFactory, *, include_diagnostics: bool=False) -> None` - Stores the factory; `include_diagnostics` also forwards prompt context, project state, episodes, events and profile (still redacted).
  - `LangChainSdkRunnable.ainvoke(input: ScopedAgentTask | Mapping[str, Any], config: Mapping[str, Any] | None=None) -> dict[str, Any]` *(async)* - Builds or validates the task, runs the agent and returns `_agent_result_projection` of the result.
  - `LangChainSdkRunnable.as_runnable() -> Any` - Wraps `ainvoke` in a real `RunnableLambda` (needs `langchain_core`). · *No in-package callers (public API, entry point, or protocol hook).*
- **class `LangChainSdkToolFacade`** *(class)* - A schema-only LangChain tool whose calls go through the SDK executor after a local preflight.
  - `LangChainSdkToolFacade.__init__(tool: ToolDefinition, executor: ToolExecutor, invocation_factory: LangChainToolInvocationFactory, *, preflight: LangChainToolPreflight | None=None...` - Stores the tool, executor, invocation factory and optional preflight.
  - `LangChainSdkToolFacade.ainvoke(arguments: Mapping[str, Any]) -> dict[str, Any]` *(async)* - Builds the invocation, checks it matches the declared tool and payload, runs the optional preflight (a returned value short-circuits) and otherwise executes through the executor, dropping the `failure` field. · *Called within this file by:* `integrations/langchain.py::LangChainSdkToolFacade.as_tool.call`
  - `LangChainSdkToolFacade.as_tool() -> Any` - Builds a `StructuredTool` with the SDK JSON schema unchanged. · *No in-package callers (public API, entry point, or protocol hook).*
    - `LangChainSdkToolFacade.as_tool.call(**kwargs: Any) -> dict[str, Any]` *(async)* - The tool coroutine; dispatches only through `ainvoke`. · *Called by:* `base_agent/agent.py::BaseAgent._apply_tool_outcome_to_project_state`, `base_agent/agent.py::BaseAgent._effective_call`, `base_agent/agent.py::BaseAgent._episode_summary_text`, `base_agent/agent.py::BaseAgent._execute_profiled_tool_call` (+19 more)
  - `LangChainSdkToolFacade._validate_invocation(invocation: ToolInvocationContext, payload: dict[str, Any]) -> None` - Requires the invocation's tool name and arguments to equal the declared tool and dispatched payload. · *Called by:* `integrations/langchain.py::LangChainSdkToolFacade.ainvoke`
- `parse_structured_sdk_turn(response: Any, _: ModelContext) -> AgentTurn` - Accepts a mapping or pydantic-style response and validates it as an `AgentTurn`; anything else raises `TypeError`. · *No in-package callers (public API, entry point, or protocol hook).*
- `_assert_safe_langchain_prompt(prompt: Any) -> None` - Allows at most one system and one user message with only role and content, each non-empty and at most 16,384 characters; everything else must pass the generic sanitiser. · *Called by:* `integrations/langchain.py::LangChainAgentModelAdapter.next_turn`
- `_agent_result_projection(result: AgentResult, *, include_diagnostics: bool=False) -> dict[str, Any]` - Status, task id, iterations, output, reason, failure and escalation (or everything with diagnostics), passed through `redact_secrets`. · *Called by:* `integrations/langchain.py::LangChainSdkRunnable.ainvoke`

*Module-level names:* `_OUTCOME_FIELDS`
- `_result_mapping(result: Any) -> dict[str, Any]` - Converts a preflight result (model or mapping) to a dictionary. · *Called by:* `integrations/langchain.py::LangChainSdkToolFacade.ainvoke`

*Module-level names:* `_AGENT_TURN_ADAPTER`, `LangChainPromptProjector`, `LangChainTurnParser`, `LangChainAgentFactory`, `LangChainToolPreflight`, `LangChainToolInvocationFactory`

---

### `integrations/langgraph.py` - optional LangGraph adapters

*298 lines · depends on: `agent/base_agent/__init__.py`, `agent/model.py`, `agent/runtime.py`, `agent/verification.py`, `foundations/contracts.py`, `integrations/_utils.py`, `integrations/contracts.py`, `integrations/receipts.py`, `state/graph_models.py`, `tools/tools.py` · used by: `integrations/__init__.py` · re-exported at the package root: 6 name(s)*

**Role in the workflow.** `LangGraphSdkNode` runs a bounded SDK agent as a LangGraph node and returns a compact digest-bearing transition; `LangGraphNodeExecutor` does the reverse, running a compiled LangGraph inside an SDK graph node through a projector and a typed reducer.

**Contents**

- **class `LangGraphSdkNodeBinding`** *(dataclass)* - Host-owned binding for one LangGraph node: definition, task adapter, model factory, optional tool factory, gates, hooks and version.
  - fields: `node_name`, `definition`, `task_adapter`, `model_factory`, `tool_executor_factory`, `verification_gates`, `pre_tool_hooks`, `post_tool_hooks`, `binding_version`
  - `LangGraphSdkNodeBinding.binding_digest() -> str` *(property)* - Property: digest of node name, identity, instruction version and binding version. · *Called by:* `integrations/langgraph.py::LangGraphSdkNode.__call__`
- **class `LangGraphSdkNode`** *(class)* - A callable node that invokes a host-owned SDK agent from a sanitised envelope.
  - `LangGraphSdkNode.__init__(services: AgentRuntimeServices, binding: LangGraphSdkNodeBinding, envelope_factory: Callable[[Mapping[str, Any]], InteropRunEnvelope], *, receipt_...` - Stores the services, binding, envelope factory and receipt sink.
  - `LangGraphSdkNode.__call__(state: Mapping[str, Any]) -> dict[str, Any]` *(async)* - Builds and validates the envelope, runs the agent, emits a receipt and returns `{agent_sdk_transition: ...}` with the status, remaining budget and digests; on any exception emits a FAILED receipt (`sdk-node-<type>`) and re-raises. Result digests use `content_digest`, so the agent's output keys never cause a spurious failure.
  - `LangGraphSdkNode._validate_envelope(envelope: InteropRunEnvelope) -> None` - Requires a positive remaining turn budget and a sanitised projection (outside the receipt-emitting try block). · *Called by:* `integrations/langgraph.py::LangGraphSdkNode.__call__`
  - `LangGraphSdkNode._emit_receipt(envelope: InteropRunEnvelope, status: InteropOperationStatus, *, result_digest: str, duration_ms: float, detail_code: str | None=None) -> None` *(async)* - Builds an interop receipt (with checkpoint ids from the projection) and passes it to the sink. · *Called by:* `integrations/langgraph.py::LangGraphSdkNode.__call__`
- **class `LangGraphApprovalChallenge`** *(pydantic model; bases: StrictModel)* - Display-safe, resumable approval payload; not a decision.
  - fields: `challenge_id`, `action_digest`, `policy_version`, `expires_at_utc`, `display_summary`, `idempotency_key`
- `langgraph_interrupt_payload(challenge: LangGraphApprovalChallenge) -> dict[str, Any]` - The only payload safe to pass to a LangGraph interrupt. · *No in-package callers (public API, entry point, or protocol hook).*
- **class `AsyncLangGraphRunnable`** *(Protocol; bases: Protocol)* - Structural protocol for a compiled LangGraph.
  - `AsyncLangGraphRunnable.ainvoke(input: Mapping[str, Any], config: Mapping[str, Any] | None=None) -> Any` *(async)* - Invoke asynchronously.
- **class `LangGraphNodeExecutor`** *(class)* - Adapts a compiled LangGraph to the SDK `NodeExecutor` protocol.
  - `LangGraphNodeExecutor.__init__(runnable: AsyncLangGraphRunnable, input_projector: LangGraphInputProjector, output_reducer: LangGraphOutputReducer, *, config_factory: Callable[[G...` - Stores the runnable, input projector, output reducer and optional config factory.
  - `LangGraphNodeExecutor.execute(node: GraphNode, context: GraphNodeExecutionContext) -> GraphNodeResult` *(async)* - Projects and sanitises input and config, invokes the runnable and requires the reducer to return a `GraphNodeResult`. · *Called within this file by:* `integrations/langgraph.py::LangGraphNodeExecutor.as_node_executor`
  - `LangGraphNodeExecutor.as_node_executor() -> NodeExecutor` - Returns `execute` as a `NodeExecutor`. · *No in-package callers (public API, entry point, or protocol hook).*
- `build_langgraph_state_graph(state_schema: type[Any], node_name: str, sdk_node: LangGraphSdkNode) -> Any` - Lazily imports LangGraph and builds a START to node to END graph around one SDK node. · *No in-package callers (public API, entry point, or protocol hook).*
- `_interop_status(status: AgentRunStatus) -> InteropOperationStatus` - Maps agent statuses to interop statuses (blocked maps to escalated, cancelled to rejected). · *Called by:* `integrations/langgraph.py::LangGraphSdkNode.__call__`
- `_duration_ms(started: float) -> float` - Elapsed milliseconds. · *Called within this file by:* `integrations/langgraph.py::LangGraphSdkNode.__call__`
- `_optional_string(value: Any) -> str | None` - A non-empty string or None. · *Called by:* `integrations/langgraph.py::LangGraphSdkNode._emit_receipt`

*Module-level names:* `LangGraphTaskAdapter`, `LangGraphModelFactory`, `LangGraphToolExecutorFactory`, `LangGraphInputProjector`, `LangGraphOutputReducer`

---

### `integrations/receipts.py` - receipt sinks for external operations

*65 lines · depends on: `integrations/contracts.py`, `observability/telemetry_models.py`, `observability/telemetry_store.py` · used by: `integrations/__init__.py`, `integrations/langgraph.py` · re-exported at the package root: 3 name(s)*

**Role in the workflow.** Adapters call a sink with each `InteropReceipt`; the telemetry sink persists it as an `interop.external_operation` event.

**Contents**

- **class `InMemoryInteropReceiptStore`** *(dataclass)* - Test and development sink that keeps receipts in a list.
  - fields: `receipts`
  - `InMemoryInteropReceiptStore.__call__(receipt: InteropReceipt) -> None` - Appends a receipt.
- **class `TelemetryInteropReceiptSink`** *(class)* - Persists digest-only receipts in the telemetry ledger.
  - `TelemetryInteropReceiptSink.__init__(telemetry: TelemetryStore, context_factory: Callable[[InteropReceipt], TelemetryContext]) -> None` - Stores the telemetry store and a context factory.
  - `TelemetryInteropReceiptSink.__call__(receipt: InteropReceipt) -> None` - Emits the receipt with INFO severity when it succeeded and WARNING otherwise.

*Module-level names:* `InteropReceiptSink`

