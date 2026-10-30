"""FALAT: failure attribution by dependency-guided search (arXiv 2606.00765), reimplemented.

Written from the published description of the method, not from its code, and with prompts of my own;
the authors' prompts and thresholds are not public in the description, so the parameters below
(window of 4 steps, K = 3 candidates, a re-search window of 2 steps) are my choices where the
description gives none. Scores may differ from the authors' implementation.

Stage 1  an external prior from the task, the agents, their roles and the failed output (not the
         intermediate steps); a hierarchical summary of the log in windows (raw steps, per-step
         summaries and a window summary, all read through the prior); and the candidate set, the
         steps flagged by any of three indicators: outcome relevance, prior inconsistency, local
         execution inconsistency.
Stage 2  a type for every ordered pair of candidates (follow_up, redundancy, no_influence,
         error_shift, correction, dead_end); candidates whose every link is a prunable type are
         dropped.
Stage 3  the remaining candidates are ranked, the top K are checked for error independence and
         counterfactual sufficiency with their neighbouring raw steps and typed links, and the
         earliest candidate that passes both is chosen (the best-ranked one if none does).
Stage 4  a local re-search over the steps around the choice: arguments for and against, a
         suspicion score and a role per step, and a CONFIRM or REVISE verdict.

The with-ground-truth setting is used: the correct final answer is part of the prior and of the
sufficiency check, as the method's definition of a counterfactual fix requires an expected output.
"""

from __future__ import annotations

from collections.abc import Callable

from corpus.llm import json_array, json_object
from corpus.prediction import Prediction
from corpus.trace import Step, pipeline_preamble, render_step

Chat = Callable[[str], str]
WINDOW = 4
TOP_K = 3
RE_SEARCH = 2
PRUNABLE = frozenset({"redundancy", "no_influence", "correction", "dead_end"})
TYPES = ("follow_up", "redundancy", "no_influence", "error_shift", "correction", "dead_end")

PRIOR = """You analyse the failure of a multi-agent system. You are given the task, the agents with
their roles and tools, the expected output and the output the system actually produced. You are
NOT given the intermediate steps.

{preamble}

Expected final output: {expected}
Observed final output: {observed}

Write (1) a short analysis of what the task requires, (2) what each of the agents A, B and C is
expected to do at its step, and (3) a numbered list of 8 to 12 error signals specific to this task
that would show where a run goes wrong. Be concise."""
WINDOW_PROMPT = """External prior (what correct behaviour looks like):
{prior}

LOG WINDOW (steps {first} to {last})
{log}

For every step in the window, read it through the prior and answer. Reply with a JSON array, one
object per step, and nothing else:
[{{"step": <number>, "summary": "<agent, action, decision, output, significance in one line>",
"outcome_relevant": 0 or 1, "prior_inconsistent": 0 or 1, "local_inconsistent": 0 or 1}}]
outcome_relevant: the step's output bears on the wrong final output. prior_inconsistent: the step
conflicts with the prior. local_inconsistent: the step's result does not match its own call or an
earlier result (mismatched arguments, a result that differs from what was written earlier, an
ignored tool error). Then add one more object after the array's last step:
{{"window_summary": "<two sentences on this stage of the run>"}}"""
DEPENDENCIES = """External prior:
{prior}

Candidate steps with summaries:
{candidates}

For every ordered pair of candidate steps i < j assign exactly one label describing how step i
bears on step j: follow_up (j continues from i's result, including an error in it),
redundancy (j repeats i), no_influence, error_shift (j's error is a different error that i's
shifted onto it), correction (j corrects i's error) or dead_end (i led nowhere). Reply with a
JSON array and nothing else: [{{"from": i, "to": j, "label": "<one label>"}}]"""
RANKING = """External prior:
{prior}

Remaining candidate steps (after pruning) with summaries and the typed links between them:
{candidates}

Rank the steps from most to least likely to be the decisive error, preferring steps that
(i) make an incorrect execution decision, (ii) rely on unverified or fabricated information
relative to the prior, and (iii) lie on a typed path that reaches the wrong output. Reply with a
JSON array of step numbers, best first, and nothing else."""
VERIFY = """External prior:
{prior}

Expected final output: {expected}. Observed: {observed}.

Candidate step {step}, with its neighbours:
{neighbours}

Typed links involving this step:
{links}

Judge the candidate by two criteria. Error independence: does the step originate its error
rather than inherit one from an earlier step? Counterfactual sufficiency: if this step alone were
corrected, keeping earlier steps fixed and letting later steps adapt, would the run reach the
expected output? Reply with one JSON object and nothing else:
{{"independent": true or false, "sufficient": true or false, "reason": "<one sentence>"}}"""
LOCAL = """External prior:
{prior}

Expected final output: {expected}. Observed: {observed}.

Steps {first} to {last} around the current choice, step {choice}:
{log}

For each step list three arguments for and three against it being the decisive error, give a
suspicion score between 0 and 1 and one role: ROOT_CAUSE, PROPAGATION, SYMPTOM or CONTRIBUTING.
Run a counterfactual test on the two most suspicious steps (would correcting only that step
recover the expected output?). Then give a verdict: CONFIRM step {choice} or REVISE to another
step. Reply with one JSON object and nothing else:
{{"verdict": "CONFIRM" or "REVISE", "step": <decisive step number>, "agent": "A" or "B" or "C"}}"""


def _lines(steps: list[Step], first: int, last: int) -> str:
    return "\n".join(render_step(step) for step in steps if first <= step.index <= last)


