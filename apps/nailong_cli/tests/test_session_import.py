from __future__ import annotations

import json

import pytest

from nailong_cli.session_import import TranscriptError, read_transcript
from tests.conftest import final, run_cli


def write_lines(path, records):
    path.write_text("\n".join(json.dumps(item) for item in records) + "\n", encoding="utf-8")
    return path


def claude_file(tmp_path):
    return write_lines(
        tmp_path / "claude.jsonl",
        [
            {"type": "user", "message": {"role": "user", "content": "Fix the login bug"}},
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [
                        {"type": "thinking", "thinking": "hm"},
                        {"type": "tool_use", "name": "Read", "input": {"file_path": "a.py"}},
                        {"type": "text", "text": "Fixed it in a.py"},
                    ],
                },
            },
            {"type": "user", "message": {"role": "user", "content": [{"type": "tool_result"}]}},
            {"type": "user", "isMeta": True, "message": {"role": "user", "content": "meta"}},
            {"type": "user", "message": {"role": "user", "content": "Now add tests"}},
            {"type": "attachment"},
        ],
    )


def codex_file(tmp_path):
    def message(role, kind, text):
        return {
            "type": "response_item",
            "payload": {"type": "message", "role": role, "content": [{"type": kind, "text": text}]},
        }

    return write_lines(
        tmp_path / "codex.jsonl",
        [
            {"type": "session_meta", "payload": {}},
            message("user", "input_text", "<environment_context>cwd</environment_context>"),
            message("user", "input_text", "Explain the cache"),
            {
                "type": "response_item",
                "payload": {"type": "function_call", "name": "shell", "arguments": "{}"},
            },
            message("assistant", "output_text", "It is an LRU."),
        ],
    )


def test_a_claude_transcript_becomes_turns_with_answers_and_tools(tmp_path):
    source, turns = read_transcript(claude_file(tmp_path))
    assert source == "claude" and [t["prompt"] for t in turns] == [
        "Fix the login bug",
        "Now add tests",
    ]
    assert turns[0]["answer"] == "Fixed it in a.py" and turns[0]["tools"][0].startswith("Read ")
    assert turns[1]["answer"] == ""


def test_a_codex_rollout_skips_injected_context_and_keeps_the_real_prompt(tmp_path):
    source, turns = read_transcript(codex_file(tmp_path))
    assert source == "codex" and len(turns) == 1
    assert turns[0]["prompt"] == "Explain the cache" and turns[0]["answer"] == "It is an LRU."
    assert turns[0]["tools"] == ["shell {}"]


def test_unknown_and_broken_files_are_refused_by_name(tmp_path):
    other = write_lines(tmp_path / "x.jsonl", [{"type": "nothing"}])
    with pytest.raises(TranscriptError, match="not a Claude Code or Codex transcript"):
        read_transcript(other)
    (tmp_path / "bad.jsonl").write_text("{oops\n", encoding="utf-8")
    with pytest.raises(TranscriptError, match="line 1 is not JSON"):
        read_transcript(tmp_path / "bad.jsonl")


def test_the_command_imports_a_session_that_can_be_resumed_with_its_history(
    project, home, tmp_path
):
    path = claude_file(tmp_path)
    code, out, err, setup = run_cli(
        project,
        home,
        [],
        final("carrying on"),
        lines=f"/sessions import {path} old-work\n/resume old-work\nContinue\n/exit\n",
        interactive=True,
    )
    assert code == 0, err
    assert "imported 2 turn(s) from a claude transcript as old-work" in out
    history = str(setup.model.calls[0].task.input)
    assert "Fix the login bug" in history and "Fixed it in a.py" in history


def test_importing_over_an_existing_name_is_refused(project, home, tmp_path):
    path = claude_file(tmp_path)
    _, out, err, _ = run_cli(
        project,
        home,
        [],
        lines=f"/sessions import {path} same\n/sessions import {path} same\n/exit\n",
        interactive=True,
    )
    assert 'Session "same" already exists' in out + err
