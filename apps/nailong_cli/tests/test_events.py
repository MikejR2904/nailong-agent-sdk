from __future__ import annotations

import json
import re
import threading

import pytest

from nailong_cli import events as events_module
from nailong_cli.events import EventError, EventLog, check, read_events, recover


@pytest.fixture
def log(tmp_path):
    return EventLog(tmp_path / "events" / "run.jsonl", session_id="s1")


def test_nested_spans_carry_parent_trace_and_ids_and_the_log_is_clean(log):
    with log.span("session.start"):
        with log.span("turn.start", {}):
            with log.span("tool.start", {"tool": "read_file"}) as tool:
                log.emit("wiki.written", {"page": "a", "revision": 1})
    events = read_events(log.path)
    assert [e["event_type"] for e in events] == [
        "session.start",
        "turn.start",
        "tool.start",
        "wiki.written",
        "tool.end",
        "turn.end",
        "session.end",
    ]
    by_type = {e["event_type"]: e for e in events}
    assert by_type["turn.start"]["parent_event_id"] == by_type["session.start"]["event_id"]
    assert by_type["tool.start"]["parent_event_id"] == by_type["turn.start"]["event_id"]
    assert by_type["wiki.written"]["parent_event_id"] == by_type["tool.start"]["event_id"]
    assert len({e["trace_id"] for e in events}) == 1
    assert by_type["tool.end"]["span_id"] == by_type["tool.start"]["span_id"] == tool.span_id
    assert by_type["tool.end"]["parent_span_id"] == by_type["turn.start"]["span_id"]
    assert re.fullmatch(r"00-[0-9a-f]{32}-[0-9a-f]{16}-01", by_type["tool.start"]["traceparent"])
    assert check(events) == []


def test_an_error_inside_a_span_still_closes_it_with_the_error_named(log):
    with pytest.raises(RuntimeError):
        with log.span("turn.start"):
            raise RuntimeError("model fell over")
    end = read_events(log.path)[-1]
    assert end["event_type"] == "turn.end"
    assert end["payload"]["status"] == "error" and "model fell over" in end["payload"]["error"]


def test_unknown_types_and_missing_payload_are_refused_by_name(log):
    with pytest.raises(EventError, match="'nope' is not an event type"):
        log.emit("nope")
    with pytest.raises(EventError, match=r"tool.start needs \['tool'\]"):
        log.emit("tool.start", {})
    with pytest.raises(EventError, match="ends a span; pass the span_id"):
        log.emit("turn.end", {"status": "ok"})


def test_a_start_with_no_end_an_orphan_end_and_a_missing_parent_are_all_found(log):
    started = log.start("task.start", {"task": "t1"})
    log.emit("task.retry", {"task": "t1"})
    problems = check(read_events(log.path))
    assert any("started and never ended" in item for item in problems)
    assert check(read_events(log.path), running=True) == []
    log.end(started)
    events = read_events(log.path)
    assert check(events) == []
    orphan = [e for e in events if e["event_type"] != "task.start"]
    assert any("ends a span that never started" in item for item in check(orphan))
    lonely = {**events[1], "parent_event_id": "ev-ghost"}
    assert any("parent ev-ghost was never recorded" in item for item in check([lonely]))


def test_a_removed_or_edited_line_breaks_the_chain(log):
    for number in range(4):
        log.emit("discovery.published", {"id": f"d-{number}", "topic": "t"})
    lines = log.path.read_text(encoding="utf-8").splitlines()
    removed = [json.loads(line) for index, line in enumerate(lines) if index != 1]
    assert any("sequence jumps" in item for item in check(removed))
    assert any("chain broken" in item for item in check(removed))
    edited = [json.loads(line) for line in lines]
    edited[2]["payload"]["id"] = "d-forged"
    assert any("does not match its hash" in item for item in check(edited))


def test_a_log_reopened_by_a_new_process_continues_the_chain(tmp_path):
    first = EventLog(tmp_path / "e.jsonl")
    first.emit("memory.compacted", {"turns": 2})
    second = EventLog(tmp_path / "e.jsonl")
    second.emit("memory.compacted", {"turns": 3})
    assert check(read_events(tmp_path / "e.jsonl")) == []
    assert [e["sequence"] for e in read_events(tmp_path / "e.jsonl")] == [1, 2]


def test_recover_closes_what_a_dead_process_left_open_and_says_so(tmp_path):
    path = tmp_path / "e.jsonl"
    dead = EventLog(path, run_id="run-dead")
    dead.start("session.start")
    turn = dead.start("turn.start")
    dead.start("tool.start", {"tool": "run_command"}, parent_event_id=turn["event_id"])
    assert len(check(read_events(path))) == 3
    closed = recover(path)
    assert len(closed) == 3
    events = read_events(path)
    assert check(events) == []
    ends = [e for e in events if e["event_type"].endswith(".end")]
    assert {e["payload"]["status"] for e in ends} == {"interrupted"}
    assert sum(e["event_type"] == "recovery.closed" for e in events) == 3
    assert recover(path) == []


def test_a_cut_off_last_line_is_ignored_and_damage_elsewhere_is_named(tmp_path):
    path = tmp_path / "e.jsonl"
    EventLog(path).emit("memory.compacted", {"turns": 1})
    with path.open("a", encoding="utf-8") as handle:
        handle.write('{"sequence":2,"ev')
    assert len(read_events(path)) == 1
    path.write_text('{broken\n{"a":1}\n', encoding="utf-8")
    with pytest.raises(EventError, match="Line 1 of .* is not JSON"):
        read_events(path)


def test_many_threads_emitting_at_once_keep_one_unbroken_chain(log):
    barrier = threading.Barrier(8)

    def work(number: int) -> None:
        barrier.wait()
        for _ in range(10):
            log.emit("discovery.published", {"id": f"d-{number}", "topic": "t"})

    threads = [threading.Thread(target=work, args=(n,)) for n in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    events = read_events(log.path)
    assert len(events) == 80 and check(events) == []


def test_long_payload_values_are_clipped(log):
    log.emit("discovery.published", {"id": "d", "topic": "x" * 5000})
    assert len(read_events(log.path)[0]["payload"]["topic"]) < 2100
    assert events_module.EVENT_TYPES[0] == "session.start"
