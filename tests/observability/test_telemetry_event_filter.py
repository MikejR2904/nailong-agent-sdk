import sqlite3

import pytest

from nailong_agent_sdk.observability.telemetry_store import MAX_EVENT_TYPE_FILTER, TelemetryStore
from tests.support.observability_cases import emit


@pytest.fixture
def store(tmp_path):
    opened = TelemetryStore(tmp_path)
    pattern = ["agent.started", "agent.tool-completed", "agent.model-turn", "agent.tool-completed"]
    for index in range(12):
        emit(opened, event_type=pattern[index % 4], payload={"i": index})
    emit(opened, run_id="other-run", event_type="agent.tool-completed")
    yield opened
    opened.close()


def test_events_can_be_read_by_type_in_sequence_order(store):
    events = store.list_events("run-1", event_types=["agent.tool-completed"])
    assert [e.event_type for e in events] == ["agent.tool-completed"] * 6
    assert [e.sequence for e in events] == [2, 4, 6, 8, 10, 12]
    both = store.list_events("run-1", event_types={"agent.started", "agent.model-turn"})
    assert [e.sequence for e in both] == [1, 3, 5, 7, 9, 11]


def test_a_type_nobody_emitted_returns_nothing(store):
    assert store.list_events("run-1", event_types=["agent.never-happened"]) == []


def test_a_filtered_page_continues_from_the_last_sequence_it_returned(store):
    wanted = ["agent.tool-completed"]
    first = store.list_events("run-1", event_types=wanted, limit=4)
    assert [e.sequence for e in first] == [2, 4, 6, 8]
    second = store.list_events(
        "run-1", event_types=wanted, limit=4, after_sequence=first[-1].sequence
    )
    assert [e.sequence for e in second] == [10, 12]


def test_the_filter_respects_the_sequence_boundary(store):
    events = store.list_events(
        "run-1", event_types=["agent.tool-completed"], after_sequence=2, through_sequence=8
    )
    assert [e.sequence for e in events] == [4, 6, 8]


def test_streaming_applies_the_filter_across_pages(store):
    streamed = list(store.iter_events("run-1", page_size=2, event_types=["agent.tool-completed"]))
    assert [e.sequence for e in streamed] == [2, 4, 6, 8, 10, 12]


def test_other_runs_never_leak_into_a_filtered_read(store):
    events = store.list_events("other-run", event_types=["agent.tool-completed"])
    assert [e.context.run_id for e in events] == ["other-run"]


def test_filtering_does_not_change_what_the_chain_verifies(store):
    store.list_events("run-1", event_types=["agent.started"])
    assert store.chain_break("run-1") is None and store.verify_run_chain("run-1")


def test_a_forged_type_column_cannot_hide_an_event(store):
    with sqlite3.connect(store.database_path) as connection:
        connection.execute(
            "UPDATE events SET event_type = 'forged' WHERE run_id = 'run-1' AND sequence = 2"
        )
    events = store.list_events("run-1", event_types=["agent.tool-completed"])
    assert [e.sequence for e in events] == [2, 4, 6, 8, 10, 12]


def test_an_empty_filter_is_refused_rather_than_meaning_everything(store):
    with pytest.raises(ValueError, match="event_types must name at least one event type"):
        store.list_events("run-1", event_types=[])


def test_a_bare_string_is_refused_with_the_form_to_use(store):
    with pytest.raises(ValueError, match=r'pass \["agent.started"\]'):
        store.list_events("run-1", event_types="agent.started")


def test_blank_names_are_refused(store):
    with pytest.raises(ValueError, match="non-blank strings"):
        store.list_events("run-1", event_types=["agent.started", "  "])


def test_the_filter_is_bounded(store):
    too_many = [f"type-{index}" for index in range(MAX_EVENT_TYPE_FILTER + 1)]
    with pytest.raises(ValueError, match=f"at most {MAX_EVENT_TYPE_FILTER}"):
        store.list_events("run-1", event_types=too_many)
