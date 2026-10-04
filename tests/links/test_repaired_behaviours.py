import json
import sqlite3
import threading
import time

import pytest

from nailong_agent_sdk.agent.model import FailoverAgentModel
from nailong_agent_sdk.agent.openai_compatible.transport import (
    HttpxJsonTransport,
    UrlLibJsonTransport,
)
from nailong_agent_sdk.agent.orchestrator import OrchestrationStatus, Orchestrator
from nailong_agent_sdk.agent.runtime import AgentRuntimeServices
from nailong_agent_sdk.foundations.contracts import AgentRunStatus, ToolCall, ToolExecutionResult
from nailong_agent_sdk.foundations.errors import AgentSdkError, TransientProviderError
from nailong_agent_sdk.memory.context_projection import FileToolResultJournal
from nailong_agent_sdk.observability.audit_log import AuditTranscriptStore
from nailong_agent_sdk.observability.telemetry_models import (
    TelemetryActor,
    TelemetryAuthority,
    TelemetryContext,
)
from nailong_agent_sdk.observability.telemetry_store import TelemetryStore
from nailong_agent_sdk.state.controller_runtime import ControllerRuntime
from nailong_agent_sdk.state.graph import StateGraph
from nailong_agent_sdk.state.graph_models import GraphNode, GraphNodeKind
from nailong_agent_sdk.state.planning import PlanValidator
from tests.support.agents import (
    FnExecutor,
    arun,
    definition,
    ok,
    task,
    tool,
)
from tests.support.controllers import new_controller
from tests.support.model_context import FAKE_KEY
from tests.support.orchestration import close_all, make_factory, make_policy, make_request
from tests.support.plans import chain_plan
from tests.support.processes import run_workers


def test_controller_ids_are_not_reused_after_a_restart(tmp_path):
    first = new_controller(ControllerRuntime(tmp_path))
    second_runtime = ControllerRuntime(tmp_path)
    second = new_controller(second_runtime)
    assert first.controller_id != second.controller_id
    assert second_runtime.get_controller(first.controller_id).controller_id == first.controller_id


def test_controller_ids_from_two_simultaneous_processes_do_not_collide(tmp_path):
    script = f"""
        import sys, json, time
        from pathlib import Path
        from nailong_agent_sdk.state.controller_runtime import ControllerRuntime
        from tests.support.controllers import new_controller
        runtime = ControllerRuntime(Path(r"{tmp_path}"))
        deadline = float(sys.argv[2])
        while time.time() < deadline:
            time.sleep(0.0005)
        out = []
        for _ in range(20):
            try:
                out.append(["ok", new_controller(runtime).controller_id])
            except Exception as error:
                out.append(["err", type(error).__name__ + ": " + str(error)[:100]])
        print(json.dumps(out))
    """
    start_at = str(time.time() + 6)
    outputs = run_workers(script, 2, start_at)
    results = [json.loads(out) for code, out, err in outputs if code == 0]
    assert len(results) == 2, [err[-300:] for code, out, err in outputs]
    ids = [entry[1] for result in results for entry in result if entry[0] == "ok"]
    errors = [entry[1] for result in results for entry in result if entry[0] == "err"]
    duplicates = sorted({i for i in ids if ids.count(i) > 1})
    assert not duplicates and not errors, (
        f"{len(duplicates)} controller ids were issued to both processes; {len(errors)} errors"
    )


