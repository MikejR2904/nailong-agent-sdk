import json
import shutil
from collections import namedtuple

import pytest

from nailong_agent_sdk.developer_tools.cli import main
from nailong_agent_sdk.foundations.errors import AgentSdkError
from nailong_agent_sdk.observability.telemetry_models import (
    TelemetryActor,
    TelemetryAuthority,
    TelemetryContext,
)
from nailong_agent_sdk.observability.telemetry_store import TelemetryStore

ACTOR = TelemetryActor(kind="system", identifier="test")


def fill(store, run_id, events, size=4_000):
    for index in range(events):
        store.emit(
            "step",
            TelemetryContext(run_id=run_id),
            actor=ACTOR,
            authority=TelemetryAuthority.DETERMINISTIC,
            status="ok",
            payload={"blob": f"{index}-" + "x" * size},
        )


@pytest.fixture
def store(tmp_path):
    opened = TelemetryStore(tmp_path)
    fill(opened, "big", 60)
    fill(opened, "small", 3, size=10)
    yield opened
    opened.close()


def test_deleting_a_run_leaves_free_pages_that_the_report_counts_and_vacuum_returns(store):
    store.prune_run("big")
    report = store.maintenance_report()
    assert report.free_pages > 0
    assert report.reclaimable_bytes == report.free_pages * report.page_size
    result = store.vacuum()
    assert result.integrity_check == "ok" and result.clean
    assert result.after.free_pages == 0
    assert result.reclaimed_bytes > 0
    assert result.after.database_bytes < result.before.database_bytes


def test_vacuum_changes_no_event_and_keeps_every_chain_intact(store):
    before = [event.integrity_hash for event in store.list_events("small", limit=100)]
    store.prune_run("big")
    store.vacuum()
    after = store.list_events("small", limit=100)
    assert [event.integrity_hash for event in after] == before
    assert store.chain_break("small") is None


def test_vacuum_is_refused_while_a_prune_is_in_progress(store):
    store.fence.begin("small", holder="retention")
    with pytest.raises(AgentSdkError) as raised:
        store.vacuum()
    assert raised.value.code == "TELEMETRY_VACUUM_REFUSED"
    assert "small" in str(raised.value) and "applied prune pass" in str(raised.value)
    store.fence.end("small")
    assert store.vacuum().clean


def test_vacuum_is_refused_without_the_free_space_it_needs(store, monkeypatch):
    usage = namedtuple("usage", "total used free")
    monkeypatch.setattr(shutil, "disk_usage", lambda _path: usage(10, 10, 0))
    with pytest.raises(AgentSdkError) as raised:
        store.vacuum()
    assert raised.value.code == "TELEMETRY_VACUUM_REFUSED"
    assert "free bytes beside" in str(raised.value) and "only 0 are available" in str(raised.value)


def test_a_read_only_store_cannot_vacuum_but_can_report(tmp_path):
    writer = TelemetryStore(tmp_path)
    fill(writer, "run", 2, size=10)
    writer.close()
    reader = TelemetryStore(tmp_path, read_only=True)
    try:
        assert reader.maintenance_report().page_count > 0
        with pytest.raises(RuntimeError, match="vacuum"):
            reader.vacuum()
    finally:
        reader.close()


def test_the_command_line_reports_by_default_and_vacuums_only_when_told_to(tmp_path, store, capsys):
    store.prune_run("big")
    store.close()
    database = tmp_path / ".agent-telemetry" / "telemetry.sqlite3"
    size = database.stat().st_size
    assert main(["vacuum-telemetry", str(tmp_path)]) == 0
    reported = json.loads(capsys.readouterr().out)
    assert reported["free_pages"] > 0 and database.stat().st_size == size
    assert main(["vacuum-telemetry", str(tmp_path), "--apply"]) == 0
    applied = json.loads(capsys.readouterr().out)
    assert applied["integrity_check"] == "ok" and applied["reclaimed_bytes"] > 0
    assert database.stat().st_size < size


def test_the_command_line_names_a_run_root_without_a_database(tmp_path, capsys):
    assert main(["vacuum-telemetry", str(tmp_path)]) == 3
    assert "has no telemetry database" in capsys.readouterr().err
