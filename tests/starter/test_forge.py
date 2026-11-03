import asyncio

import pytest

from nailong_agent_sdk import ScriptedModel
from nailong_agent_sdk.foundations.errors import AgentSdkError
from nailong_agent_sdk.starter import Agent, Forge, ModelSetup

ADD_SOURCE = "def run(a, b):\n    return a + b\n"
ADD_SCHEMA = {
    "type": "object",
    "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}},
    "required": ["a", "b"],
    "additionalProperties": False,
}
ADD_TESTS = [{"arguments": {"a": 1, "b": 2}, "expect": 3}]


def arun(coro):
    return asyncio.run(coro)


def approve_all(proposal):
    return True


def proposal_args(name="add", source=ADD_SOURCE, tests=None, schema=None, description="Add."):
    return {
        "name": name,
        "description": description,
        "input_schema": schema or ADD_SCHEMA,
        "source": source,
        "tests": tests if tests is not None else ADD_TESTS,
    }


def forge_in(tmp_path, **options):
    options.setdefault("reviewer", approve_all)
    options.setdefault("isolation", "native")
    return Forge(tmp_path / "forge", **options)


def test_an_approved_tool_passes_its_tests_and_becomes_callable(tmp_path):
    forge = forge_in(tmp_path)
    result = arun(forge.propose(**proposal_args()))
    assert (
        result["status"] == "active" and result["tool"] == "forged_add" and result["version"] == 1
    )
    assert arun(forge.call("forged_add", {"a": 20, "b": 22})) == 42
    names = [d.name for d in forge.definitions]
    assert names[:3] == ["propose_tool", "use_forged_tool", "list_forged_tools"]
    assert "forged_add" in names
    assert forge.listing()["active"][0]["name"] == "forged_add"
    assert forge.verify() == {"events": 2, "active_tools": 1}


def test_nothing_runs_until_the_reviewer_approves(tmp_path):
    ran = tmp_path / "ran.txt"
    source = f"open({str(ran)!r}, 'w').write('executed')\ndef run():\n    return 1\n"
    forge = forge_in(tmp_path, reviewer=None)
    pending = arun(
        forge.propose(
            **proposal_args(
                name="probe",
                source=source,
                schema={"type": "object"},
                tests=[{"arguments": {}, "expect": 1}],
            )
        )
    )
    assert pending["status"] == "pending" and not ran.exists()
    assert [p.id for p in forge.pending()] == [pending["proposal"]]
    approved = arun(forge.approve(pending["proposal"]))
    assert approved["status"] == "active" and ran.exists() and forge.pending() == []


def test_a_rejected_proposal_never_runs_and_keeps_the_reason(tmp_path):
    forge = forge_in(tmp_path, reviewer=lambda proposal: "it touches the network")
    result = arun(forge.propose(**proposal_args()))
    assert result["status"] == "rejected" and "network" in result["note"]
    assert forge.listing() == {"active": [], "pending": []}
    with pytest.raises(ValueError, match="already been decided"):
        forge.reject(result["proposal"], "again")


def test_the_reviewer_sees_the_source_diff_and_a_note_about_what_it_reaches_for(tmp_path):
    seen = []

    def reviewer(proposal):
        seen.append(proposal)
        return True

    forge = forge_in(tmp_path, reviewer=reviewer)
    risky = "import socket, os\n\ndef run(a, b):\n    return eval('a + b')\n"
    arun(forge.propose(**proposal_args(source=risky)))
    report = seen[0].report
    assert report["network_modules"] == ["socket"] and report["process_or_file_modules"] == ["os"]
    assert report["dynamic_calls"] == ["eval"] and "not a security boundary" in report["note"]
    assert seen[0].parent_version is None and "+def run(a, b):" in seen[0].diff


