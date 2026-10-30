import json
from dataclasses import replace

import pytest
from trust_bench.metrics.degradation import (
    CONDITIONS,
    cut_payloads,
    drop_events,
    strip_actors,
    strip_writer_links,
)
from trust_bench.metrics.sufficiency import sufficiency_rates
from trust_bench.metrics.traceability import resolve, score_events, score_resolution, score_run
from trust_bench.schema import (
    ArtifactTruth,
    ArtifactVersion,
    Event,
    Kind,
    RunTruth,
    SystemRecords,
    canonical,
    digest_bytes,
    digest_of,
)

AGENT = "agent"
BODY = b'{"total": 3}'


def call_event(call_id, index, name, arguments):
    payload = {"id": call_id, "name": name, "arguments": arguments}
    return Event(
        Kind.TOOL_CALL,
        agent_id=AGENT,
        model_index=index,
        call_id=call_id,
        name=name,
        digest=digest_of(payload),
        content=canonical(payload),
    )


def result_event(call_id, name, output=None):
    envelope = {"error": None, "failure": None, "output": output, "status": "succeeded"}
    return Event(
        Kind.TOOL_RESULT,
        agent_id=AGENT,
        call_id=call_id,
        name=name,
        digest=digest_of(envelope),
        status="succeeded",
        content=canonical(envelope),
    )


def model_event(index, calls):
    payload = {"calls": calls, "final": None}
    return Event(
        Kind.MODEL_CALL,
        agent_id=AGENT,
        model_index=index,
        digest=digest_of(payload),
        content=canonical(payload),
    )


def truth_events():
    read = {"path": "inputs/rec-001.json"}
    write = {"path": "report.json", "content": BODY.decode()}
    return [
        model_event(0, [{"id": "c1", "name": "read_file", "arguments": read}]),
        call_event("c1", 0, "read_file", read),
        result_event("c1", "read_file", {"lines": []}),
        model_event(1, [{"id": "c2", "name": "write_draft", "arguments": write}]),
        call_event("c2", 1, "write_draft", write),
        result_event("c2", "write_draft", {"sha256": digest_bytes(BODY)}),
        Event(
            Kind.WRITE, agent_id=AGENT, call_id="c2", path="report.json", digest=digest_bytes(BODY)
        ),
    ]


def make_truth(sources=frozenset({"rec-001"})):
    artifact = ArtifactTruth("report.json", digest_bytes(BODY), "c2", AGENT, 1, sources)
    return RunTruth("case-1", truth_events(), {"report.json": artifact})


def faithful_records(events=None, *, versions=None, blobs=None):
    events = list(truth_events() if events is None else events)
    if versions is None:
        versions = {
            "report.json": [ArtifactVersion("report.json", digest_bytes(BODY), "c2", AGENT, 1)]
        }
    store = {digest_bytes(BODY): BODY} if blobs is None else blobs
    return SystemRecords("system", events, versions, store.get)


def test_a_faithful_record_is_complete_recoverable_and_resolves_the_artifact():
    traced = score_run(make_truth(), faithful_records())
    assert traced.calls_complete
    for kind, counts in traced.counts.items():
        assert counts.present == counts.total and counts.phantom == 0, kind
        assert counts.agree == counts.comparable == counts.total
        assert counts.recoverable == counts.total
    (outcome,) = traced.outcomes
    assert outcome.outcome == "correct" and outcome.decision_ok and outcome.actor_ok
    assert outcome.source_precision == outcome.source_recall == 1.0


def test_a_missing_event_lowers_completeness_and_flags_the_calls_as_incomplete():
    events = [e for e in truth_events() if e.key != ("C", "c1")]
    traced = score_run(make_truth(), faithful_records(events))
    assert traced.counts[Kind.TOOL_CALL].present == 1 and traced.counts[Kind.TOOL_CALL].total == 2
    assert not traced.calls_complete


def test_an_event_the_system_invented_is_counted_as_phantom():
    extra = call_event("c9", 5, "read_file", {"path": "x"})
    traced = score_run(make_truth(), faithful_records([*truth_events(), extra]))
    assert traced.counts[Kind.TOOL_CALL].phantom == 1
    assert traced.counts[Kind.TOOL_CALL].present == 2


def test_a_record_whose_content_differs_is_present_but_does_not_agree():
    events = truth_events()
    events[1] = replace(events[1], digest="not-the-same")
    counts = score_events(make_truth(), faithful_records(events))[Kind.TOOL_CALL]
    assert counts.present == 2 and counts.comparable == 2 and counts.agree == 1


def test_a_truncated_record_is_present_but_neither_comparable_nor_recoverable():
    events = truth_events()
    events[3] = replace(events[3], digest=None, content=None, truncated=True)
    counts = score_events(make_truth(), faithful_records(events))[Kind.MODEL_CALL]
    assert counts.present == 2 and counts.comparable == 1 and counts.recoverable == 1


