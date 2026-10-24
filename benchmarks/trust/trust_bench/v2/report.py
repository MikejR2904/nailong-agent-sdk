# Copyright (c) 2026 David Michael Indraputra

"""Render the v2 report: three tiers apart, every criterion as claim, estimate, rule and verdict."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from ..stats import exact_lower, exact_upper, wilson, zero_event_cases

PASS, FAIL, NOT_RUN = "PASS", "FAIL", "NOT RUN"
ZERO_EVENT_TRIALS = zero_event_cases(0.02)
CELL_FLOOR = 0.98


def _fmt(value: float) -> str:
    return f"{value:.4f}"


def zero_event(events: int, trials: int) -> tuple[str, str]:
    """Verdict and estimate text of a criterion that must see no event."""

    if trials == 0:
        return NOT_RUN, "no trials"
    upper = exact_upper(events, trials)
    estimate = f"{events}/{trials}; exact upper {_fmt(upper)}"
    if events:
        return FAIL, estimate
    if trials < ZERO_EVENT_TRIALS:
        return NOT_RUN, f"{estimate}; fewer than the {ZERO_EVENT_TRIALS} trials a 0.02 bound needs"
    return (PASS if upper <= 0.02 else FAIL), estimate


def detection(detected: int, trials: int) -> tuple[str, str]:
    if trials == 0:
        return NOT_RUN, "no trials"
    lower = exact_lower(detected, trials)
    estimate = f"{detected}/{trials}; exact lower {_fmt(lower)}"
    if detected < trials:
        return FAIL, estimate
    if lower < CELL_FLOOR:
        return NOT_RUN, f"{estimate}; a 0.98 lower bound needs 183 trials with no miss"
    return PASS, estimate


def table(rows: list[tuple[str, ...]], header: tuple[str, ...]) -> list[str]:
    lines = ["| " + " | ".join(header) + " |", "|" + "|".join("---" for _ in header) + "|"]
    lines.extend("| " + " | ".join(row) + " |" for row in rows)
    return lines


def tamper_section(rows: list[dict[str, Any]]) -> tuple[list[str], list[tuple[str, ...]]]:
    if not rows:
        return ["Not run."], [("V2.1a", "anchored tamper detection", "-", NOT_RUN)]
    cells: dict[tuple[str, str, str], list[int]] = defaultdict(lambda: [0, 0])
    alarms: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for row in rows:
        arm = row["arm"]
        alarms[arm][1] += max(1, row.get("pristine_checks", 1))
        alarms[arm][0] += len(row.get("false_alarms", []))
        for store, klass, _position, found in row["trials"]:
            cell = cells[(arm, store, klass)]
            cell[1] += 1
            cell[0] += bool(found)
    out: list[str] = []
    cards: list[tuple[str, ...]] = []
    anchored = {key: value for key, value in cells.items() if key[0] == "sdk+anchor"}
    failing = [
        f"{key[1]}/{key[2]} {value[0]}/{value[1]}"
        for key, value in sorted(anchored.items())
        if detection(*value)[0] == FAIL
    ]
    underpowered = [
        f"{key[1]}/{key[2]} {value[0]}/{value[1]}"
        for key, value in sorted(anchored.items())
        if detection(*value)[0] == NOT_RUN
    ]
    if not anchored:
        verdict = NOT_RUN
    elif failing:
        verdict = FAIL
    else:
        verdict = NOT_RUN if underpowered else PASS
    cards.append(
        (
            "V2.1a",
            "with the anchor every class on every store is detected",
            f"{len(anchored) - len(failing) - len(underpowered)} of {len(anchored)} cells pass"
            + (f"; missed: {', '.join(failing)}" if failing else "")
            + (f"; too few trials for 0.98: {', '.join(underpowered)}" if underpowered else ""),
            verdict,
        )
    )
    total_events = sum(value[0] for key, value in alarms.items() if key in ("sdk", "sdk+anchor"))
    total_trials = sum(value[1] for key, value in alarms.items() if key in ("sdk", "sdk+anchor"))
    state, estimate = zero_event(total_events, total_trials)
    cards.append(("V2.1b", "an untouched run raises no alarm", estimate, state))
    for arm in sorted({key[0] for key in cells}):
        out.append(f"### Detection by cell, arm `{arm}`")
        out.append("")
        body = [
            (
                f"{store}/{klass}",
                f"{value[0]}/{value[1]}",
                _fmt(exact_lower(value[0], value[1])) if value[0] else "0.0000",
            )
            for (cell_arm, store, klass), value in sorted(cells.items())
            if cell_arm == arm
        ]
        out.extend(table(body, ("cell", "detected", "exact lower")))
        out.append("")
    plain = {key: value for key, value in cells.items() if key[0] == "sdk"}
    undetected = sorted(f"{key[1]}/{key[2]}" for key, value in plain.items() if value[0] < value[1])
    held = any(item.startswith("artifacts/") for item in undetected)
    cards.append(
        (
            "V2.1c",
            "(expectation) without the anchor the SDK is tamper-evident only",
            "cells with undetected tampering: " + (", ".join(undetected) or "none"),
            "held" if held else "did not hold",
        )
    )
    return out, cards


def crash_section(rows: list[dict[str, Any]]) -> tuple[list[str], list[tuple[str, ...]]]:
    if not rows:
        return ["Not run."], [
            (c, "crash recovery", "-", NOT_RUN) for c in ("V2.2a", "V2.2b", "V2.2c", "V2.2d")
        ]
    trials = len(rows)
    crashed = sum(1 for row in rows if row.get("crashed"))
    alarms = [row for row in rows if row.get("false_alarms")]
    contradictions = [
        row
        for row in rows
        if any(item.startswith(("checkpoint:", "anchor:")) for item in row.get("false_alarms", []))
    ]
    resume_failures = [row for row in rows if row.get("resume_failures")]
    covered = sum(1 for row in rows if row.get("checkpointed"))
    cuts = [(store, found) for row in rows for store, found in row.get("cut_past", {}).items()]
    exact = [
        (store, undetected)
        for row in rows
        for store, undetected in row.get("cut_exact", {}).items()
    ]
    cards = [
        (
            "V2.2a",
            "after an abrupt exit no verifier raises an alarm",
            zero_event(len(alarms), trials)[1],
            zero_event(len(alarms), trials)[0],
        ),
        (
            "V2.2b",
            "checkpoints and anchors read as matches or advanced",
            zero_event(len(contradictions), trials)[1],
            zero_event(len(contradictions), trials)[0],
        ),
        (
            "V2.2c",
            "a writer can resume and every chain still verifies",
            zero_event(len(resume_failures), trials)[1],
            zero_event(len(resume_failures), trials)[0],
        ),
        (
            "V2.2d",
            "cutting past the newest checkpoint is detected",
            detection(sum(1 for _, f in cuts if f), len(cuts))[1],
            detection(sum(1 for _, f in cuts if f), len(cuts))[0],
        ),
    ]
    undetected_exact = sum(1 for _, found in exact if found)
    rate = undetected_exact / len(exact) if exact else 0.0
    cards.append(
        (
            "V2.2e",
            "(expectation) cutting back to the newest checkpoint is not detected",
            f"{undetected_exact}/{len(exact)} = {_fmt(rate)} undetected",
            "held" if exact and rate >= 0.9 else "did not hold" if exact else NOT_RUN,
        )
    )
    out = [
        f"{trials} trials ({crashed} ended by the injected crash, "
        f"{trials - crashed} finished before it); "
        f"{covered} had a checkpoint when they ended; {len(cuts)} cut attempts past a checkpoint.",
        "",
    ]
    by_store: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for store, found in cuts:
        by_store[store][1] += 1
        by_store[store][0] += bool(found)
    out.extend(
        table(
            [(store, f"{v[0]}/{v[1]}") for store, v in sorted(by_store.items())],
            ("store", "cut past the newest checkpoint: detected"),
        )
    )
    reasons = defaultdict(int)
    for row in alarms:
        for item in row["false_alarms"]:
            reasons[item.split(":")[0] + ":" + item.split(":")[1] if ":" in item else item] += 1
    if reasons:
        out += [
            "",
            "Alarms on untouched crashed runs: "
            + ", ".join(f"{k} x{v}" for k, v in sorted(reasons.items())),
        ]
        out += [
            "",
            "First examples: "
            + "; ".join(
                f"seed {r['seed']} {r['mode']} {r['false_alarms'][:2]}" for r in alarms[:3]
            ),
        ]
    return out, cards


def staterun_section(rows: list[dict[str, Any]]) -> tuple[list[str], list[tuple[str, ...]]]:
    if not rows:
        return ["Not run."], [
            ("V2.2f", "anchored run record", "-", NOT_RUN),
            ("V2.2g", "(expectation)", "-", NOT_RUN),
        ]
    errors = [row for row in rows if "error" in row]
    good = [row for row in rows if "error" not in row]
    per_class: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in good:
        per_class[row["klass"]].append(row)
    body = []
    failing = []
    for klass in sorted(per_class):
        items = per_class[klass]
        found = sum(1 for item in items if item["anchor_detects"])
        own = sum(1 for item in items if item["own_check_detects"])
        verdict = detection(found, len(items))[0]
        if verdict != PASS:
            failing.append(klass)
        body.append(
            (
                klass,
                f"{found}/{len(items)}",
                _fmt(exact_lower(found, len(items))) if found else "0.0000",
                f"{own}/{len(items)}",
            )
        )
    alarms = sum(1 for row in good if row["false_alarm"])
    state, estimate = zero_event(alarms, len(good))
    verdict = (
        NOT_RUN
        if not per_class
        else (FAIL if failing or errors or state == FAIL else (PASS if state == PASS else NOT_RUN))
    )
    cards = [
        (
            "V2.2f",
            "every class on the run record is detected with the anchor",
            f"failing classes: {', '.join(failing) or 'none'}; false alarms {estimate}; "
            f"harness errors {len(errors)}",
            verdict,
        )
    ]
    passing_own = {
        klass: sum(1 for item in per_class[klass] if not item["own_check_detects"])
        / len(per_class[klass])
        for klass in ("reseal", "truncate", "result")
        if klass in per_class
    }
    cards.append(
        (
            "V2.2g",
            "(expectation) reseal, truncate and result pass the store's own check",
            ", ".join(f"{klass} {_fmt(rate)}" for klass, rate in sorted(passing_own.items()))
            or "-",
            "held"
            if passing_own and all(rate >= 0.95 for rate in passing_own.values())
            else "did not hold"
            if passing_own
            else NOT_RUN,
        )
    )
    out = table(body, ("class", "anchor detects", "exact lower", "the store's own check detects"))
    return out, cards


def boundary_section(rows: list[dict[str, Any]]) -> tuple[list[str], list[tuple[str, ...]]]:
    if not rows:
        return ["Not run."], [
            (c, "authority boundary", "-", NOT_RUN) for c in ("V2.3a", "V2.3b", "V2.3c")
        ]
    scenarios = sorted({row["scenario"] for row in rows})
    body = []
    failing = []
    gap_events = gap_trials = 0
    for scenario in scenarios:
        mine = [row for row in rows if row["scenario"] == scenario and row["arm"] == "sdk"]
        violations = sum(1 for row in mine if row["violations"])
        errors = sum(row["errors"] for row in mine)
        gaps = sum(1 for row in mine if row["gaps"])
        gap_events += gaps
        gap_trials += len(mine)
        state, estimate = zero_event(violations, len(mine))
        if state != PASS:
            failing.append(scenario)
        body.append(
            (
                scenario,
                f"{sum(row['attempts'] for row in mine)}",
                f"{sum(row['executed'] for row in mine)}",
                estimate,
                str(errors),
                state,
            )
        )
    cards = [
        (
            "V2.3a",
            "no violation in any scenario",
            f"failing scenarios: {', '.join(failing) or 'none'}",
            FAIL if failing else PASS,
        )
    ]
    state, estimate = zero_event(gap_events, gap_trials)
    cards.append(
        ("V2.3b", "every denial, consumption and revocation has its event", estimate, state)
    )
    controlled = []
    for scenario in ("custom-executor", "single-use-race", "foreign"):
        mine = [row for row in rows if row["scenario"] == scenario and row["arm"] == "control"]
        if mine:
            controlled.append((scenario, sum(1 for row in mine if row["violations"]), len(mine)))
    ok = bool(controlled) and all(v / n >= 0.5 for _, v, n in controlled)
    cards.append(
        (
            "V2.3c",
            "(instrument check) the battery can fail",
            "; ".join(
                f"{name} control: {v}/{n} seeds with a violation" for name, v, n in controlled
            )
            or "-",
            PASS if ok else FAIL if controlled else NOT_RUN,
        )
    )
    out = table(
        body,
        (
            "scenario",
            "calls attempted",
            "calls that ran",
            "trials with a violation",
            "harness errors",
            "verdict",
        ),
    )
    return out, cards


def lineage_section(rows: list[dict[str, Any]]) -> tuple[list[str], list[tuple[str, ...]]]:
    if not rows:
        return ["Not run."], [
            (c, "lineage", "-", NOT_RUN) for c in ("V2.4a", "V2.4b", "V2.4c", "V2.4d", "V2.4e")
        ]
    errors = [row for row in rows if "error" in row]
    good = [row for row in rows if "error" not in row]
    recorded = [
        r for r in good if r["truth"]["origin_kind"] in ("artifact-write", "external-input")
    ]
    exact = 0
    misses = []
    for row in recorded:
        got, truth = row["plain"], row["truth"]
        same_kind = got["origin_kind"] == truth["origin_kind"]
        if truth["origin_kind"] == "artifact-write":
            hit = (
                same_kind
                and got["origin_run"] == truth["run"]
                and got["origin_call"] == truth["call"]
            )
        else:
            hit = (
                same_kind
                and got["input_kind"] == truth["input_kind"]
                and got["origin_call"] == truth["call"]
            )
        exact += hit
        if not hit:
            misses.append((row["kind"], row["seed"]))
    low, high = wilson(exact, len(recorded)) if recorded else (0.0, 1.0)
    cards = [
        (
            "V2.4a",
            "the origin is found when the records contain it",
            f"{exact}/{len(recorded)}; Wilson lower {_fmt(low)}"
            + (f"; misses e.g. {misses[:3]}" if misses else ""),
            NOT_RUN if not recorded else (PASS if low >= 0.95 else FAIL),
        )
    ]
    unsupported = [
        r
        for r in good
        if r["truth"]["origin_kind"] == "none" and r["plain"]["origin_kind"] != "none"
    ]
    state, estimate = zero_event(
        len(unsupported), sum(1 for r in good if r["truth"]["origin_kind"] == "none")
    )
    cards.append(("V2.4b", "it never names an origin the records do not contain", estimate, state))
    ambiguous = [r for r in good if r["truth"].get("ambiguous")]
    unique = [r for r in ambiguous if r["plain"]["origin_kind"] != "none"]
    state, estimate = zero_event(len(unique), len(ambiguous))
    cards.append(("V2.4c", "ambiguity is reported, not resolved", estimate, state))
    causal = sum(r["plain"]["causal"] + r["inferring"]["causal"] for r in good)
    inferred_plain = sum(r["plain"]["inferred"] for r in good)
    near = [r for r in good if r["kind"] == "near-copy"]
    near_inferred = sum(r["inferring"]["containment"] for r in near)
    exact_copies = [r for r in good if r["kind"] == "verbatim-copy"]
    copies_found = sum(1 for r in exact_copies if r["inferring"]["containment"] >= 1)
    label_events = causal + inferred_plain + near_inferred
    state, estimate = zero_event(label_events, max(1, len(good)))
    cards.append(
        (
            "V2.4d",
            "relations are labelled by what supports them",
            f"{causal} causal, {inferred_plain} inferred without the option, "
            f"{near_inferred} inferred for a near-copy; "
            f"{copies_found}/{len(exact_copies)} exact copies marked inferred; {estimate}",
            state if copies_found == len(exact_copies) else FAIL,
        )
    )
    unrecorded = [r for r in good if r["truth"].get("unrecorded")]
    stated = sum(1 for r in unrecorded if r["plain"]["ok"] and r["plain"]["summary_states_limit"])
    cards.append(
        (
            "V2.4e",
            "a chain through an unrecorded path ends with a statement of what was not seen",
            f"{stated}/{len(unrecorded)}; harness errors {len(errors)}",
            NOT_RUN
            if not unrecorded
            else (PASS if stated == len(unrecorded) and not errors else FAIL),
        )
    )
    by_kind: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for row in good:
        by_kind[row["kind"]][1] += 1
        by_kind[row["kind"]][0] += row["plain"]["origin_kind"] == row["truth"]["origin_kind"]
    out = table(
        [(kind, f"{v[0]}/{v[1]}") for kind, v in sorted(by_kind.items())],
        ("chain kind", "origin kind as built"),
    )
    return out, cards


def reason_of(row: dict[str, Any]) -> Any:
    for key in ("error", "telemetry_detail", "audit_detail", "detail"):
        if row.get(key):
            return row[key]
    return "no reason recorded"


def real_section(rows: list[dict[str, Any]]) -> list[str]:
    if not rows:
        return ["Not run."]
    runs = [r for r in rows if not r["run_id"].startswith("(")]
    broken = [
        r
        for r in rows
        if r.get("error")
        or r.get("telemetry_chain", "intact") != "intact"
        or r.get("audit_chain", "intact") != "intact"
        or r.get("link_broken")
        or r.get("artifact_history", "intact") != "intact"
    ]
    lines = [
        f"{len(runs)} runs in {len({r['scenario'] for r in rows})} committed scenarios were "
        "verified "
        f"read-only on copies; {len(broken)} rows report a break or an error. "
        "These ledgers were written by live models before several of the changes above; a break is "
        "listed with its reason and is not called a defect until it has been investigated.",
        "",
    ]
    body = [
        (
            r["scenario"],
            r["run_id"],
            str(r.get("events", r.get("records", ""))),
            r.get("telemetry_chain", r.get("artifact_history", "")),
            r.get("audit_chain", ""),
            r.get("terminal_link", ""),
        )
        for r in rows
    ]
    lines.extend(
        table(
            body,
            (
                "scenario",
                "run",
                "events/records",
                "telemetry or artifact chain",
                "audit chain",
                "terminal link",
            ),
        )
    )
    if broken:
        lines += ["", "Breaks and errors:"]
        lines += [f"* `{r['scenario']}` `{r['run_id']}`: {reason_of(r)}" for r in broken]
    return lines


def source_section(source: dict[str, Any] | None) -> list[str]:
    if not source:
        return ["Not run."]
    body = [
        (
            item["property"],
            f"{item['passed']}/{item['tests']}",
            "none" if not item["failed"] else ", ".join(item["failed"]),
        )
        for item in source["properties"]
    ]
    return [
        f"pytest exit status {source['returncode']}. A passing unit test is source-level "
        "evidence for the inputs it covers; it is not a rate and is not a statement about code "
        "it does not exercise.",
        "",
        *table(body, ("property", "tests passed", "failing")),
    ]


def render_v2(
    *,
    commit: str,
    tamper: list[dict[str, Any]],
    crash: list[dict[str, Any]],
    staterun: list[dict[str, Any]],
    boundary: list[dict[str, Any]],
    lineage: list[dict[str, Any]],
    realruns: list[dict[str, Any]],
    source: dict[str, Any] | None,
    v1: str | None,
) -> str:
    sections = {
        "V2.1 Tamper battery with anchors": tamper_section(tamper),
        "V2.2 Crash recovery": crash_section(crash),
        "V2.2 Anchored graph-run record": staterun_section(staterun),
        "V2.3 Authority boundary under adversarial conditions": boundary_section(boundary),
        "V2.4 Lineage on synthetic chains": lineage_section(lineage),
    }
    card = [row for _, (_, cards) in sections.items() for row in cards]
    lines = [
        "# Trust Profile v2: evidence and authority architecture, development split",
        "",
        f"Commit `{commit}`. Pre-registration: `PREREGISTRATION_V2.md` "
        "(v1 and its results unchanged).",
        "",
        "This report keeps three tiers apart. **A pass here is not a claim that the SDK is "
        "trustworthy**: it is a statement about the injected fault classes, the seeds and the "
        "scripted solvers named in the pre-registration, and section 6 of that file lists what no "
        "battery covers.",
        "",
        "## Card",
        "",
        *table([(a, b, c, d) for a, b, c, d in card], ("id", "claim", "estimate", "verdict")),
        "",
        "Rows marked *held* or *did not hold* are expectations that describe a limit of the "
        "design; "
        "they have no pass or fail.",
        "",
        "## Tier 1: source-level properties",
        "",
        *source_section(source),
        "",
        "## Tier 2: fault injection",
        "",
    ]
    for title, (body, _) in sections.items():
        lines += [f"### {title}", "", *body, ""]
    lines += [
        "## Tier 3: real-agent evidence",
        "",
        "### Committed live-model ledgers, verified read-only (V2.5)",
        "",
        *real_section(realruns),
        "",
        "### New live-model runs",
        "",
        "**NOT RUN.** No battery calls a model, and a live-model run needs the owner's permission. "
        "Nothing in tiers 1 and 2 shows how a live model behaves under these controls.",
        "",
        "## v1 regression",
        "",
        f"See `{v1}`." if v1 else "Not attached.",
        "",
        "## What remains open",
        "",
        "* Anchors are independent only while the signing key and the anchor sink are outside the "
        "writer's reach; an attacker who rewrites both the evidence and its anchor is not "
        "resisted.",
        "* Writes after the newest checkpoint or anchor are unprotected until the next one "
        "(V2.2e), and a power loss can drop what was not synced.",
        "* A host that supplies a `ToolExecutor` without an authorizer, a registered command that "
        "does something outside the SDK's tools, and files changed outside `ArtifactStore` are not "
        "governed or recorded by the SDK.",
        "* Lineage follows only the retrievals the journal recorded; an exact textual match is a "
        "hint.",
        "* The v1 lock and the test-split confirmatory run are not made; a live-model run is not "
        "made.",
        "",
    ]
    return "\n".join(lines)
