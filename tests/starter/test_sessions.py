import json

import pytest

from nailong_agent_sdk import ScriptedModel
from nailong_agent_sdk.foundations.errors import AgentSdkError
from nailong_agent_sdk.starter import Agent, ModelSetup, tool


def final(answer):
    return {"type": "final", "output": {"status": "complete", "answer": answer}}


def setup_with(*answers):
    return ModelSetup(ScriptedModel([final(a) for a in answers]), "scripted", "scripted-1")


def agent_in(workspace, setup, *tools):
    return Agent(model=setup, instructions="Answer.", tools=tools, workspace=workspace)


def test_a_session_remembers_earlier_turns_for_the_next_one(tmp_path):
    setup = setup_with("first reply", "second reply")
    with agent_in(tmp_path, setup) as agent:
        session = agent.session("plan")
        session.run_sync("Draft the plan.")
        session.run_sync("Tighten it.")
    first, second = setup.model.calls
    assert "session_history" not in first.task.input
    history = second.task.input["session_history"]
    assert [t["prompt"] for t in history["turns"]] == ["Draft the plan."]
    assert history["turns"][0]["answer"] == "first reply" and history["omitted_turns"] == 0
    assert second.task.scope.boundaries == {"project_id": "plan"}


def test_a_session_resumes_in_a_new_process_from_disk(tmp_path):
    with agent_in(tmp_path, setup_with("one")) as agent:
        agent.session("work").run_sync("Start.")
    later = setup_with("two")
    with agent_in(tmp_path, later) as agent:
        assert agent.sessions() == ["work"]
        resumed = agent.resume("work")
        assert [turn["prompt"] for turn in resumed.turns] == ["Start."]
        resumed.run_sync("Continue.")
        assert len(resumed.turns) == 2
    assert later.model.calls[0].task.input["session_history"]["turns"][0]["answer"] == "one"


def test_a_fork_starts_from_a_turn_and_leaves_the_original_alone(tmp_path):
    setup = setup_with("a", "b", "c")
    with agent_in(tmp_path, setup) as agent:
        main = agent.session("main")
        main.run_sync("Step 1.")
        main.run_sync("Step 2.")
        branch = main.fork("experiment", at_turn=1)
        branch.run_sync("Try another way.")
        assert len(main.turns) == 2 and len(branch.turns) == 2
        assert branch.forked_from["session"] == "main" and branch.forked_from["turn"] == 1
        assert branch.forked_from["hash"] == main.turns[0]["hash"]
        assert [t["prompt"] for t in branch.turns] == ["Step 1.", "Try another way."]
    seen_by_branch = setup.model.calls[2].task.input["session_history"]["turns"]
    assert [t["prompt"] for t in seen_by_branch] == ["Step 1."]


def test_fork_without_a_name_makes_a_distinct_id_and_keeps_all_turns(tmp_path):
    with agent_in(tmp_path, setup_with("a")) as agent:
        main = agent.session("main")
        main.run_sync("Only turn.")
        copy = main.fork()
        assert copy.id.startswith("main-fork-") and len(copy.turns) == 1
        assert copy.verify() == 1 and main.verify() == 1


def test_sessions_refuse_unknown_duplicate_and_unsafe_ids_by_name(tmp_path):
    with agent_in(tmp_path, setup_with("a")) as agent:
        agent.session("taken")
        with pytest.raises(AgentSdkError, match='Session "taken" already exists') as exists:
            agent.session("taken")
        assert exists.value.code == "SESSION_EXISTS"
        with pytest.raises(AgentSdkError) as missing:
            agent.resume("nope")
        assert missing.value.code == "SESSION_NOT_FOUND" and '"nope"' in str(missing.value)
        with pytest.raises(ValueError, match="must be 1 to 64 characters"):
            agent.session("../escape")
        with pytest.raises(AgentSdkError) as bad_turn:
            agent.resume("taken").fork(at_turn=3)
        assert bad_turn.value.code == "SESSION_TURN_UNKNOWN"
        assert "has 0 turn(s)" in str(bad_turn.value)


def test_editing_a_session_file_is_caught_when_it_is_resumed(tmp_path):
    with agent_in(tmp_path, setup_with("honest")) as agent:
        agent.session("audited").run_sync("Question.")
    path = tmp_path / ".agent-sessions" / "audited.jsonl"
    path.write_text(path.read_text(encoding="utf-8").replace("honest", "forged"), encoding="utf-8")
    with agent_in(tmp_path, setup_with("x")) as agent:
        with pytest.raises(AgentSdkError) as raised:
            agent.resume("audited")
    assert raised.value.code == "CHAIN_LOG_BROKEN"
    assert 'Session "audited" fails verification at line 2' in str(raised.value)