def test_a_failing_test_keeps_the_tool_inactive_and_says_which_test(tmp_path):
    forge = forge_in(tmp_path)
    wrong = "def run(a, b):\n    return a - b\n"
    result = arun(forge.propose(**proposal_args(source=wrong)))
    assert result["status"] == "failed-tests"
    assert result["failures"] == [{"test": 0, "expected": 3, "got": -1}]
    assert forge.listing()["active"] == []


def test_code_that_raises_or_hangs_fails_its_test_with_the_reason(tmp_path):
    forge = forge_in(tmp_path, timeout_seconds=1.0)
    boom = arun(
        forge.propose(**proposal_args(source="def run(a, b):\n    raise KeyError('boom')\n"))
    )
    assert "KeyError" in boom["failures"][0]["error"] and "boom" in boom["failures"][0]["error"]
    hang = arun(
        forge.propose(
            **proposal_args(name="hang", source="def run(a, b):\n    while True:\n        pass\n")
        )
    )
    assert "timed-out" in hang["failures"][0]["error"]


def test_a_non_json_result_fails_the_test(tmp_path):
    forge = forge_in(tmp_path)
    result = arun(forge.propose(**proposal_args(source="def run(a, b):\n    return {1, 2}\n")))
    assert result["status"] == "failed-tests"


@pytest.mark.parametrize(
    ("changes", "fragment"),
    [
        ({"name": "Bad-Name"}, "must be 3 to 41 characters"),
        ({"description": " "}, "description must be"),
        ({"source": "def run(:\n"}, "source does not parse: line 1"),
        ({"source": "x = 1\n"}, "top-level function run"),
        ({"tests": []}, "at least 1 test"),
        ({"tests": [{"arguments": {"a": "x", "b": 1}, "expect": 1}]}, "break input_schema"),
        ({"tests": [{"arguments": {"a": 1, "b": 1}}]}, 'with "arguments" and "expect"'),
        ({"schema": {"type": "string"}}, "must describe an object"),
        ({"schema": {"type": 5}}, "not a valid JSON Schema"),
        ({"source": "# " + "x" * 20_001}, "the limit is 20000"),
    ],
)
def test_a_malformed_proposal_is_refused_before_anything_is_stored(tmp_path, changes, fragment):
    forge = forge_in(tmp_path)
    arguments = proposal_args()
    if "schema" in changes:
        arguments["input_schema"] = changes.pop("schema")
    arguments.update(changes)
    with pytest.raises(ValueError, match=fragment):
        arun(forge.propose(**arguments))
    assert forge.pending() == [] and not (tmp_path / "forge" / "proposals").exists()


def test_a_modified_tool_gets_a_new_version_a_diff_and_can_be_rolled_back(tmp_path):
    seen = []

    def reviewer(proposal):
        seen.append(proposal)
        return True

    forge = forge_in(tmp_path, reviewer=reviewer)
    arun(forge.propose(**proposal_args()))
    doubled = "def run(a, b):\n    return (a + b) * 2\n"
    second = arun(
        forge.propose(
            **proposal_args(source=doubled, tests=[{"arguments": {"a": 1, "b": 2}, "expect": 6}])
        )
    )
    assert second["version"] == 2 and seen[1].parent_version == 1
    assert "-    return a + b" in seen[1].diff and "+    return (a + b) * 2" in seen[1].diff
    assert arun(forge.call("forged_add", {"a": 1, "b": 2})) == 6
    forge.rollback("forged_add", 1)
    assert arun(forge.call("forged_add", {"a": 1, "b": 2})) == 3
    with pytest.raises(ValueError, match="never active at version 9"):
        forge.rollback("forged_add", 9)
    forge.retire("forged_add")
    assert forge.listing()["active"] == []
    with pytest.raises(ValueError, match="No active forged tool"):
        arun(forge.call("forged_add", {"a": 1, "b": 2}))