def test_recoverability_of_a_write_needs_a_blob_whose_hash_matches():
    missing = score_events(make_truth(), faithful_records(blobs={}))[Kind.WRITE]
    assert missing.present == 1 and missing.recoverable == 0
    wrong = score_events(make_truth(), faithful_records(blobs={digest_bytes(BODY): b"other"}))
    assert wrong[Kind.WRITE].recoverable == 0


def test_resolution_follows_the_write_to_its_call_its_turn_and_the_files_it_read():
    resolved = resolve(faithful_records(), "report.json")
    assert not resolved.abstained
    assert (resolved.call_id, resolved.agent_id, resolved.model_index) == ("c2", AGENT, 1)
    assert resolved.sources == frozenset({"rec-001"})


def test_strict_resolution_abstains_without_a_writer_link_and_lenient_guesses_it():
    versions = {"report.json": [ArtifactVersion("report.json", digest_bytes(BODY), None, AGENT, 1)]}
    records = faithful_records(versions=versions)
    strict = resolve(records, "report.json")
    assert strict.abstained and "names no tool call" in str(strict.reason)
    lenient = resolve(records, "report.json", lenient=True)
    assert not lenient.abstained and lenient.call_id == "c2"


def test_strict_resolution_abstains_when_the_named_call_is_not_in_the_ledger():
    events = [e for e in truth_events() if e.key != ("C", "c2")]
    strict = resolve(faithful_records(events), "report.json")
    assert strict.abstained and 'tool call "c2"' in str(strict.reason)


def test_a_lenient_guess_can_name_the_wrong_decision():
    versions = {"report.json": [ArtifactVersion("report.json", digest_bytes(BODY), None, AGENT, 1)]}
    late = {"path": "report.json", "content": "later"}
    events = [*truth_events(), call_event("c3", 2, "write_draft", late)]
    (outcome,) = score_resolution(
        make_truth(), faithful_records(events, versions=versions), lenient=True
    )
    assert outcome.outcome == "wrong" and not outcome.decision_ok and outcome.actor_ok


def test_an_artifact_with_no_recorded_version_is_an_abstention():
    (outcome,) = score_resolution(make_truth(), faithful_records(versions={}))
    assert outcome.outcome == "abstain" and "no version" in str(outcome.reason)


def test_source_scores_separate_missing_reads_from_spurious_ones():
    truth = make_truth(sources=frozenset({"rec-001", "rec-002"}))
    (outcome,) = score_resolution(truth, faithful_records())
    assert outcome.source_precision == 1.0 and outcome.source_recall == 0.5
    extra = make_truth(sources=frozenset())
    (spurious,) = score_resolution(extra, faithful_records())
    assert spurious.source_precision == 0.0 and spurious.source_recall == 1.0


def test_dropping_events_removes_ledger_events_but_never_writes():
    records = faithful_records()
    dropped = drop_events(1.0)(records, "seed")
    assert [e.kind for e in dropped.events] == [Kind.WRITE]
    assert drop_events(0.0)(records, "seed").events == records.events


def test_dropping_is_deterministic_for_a_seed():
    records = faithful_records()
    first = drop_events(0.5)(records, "seed").events
    assert first == drop_events(0.5)(records, "seed").events


def test_each_degradation_removes_exactly_the_information_it_names():
    records = faithful_records()
    assert all(
        v.call_id is None for h in strip_writer_links(records, "s").versions.values() for v in h
    )
    stripped = strip_actors(records, "s")
    assert all(e.agent_id is None for e in stripped.events)
    assert all(v.agent_id is None for h in stripped.versions.values() for v in h)
    cut = cut_payloads(records, "s")
    ledger = [e for e in cut.events if e.kind is not Kind.WRITE]
    assert ledger and all(e.content is None and e.digest is None and e.truncated for e in ledger)
    assert set(CONDITIONS) == {"drop-5%", "drop-20%", "no-writer-link", "no-actor", "no-payloads"}


def test_a_strict_resolver_abstains_on_degraded_records_where_a_lenient_one_may_not():
    truth = make_truth()
    degraded = strip_writer_links(faithful_records(), "s")
    (strict,) = score_resolution(truth, degraded)
    (lenient,) = score_resolution(truth, degraded, lenient=True)
    assert strict.outcome == "abstain" and lenient.outcome == "correct"


def test_sufficiency_rates_follow_the_published_definitions():
    rates = sufficiency_rates([True, True, False, False], [True, False, False, True])
    assert rates.overclaim == 0.25 and rates.underclaim == 0.5 and rates.sufficient_claim == 0.5


def test_sufficiency_rates_refuse_unpaired_or_empty_input():
    with pytest.raises(ValueError, match="must pair up, got 2 and 1"):
        sufficiency_rates([True, False], [True])
    with pytest.raises(ValueError, match="needs at least one verdict"):
        sufficiency_rates([], [])


def test_a_digest_of_loaded_content_matches_the_digest_the_recorder_stores():
    event = truth_events()[1]
    assert digest_of(json.loads(str(event.content))) == event.digest