def test_a_truncated_session_file_is_caught(tmp_path):
    with agent_in(tmp_path, setup_with("a", "b")) as agent:
        session = agent.session("cut")
        session.run_sync("One.")
        session.run_sync("Two.")
    path = tmp_path / ".agent-sessions" / "cut.jsonl"
    lines = path.read_text(encoding="utf-8").splitlines()
    path.write_text("\n".join([lines[0], lines[2]]) + "\n", encoding="utf-8")
    with agent_in(tmp_path, setup_with("x")) as agent, pytest.raises(AgentSdkError) as raised:
        agent.resume("cut")
    assert raised.value.code == "CHAIN_LOG_BROKEN" and "does not follow" in str(raised.value)


def test_history_is_bounded_and_says_how_much_was_left_out(tmp_path):
    setup = setup_with(*[f"answer number {i} " + "x" * 300 for i in range(6)], "last")
    with agent_in(tmp_path, setup) as agent:
        session = agent.session("long", history_chars=900)
        for index in range(6):
            session.run_sync(f"Question {index}.")
        digest = session.history()
        session.run_sync("Final question.")
    assert digest["omitted_turns"] >= 3 and 1 <= len(digest["turns"]) <= 3
    assert digest["turns"][-1]["prompt"] == "Question 5."
    assert len(json.dumps(digest["turns"])) <= 900 + 600
    with pytest.raises(ValueError, match="at least 1"):
        with agent_in(tmp_path / "b", setup_with("a")) as agent:
            agent.session("bad", history_turns=0)


def test_failed_turns_are_recorded_with_their_status_and_tools(tmp_path):
    @tool
    def add(a: int, b: int = 1) -> int:
        """Add."""
        return a + b

    call = {"type": "tool-call", "call": {"id": "c", "name": "add", "arguments": {"a": 1}}}
    setup = ModelSetup(ScriptedModel([call, call]), "scripted", "scripted-1")
    with Agent(
        model=setup, instructions="x", tools=[add], workspace=tmp_path, max_steps=2
    ) as agent:
        session = agent.session("failing")
        answer = session.run_sync("Loop.")
        turn = session.turns[0]
    assert not answer.ok and turn["status"] == answer.status
    assert turn["tools"] and "add" in turn["tools"][0]


def test_the_latest_session_is_the_one_written_last(tmp_path):
    with agent_in(tmp_path, setup_with("a", "b")) as agent:
        assert agent.latest_session() is None
        agent.session("older").run_sync("One.")
        agent.session("newer").run_sync("Two.")
        assert agent.latest_session() == "newer"
        agent.resume("older").run_sync("Back again.")
        assert agent.latest_session() == "older"


def test_a_session_can_be_compacted_and_the_model_then_sees_the_summary(tmp_path):
    setup = setup_with("a1", "a2", "a3", "a4")
    with agent_in(tmp_path, setup) as agent:
        session = agent.session("long")
        for index in range(3):
            session.run_sync(f"Question {index}.")
        assert session.compact("We covered questions 0 to 2.") == 3
        assert session.uncompacted_turns() == []
        session.run_sync("Question 3.")
        assert [t["number"] for t in session.uncompacted_turns()] == [4]
    digest = setup.model.calls[3].task.input["session_history"]
    assert digest["summary"] == {"covers_turns": 3, "text": "We covered questions 0 to 2."}
    assert digest["turns"] == [] and digest["omitted_turns"] == 0


def test_a_summary_only_covers_turns_that_exist_and_needs_text(tmp_path):
    with agent_in(tmp_path, setup_with("a")) as agent:
        session = agent.session("short")
        session.run_sync("One.")
        with pytest.raises(ValueError, match="cannot cover turn 5"):
            session.compact("x", upto=5)
        with pytest.raises(ValueError, match="needs text"):
            session.compact("  ")


def test_a_fork_keeps_a_summary_only_when_the_fork_point_includes_what_it_covers(tmp_path):
    with agent_in(tmp_path, setup_with("a", "b", "c")) as agent:
        main = agent.session("main")
        for index in range(3):
            main.run_sync(f"Q{index}.")
        main.compact("first two", upto=2)
        early = main.fork("early", at_turn=1)
        late = main.fork("late", at_turn=3)
        assert early.summary is None and late.summary["text"] == "first two"
        assert late.verify() == 3 and main.verify() == 3
