"""The three automated failure-attribution methods of Who&When (Zhang et al., arXiv 2505.00212).

Ported from the paper's description (section on automated failure attribution), not from its code:
the prompts are written here and follow the paper's wording where the paper gives it.

* all-at-once: one call over the whole log asking for the agent, the step and the reason;
* step-by-step: one call per step over the log up to that step, asking whether the most recent
  action contains an error that could hinder solving the problem; the first "yes" is the answer
  and "no error found" is an answer too (counted as wrong, not replaced by a guess);
* binary search: repeatedly ask whether the error is in the lower or the upper half of the
  current segment of the log, until one step is left.

Each can be given the correct final answer ("with ground truth") or not. A method that cannot
name a step returns a prediction with no call.
"""

from __future__ import annotations

import re
from collections.abc import Callable

from corpus.prediction import Prediction
from corpus.trace import Step, pipeline_preamble, render_step

Chat = Callable[[str], str]

SYSTEM = (
    "You are an AI assistant that analyses the failure log of a multi-agent system. "
    "You are given a description of the system and a log of numbered steps, each a tool call by "
    "one agent and its result."
)
ALL_AT_ONCE = """{system}

{preamble}

LOG
{log}

The final output of the run, summary.txt, is wrong.{ground_truth}
Identify which agent made an error, at which step, and explain the reason for the error. Reply in
exactly this format:
Agent Name: <A, B or C>
Step Number: <the step number>
Reason for Mistake: <one or two sentences>"""
STEP_BY_STEP = """{system}

{preamble}

LOG (up to step {step})
{log}

The run this log comes from ended with a wrong final output.{ground_truth}
Determine whether the most recent agent action (step {step}) contains an error that could hinder
solving the problem. Reply in exactly this format:
1. Yes or No
2. Reason: <one sentence>"""
BINARY_SEARCH = """{system}

{preamble}

LOG (steps 1 to {high})
{log}

The run this log comes from ended with a wrong final output.{ground_truth}
The error lies somewhere in steps {low} to {high}. The lower half is steps {low} to {mid}; the
upper half is steps {upper} to {high}. Please simply output either 'upper half' or 'lower half'.
You should not output anything else."""


def _gt(answer: str | None) -> str:
    return f" The correct final answer is: {answer}." if answer else ""


def _log(steps: list[Step], upto: int | None = None) -> str:
    chosen = steps if upto is None else [step for step in steps if step.index <= upto]
    return "\n".join(render_step(step) for step in chosen)


def all_at_once(
    steps: list[Step], chat: Chat, answer: str | None = None, preamble: str | None = None
) -> Prediction:
    reply = chat(
        ALL_AT_ONCE.format(
            system=SYSTEM,
            preamble=preamble or pipeline_preamble(),
            log=_log(steps),
            ground_truth=_gt(answer),
        )
    )
    number = re.search(r"Step\s*Number\s*:\s*\**\s*(\d+)", reply, re.IGNORECASE)
    agent = re.search(r"Agent\s*Name\s*:\s*\**\s*(?:Agent\s*)?([ABC])\b", reply, re.IGNORECASE)
    if number is None:
        return Prediction(agent.group(1).upper() if agent else None, None, "no step")
    chosen = next((step for step in steps if step.index == int(number.group(1))), None)
    if chosen is None:
        return Prediction(
            agent.group(1).upper() if agent else None,
            None,
            f"step {number.group(1)} not in the log",
        )
    return Prediction(chosen.node, chosen.call_id, "all-at-once")


def step_by_step(
    steps: list[Step], chat: Chat, answer: str | None = None, preamble: str | None = None
) -> Prediction:
    for step in steps:
        reply = chat(
            STEP_BY_STEP.format(
                system=SYSTEM,
                preamble=preamble or pipeline_preamble(),
                log=_log(steps, step.index),
                step=step.index,
                ground_truth=_gt(answer),
            )
        )
        verdict = re.search(r"1\s*[.)]?\s*\**\s*(yes|no)\b", reply, re.IGNORECASE)
        if verdict is None:
            verdict = re.match(r"\s*\**\s*(yes|no)\b", reply, re.IGNORECASE)
        if verdict is not None and verdict.group(1).lower() == "yes":
            return Prediction(step.node, step.call_id, "step-by-step")
    return Prediction(None, None, "step-by-step: no error found")


def binary_search(
    steps: list[Step], chat: Chat, answer: str | None = None, preamble: str | None = None
) -> Prediction:
    low, high = 1, len(steps)
    while low < high:
        mid = (low + high) // 2
        reply = chat(
            BINARY_SEARCH.format(
                system=SYSTEM,
                preamble=preamble or pipeline_preamble(),
                log=_log(steps, high),
                low=low,
                mid=mid,
                upper=mid + 1,
                high=high,
                ground_truth=_gt(answer),
            )
        ).lower()
        lower_at = reply.find("lower half")
        upper_at = reply.find("upper half")
        if lower_at < 0 and upper_at < 0:
            return Prediction(None, None, "binary search: unparseable answer")
        if upper_at < 0 or (0 <= lower_at < upper_at):
            high = mid
        else:
            low = mid + 1
    chosen = steps[low - 1]
    return Prediction(chosen.node, chosen.call_id, "binary-search")
