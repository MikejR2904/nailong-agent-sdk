import pytest
from trust_bench import aggregate as agg
from trust_bench.metrics.twins import build_twin
from trust_bench.schema import Event, Kind, SystemRecords, digest_bytes

from tests.support.trust_runs import evaluated, fault_ids

AGENT = "worker"
READS = 3


def step_events(index, call_id, name, status="succeeded", write=None):
    events = [
        Event(Kind.MODEL_CALL, agent_id=AGENT, model_index=index, digest=f"m{index}", content="{}"),
        Event(
            Kind.TOOL_CALL,
            agent_id=AGENT,
            model_index=index,
            call_id=call_id,
            name=name,
            digest=f"t{index}",
            content="{}",
        ),
        Event(
            Kind.TOOL_RESULT,
            agent_id=AGENT,
            call_id=call_id,
            name=name,
            digest=f"r{index}",
            status=status,
            content="{}",
        ),
    ]
    if write is not None:
        events.append(
            Event(Kind.WRITE, agent_id=AGENT, call_id=call_id, path=f"out{index}", digest=write)
        )
    return events


def synthetic_records(writes=1):
    events, blobs = [], {}
    for index in range(READS):
        status = "failed" if index == 1 else "succeeded"
        events += step_events(index, f"c{index}", "read_file", status)
    for offset in range(writes):
        index = READS + offset
        body = f"payload-{index}".encode()
        blobs[digest_bytes(body)] = body
        events += step_events(index, f"c{index}", "write_draft", write=digest_bytes(body))
    return SystemRecords("sys", events, {}, blobs.get)


def masked(event):
    return event.content is None and event.digest is None and event.truncated


def test_a_twin_hides_the_decisive_step_and_one_look_alike_and_nothing_else():
    records = synthetic_records()
    twin = build_twin(records, (AGENT, 1), "seed")
    assert twin is not None and twin.decoy in {(AGENT, 0), (AGENT, 2)}
    indexes = {1, twin.decoy[1]}
    hidden_calls = {f"c{i}" for i in indexes}
    for before, after in zip(records.events, twin.records.events, strict=True):
        if before.kind is Kind.WRITE:
            hides = False
        elif before.kind is Kind.TOOL_RESULT:
            hides = before.call_id in hidden_calls
        else:
            hides = before.model_index in indexes
        if hides:
            assert masked(after) and after.status is None
        else:
            assert after == before


def test_the_failed_status_of_a_hidden_result_is_hidden_with_it():
    twin = build_twin(synthetic_records(), (AGENT, 1), "seed")
    assert not any(e.status == "failed" for e in twin.records.events)


def test_the_blob_of_a_hidden_write_cannot_be_fetched_but_a_visible_one_can():
    records = synthetic_records(writes=3)
    twin = build_twin(records, (AGENT, READS), "seed")
    digests = {e.call_id: e.digest for e in records.events if e.kind is Kind.WRITE}
    hidden = {f"c{READS}", f"c{twin.decoy[1]}"}
    for call_id, digest in digests.items():
        fetched = twin.records.fetch_blob(digest)
        assert (fetched is None) == (call_id in hidden)
        assert records.fetch_blob(digest) is not None


def test_no_twin_exists_without_a_look_alike_step_or_a_tool_call_to_hide():
    records = synthetic_records(writes=1)
    assert build_twin(records, (AGENT, READS), "seed") is None
    assert build_twin(records, (AGENT, 99), "seed") is None


def test_the_same_seed_hides_the_same_decoy():
    first = build_twin(synthetic_records(), (AGENT, 1), "case")
    assert first.decoy == build_twin(synthetic_records(), (AGENT, 1), "case").decoy


@pytest.mark.parametrize("operator", ("env_corrupt:digit_swap", "env_unreadable"))
def test_on_a_real_doc_twin_the_ledger_scan_abstains_and_a_trivial_baseline_guesses(operator):
    twins = evaluated("doc", 1, "langgraph", operator)["twins"]
    assert twins["ledger-scan+oracle"]["named"] is False
    assert twins["first-step"]["named"] is True and twins["random-step"]["named"] is True


def test_on_a_real_data_twin_the_hidden_write_blobs_stop_the_scan_from_guessing():
    twins = evaluated("data", 1, "sdk", "decision_wrong_stage:summary")["twins"]
    assert twins["ledger-scan+oracle"]["named"] is False


def test_a_run_with_no_single_cause_has_no_twin():
    assert evaluated("doc", 1, "langgraph", "limit")["twins"] == {}
    assert evaluated("doc", 1, "langgraph")["twins"] == {}


def test_every_single_cause_fault_of_the_data_family_has_a_twin():
    for operator in fault_ids("data", 1):
        result = evaluated("data", 1, "langgraph", operator)
        if result["truth_step"] is not None:
            assert result["twins"] and result["twin_decoy"], operator


def test_the_ledger_scan_still_finds_the_origin_when_nothing_is_hidden():
    result = evaluated("doc", 1, "langgraph", "env_corrupt:digit_swap")
    assert result["diagnoses"]["ledger-scan+oracle"]["top1"]


def test_the_twin_table_counts_guesses_abstentions_and_lucky_hits():
    def entry(named, truth=False):
        return {"named": named, "names_truth": truth, "names_decoy": False, "steps": []}

    table = agg.twin_table(
        [
            {"twins": {"a": entry(True, True), "b": entry(False)}},
            {"twins": {"a": entry(True), "b": entry(False)}},
            {"twins": {}},
        ]
    )
    assert table["cases"] == 2
    assert table["methods"]["a"]["unsupported"].successes == 2
    assert table["methods"]["a"]["named_truth"].successes == 1
    assert table["methods"]["b"]["abstained"].successes == 2
