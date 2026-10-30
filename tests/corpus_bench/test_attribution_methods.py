import sys

import pytest

from tests.support.paths import REPO_ROOT

BENCHMARK = str(REPO_ROOT / "benchmarks" / "trust")
if BENCHMARK not in sys.path:
    sys.path.insert(0, BENCHMARK)

from corpus import falat, stats, whowhen  # noqa: E402
from corpus.trace import Step  # noqa: E402


def make_steps(count):
    nodes = "AAABBC"
    return [
        Step(i, nodes[min(i - 1, len(nodes) - 1)], i, "write_draft", f"call-{i}", {}, "ok", "x")
        for i in range(1, count + 1)
    ]


def test_all_at_once_reads_the_named_step_and_rejects_a_step_outside_the_log():
    steps = make_steps(6)
    reply = "Agent Name: B\nStep Number: 4\nReason for Mistake: wrong sum"
    found = whowhen.all_at_once(steps, lambda prompt: reply)
    assert (found.node, found.call_id) == ("B", "call-4")
    outside = whowhen.all_at_once(steps, lambda prompt: "Agent Name: A\nStep Number: 99")
    assert outside.call_id is None and outside.node == "A"
    assert whowhen.all_at_once(steps, lambda prompt: "no idea").call_id is None


def test_the_correct_answer_is_only_in_the_prompt_when_given():
    seen = []
    whowhen.all_at_once(make_steps(3), lambda p: seen.append(p) or "", "TOTAL=1;TOP=x")
    whowhen.all_at_once(make_steps(3), lambda p: seen.append(p) or "")
    assert "correct final answer is: TOTAL=1;TOP=x" in seen[0]
    assert "correct final answer" not in seen[1]


def test_step_by_step_returns_the_first_yes_and_abstains_when_there_is_none():
    steps = make_steps(5)
    asked = []

    def chat(prompt):
        asked.append(prompt)
        return "1. Yes\n2. Reason: bad" if "step 3)" in prompt else "1. No\n2. Reason: fine"

    found = whowhen.step_by_step(steps, chat)
    assert found.call_id == "call-3" and len(asked) == 3
    nothing = whowhen.step_by_step(steps, lambda prompt: "1. No")
    assert nothing.call_id is None and "no error found" in nothing.note


@pytest.mark.parametrize("target", range(1, 8))
def test_binary_search_finds_any_step_in_log_n_questions(target):
    steps = make_steps(7)
    asked = []

    def chat(prompt):
        asked.append(prompt)
        low = int(prompt.split("The error lies somewhere in steps ")[1].split(" to ")[0])
        mid = int(prompt.split("lower half is steps ")[1].split(" to ")[1].split(";")[0])
        assert low <= mid
        return "lower half" if target <= mid else "upper half"

    assert whowhen.binary_search(steps, chat).call_id == f"call-{target}"
    assert len(asked) <= 3


def test_binary_search_stops_on_an_unparseable_answer():
    found = whowhen.binary_search(make_steps(6), lambda prompt: "maybe")
    assert found.call_id is None and "unparseable" in found.note


def test_falat_runs_its_four_stages_and_ranks_the_chosen_step_first():
    steps = make_steps(6)
    stages = []

    def chat(prompt):
        if prompt.startswith("You analyse the failure"):
            stages.append("prior")
            return "prior text"
        if "LOG WINDOW" in prompt:
            stages.append("window")
            first = int(prompt.split("(steps ")[1].split(" to")[0])
            items = ",".join(
                f'{{"step": {i}, "summary": "s{i}", "outcome_relevant": {int(i in (2, 5))},'
                f' "prior_inconsistent": 0, "local_inconsistent": 0}}'
                for i in range(first, min(first + 4, 7))
            )
            return f"[{items}]"
        if "ordered pair" in prompt:
            stages.append("links")
            return '[{"from": 2, "to": 5, "label": "follow_up"}]'
        if "Rank the steps" in prompt:
            stages.append("rank")
            return "[5, 2]"
        if "Error independence" in prompt:
            stages.append("verify")
            return '{"independent": true, "sufficient": true}'
        stages.append("local")
        return '{"verdict": "CONFIRM", "step": 2, "agent": "A"}'

    found = falat.falat(steps, chat, "TOTAL=1;TOP=x")
    assert found.call_id == "call-2" and found.node == "A"
    assert found.ranked[0] == "call-2" and set(found.ranked) <= {f"call-{i}" for i in range(1, 7)}
    assert stages[0] == "prior" and stages[-1] == "local"
    assert {"window", "links", "rank", "verify"} <= set(stages)


def test_falat_can_revise_within_its_local_window_only():
    steps = make_steps(6)

    def chat(prompt):
        if "LOG WINDOW" in prompt:
            return '[{"step": 3, "summary": "s", "outcome_relevant": 1}]'
        if "Counterfactual" in prompt or "CONFIRM step" in prompt:
            return '{"verdict": "REVISE", "step": 99, "agent": "C"}'
        return "text"

    assert falat.falat(steps, chat, "a").call_id == "call-3"


def test_mcnemar_is_exact_and_the_bootstrap_difference_matches_the_counts():
    a = [True] * 8 + [False] * 2
    b = [False] * 2 + [True] * 6 + [False] * 2
    only_a, only_b, p = stats.mcnemar_exact(a, b)
    assert (only_a, only_b) == (2, 0) and p == pytest.approx(0.5)
    delta, low, high = stats.paired_difference_interval(a, b, draws=500)
    assert delta == pytest.approx(0.2) and low <= delta <= high
    with pytest.raises(ValueError, match="equal lengths"):
        stats.mcnemar_exact([True], [True, False])
    assert stats.agreement([[1, 2, 3], [1, 2, 4]]) == pytest.approx(2 / 3)
