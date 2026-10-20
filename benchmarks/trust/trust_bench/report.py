# Copyright (c) 2026 David Michael Indraputra

"""Render results as a markdown report."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from . import aggregate as agg
from . import criteria
from . import verify_tables as vt
from .authority import tables as at
from .gates import GateResult
from .tamper_jobs import tamper_table


def _row(*cells: object) -> str:
    return "| " + " | ".join(str(cell) for cell in cells) + " |"


def _table(header: Sequence[str], rows: Sequence[Sequence[object]]) -> list[str]:
    return [_row(*header), _row(*("---" for _ in header)), *(_row(*row) for row in rows), ""]


def _interval(triple: Sequence[float]) -> str:
    return f"{triple[0]:.3f} [{triple[1]:.3f}, {triple[2]:.3f}]"


def _split(outcomes: Mapping[str, Any]) -> str:
    return "/".join(f"{outcomes[name].estimate:.2f}" for name in ("correct", "wrong", "abstain"))


def _gates_section(gates: Sequence[GateResult]) -> list[str]:
    rows = [(g.name, g.source, "PASS" if g.passed else "FAIL") for g in gates]
    return ["## Reproduction gates", "", *_table(("gate", "source", "result"), rows)]


def _card_section(card: Mapping[str, Sequence[criteria.Verdict]]) -> list[str]:
    lines = ["## Trust Profile card (pre-registered criteria)", ""]
    for arm, verdicts in card.items():
        body = [
            [
                v.criterion.id,
                v.criterion.claim,
                v.estimate,
                v.criterion.rule,
                v.criterion.provenance,
                v.label + (f" ({v.detail})" if v.detail else ""),
            ]
            for v in verdicts
        ]
        header = ("id", "claim", "estimate", "pass rule", "source of the threshold", "verdict")
        lines += [f"### {arm}", "", *_table(header, body)]
    return lines


def _event_rows(trace: Mapping[str, Any]) -> list[list[object]]:
    return [
        [
            kind,
            trace[kind]["completeness"].text(),
            trace[kind]["agreement"].text(),
            trace[kind]["recoverability"].text(),
            trace[kind]["phantom"],
        ]
        for kind in agg.KINDS
    ]


def _traceability_section(family: str, rows: Sequence[Mapping[str, Any]]) -> list[str]:
    trace = agg.traceability(rows)
    lines = [f"### {family}: traceability on {trace['runs']} clean runs", ""]
    if trace["runs"] == 0:
        return [*lines, "No clean completed runs.", ""]
    resolution = trace["resolution"]
    lines += [
        f"- runs with every model call and tool call recorded: {trace['calls_complete'].text()}",
        "",
        *_table(
            ("event", "completeness", "content agreement", "recoverable", "phantom"),
            _event_rows(trace),
        ),
        f"- artifacts resolved to the right decision and actor (DARR): {resolution['darr'].text()}",
        f"- cluster-bootstrap interval for DARR: {_interval(resolution['darr_cluster'])}",
        f"- false resolutions: {resolution['false_resolution'].text()}",
        f"- abstentions: {resolution['abstained'].text()}",
        f"- source precision {resolution['source_precision']:.3f}, "
        f"recall {resolution['source_recall']:.3f} (exploratory)",
        "",
    ]
    degradation = agg.degradation_table(rows)
    if degradation:
        header = ("condition", "strict correct/wrong/abstain", "lenient correct/wrong/abstain")
        body = [
            (name, _split(d["strict"]), _split(d["lenient"])) for name, d in degradation.items()
        ]
        lines += _table(header, body)
    return lines


def _failure_section(family: str, rows: Sequence[Mapping[str, Any]]) -> list[str]:
    failed = agg.failure_completeness(rows)
    if failed["runs"] == 0:
        return []
    body = [
        (kind, failed[kind]["completeness"].text(), failed[kind]["phantom"]) for kind in agg.KINDS
    ]
    return [
        f"### {family}: records of {failed['runs']} runs that ended by exhausting a limit",
        "",
        f"- runs with every model call and tool call recorded: {failed['calls_complete'].text()}",
        "",
        *_table(("event", "completeness", "phantom"), body),
    ]


def _diagnosis_section(arm: str, rows: Sequence[Mapping[str, Any]]) -> list[str]:
    diag = agg.diagnosis(rows)
    if not diag["cases"]:
        return []
    header = (
        "method",
        "top-1",
        "top-3",
        "MRR",
        "macro top-1 (F1-F3)",
        "unsupported on F8",
        "abstained on truth",
    )
    body = []
    for method, e in diag["methods"].items():
        macro = e.get("macro_top1")
        body.append(
            [
                method,
                e["top1"].text(),
                e["top3"].text(),
                f"{e['mrr']:.3f}",
                "-" if macro is None else _interval(macro),
                e["unsupported"].text(),
                e["abstain_on_truth"].text(),
            ]
        )
    by_class = [
        [
            method,
            *(e["by_class"][c].text() if c in e["by_class"] else "-" for c in agg.TRUTH_CLASSES),
        ]
        for method, e in diag["methods"].items()
    ]
    return [
        f"### {arm}: diagnosis on {diag['cases']} faulty runs with a symptom",
        "",
        *_table(header, body),
        "By fault class (top-1):",
        "",
        *_table(("method", *agg.TRUTH_CLASSES), by_class),
    ]


def _twin_section(arm: str, rows: Sequence[Mapping[str, Any]]) -> list[str]:
    twins = agg.twin_table(rows)
    if not twins["cases"]:
        return []
    body = [
        [
            method,
            entry["unsupported"].text(),
            entry["abstained"].text(),
            entry["named_truth"].text(),
        ]
        for method, entry in twins["methods"].items()
    ]
    return [
        f"### {arm}: {twins['cases']} insufficient-evidence twins (generic log)",
        "",
        "The decisive step and one look-alike are hidden; naming a unique step is unsupported.",
        "",
        *_table(
            ("method", "named a unique step (unsupported)", "abstained", "named the true step"),
            body,
        ),
    ]


def _strata_section(arm: str, rows: Sequence[Mapping[str, Any]]) -> list[str]:
    method = criteria.SHIPPED_METHOD.get(arm, "ledger-scan+oracle")
    table = agg.stratum_table(rows, method)
    resolution = agg.resolution_strata(rows)
    if not table["strata"] and not resolution:
        return []
    body = []
    for name, value in table["strata"].items():
        diff = table["differences"].get(name)
        shown = "-" if diff is None else f"{diff[0]:+.3f} [{diff[1]:+.3f}, {diff[2]:+.3f}]"
        body.append([name, value.text(), shown])
    lines = [f"### {arm}: held-out strata (top-1 of `{method}`)", ""]
    if body:
        lines += _table(("stratum", "top-1", "difference to development-like"), body)
    if resolution:
        lines += _table(
            ("stratum", "chain resolution"), [[n, v.text()] for n, v in resolution.items()]
        )
    return lines


def _comparison_section(results: Sequence[Mapping[str, Any]]) -> list[str]:
    arms = {r["arm"] for r in results}
    if not {"sdk", "langgraph"} <= arms:
        return []
    comparison = agg.compare_arms(results, "sdk", "langgraph")
    body = [
        [
            name,
            t["both"],
            t["only_first"],
            t["only_second"],
            t["neither"],
            f"{t['p_exact']:.4f}",
            f"{t['p_holm']:.4f}",
        ]
        for name, t in comparison["tests"].items()
    ]
    header = (
        "paired outcome",
        "both",
        "sdk only",
        "langgraph only",
        "neither",
        "exact p",
        "Holm p",
    )
    return [
        f"## Paired comparison, sdk against langgraph ({comparison['pairs']} matched runs)",
        "",
        *_table(header, body),
    ]


def _tamper_section(tamper: Sequence[Mapping[str, Any]]) -> list[str]:
    lines = ["## Tamper detection (native verification)", ""]
    for arm, rows in agg.split_by(tamper, "arm").items():
        table = tamper_table(rows)
        cells = [(name, value.text()) for name, value in table["cells"].items()]
        lines += [f"### {arm}", "", *_table(("store/class", "detected"), cells)]
        lines += [f"- false alarms on untampered copies: {table['false_alarms'].text()}", ""]
    return lines


def _verifiability_section(
    verify: Sequence[Mapping[str, Any]], attacks: Sequence[Mapping[str, Any]]
) -> list[str]:
    lines = ["## Verifiability (acceptance of defective deliverables)", ""]
    attacks_by_arm = agg.split_by(attacks, "arm")
    for arm, rows in agg.split_by(verify, "arm").items():
        table = vt.verification(rows)
        counts = ", ".join(f"q{level}: {table['levels'][level]}" for level in vt.LEVELS)
        lines += [
            f"### {arm}: {table['defective']} defective and {table['clean']} correct deliverables",
            "",
            f"Defects by quietness: {counts}. An escape is a defective deliverable accepted.",
            "",
        ]
        body = [
            [
                name,
                *(entry["escape"][level].text() for level in vt.LEVELS),
                entry["false_rejection"].text(),
            ]
            for name, entry in table["mechanisms"].items()
        ]
        header = ("acceptance by", "q0 escapes", "q1 escapes", "q2 escapes", "correct rejected")
        lines += _table(header, body)
        lines += [
            f"- gate decision found in the system's own records: {table['recorded'].text()}",
            f"- of those, bound to the digest of the checked bytes: {table['binds'].text()}",
            f"- runs whose bytes or recorder disagree (a harness fault): {table['inconsistent']}",
            "",
        ]
        cells = vt.attack_table(attacks_by_arm.get(arm, []))
        if cells:
            lines += _table(
                ("gate/attack", "attacks that got a defective deliverable accepted"),
                [(name, value.text()) for name, value in cells.items()],
            )
    return lines


def _authority_section(rows: Sequence[Mapping[str, Any]]) -> list[str]:
    lines = ["## Authority (who decided, and what the records say)", ""]
    header = (
        "allocation",
        "decision points",
        "executed",
        "violations",
        "over-escalation",
        "paired accuracy",
        "records named and correct",
        "recomputable",
    )
    by_arm = agg.split_by(rows, "arm")
    for arm, arm_rows in by_arm.items():
        groups = at.by_allocation(arm_rows)
        body = [
            [
                name,
                group["tickets"],
                group["executed"],
                group["violations"].text(),
                group["over_escalation"].text(),
                group["pairs"].text(),
                group["correct"].text(),
                group["recomputable"].text(),
            ]
            for name, group in groups.items()
        ]
        faults = sum(group["harness_faults"] for group in groups.values())
        lines += [f"### {arm}", "", *_table(header, body)]
        lines += [
            f"- runs whose decision log disagrees with the allocation (a harness fault): {faults}",
            "",
        ]
    first = by_arm.get("sdk") or next(iter(by_arm.values()), [])
    grid_rows = []
    for name, group_rows in agg.split_by(first, "allocation").items():
        for cell in at.reviewer_grid(group_rows):
            grid_rows.append(
                [
                    name,
                    cell["sensitivity"],
                    cell["specificity"],
                    f"{cell['bad_executed']:.3f}",
                    f"{cell['good_rejected']:.3f}",
                    f"{cell['human_load']:.3f}",
                ]
            )
    if grid_rows:
        lines += [
            "A simulated human reviewer swept over sensitivity and specificity; the other "
            "classes are taken to approve everything, so these are expected shares of all "
            "decision points, not measurements.",
            "",
            *_table(
                (
                    "allocation",
                    "sensitivity",
                    "specificity",
                    "bad actions executed",
                    "good actions rejected",
                    "sent to a human",
                ),
                grid_rows,
            ),
        ]
    return lines


def _chain_section(rows: Sequence[Mapping[str, Any]]) -> list[str]:
    lines = [
        "## Approval scope (a benign write and then a destructive delete in one run)",
        "",
        "Both actions use the same capability. The first is one a deterministic check satisfies, "
        "the second needs a human. A system whose approval covers a capability for the rest of "
        "the run lets the second ride on the first.",
        "",
    ]
    body = []
    for arm, arm_rows in agg.split_by(rows, "arm").items():
        summary = at.chain(arm_rows)
        body.append(
            [
                arm,
                summary["runs"],
                f"{summary['asked']:.2f}",
                summary["executed"],
                summary["violations"].text(),
            ]
        )
    header = (
        "system",
        "runs",
        "approvals asked per run",
        "destructive actions executed",
        "executed without a sufficient approval",
    )
    return [*lines, *_table(header, body)]


def render(
    title: str,
    results: Sequence[Mapping[str, Any]],
    gates: Sequence[GateResult] = (),
    tamper: Sequence[Mapping[str, Any]] = (),
    header: Sequence[str] = (),
    verify: Sequence[Mapping[str, Any]] = (),
    attacks: Sequence[Mapping[str, Any]] = (),
    authority: Sequence[Mapping[str, Any]] = (),
    chains: Sequence[Mapping[str, Any]] = (),
) -> str:
    lines = [f"# {title}", "", *header, ""]
    if gates:
        lines += _gates_section(gates)
    if results or verify or attacks or authority:
        lines += _card_section(criteria.card(results, tamper, verify, attacks, authority))
    for arm, arm_rows in agg.split_by(results, "arm").items():
        lines += [f"## Arm: {arm}", ""]
        for family, rows in agg.split_by(arm_rows, "family").items():
            lines += _traceability_section(family, rows)
            lines += _failure_section(family, rows)
        lines += _diagnosis_section(arm, arm_rows)
        lines += _twin_section(arm, arm_rows)
        lines += _strata_section(arm, arm_rows)
    lines += _comparison_section(results)
    if tamper:
        lines += _tamper_section(tamper)
    if verify:
        lines += _verifiability_section(verify, attacks)
    if authority:
        lines += _authority_section(authority)
    if chains:
        lines += _chain_section(chains)
    return "\n".join(lines) + "\n"
