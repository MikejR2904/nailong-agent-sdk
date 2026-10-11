# Copyright (c) 2026 David Michael Indraputra

"""Source-preserving structural context closure and bounded evidence packing.

Selection is a pure operation over an immutable typed evidence graph.  It first
retains a policy-defined mandatory reachability closure, then uses residual
budget only for optional source-preserving evidence.  The implementation does
not create relationships from model output or semantic similarity.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict, deque
from enum import StrEnum
from typing import Any

from pydantic import Field, model_validator

from ..foundations.contracts import StrictModel
from ..foundations.hashing import sha256_hex, strict_canonical_json
from ..foundations.identifiers import require_unique
from ..foundations.optimization import ExactPckpSolver, PckpItem, PckpProblem, PckpStatus
from .documents import SourceRef


class EvidenceNodeKind(StrEnum):
    REQUIREMENT = "requirement"
    INTERFACE = "interface"
    SIGNAL = "signal"
    ACCEPTANCE = "acceptance"
    VERIFICATION = "verification"
    DEPENDENCY = "dependency"
    SOURCE_SPAN = "source-span"
    DECISION = "decision"


class EvidenceRelationKind(StrEnum):
    REQUIRES = "requires"
    GOVERNS = "governs"
    INTERFACE_CONSTRAINT = "interface-constraint"
    ACCEPTANCE_CRITERION = "acceptance-criterion"
    VERIFIED_BY = "verified-by"
    DECLARED_DEPENDENCY = "declared-dependency"
    ASSERTED_BY = "asserted-by"
    ADVISORY = "advisory"


class EvidenceNode(StrictModel):
    """One exact evidence unit with a source locator and deterministic cost."""

    node_id: str = Field(min_length=1)
    kind: EvidenceNodeKind
    content: Any
    source: SourceRef
    token_cost: int = Field(ge=1)
    authority_tier: int = Field(default=0, ge=0, le=100)
    aliases: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def aliases_are_unique(self) -> EvidenceNode:
        require_unique(self.aliases, f'Evidence node "{self.node_id}" aliases')
        return self


class EvidenceRelation(StrictModel):
    """A source-backed directed relation: including ``from`` requires ``to``."""

    from_node_id: str = Field(min_length=1)
    to_node_id: str = Field(min_length=1)
    kind: EvidenceRelationKind
    source: SourceRef

    @model_validator(mode="after")
    def endpoints_are_distinct(self) -> EvidenceRelation:
        if self.from_node_id == self.to_node_id:
            raise ValueError(
                f'Evidence relation endpoints must be distinct; both are "{self.from_node_id}"'
            )
        return self


class EvidenceGraph(StrictModel):
    """A frozen typed graph whose IDs and source hashes are part of its identity."""

    snapshot_id: str = Field(min_length=1)
    nodes: list[EvidenceNode] = Field(min_length=1)
    relations: list[EvidenceRelation] = Field(default_factory=list)

    @model_validator(mode="after")
    def graph_is_well_formed(self) -> EvidenceGraph:
        node_ids = [node.node_id for node in self.nodes]
        require_unique(node_ids, "Evidence node IDs")
        known = set(node_ids)
        for relation in self.relations:
            unknown = sorted({relation.from_node_id, relation.to_node_id} - known)
            if unknown:
                raise ValueError(
                    f"Evidence relations must reference known nodes; unknown: {unknown}"
                )
        return self

    @property
    def content_hash(self) -> str:
        return sha256_hex(strict_canonical_json(self.model_dump(mode="json")))


class EvidenceSelectionPolicy(StrictModel):
    """Versioned traversal and static additive scoring policy."""

    policy_id: str = Field(min_length=1)
    mandatory_relation_kinds: list[EvidenceRelationKind] = Field(
        default_factory=lambda: [
            EvidenceRelationKind.REQUIRES,
            EvidenceRelationKind.GOVERNS,
            EvidenceRelationKind.INTERFACE_CONSTRAINT,
            EvidenceRelationKind.ACCEPTANCE_CRITERION,
            EvidenceRelationKind.VERIFIED_BY,
            EvidenceRelationKind.DECLARED_DEPENDENCY,
            EvidenceRelationKind.ASSERTED_BY,
        ]
    )
    node_kind_utility: dict[EvidenceNodeKind, int] = Field(
        default_factory=lambda: {
            EvidenceNodeKind.REQUIREMENT: 50,
            EvidenceNodeKind.INTERFACE: 45,
            EvidenceNodeKind.SIGNAL: 40,
            EvidenceNodeKind.ACCEPTANCE: 45,
            EvidenceNodeKind.VERIFICATION: 40,
            EvidenceNodeKind.DEPENDENCY: 35,
            EvidenceNodeKind.SOURCE_SPAN: 30,
            EvidenceNodeKind.DECISION: 25,
        }
    )
    exact_target_bonus: int = Field(default=100, ge=0)
    authority_multiplier: int = Field(default=1, ge=0)
    lexical_overlap_weight: int = Field(default=5, ge=0)

    @model_validator(mode="after")
    def relation_kinds_are_unique(self) -> EvidenceSelectionPolicy:
        require_unique(self.mandatory_relation_kinds, "Mandatory relation kinds")
        return self

    @property
    def policy_hash(self) -> str:
        return sha256_hex(self.model_dump_json(by_alias=False, exclude_none=False))


class EvidenceSelectionRequest(StrictModel):
    snapshot_id: str = Field(min_length=1)
    target_ids: list[str] = Field(min_length=1)
    token_budget: int = Field(ge=0)
    task_text: str = ""
    strict: bool = True

    @model_validator(mode="after")
    def target_ids_are_unique(self) -> EvidenceSelectionRequest:
        require_unique(self.target_ids, "Evidence selection target IDs")
        return self


class ClosureWitness(StrictModel):
    node_id: str = Field(min_length=1)
    root_target_id: str = Field(min_length=1)
    relation_path: list[EvidenceRelation] = Field(default_factory=list)


class EvidenceOmission(StrictModel):
    node_id: str = Field(min_length=1)
    reason: str = Field(min_length=1)


class EvidenceSelectionStatus(StrEnum):
    SELECTED = "selected"
    INFEASIBLE_REQUIRED_CLOSURE = "infeasible-required-closure"
    BEST_EFFORT = "best-effort"


class EvidenceSelectionResult(StrictModel):
    status: EvidenceSelectionStatus
    graph_hash: str = Field(min_length=1)
    policy_hash: str = Field(min_length=1)
    selected_nodes: list[EvidenceNode] = Field(default_factory=list)
    mandatory_node_ids: list[str] = Field(default_factory=list)
    selected_optional_node_ids: list[str] = Field(default_factory=list)
    token_cost: int = Field(ge=0)
    witnesses: list[ClosureWitness] = Field(default_factory=list)
    omissions: list[EvidenceOmission] = Field(default_factory=list)
    diagnostics: list[str] = Field(default_factory=list)
    solver: dict[str, Any] = Field(default_factory=dict)


class StructuralContextSelector:
    """Compute required graph closure before exact optional evidence packing."""

    def select(
        self,
        graph: EvidenceGraph,
        request: EvidenceSelectionRequest,
        policy: EvidenceSelectionPolicy,
    ) -> EvidenceSelectionResult:
        if graph.snapshot_id != request.snapshot_id:
            raise ValueError("Evidence selection request references an unexpected graph snapshot.")
        nodes = {node.node_id: node for node in graph.nodes}
        target_ids, target_diagnostics = self._resolve_targets(
            nodes, request.target_ids, strict=request.strict
        )
        mandatory, witnesses = self._mandatory_closure(graph, target_ids, policy)
        mandatory_cost = sum(nodes[node_id].token_cost for node_id in mandatory)
        graph_hash = graph.content_hash
        if mandatory_cost > request.token_budget:
            omissions = [
                EvidenceOmission(node_id=node_id, reason="mandatory-closure-exceeds-budget")
                for node_id in sorted(mandatory)
            ]
            return EvidenceSelectionResult(
                status=EvidenceSelectionStatus.INFEASIBLE_REQUIRED_CLOSURE,
                graph_hash=graph_hash,
                policy_hash=policy.policy_hash,
                mandatory_node_ids=sorted(mandatory),
                token_cost=mandatory_cost,
                witnesses=witnesses,
                omissions=omissions,
                diagnostics=[
                    *target_diagnostics,
                    "Mandatory evidence closure exceeds the requested token budget.",
                ],
            )

        task_terms = _terms(request.task_text)
        optional_ids = sorted(set(nodes) - mandatory)
        problem = PckpProblem(
            token_budget=request.token_budget - mandatory_cost,
            items=[
                PckpItem(
                    item_id=node_id,
                    token_cost=nodes[node_id].token_cost,
                    utility=self._optional_utility(nodes[node_id], target_ids, task_terms, policy),
                )
                for node_id in optional_ids
            ]
            or [PckpItem(item_id="__empty__", token_cost=0, utility=0)],
        )
        solution = ExactPckpSolver().solve(problem)
        selected_optional = set(solution.selected_item_ids) - {"__empty__"}
        selected_ids = mandatory | selected_optional
        omissions = [
            EvidenceOmission(node_id=node_id, reason="optional-not-selected-under-budget")
            for node_id in optional_ids
            if node_id not in selected_optional
        ]
        status = (
            EvidenceSelectionStatus.SELECTED
            if solution.status is PckpStatus.OPTIMAL
            else EvidenceSelectionStatus.BEST_EFFORT
        )
        return EvidenceSelectionResult(
            status=status,
            graph_hash=graph_hash,
            policy_hash=policy.policy_hash,
            selected_nodes=[nodes[node_id] for node_id in sorted(selected_ids)],
            mandatory_node_ids=sorted(mandatory),
            selected_optional_node_ids=sorted(selected_optional),
            token_cost=sum(nodes[node_id].token_cost for node_id in selected_ids),
            witnesses=witnesses,
            omissions=omissions,
            diagnostics=target_diagnostics,
            solver=solution.model_dump(mode="json"),
        )

    @staticmethod
    def _resolve_targets(
        nodes: dict[str, EvidenceNode], requested: list[str], *, strict: bool = True
    ) -> tuple[list[str], list[str]]:
        aliases: dict[str, list[str]] = defaultdict(list)
        for node in nodes.values():
            for alias in node.aliases:
                aliases[alias].append(node.node_id)
        resolved: list[str] = []
        diagnostics: list[str] = []
        for target in requested:
            if target in nodes:
                resolved.append(target)
                continue
            candidates = sorted(aliases.get(target, []))
            if len(candidates) == 1:
                resolved.append(candidates[0])
                diagnostics.append(f'Approved alias "{target}" resolved to "{candidates[0]}".')
                continue
            problem = (
                f'Evidence target "{target}" is unknown.'
                if not candidates
                else f'Evidence target alias "{target}" is ambiguous: {candidates}'
            )
            if strict:
                raise ValueError(problem)
            diagnostics.append(f"{problem[:-1] if problem.endswith('.') else problem} (ignored).")
        if not resolved:
            raise ValueError(
                f"Evidence selection has no resolvable target among {requested}; "
                "none exists in the graph."
            )
        return sorted(set(resolved)), diagnostics

    @staticmethod
    def _mandatory_closure(
        graph: EvidenceGraph,
        targets: list[str],
        policy: EvidenceSelectionPolicy,
    ) -> tuple[set[str], list[ClosureWitness]]:
        mandatory_kinds = set(policy.mandatory_relation_kinds)
        by_from: dict[str, list[EvidenceRelation]] = defaultdict(list)
        for relation in graph.relations:
            if relation.kind in mandatory_kinds:
                by_from[relation.from_node_id].append(relation)
        for relations in by_from.values():
            relations.sort(key=lambda value: (value.to_node_id, value.kind.value))
        retained = set(targets)
        witness_paths: dict[str, ClosureWitness] = {
            target: ClosureWitness(node_id=target, root_target_id=target) for target in targets
        }
        pending: deque[str] = deque(sorted(targets))
        while pending:
            current = pending.popleft()
            current_witness = witness_paths[current]
            for relation in by_from.get(current, []):
                target = relation.to_node_id
                if target in retained:
                    continue
                retained.add(target)
                witness_paths[target] = ClosureWitness(
                    node_id=target,
                    root_target_id=current_witness.root_target_id,
                    relation_path=[*current_witness.relation_path, relation],
                )
                pending.append(target)
        return retained, [witness_paths[node_id] for node_id in sorted(witness_paths)]

    @staticmethod
    def _optional_utility(
        node: EvidenceNode,
        target_ids: list[str],
        task_terms: set[str],
        policy: EvidenceSelectionPolicy,
    ) -> int:
        content_terms = _terms(json.dumps(node.content, sort_keys=True, default=str))
        overlap = len(task_terms & content_terms)
        return (
            policy.node_kind_utility.get(node.kind, 0)
            + node.authority_tier * policy.authority_multiplier
            + overlap * policy.lexical_overlap_weight
            + (policy.exact_target_bonus if node.node_id in target_ids else 0)
        )


def _terms(value: str) -> set[str]:
    return {term for term in re.findall(r"[a-z0-9_]+", value.lower()) if len(term) > 1}
