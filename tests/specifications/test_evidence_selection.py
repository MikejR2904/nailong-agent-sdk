import random
import time

import pytest

from nailong_agent_sdk.specifications.evidence_graph import (
    EvidenceGraph,
    EvidenceNode,
    EvidenceNodeKind,
    EvidenceRelation,
    EvidenceRelationKind,
    EvidenceSelectionPolicy,
    EvidenceSelectionRequest,
    EvidenceSelectionStatus,
    StructuralContextSelector,
)
from tests.support.specs import sref


def _egraph(node_specs, relations=()):
    nodes = [
        EvidenceNode(
            node_id=node_id,
            kind=kind,
            content=content,
            source=sref(loc=f"line:{node_id}"),
            token_cost=cost,
            authority_tier=authority,
            aliases=aliases,
        )
        for node_id, kind, content, cost, authority, aliases in node_specs
    ]
    rels = [
        EvidenceRelation(from_node_id=a, to_node_id=b, kind=kind, source=sref(loc=f"{a}->{b}"))
        for a, b, kind in relations
    ]
    return EvidenceGraph(snapshot_id="snap", nodes=nodes, relations=rels)


def test_evidence_closure_aliases_budget_and_witnesses():
    K = EvidenceNodeKind
    graph = _egraph(
        [
            ("T", K.REQUIREMENT, "target", 10, 0, ["alias-t"]),
            ("A", K.INTERFACE, "iface", 10, 0, []),
            ("B", K.SIGNAL, "sig", 10, 0, []),
            ("C", K.DECISION, "optional note about target", 5, 0, []),
            ("D", K.SOURCE_SPAN, "advisory only", 5, 0, []),
        ],
        [
            ("T", "A", EvidenceRelationKind.REQUIRES),
            ("A", "B", EvidenceRelationKind.INTERFACE_CONSTRAINT),
            ("T", "D", EvidenceRelationKind.ADVISORY),
        ],
    )
    selector = StructuralContextSelector()
    policy = EvidenceSelectionPolicy(policy_id="p")
    result = selector.select(
        graph,
        EvidenceSelectionRequest(snapshot_id="snap", target_ids=["alias-t"], token_budget=100),
        policy,
    )
    assert result.status is EvidenceSelectionStatus.SELECTED
    assert result.mandatory_node_ids == ["A", "B", "T"]
    assert {n.node_id for n in result.selected_nodes} == {"T", "A", "B", "C", "D"}
    witness = next(w for w in result.witnesses if w.node_id == "B")
    assert [r.to_node_id for r in witness.relation_path] == [
        "A",
        "B",
    ] and witness.root_target_id == "T"
    tight = selector.select(
        graph,
        EvidenceSelectionRequest(snapshot_id="snap", target_ids=["T"], token_budget=29),
        policy,
    )
    assert (
        tight.status is EvidenceSelectionStatus.INFEASIBLE_REQUIRED_CLOSURE
        and tight.selected_nodes == []
    )
    with pytest.raises(ValueError, match='"nope" is unknown'):
        selector.select(
            graph,
            EvidenceSelectionRequest(snapshot_id="snap", target_ids=["nope"], token_budget=100),
            policy,
        )
    with pytest.raises(ValueError, match="unexpected graph snapshot"):
        selector.select(
            graph,
            EvidenceSelectionRequest(snapshot_id="other", target_ids=["T"], token_budget=100),
            policy,
        )
    ambiguous = _egraph(
        [("X", K.REQUIREMENT, "x", 1, 0, ["dup"]), ("Y", K.REQUIREMENT, "y", 1, 0, ["dup"])]
    )
    with pytest.raises(ValueError, match="ambiguous"):
        selector.select(
            ambiguous,
            EvidenceSelectionRequest(snapshot_id="snap", target_ids=["dup"], token_budget=10),
            policy,
        )


