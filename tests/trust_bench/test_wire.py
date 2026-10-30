import json

import pytest
from trust_bench.recorder import BoundaryRecorder, WorkspaceWatcher
from trust_bench.schema import Kind, digest_of
from trust_bench.wire import (
    Decision,
    PolicyEndpoint,
    SdkTransport,
    ToolCallSpec,
    decision_payload,
    normalize_path,
    parse_envelope,
    sdk_body,
    tool_envelope,
)


class Scripted:
    def __init__(self, decisions):
        self._decisions = list(decisions)
        self.seen = []

    def decide(self, observed):
        self.seen.append(list(observed))
        return self._decisions.pop(0)


def call(call_id, name, **arguments):
    return ToolCallSpec(call_id, name, arguments)


def endpoint_for(tmp_path, decisions, *, watch=False):
    recorder = BoundaryRecorder(tmp_path / "truth.jsonl")
    watcher = WorkspaceWatcher(tmp_path / "ws") if watch else None
    policy = Scripted(decisions)
    return PolicyEndpoint(policy, recorder, "agent-1", watcher), recorder, policy


def test_a_decision_is_either_calls_or_a_final_output():
    with pytest.raises(ValueError, match="either tool calls or a final output"):
        Decision()
    with pytest.raises(ValueError, match="either tool calls or a final output"):
        Decision(calls=(call("c1", "glob"),), final={"status": "complete"})


def test_envelopes_carry_the_status_the_sdk_tools_report():
    ok = tool_envelope({"x": 1})
    assert ok["status"] == "succeeded" and ok["output"] == {"x": 1} and ok["error"] is None
    failed = tool_envelope(error="boom", tool="read_file")
    assert failed["status"] == "failed" and failed["failure"]["details"]["tool"] == "read_file"


def test_an_envelope_that_is_not_one_is_wrapped_as_unparsed():
    assert parse_envelope(json.dumps(tool_envelope({"x": 1})))["status"] == "succeeded"
    assert parse_envelope("not json")["status"] == "unparsed"
    assert parse_envelope('{"no":"status"}')["status"] == "unparsed"


def test_windows_separators_are_normalised():
    assert normalize_path("work\\clean.csv") == "work/clean.csv"


def test_the_endpoint_records_decisions_calls_and_results_in_order(tmp_path):
    first = Decision(calls=(call("c1", "glob", pattern="*"),))
    second = Decision(final={"status": "complete"})
    endpoint, recorder, policy = endpoint_for(tmp_path, [first, second])
    endpoint.respond([])
    result = json.dumps(tool_envelope({"matches": []}))
    endpoint.respond([("c1", result)])
    kinds = [event.kind for event in recorder.events]
    assert kinds == [Kind.MODEL_CALL, Kind.TOOL_CALL, Kind.TOOL_RESULT, Kind.MODEL_CALL]
    model_0, tool_call, tool_result, model_1 = recorder.events
    assert (model_0.model_index, model_1.model_index) == (0, 1)
    assert tool_call.call_id == tool_result.call_id == "c1" and tool_call.model_index == 0
    assert tool_result.status == "succeeded"
    assert tool_result.digest == digest_of(json.loads(result))
    assert [o.name for o in policy.seen[1]] == ["glob"]


def test_a_tool_message_is_recorded_once_even_if_it_is_resent(tmp_path):
    decisions = [
        Decision(calls=(call("c1", "glob"),)),
        Decision(calls=(call("c2", "glob"),)),
        Decision(final={"status": "complete"}),
    ]
    endpoint, recorder, _ = endpoint_for(tmp_path, decisions)
    result = json.dumps(tool_envelope({}))
    endpoint.respond([])
    endpoint.respond([("c1", result)])
    endpoint.respond([("c1", result), ("c2", result)])
    results = [event for event in recorder.events if event.kind is Kind.TOOL_RESULT]
    assert [event.call_id for event in results] == ["c1", "c2"]


def test_a_tool_message_for_a_call_that_was_never_issued_is_ignored(tmp_path):
    endpoint, recorder, _ = endpoint_for(tmp_path, [Decision(final={"status": "complete"})])
    endpoint.respond([("ghost", json.dumps(tool_envelope({})))])
    assert [event.kind for event in recorder.events] == [Kind.MODEL_CALL]


def test_writes_are_recorded_against_the_call_that_wrote_the_path(tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    decisions = [
        Decision(calls=(call("c1", "write_draft", path="out/a.json", content="{}"),)),
        Decision(final={"status": "complete"}),
    ]
    endpoint, recorder, _ = endpoint_for(tmp_path, decisions, watch=True)
    endpoint.respond([])
    (workspace / "out").mkdir()
    (workspace / "out" / "a.json").write_text("{}", encoding="utf-8")
    endpoint.respond([("c1", json.dumps(tool_envelope({})))])
    (write,) = [event for event in recorder.events if event.kind is Kind.WRITE]
    assert write.path == "out/a.json" and write.call_id == "c1" and write.agent_id == "agent-1"


def test_a_write_by_no_known_call_has_no_writer(tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    endpoint, recorder, _ = endpoint_for(
        tmp_path, [Decision(final={"status": "complete"})], watch=True
    )
    (workspace / "stray.txt").write_text("x", encoding="utf-8")
    endpoint.respond([])
    (write,) = [event for event in recorder.events if event.kind is Kind.WRITE]
    assert write.call_id is None


def test_the_sdk_wire_body_for_tool_calls_and_for_a_final_answer():
    calls = sdk_body(Decision(calls=(call("c1", "read_file", path="a.txt"),)))
    message = calls["choices"][0]["message"]
    assert calls["choices"][0]["finish_reason"] == "tool_calls"
    assert message["tool_calls"][0]["id"] == "c1"
    assert json.loads(message["tool_calls"][0]["function"]["arguments"]) == {"path": "a.txt"}
    final = sdk_body(Decision(final={"status": "complete"}))
    assert final["choices"][0]["finish_reason"] == "stop"
    assert json.loads(final["choices"][0]["message"]["content"]) == {
        "type": "final",
        "output": {"status": "complete"},
    }


def test_the_transport_hands_only_tool_messages_to_the_endpoint(tmp_path):
    decisions = [Decision(calls=(call("c1", "glob"),)), Decision(final={"status": "complete"})]
    endpoint, recorder, _ = endpoint_for(tmp_path, decisions)
    transport = SdkTransport(endpoint)
    transport.post_json("http://x", headers={}, payload={"messages": []}, timeout_seconds=1)
    body = transport.post_json(
        "http://x",
        headers={},
        payload={
            "messages": [
                {"role": "system", "content": "s"},
                {"role": "tool", "tool_call_id": "c1", "content": json.dumps(tool_envelope({}))},
            ]
        },
        timeout_seconds=1,
    )
    assert body["choices"][0]["finish_reason"] == "stop"
    assert any(e.kind is Kind.TOOL_RESULT and e.call_id == "c1" for e in recorder.events)


def test_decision_payloads_do_not_depend_on_where_they_came_from():
    spec = call("c1", "glob", pattern="*")
    assert decision_payload([spec.as_dict()], None) == decision_payload(
        [{"id": "c1", "name": "glob", "arguments": {"pattern": "*"}}], None
    )