def test_journal_handles_are_unique_across_instances_threads_and_processes(tmp_path):
    journals = [FileToolResultJournal(tmp_path) for _ in range(2)]
    handles = []
    guard = threading.Lock()

    def worker(index):
        journal = journals[index % 2]
        for i in range(25):
            handle = journal.record(
                ToolCall(id=f"c{index}-{i}", name="t", arguments={}),
                ToolExecutionResult(status="succeeded", output={"who": index, "i": i}),
            )
            with guard:
                handles.append((handle.handle_id, index, i))

    threads = [threading.Thread(target=worker, args=(k,)) for k in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    ids = [h[0] for h in handles]
    assert len(ids) == len(set(ids)) == 100
    for handle_id, index, i in handles:
        assert journals[0].read(handle_id)["result"]["output"] == {"who": index, "i": i}

    script = f"""
        import sys, json, time
        from pathlib import Path
        from nailong_agent_sdk.foundations.contracts import ToolCall, ToolExecutionResult
        from nailong_agent_sdk.memory.context_projection import FileToolResultJournal
        journal = FileToolResultJournal(Path(r"{tmp_path}"))
        deadline = float(sys.argv[2])
        while time.time() < deadline:
            time.sleep(0.0005)
        who = int(sys.argv[1])
        out = []
        for i in range(40):
            call = ToolCall(id=f"p{{who}}-{{i}}", name="t", arguments={{}})
            result = ToolExecutionResult(status="succeeded", output={{"proc": who, "i": i}})
            handle = journal.record(call, result)
            out.append([handle.handle_id, who, i])
        print(json.dumps(out))
    """
    outputs = run_workers(script, 3, str(time.time() + 6))
    assert all(code == 0 for code, out, err in outputs), [err[-300:] for code, out, err in outputs]
    issued = [tuple(entry) for code, out, err in outputs for entry in json.loads(out)]
    issued_ids = [entry[0] for entry in issued]
    reader = FileToolResultJournal(tmp_path)
    overwritten = [
        entry
        for entry in issued
        if reader.read(entry[0])["result"]["output"] != {"proc": entry[1], "i": entry[2]}
    ]
    assert len(set(issued_ids)) == len(issued_ids) == 120 and not overwritten


def test_tool_call_turn_listing_dependencies_fails_with_the_field_named():
    from nailong_agent_sdk.agent.base_agent import BaseAgent

    class RawModel:
        async def next_turn(self, context):
            return {
                "type": "tool-call",
                "call": {"id": "c1", "name": "echo", "arguments": {}, "dependencies": ["x"]},
            }

    subject = BaseAgent(
        definition(tools=[tool("echo")]), RawModel(), tool_executor=FnExecutor(lambda t, c: ok({}))
    )
    result = arun(subject.run(task()))
    assert result.status is AgentRunStatus.FAILED
    assert "dependencies" in (result.reason or ""), result.reason


def test_invalid_host_binding_leaves_the_orchestration_approved_and_starts_no_run(
    tmp_path,
):
    orchestrator = Orchestrator(tmp_path, make_policy())
    services = AgentRuntimeServices.open(tmp_path)
    try:
        record = arun(orchestrator.prepare(make_request(blast=5)))
        orchestrator.submit_for_approval(record.orchestration_id)
        orchestrator.approve(record.orchestration_id, True)
        with pytest.raises(ValueError, match="identity does not equal"):
            arun(
                orchestrator.dispatch_and_execute(
                    record.orchestration_id, services, make_factory(identity_override="impostor")
                )
            )
        after = orchestrator.get(record.orchestration_id)
        runs = (
            sorted(p.name for p in (tmp_path / ".agent-runs").glob("*"))
            if (tmp_path / ".agent-runs").exists()
            else []
        )
        assert (
            after.status is OrchestrationStatus.APPROVED and after.graph_run_id is None and not runs
        )
        executed = arun(
            orchestrator.dispatch_and_execute(record.orchestration_id, services, make_factory()),
            timeout=300,
        )
        assert executed.status is OrchestrationStatus.EXECUTED
    finally:
        close_all(orchestrator, services)


@pytest.mark.parametrize("count", [1500, 5000])
def test_long_dependency_chains_validate_and_build_a_graph(count):
    plan = chain_plan(count)
    report = PlanValidator().validate(plan)
    graph = StateGraph(
        [
            GraphNode(
                node_id=f"n{i}", kind=GraphNodeKind.AGENT, dependencies=[f"n{i - 1}"] if i else []
            )
            for i in range(count)
        ]
    )
    runnable = [node.node_id for node in graph.runnable()]
    assert report.valid and runnable == ["n0"]


def test_failover_attempts_are_reported_per_call_for_sequential_calls():
    class Failing:
        def __init__(self, text):
            self.text = text

        async def next_turn(self, context):
            raise RuntimeError(self.text)

    model = FailoverAgentModel([Failing("first-adapter-down"), Failing("second-adapter-down")])
    seen = []
    for _ in range(3):
        with pytest.raises(AgentSdkError) as error:
            arun(model.next_turn(None))
        seen.append((str(error.value), len(error.value.details["attempts"])))
    assert [m[1] for m in seen] == [2, 2, 2]
    assert all("after 2 attempt(s) total" in m[0] and "second-adapter-down" in m[0] for m in seen)


def _tamper_audit(tmp_path, mutate):
    store = AuditTranscriptStore(tmp_path)
    for i in range(6):
        store.append("run-x", "evt", {"i": i})
    store.close()
    path = next((tmp_path / ".agent-audit-logs").glob("*.jsonl"))
    lines = path.read_text("utf-8").splitlines()
    path.write_text("\n".join(mutate(lines)) + "\n", "utf-8")
    verifier = AuditTranscriptStore(tmp_path)
    try:
        return verifier.chain_break("run-x")
    finally:
        verifier.close()


def test_audit_chain_break_names_the_first_bad_sequence_for_every_tamper_kind(tmp_path):
    def alter(lines):
        entry = json.loads(lines[2])
        entry["payload"]["i"] = 999
        lines[2] = json.dumps(entry)
        return lines

    def remove(lines):
        return lines[:2] + lines[3:]

    def reorder(lines):
        lines[2], lines[3] = lines[3], lines[2]
        return lines

    def corrupt(lines):
        lines[3] = lines[3][:20]
        return lines

    def truncate_tail(lines):
        return lines[:3]

    outcomes = {}
    for name, mutate in (
        ("alter", alter),
        ("remove", remove),
        ("reorder", reorder),
        ("corrupt", corrupt),
        ("truncate_tail", truncate_tail),
    ):
        failure = _tamper_audit(tmp_path / name, mutate)
        outcomes[name] = None if failure is None else (failure.sequence, failure.kind)
    assert outcomes["alter"] == (3, "content-hash-mismatch")
    assert outcomes["remove"] is not None and outcomes["remove"][0] == 4
    assert outcomes["reorder"] is not None and outcomes["reorder"][0] in (3, 4)
    assert outcomes["corrupt"] is not None and outcomes["corrupt"][1] == "unreadable-entry"
    assert outcomes["truncate_tail"] is None, (
        "informational: deleting the newest entries is not detected"
    )


def test_telemetry_chain_break_names_removed_and_reordered_events(tmp_path):
    actor = TelemetryActor(kind="agent", identifier="tester")

    def populate(path):
        store = TelemetryStore(path)
        for i in range(6):
            store.emit(
                "agent.test",
                TelemetryContext(run_id="run-x"),
                actor=actor,
                authority=TelemetryAuthority.DETERMINISTIC,
                status="ok",
                payload={"i": i},
            )
        store.close()
        return path / ".agent-telemetry" / "telemetry.sqlite3"

    outcomes = {}
    removed = populate(tmp_path / "removed")
    connection = sqlite3.connect(removed)
    connection.execute("DELETE FROM events WHERE run_id='run-x' AND sequence=3")
    connection.commit()
    connection.close()
    reordered = populate(tmp_path / "reordered")
    connection = sqlite3.connect(reordered)
    rows = connection.execute(
        "SELECT sequence, event_json FROM events "
        "WHERE run_id='run-x' AND sequence IN (3,4) ORDER BY sequence"
    ).fetchall()
    (s3, j3), (s4, j4) = rows
    connection.execute("UPDATE events SET event_json=? WHERE run_id='run-x' AND sequence=3", (j4,))
    connection.execute("UPDATE events SET event_json=? WHERE run_id='run-x' AND sequence=4", (j3,))
    connection.commit()
    connection.close()
    for name, base in (("removed", tmp_path / "removed"), ("reordered", tmp_path / "reordered")):
        store = TelemetryStore(base)
        try:
            failure = store.chain_break("run-x")
        finally:
            store.close()
        outcomes[name] = None if failure is None else (failure.sequence, failure.kind)
    assert outcomes["removed"] is not None and outcomes["reordered"] is not None


def test_unreachable_provider_errors_name_host_exception_root_cause_and_likely_cause():
    for label, url in (
        ("refused", "http://127.0.0.1:9/v1/chat/completions"),
        ("dns", "http://nonexistent-host-audit.invalid/v1/chat/completions"),
    ):
        for transport in (UrlLibJsonTransport(), HttpxJsonTransport()):
            with pytest.raises(TransientProviderError) as error:
                transport.post_json(
                    url,
                    headers={"Authorization": f"Bearer {FAKE_KEY}"},
                    payload={"x": 1},
                    timeout_seconds=6,
                )
            text = str(error.value)
            assert url.split("/")[2].split(":")[0] in text and "likely cause" in text, text
            assert FAKE_KEY not in text


def test_cycle_finder_handles_50000_and_200000_node_chains():
    from nailong_agent_sdk.foundations.dependency_graph import deterministic_cycles

    timings = {}
    for count in (50_000, 200_000):
        nodes = [f"n{i}" for i in range(count)]
        edges = [(f"n{i}", f"n{i - 1}") for i in range(1, count)]
        started = time.monotonic()
        assert deterministic_cycles(nodes, edges) == []
        ring = deterministic_cycles(nodes, [*edges, ("n0", f"n{count - 1}")])
        timings[count] = round(time.monotonic() - started, 2)
        assert len(ring) == 1 and len(ring[0]) == count + 1


def test_plan_validator_cost_is_quadratic_in_the_number_of_tasks():
    from nailong_agent_sdk.state.planning import PlanTask

    calls = {"count": 0}
    original = PlanTask.signal_ids

    def counting(self):
        calls["count"] += 1
        return original(self)

    PlanTask.signal_ids = counting
    try:
        measured = {}
        for count in (200, 400, 800):
            calls["count"] = 0
            report = PlanValidator().validate(chain_plan(count))
            measured[count] = calls["count"]
            assert report.valid
    finally:
        PlanTask.signal_ids = original
    assert measured[800] < 4 * measured[400] * 0.6, (
        f"signal_ids() calls grow quadratically: {measured}"
    )