def _final_output(steps: list[Step]) -> str:
    writes = [step for step in steps if step.tool == "write_draft"]
    if not writes:
        return "(nothing was written)"
    return str(writes[-1].arguments.get("content", "")).strip()


def falat(
    steps: list[Step],
    chat: Chat,
    answer: str,
    preamble: str | None = None,
) -> Prediction:
    expected, observed = answer, _final_output(steps)
    prior = chat(
        PRIOR.format(preamble=preamble or pipeline_preamble(), expected=expected, observed=observed)
    ).strip()
    summaries: dict[int, str] = {}
    flagged: set[int] = set()
    for first in range(1, len(steps) + 1, WINDOW):
        last = min(first + WINDOW - 1, len(steps))
        reply = chat(
            WINDOW_PROMPT.format(
                prior=prior, first=first, last=last, log=_lines(steps, first, last)
            )
        )
        for item in json_array(reply) or []:
            if not isinstance(item, dict) or "step" not in item:
                continue
            try:
                index = int(item["step"])
            except (TypeError, ValueError):
                continue
            summaries[index] = str(item.get("summary", ""))
            indicators = (
                item.get("outcome_relevant"),
                item.get("prior_inconsistent"),
                item.get("local_inconsistent"),
            )
            if any(value in (1, True, "1") for value in indicators):
                flagged.add(index)
    candidates = sorted(index for index in flagged if 1 <= index <= len(steps))
    if not candidates:
        candidates = list(range(1, len(steps) + 1))
    labels = _dependencies(chat, prior, candidates, summaries)
    kept = _prune(candidates, labels)
    ranked = _rank(chat, prior, kept, summaries, labels)
    top = ranked[:TOP_K]
    chosen = _verify(chat, prior, top, labels, steps, expected, observed)
    final = _local(chat, prior, chosen, steps, expected, observed)
    ordered = [final, *[index for index in ranked if index != final]]
    by_index = {step.index: step for step in steps}
    picked = by_index[final]
    return Prediction(
        picked.node,
        picked.call_id,
        "falat",
        tuple(by_index[index].call_id for index in ordered[:TOP_K] if index in by_index),
    )


def _dependencies(
    chat: Chat, prior: str, candidates: list[int], summaries: dict[int, str]
) -> dict[tuple[int, int], str]:
    if len(candidates) < 2:
        return {}
    text = "\n".join(f"step {index}: {summaries.get(index, '')}" for index in candidates)
    reply = chat(DEPENDENCIES.format(prior=prior, candidates=text))
    labels: dict[tuple[int, int], str] = {}
    for item in json_array(reply) or []:
        if not isinstance(item, dict):
            continue
        try:
            pair = (int(item["from"]), int(item["to"]))
        except (KeyError, TypeError, ValueError):
            continue
        label = str(item.get("label", "")).strip().lower()
        if label in TYPES and pair[0] < pair[1] and {pair[0], pair[1]} <= set(candidates):
            labels[pair] = label
    return labels


def _prune(candidates: list[int], labels: dict[tuple[int, int], str]) -> list[int]:
    kept = []
    for index in candidates:
        own = [label for pair, label in labels.items() if index in pair]
        if own and all(label in PRUNABLE for label in own):
            continue
        kept.append(index)
    return kept or candidates


def _rank(
    chat: Chat,
    prior: str,
    kept: list[int],
    summaries: dict[int, str],
    labels: dict[tuple[int, int], str],
) -> list[int]:
    if len(kept) == 1:
        return kept
    text = "\n".join(f"step {index}: {summaries.get(index, '')}" for index in kept)
    links = "\n".join(
        f"{a} -> {b}: {label}" for (a, b), label in labels.items() if a in kept and b in kept
    )
    reply = chat(RANKING.format(prior=prior, candidates=text + "\n" + links))
    order = []
    for value in json_array(reply) or []:
        try:
            index = int(value)
        except (TypeError, ValueError):
            continue
        if index in kept and index not in order:
            order.append(index)
    return order + [index for index in kept if index not in order]


def _verify(
    chat: Chat,
    prior: str,
    top: list[int],
    labels: dict[tuple[int, int], str],
    steps: list[Step],
    expected: str,
    observed: str,
) -> int:
    passed = []
    for index in top:
        links = "\n".join(
            f"{a} -> {b}: {label}" for (a, b), label in labels.items() if index in (a, b)
        )
        reply = chat(
            VERIFY.format(
                prior=prior,
                expected=expected,
                observed=observed,
                step=index,
                neighbours=_lines(steps, index - 1, index + 1),
                links=links or "(none)",
            )
        )
        found = json_object(reply) or {}
        if found.get("independent") is True and found.get("sufficient") is True:
            passed.append(index)
    return min(passed) if passed else top[0]


def _local(
    chat: Chat, prior: str, choice: int, steps: list[Step], expected: str, observed: str
) -> int:
    first, last = max(1, choice - RE_SEARCH), min(len(steps), choice + RE_SEARCH)
    reply = chat(
        LOCAL.format(
            prior=prior,
            expected=expected,
            observed=observed,
            first=first,
            last=last,
            choice=choice,
            log=_lines(steps, first, last),
        )
    )
    found = json_object(reply) or {}
    if str(found.get("verdict", "")).upper() == "REVISE":
        try:
            revised = int(found["step"])
        except (KeyError, TypeError, ValueError):
            return choice
        if first <= revised <= last:
            return revised
    return choice