def test_evidence_strict_flag_has_an_effect():
    K = EvidenceNodeKind
    graph = _egraph([("T", K.REQUIREMENT, "t", 1, 0, [])])
    selector = StructuralContextSelector()
    policy = EvidenceSelectionPolicy(policy_id="p")
    try:
        selector.select(
            graph,
            EvidenceSelectionRequest(
                snapshot_id="snap", target_ids=["T", "ghost"], token_budget=10, strict=False
            ),
            policy,
        )
        outcome = "tolerated"
    except ValueError as error:
        outcome = f"ValueError: {error}"
    assert outcome == "tolerated", outcome
    result = selector.select(
        graph,
        EvidenceSelectionRequest(
            snapshot_id="snap", target_ids=["T", "ghost"], token_budget=10, strict=False
        ),
        policy,
    )
    assert 'Evidence target "ghost" is unknown (ignored).' in result.diagnostics
    assert [node.node_id for node in result.selected_nodes] == ["T"]
    with pytest.raises(ValueError, match='Evidence target "ghost" is unknown'):
        selector.select(
            graph,
            EvidenceSelectionRequest(
                snapshot_id="snap", target_ids=["T", "ghost"], token_budget=10
            ),
            policy,
        )
    with pytest.raises(ValueError, match="no resolvable target"):
        selector.select(
            graph,
            EvidenceSelectionRequest(
                snapshot_id="snap", target_ids=["ghost"], token_budget=10, strict=False
            ),
            policy,
        )


def test_evidence_optional_packing_is_optimal_against_brute_force():
    K = EvidenceNodeKind
    rng = random.Random(11)
    for trial in range(40):
        count = rng.randint(1, 9)
        specs = [("T", K.REQUIREMENT, "target text", 3, 0, [])]
        for i in range(count):
            specs.append(
                (
                    f"O{i}",
                    rng.choice(list(K)),
                    "word " * rng.randint(0, 3),
                    rng.randint(1, 9),
                    rng.randint(0, 5),
                    [],
                )
            )
        graph = _egraph(specs)
        budget = rng.randint(3, 40)
        policy = EvidenceSelectionPolicy(policy_id="p")
        result = StructuralContextSelector().select(
            graph,
            EvidenceSelectionRequest(
                snapshot_id="snap", target_ids=["T"], token_budget=budget, task_text="word target"
            ),
            policy,
        )
        nodes = {n.node_id: n for n in graph.nodes}
        task_terms = {"word", "target"}
        utilities = {}
        for node_id, n in nodes.items():
            if node_id == "T":
                continue
            content_terms = {t for t in str(n.content).lower().split() if len(t) > 1}
            utilities[node_id] = (
                policy.node_kind_utility[n.kind]
                + n.authority_tier * policy.authority_multiplier
                + len(task_terms & content_terms) * policy.lexical_overlap_weight
            )
        remaining = budget - 3
        best = 0
        ids = list(utilities)
        for mask in range(1 << len(ids)):
            cost = sum(nodes[ids[i]].token_cost for i in range(len(ids)) if mask >> i & 1)
            if cost <= remaining:
                best = max(best, sum(utilities[ids[i]] for i in range(len(ids)) if mask >> i & 1))
        got = sum(utilities[i] for i in result.selected_optional_node_ids)
        assert got == best, (trial, got, best)
        assert result.token_cost <= budget


def test_evidence_large_budget_behaviour():
    K = EvidenceNodeKind
    rng = random.Random(5)
    specs = [("T", K.REQUIREMENT, "target", 10, 0, [])]
    for i in range(300):
        specs.append(
            (
                f"O{i}",
                rng.choice(list(K)),
                f"word{i % 7}",
                rng.randint(50, 500),
                rng.randint(0, 20),
                [],
            )
        )
    graph = _egraph(specs)
    policy = EvidenceSelectionPolicy(policy_id="p")
    report = {}
    for budget in (20_000, 50_000, 100_000):
        started = time.monotonic()
        result = StructuralContextSelector().select(
            graph,
            EvidenceSelectionRequest(
                snapshot_id="snap", target_ids=["T"], token_budget=budget, task_text="word1"
            ),
            policy,
        )
        report[budget] = {
            "seconds": round(time.monotonic() - started, 2),
            "status": result.status.value,
            "cost": result.token_cost,
        }
    assert all(entry["status"] == "selected" for entry in report.values()), report
    assert max(entry["seconds"] for entry in report.values()) < 15, report
