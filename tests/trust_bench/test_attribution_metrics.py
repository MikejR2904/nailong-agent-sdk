import json

import pytest
from trust_bench.cases import Case
from trust_bench.diagnosers import (
    TRIVIAL,
    Diagnosis,
    DiagnosisInput,
    Symptom,
    ledger_scan,
)
from trust_bench.metrics.attribution import score_diagnosis
from trust_bench.schema import Event, Kind, SystemRecords, canonical, digest_bytes

AGENT = "worker"


class TinyCase(Case):
    family = "tiny"
    case_id = "tiny-1"
    agent_label = AGENT
    task_text = "t"
    tools = ("read_file",)
    output_paths = ("out.txt",)

    def materialize(self, workspace, fault):
        pass

    def policy(self, fault):
        raise NotImplementedError

    def faults(self, operators):
        return []

    def decisive_step(self, fault):
        return None

    def sources(self):
        return {}

    def is_defective(self, path, content):
        return content == b"bad"

    def is_defective_result(self, tool, arguments, envelope):
        return tool == "read_file" and arguments.get("path") == "bad.txt"


def call(call_id, index, name, **arguments):
    payload = {"id": call_id, "name": name, "arguments": arguments}
    return Event(
        Kind.TOOL_CALL,
        agent_id=AGENT,
        model_index=index,
        call_id=call_id,
        name=name,
        content=canonical(payload),
    )


def result(call_id, name, status="succeeded"):
    envelope = {"status": status, "output": {}, "error": None, "failure": None}
    return Event(
        Kind.TOOL_RESULT,
        agent_id=AGENT,
        call_id=call_id,
        name=name,
        status=status,
        content=canonical(envelope),
    )


def model(index):
    return Event(Kind.MODEL_CALL, agent_id=AGENT, model_index=index)


def records(events, blobs=None):
    return SystemRecords("sys", events, {}, (blobs or {}).get)


def diagnose(method, events, blobs=None):
    inp = DiagnosisInput(
        TinyCase(), records(events, blobs), Symptom("artifact-defective", "out.txt")
    )
    return TRIVIAL[method](inp) if method in TRIVIAL else ledger_scan(inp)


def run_events():
    return [
        model(0),
        call("c1", 0, "read_file", path="ok.txt"),
        result("c1", "read_file"),
        model(1),
        call("c2", 1, "read_file", path="bad.txt"),
        result("c2", "read_file", "failed"),
        model(2),
        call("c3", 2, "write_draft", path="out.txt", content="bad"),
        result("c3", "write_draft"),
        Event(
            Kind.WRITE, agent_id=AGENT, call_id="c3", path="out.txt", digest=digest_bytes(b"bad")
        ),
        model(3),
    ]


def test_top_one_requires_the_truth_to_be_named_first():
    outcome = score_diagnosis((AGENT, 2), Diagnosis("m", ((AGENT, 2), (AGENT, 0))))
    assert outcome.has_truth and outcome.top1 and outcome.top3
    assert outcome.reciprocal_rank == 1.0 and outcome.agent_ok and not outcome.abstained


def test_a_truth_named_second_is_top_three_but_not_top_one():
    outcome = score_diagnosis((AGENT, 2), Diagnosis("m", ((AGENT, 0), (AGENT, 2))))
    assert not outcome.top1 and outcome.top3 and outcome.reciprocal_rank == 0.5
    assert outcome.agent_ok


def test_a_truth_never_named_scores_zero_everywhere():
    outcome = score_diagnosis((AGENT, 2), Diagnosis("m", (("other", 1),)))
    assert not outcome.top1 and not outcome.top3 and outcome.reciprocal_rank == 0.0
    assert not outcome.agent_ok and not outcome.abstained


def test_abstaining_on_a_case_with_a_cause_is_neither_correct_nor_unsupported():
    outcome = score_diagnosis((AGENT, 2), Diagnosis("m"))
    assert outcome.abstained and not outcome.top1 and not outcome.unsupported


def test_naming_a_step_where_no_single_decision_is_responsible_is_unsupported():
    named = score_diagnosis(None, Diagnosis("m", ((AGENT, 1),)))
    assert not named.has_truth and named.unsupported and not named.abstained
    abstained = score_diagnosis(None, Diagnosis("m"))
    assert abstained.abstained and not abstained.unsupported


def test_first_and_last_error_name_the_step_of_the_failed_tool_call():
    assert diagnose("first-error", run_events()).steps == ((AGENT, 1),)
    assert diagnose("last-error", run_events()).steps == ((AGENT, 1),)
    clean = [e for e in run_events() if e.status != "failed"]
    assert diagnose("first-error", clean).steps == ()


def test_first_and_last_write_name_the_step_that_issued_the_write():
    assert diagnose("first-write", run_events()).steps == ((AGENT, 2),)
    assert diagnose("last-write", run_events()).steps == ((AGENT, 2),)
    assert diagnose("first-write", [model(0)]).steps == ()


def test_first_and_last_step_ignore_the_content_of_the_run():
    assert diagnose("first-step", run_events()).steps == ((AGENT, 0),)
    assert diagnose("last-step", run_events()).steps == ((AGENT, 3),)
    assert diagnose("first-step", []).steps == ()


def test_the_random_baseline_is_a_fixed_choice_among_real_steps():
    first = diagnose("random-step", run_events())
    assert first.steps == diagnose("random-step", run_events()).steps
    assert first.steps[0] in {(AGENT, i) for i in range(4)}
    assert diagnose("random-step", []).steps == ()


def test_the_ledger_scan_names_the_earliest_defective_result_or_write():
    events = run_events()
    blobs = {digest_bytes(b"bad"): b"bad"}
    scan = diagnose("ledger-scan+oracle", events, blobs)
    assert scan.steps == ((AGENT, 1),) and scan.evaluations >= 2


def test_the_ledger_scan_abstains_when_nothing_in_the_record_is_defective():
    events = [e for e in run_events() if e.call_id != "c2"]
    blobs = {digest_bytes(b"bad"): b"fine"}
    scan = diagnose("ledger-scan+oracle", events, blobs)
    assert scan.steps == ()


def test_the_ledger_scan_counts_what_it_could_not_evaluate_instead_of_guessing():
    events = [
        *(e for e in run_events() if e.kind is not Kind.TOOL_RESULT),
        Event(Kind.TOOL_RESULT, agent_id=AGENT, call_id="c2", name="read_file", content=None),
    ]
    scan = diagnose("ledger-scan+oracle", events, {})
    assert scan.steps == () and "could not be evaluated" in scan.note


def test_a_result_event_is_json_the_oracle_can_parse():
    event = result("c1", "read_file")
    assert json.loads(str(event.content))["status"] == "succeeded"


@pytest.mark.parametrize("name", sorted(TRIVIAL))
def test_every_trivial_baseline_abstains_on_an_empty_record(name):
    assert diagnose(name, []).steps == ()
