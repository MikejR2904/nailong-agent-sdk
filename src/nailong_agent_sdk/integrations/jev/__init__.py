# Copyright (c) 2026 David Michael Indraputra

"""Optional TypeSafe Jev advisory evaluation integration.

Jev is a typed System One evaluator, not a generative model, tool executor, or
policy authority.  This package consequently exposes only read-only decisions
that local deterministic policy may accept, reject, or escalate.  It never
receives an SDK capability, approval, callback, or raw audit transcript.
"""

from __future__ import annotations

from .advisory import JevAdvisoryPolicy, JevAdvisoryVerificationGate
from .architecture import JevArchitectureAdvice, JevArchitectureRouter, JevArchitectureRoutingPolicy
from .decision import TypeSafeJevDecisionEvaluator
from .exploration import ExplorationCandidate, JevExplorationAdvice, JevExplorationAdvisor
from .models import (
    JevAnswer,
    JevChoiceAnswer,
    JevChoiceQuestion,
    JevDecisionReceipt,
    JevDecisionRequest,
    JevDecisionResult,
    JevNoulAnswer,
    JevNoulQuestion,
    JevQuestion,
    JevQuestionKind,
    JevQuestionSpec,
    JevScoreAnswer,
    JevScoreQuestion,
)
from .receipts import InMemoryJevReceiptStore, TelemetryJevReceiptSink

__all__ = [
    "ExplorationCandidate",
    "InMemoryJevReceiptStore",
    "JevAdvisoryPolicy",
    "JevAdvisoryVerificationGate",
    "JevAnswer",
    "JevArchitectureAdvice",
    "JevArchitectureRouter",
    "JevArchitectureRoutingPolicy",
    "JevChoiceAnswer",
    "JevChoiceQuestion",
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
    "TelemetryJevReceiptSink",
    "TypeSafeJevDecisionEvaluator",
]
