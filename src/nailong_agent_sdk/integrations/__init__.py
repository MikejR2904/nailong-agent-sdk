# Copyright (c) 2026 David Michael Indraputra

"""Optional, authority-preserving framework and evaluator integrations.

Importing this module never imports LangGraph, LangChain, or TypeSafe.  Concrete
optional adapters load their dependencies lazily when a host explicitly uses them.
"""

from .contracts import (
    InteropFailureMode,
    InteropOperationStatus,
    InteropReceipt,
    InteropRunEnvelope,
    SanitizedStateProjector,
    assert_sanitized_interop_value,
)
from .jev import (
    ExplorationCandidate,
    InMemoryJevReceiptStore,
    JevAdvisoryPolicy,
    JevAdvisoryVerificationGate,
    JevAnswer,
    JevArchitectureAdvice,
    JevArchitectureRouter,
    JevArchitectureRoutingPolicy,
    JevChoiceAnswer,
    JevChoiceQuestion,
    JevDecisionProvider,
    JevDecisionReceipt,
    JevDecisionRequest,
    JevDecisionResult,
    JevExplorationAdvice,
    JevExplorationAdvisor,
    JevNoulAnswer,
    JevNoulQuestion,
    JevQuestion,
    JevQuestionKind,
    JevQuestionSpec,
    JevScoreAnswer,
    JevScoreQuestion,
    TelemetryJevReceiptSink,
    TypeSafeJevDecisionEvaluator,
)
from .langchain import (
    LangChainAgentModelAdapter,
    LangChainSdkRunnable,
    LangChainSdkToolFacade,
    parse_structured_sdk_turn,
)
from .langgraph import (
    LangGraphApprovalChallenge,
    LangGraphNodeExecutor,
    LangGraphSdkNode,
    LangGraphSdkNodeBinding,
    build_langgraph_state_graph,
    langgraph_interrupt_payload,
)
from .receipts import (
    InMemoryInteropReceiptStore,
    InteropReceiptSink,
    TelemetryInteropReceiptSink,
)

__all__ = [
    "ExplorationCandidate",
    "InMemoryJevReceiptStore",
    "InMemoryInteropReceiptStore",
    "InteropFailureMode",
    "InteropOperationStatus",
    "InteropReceipt",
    "InteropReceiptSink",
    "InteropRunEnvelope",
    "JevArchitectureAdvice",
    "JevArchitectureRouter",
    "JevArchitectureRoutingPolicy",
    "JevAdvisoryPolicy",
    "JevAdvisoryVerificationGate",
    "JevAnswer",
    "JevChoiceAnswer",
    "JevChoiceQuestion",
    "JevDecisionProvider",
    "JevDecisionReceipt",
    "JevDecisionRequest",
    "JevDecisionResult",
    "JevExplorationAdvice",
    "JevExplorationAdvisor",
    "JevNoulAnswer",
    "JevNoulQuestion",
    "JevQuestion",
    "JevQuestionKind",
    "JevQuestionSpec",
    "JevScoreAnswer",
    "JevScoreQuestion",
    "LangChainAgentModelAdapter",
    "LangChainSdkRunnable",
    "LangChainSdkToolFacade",
    "LangGraphApprovalChallenge",
    "LangGraphNodeExecutor",
    "LangGraphSdkNode",
    "LangGraphSdkNodeBinding",
    "SanitizedStateProjector",
    "TelemetryJevReceiptSink",
    "TelemetryInteropReceiptSink",
    "TypeSafeJevDecisionEvaluator",
    "assert_sanitized_interop_value",
    "build_langgraph_state_graph",
    "langgraph_interrupt_payload",
    "parse_structured_sdk_turn",
]