def test_a_changed_source_file_is_refused_at_call_time_and_by_verify(tmp_path):
    forge = forge_in(tmp_path)
    arun(forge.propose(**proposal_args()))
    source = tmp_path / "forge" / "active" / "forged_add_v1.py"
    source.write_text("def run(a, b):\n    return 'tampered'\n", encoding="utf-8")
    with pytest.raises(AgentSdkError) as call_error:
        arun(forge.call("forged_add", {"a": 1, "b": 2}))
    assert call_error.value.code == "FORGE_SOURCE_CHANGED"
    with pytest.raises(AgentSdkError) as verify_error:
        forge.verify()
    assert verify_error.value.code == "FORGE_SOURCE_CHANGED"


def test_an_edited_event_log_is_detected(tmp_path):
    forge = forge_in(tmp_path)
    arun(forge.propose(**proposal_args()))
    log = tmp_path / "forge" / "events.jsonl"
    log.write_text(
        log.read_text(encoding="utf-8").replace("forged_add", "forged_evil"), encoding="utf-8"
    )
    with pytest.raises(AgentSdkError) as raised:
        forge.verify()
    assert raised.value.code == "CHAIN_LOG_BROKEN" and "The forge event log" in str(raised.value)


def test_calls_are_checked_against_the_schema_and_the_proposal_queue_is_bounded(tmp_path):
    forge = forge_in(tmp_path)
    arun(forge.propose(**proposal_args()))
    with pytest.raises(ValueError, match="do not match its schema"):
        arun(forge.call("forged_add", {"a": "x", "b": 1}))
    queue = forge_in(tmp_path / "q", reviewer=None, max_pending=2)
    arun(queue.propose(**proposal_args(name="one")))
    arun(queue.propose(**proposal_args(name="two")))
    with pytest.raises(ValueError, match="2 proposals are already waiting"):
        arun(queue.propose(**proposal_args(name="three")))
    capped = forge_in(tmp_path / "c", max_tools=1)
    arun(capped.propose(**proposal_args(name="one")))
    with pytest.raises(ValueError, match="already holds 1 active tools"):
        arun(capped.propose(**proposal_args(name="two")))


def test_an_agent_proposes_uses_and_then_natively_lists_its_own_tool(tmp_path):
    forge = forge_in(tmp_path)

    def call(tool_name, **arguments):
        return {
            "type": "tool-call",
            "call": {"id": f"c-{tool_name}", "name": tool_name, "arguments": arguments},
        }

    def final(answer):
        return {"type": "final", "output": {"status": "complete", "answer": answer}}

    first = ModelSetup(
        ScriptedModel(
            [
                call("propose_tool", **proposal_args()),
                call("use_forged_tool", name="forged_add", arguments={"a": 2, "b": 3}),
                final("5"),
            ]
        ),
        "scripted",
        "scripted-1",
    )
    with Agent(model=first, instructions="x", tools=[forge], workspace=tmp_path / "w1") as agent:
        before = [d.name for d in agent.definition.tools]
        assert agent.run_sync("Build an adder and use it.").ok
        after = [d.name for d in agent.definition.tools]
    assert "forged_add" not in before and "forged_add" in after
    seen = str(list(first.model.calls[-1].observations))
    assert "'status': 'active'" in seen or '"status": "active"' in seen

    second = ModelSetup(
        ScriptedModel([call("forged_add", a=10, b=5), final("15")]), "scripted", "scripted-1"
    )
    with Agent(model=second, instructions="x", tools=[forge], workspace=tmp_path / "w2") as agent:
        assert agent.run_sync("Add 10 and 5.").ok
    assert "15" in str(list(second.model.calls[-1].observations))


def test_a_bad_proposal_from_an_agent_is_a_failed_result_not_a_crash(tmp_path):
    forge = forge_in(tmp_path)
    result = arun(forge.run("propose_tool", proposal_args(source="def run(:\n")))
    assert result.status == "failed" and "source does not parse" in result.error
    assert arun(forge.run("use_forged_tool", {"name": "forged_missing"})).status == "failed"
