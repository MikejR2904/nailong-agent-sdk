from __future__ import annotations

import json

from tests.conftest import call, final, run_cli


def events(out: str) -> list[dict]:
    return [json.loads(line) for line in out.splitlines() if line.startswith("{")]


def test_claude_stream_json_uses_the_system_assistant_user_result_shapes(project, home):
    (project / "a.txt").write_text("hello\n", encoding="utf-8")
    code, out, _, _ = run_cli(
        project,
        home,
        ["-p", "x", "--output", "claude-stream-json"],
        call("read_file", path="a.txt"),
        final("all done"),
    )
    assert code == 0
    found = events(out)
    assert [item["type"] for item in found] == [
        "system",
        "assistant",
        "user",
        "assistant",
        "result",
    ]
    init, use, answer, text, result = found
    assert init["subtype"] == "init" and init["session_id"] and init["model"] == "test-model"
    block = use["message"]["content"][0]
    assert block["type"] == "tool_use" and block["name"] == "read_file" and block["id"]
    reply = answer["message"]["content"][0]
    assert reply["type"] == "tool_result" and reply["tool_use_id"] == block["id"]
    assert reply["is_error"] is False
    assert text["message"]["content"][0] == {"type": "text", "text": "all done"}
    assert result["subtype"] == "success" and result["is_error"] is False
    assert result["result"] == "all done" and result["num_turns"] == 2
    assert result["session_id"] == init["session_id"] and "duration_ms" in result
    assert set(result["usage"]) == {"input_tokens", "cache_read_input_tokens", "output_tokens"}


def test_a_failed_tool_is_marked_as_an_error_result(project, home):
    _, out, _, _ = run_cli(
        project,
        home,
        ["-p", "x", "--output", "claude-stream-json"],
        call("read_file", path="missing.txt"),
        final("ok"),
    )
    reply = next(item for item in events(out) if item["type"] == "user")
    assert reply["message"]["content"][0]["is_error"] is True


def test_a_run_that_hits_the_turn_limit_reports_error_max_turns(project, home):
    code, out, _, _ = run_cli(
        project,
        home,
        ["-p", "x", "--output", "claude-stream-json", "--max-turns", "1"],
        call("glob", pattern="*"),
        call("glob", pattern="*"),
        final("late"),
    )
    result = events(out)[-1]
    assert code == 1 and result["subtype"] == "error_max_turns" and result["is_error"] is True


def test_the_init_event_lists_the_tools_the_agent_has(project, home):
    _, out, _, _ = run_cli(
        project, home, ["-p", "x", "--output", "claude-stream-json"], final("ok")
    )
    init = events(out)[0]
    assert "read_file" in init["tools"] and init["tools"] == sorted(init["tools"])


def user_line(text: str) -> str:
    return json.dumps({"type": "user", "message": {"role": "user", "content": text}}) + "\n"


def test_messages_can_be_fed_one_per_line_and_each_gets_its_own_result(project, home):
    lines = user_line("first") + user_line("second")
    code, out, _, setup = run_cli(
        project,
        home,
        ["-p", "--input-format", "stream-json", "--output", "claude-stream-json"],
        final("one"),
        final("two"),
        lines=lines,
    )
    assert code == 0
    results = [item for item in events(out) if item["type"] == "result"]
    assert len(results) == 2 and all(item["subtype"] == "success" for item in results)
    sent = [json.dumps(call.prompt.model_dump()) for call in setup.model.calls]
    assert len(sent) == 2 and "first" in sent[0] and "second" in sent[1]


def test_an_input_line_that_is_not_a_user_message_is_named(project, home):
    code, _, err, _ = run_cli(
        project, home, ["-p", "--input-format", "stream-json"], final("x"), lines='{"type": "x"}\n'
    )
    assert code == 64 and "Input line 1 needs" in err
