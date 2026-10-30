import sqlite3
import time

import pytest

from nailong_agent_sdk.agent.retention import (
    RetentionPolicy,
    RunRetention,
    read_tombstones,
    tombstone_path,
)
from nailong_agent_sdk.foundations.contracts import ToolCall, ToolExecutionResult
from nailong_agent_sdk.foundations.errors import AgentSdkError
from nailong_agent_sdk.foundations.run_fence import RunFence
from nailong_agent_sdk.memory.context_projection import FileToolResultJournal
from nailong_agent_sdk.observability.audit_log import AuditTranscriptStore
from nailong_agent_sdk.observability.telemetry_models import MetricAvailability, MetricObservation
from tests.support.retention_runs import POLICY, emit, finished_run, journal_files, later


def short_fences(services, seconds=0.05):
    for store in (services.telemetry, services.audit_logs, services.result_journal):
        store.fence.wait_seconds = seconds


def test_a_run_that_gains_an_event_after_it_was_examined_is_not_pruned(services, monkeypatch):
    finished_run(services, "old")
    real = RunRetention._integrity_problem

    def revive_then_check(self, run_id):
        emit(services, run_id, "agent.state-updated")
        return real(self, run_id)

    monkeypatch.setattr(RunRetention, "_integrity_problem", revive_then_check)
    report = services.retention().prune(POLICY, now=later(), dry_run=False)
    monkeypatch.setattr(RunRetention, "_integrity_problem", real)
    assert report.runs == [] and len(report.problems) == 1
    reason = report.problems[0].reason
    for needle in (
        'Pruning run "old"',
        "confirming that it received no new event",
        "TELEMETRY_RUN_CHANGED",
        "so nothing was pruned",
    ):
        assert needle in reason, (needle, reason)
    assert "Tombstone" not in reason
    assert services.telemetry.footprint("old").event_count == 3
    assert services.audit_logs.entry_count("old") == 3 and len(journal_files(services)) == 2
    assert not tombstone_path(services.run_root).exists()
    assert not services.telemetry.fence.is_fenced("old")
    emit(services, "old", "agent.state-updated")


def test_a_writer_that_arrives_while_a_run_is_being_pruned_is_refused_not_deleted(
    services, monkeypatch
):
    finished_run(services, "old", handles=2, entries=3)
    before = services.telemetry.footprint("old")
    short_fences(services)
    refused = {}
    real = FileToolResultJournal.prune_run

    def write_during_the_prune(self, run_id, *, dry_run=False):
        if not dry_run:
            attempts = {
                "telemetry": lambda: emit(services, run_id, "agent.late-event"),
                "audit": lambda: services.audit_logs.append(run_id, "late", {"x": 1}),
                "journal": lambda: services.result_journal.for_run(run_id).record(
                    ToolCall(id="late", name="echo", arguments={}),
                    ToolExecutionResult(status="succeeded", output={}),
                ),
                "metric": lambda: services.telemetry.record_metric(
                    MetricObservation(
                        metric_id="m",
                        run_id=run_id,
                        value=2.0,
                        unit="count",
                        availability=MetricAvailability.AVAILABLE,
                        observed_at_utc="2026-01-01T00:00:00+00:00",
                    )
                ),
            }
            for name, attempt in attempts.items():
                try:
                    attempt()
                except AgentSdkError as error:
                    refused[name] = error.code
        return real(self, run_id, dry_run=dry_run)

    monkeypatch.setattr(FileToolResultJournal, "prune_run", write_during_the_prune)
    report = services.retention().prune(POLICY, now=later(), dry_run=False)
    assert refused == {name: "RUN_PRUNING" for name in ("telemetry", "audit", "journal", "metric")}
    (run,) = report.runs
    (tombstone,) = read_tombstones(services.run_root)
    assert tombstone.telemetry.event_count == before.event_count == run.events
    assert tombstone.telemetry.head_hash == before.head_hash
    assert tombstone.audit.entries == 3 and len(tombstone.journal.handles) == 2
    assert services.telemetry.footprint("old").event_count == 0
    assert not services.telemetry.fence.is_fenced("old")


def test_writers_that_wait_out_a_prune_continue_as_a_new_incarnation_and_lose_nothing(
    services, monkeypatch
):
    import threading

    finished_run(services, "busy", handles=1, entries=1)
    short_fences(services, 10)
    before = services.telemetry.footprint("busy")
    accepted = []
    failures = []
    started = threading.Event()

    def writer():
        started.set()
        for index in range(15):
            try:
                emit(services, "busy", f"agent.step-{index}")
                services.audit_logs.append("busy", "step", {"i": index})
                accepted.append(index)
            except AgentSdkError as error:
                failures.append(error.code)
                return

    thread = threading.Thread(target=writer)
    real = FileToolResultJournal.prune_run

    def start_the_writer_inside_the_prune(self, run_id, *, dry_run=False):
        if not dry_run and not started.is_set():
            thread.start()
            started.wait(5)
            time.sleep(0.1)
        return real(self, run_id, dry_run=dry_run)

    monkeypatch.setattr(FileToolResultJournal, "prune_run", start_the_writer_inside_the_prune)
    policy = RetentionPolicy(older_than_seconds=3_600, include_unfinished=True)
    report = services.retention().prune(policy, now=later(), dry_run=False)
    thread.join(30)
    assert not thread.is_alive() and failures == [] and report.problems == []
    (tombstone,) = read_tombstones(services.run_root)
    assert tombstone.telemetry.event_count == before.event_count
    assert tombstone.telemetry.head_hash == before.head_hash
    assert len(accepted) == 15
    after = services.telemetry.footprint("busy")
    assert after.event_count == 15 and after.first_sequence == 1
    assert services.telemetry.verify_run_chain("busy") is True
    assert services.audit_logs.entry_count("busy") == 15 and services.audit_logs.verify("busy")


def late_writes(services, run_id):
    return {
        "telemetry": lambda: emit(services, run_id, "agent.late-event"),
        "audit": lambda: services.audit_logs.append(run_id, "late", {"x": 1}),
        "journal": lambda: services.result_journal.for_run(run_id).record(
            ToolCall(id="late", name="echo", arguments={}),
            ToolExecutionResult(status="succeeded", output={}),
        ),
        "metric": lambda: services.telemetry.record_metric(
            MetricObservation(
                metric_id="m",
                run_id=run_id,
                value=2.0,
                unit="count",
                availability=MetricAvailability.AVAILABLE,
                observed_at_utc="2026-01-01T00:00:00+00:00",
            )
        ),
    }


def test_a_writer_that_passes_its_pre_check_just_before_the_fence_is_still_refused(
    services, monkeypatch
):
    finished_run(services, "old")
    short_fences(services)
    real = RunFence.wait_until_open

    def fence_right_after_the_pre_check(self, run_id, operation):
        real(self, run_id, operation)
        self.begin(run_id, holder="test")

    monkeypatch.setattr(RunFence, "wait_until_open", fence_right_after_the_pre_check)
    events = services.telemetry.footprint("old").event_count
    for name, attempt in late_writes(services, "old").items():
        services.telemetry.fence.end("old")
        with pytest.raises(AgentSdkError) as raised:
            attempt()
        assert raised.value.code == "RUN_PRUNING", name
    services.telemetry.fence.end("old")
    assert services.telemetry.footprint("old").event_count == events
    assert services.audit_logs.entry_count("old") == 3
    assert len(services.result_journal.handles_of("old")) == 2


def test_a_writer_waiting_for_a_prune_to_end_is_accepted_afterwards_by_every_store(services):
    import threading

    finished_run(services, "old")
    short_fences(services, 10)
    services.telemetry.fence.begin("old", holder="test")
    results = {}

    def attempt(name, action):
        try:
            action()
            results[name] = "accepted"
        except AgentSdkError as error:
            results[name] = error.code

    threads = [
        threading.Thread(target=attempt, args=(name, action))
        for name, action in late_writes(services, "old").items()
    ]
    for thread in threads:
        thread.start()
    time.sleep(0.2)
    assert results == {}
    services.telemetry.fence.end("old")
    for thread in threads:
        thread.join(10)
    assert results == {name: "accepted" for name in ("telemetry", "audit", "journal", "metric")}


def test_a_prune_waits_for_a_write_already_inside_its_transaction_and_keeps_it(
    services, monkeypatch
):
    import threading

    from nailong_agent_sdk.observability import audit_log

    finished_run(services, "old", entries=3)
    inside = threading.Event()
    proceed = threading.Event()
    real = audit_log._read_tail_entry
    state = {"blocked": False}

    def hold_the_first_append(handle, end, label):
        if not state["blocked"] and threading.current_thread().name == "writer":
            state["blocked"] = True
            inside.set()
            assert proceed.wait(10)
        return real(handle, end, label)

    monkeypatch.setattr(audit_log, "_read_tail_entry", hold_the_first_append)
    other_process = AuditTranscriptStore(services.run_root)

    def late_append():
        other_process.append("old", "late", {"x": 1})
        other_process.close()

    writer = threading.Thread(name="writer", target=late_append)
    writer.start()
    assert inside.wait(10)
    outcome = {}
    pruner = threading.Thread(
        target=lambda: outcome.update(
            report=services.retention().prune(POLICY, now=later(), dry_run=False)
        )
    )
    pruner.start()
    time.sleep(0.3)
    assert pruner.is_alive() and services.telemetry.fence.is_fenced("old")
    proceed.set()
    writer.join(10)
    pruner.join(30)
    assert not writer.is_alive() and not pruner.is_alive()
    services.retention().prune(POLICY, now=later(), dry_run=False)
    (tombstone,) = read_tombstones(services.run_root)
    assert tombstone.audit.entries == 4
    assert services.audit_logs.entry_count("old") == 0
    assert services.telemetry.footprint("old").event_count == 0
    assert not services.telemetry.fence.is_fenced("old")


def test_an_interrupted_prune_keeps_writers_out_until_the_next_pass_finishes_it(
    services, monkeypatch
):
    finished_run(services, "old")
    short_fences(services)
    real = FileToolResultJournal.prune_run

    def fail(self, run_id, *, dry_run=False):
        if dry_run:
            return real(self, run_id, dry_run=True)
        raise OSError("disk gone")

    monkeypatch.setattr(FileToolResultJournal, "prune_run", fail)
    broken = services.retention().prune(POLICY, now=later(), dry_run=False)
    assert len(broken.problems) == 1 and services.telemetry.fence.is_fenced("old")
    with pytest.raises(AgentSdkError) as raised:
        emit(services, "old", "agent.late-event")
    assert raised.value.code == "RUN_PRUNING" and "same retention policy again" in str(raised.value)
    preview = services.retention().prune(POLICY, now=later())
    assert preview.runs == [] and any("interrupted" in reason for reason in preview.kept)
    monkeypatch.setattr(FileToolResultJournal, "prune_run", real)
    done = services.retention().prune(POLICY, now=later(), dry_run=False)
    assert [run.run_id for run in done.runs] == ["old"] and done.problems == []
    assert not services.telemetry.fence.is_fenced("old")
    assert len(read_tombstones(services.run_root)) == 1
    emit(services, "old", "agent.run-started")
    assert services.telemetry.footprint("old").event_count == 1


def test_a_fence_left_before_any_tombstone_is_released_by_the_next_applied_pass(services):
    finished_run(services, "idle")
    services.telemetry.fence.begin("idle", holder="crashed-pruner")
    short_fences(services)
    with pytest.raises(AgentSdkError):
        emit(services, "idle", "agent.late-event")
    policy = RetentionPolicy(older_than_seconds=3_600, keep_most_recent=1)
    report = services.retention().prune(policy, now=later(), dry_run=False)
    assert report.recovered_runs == ["idle"] and report.runs == []
    assert not services.telemetry.fence.is_fenced("idle")
    emit(services, "idle", "agent.late-event")
    assert services.telemetry.footprint("idle").event_count == 3


def test_a_tombstoned_run_whose_data_is_already_gone_has_its_fence_released(services, monkeypatch):
    finished_run(services, "old")
    real = type(services.telemetry).prune_run
    state = {"after_delete": True}

    def crash_after_the_delete(self, run_id, *, dry_run=False, expected_last_sequence=None):
        footprint = real(
            self, run_id, dry_run=dry_run, expected_last_sequence=expected_last_sequence
        )
        if not dry_run and state["after_delete"]:
            raise sqlite3.OperationalError("process died after the commit")
        return footprint

    monkeypatch.setattr(type(services.telemetry), "prune_run", crash_after_the_delete)
    broken = services.retention().prune(POLICY, now=later(), dry_run=False)
    monkeypatch.setattr(type(services.telemetry), "prune_run", real)
    assert len(broken.problems) == 1 and services.telemetry.footprint("old").event_count == 0
    assert services.telemetry.fence.is_fenced("old")
    done = services.retention().prune(POLICY, now=later(), dry_run=False)
    assert done.recovered_runs == ["old"] and done.runs == []
    assert not services.telemetry.fence.is_fenced("old")
    assert len(read_tombstones(services.run_root)) == 1


def test_a_run_pruned_and_later_run_again_under_the_same_id_gets_its_own_tombstone(services):
    finished_run(services, "reused")
    services.retention().prune(POLICY, now=later(), dry_run=False)
    finished_run(services, "reused", handles=1, entries=1)
    services.retention().prune(POLICY, now=later(), dry_run=False)
    first, second = read_tombstones(services.run_root)
    assert (first.run_id, second.run_id) == ("reused", "reused")
    assert first.telemetry.head_hash != second.telemetry.head_hash
    assert second.sequence == 2 and second.previous_hash == first.integrity_hash
    assert len(first.journal.handles) == 2 and len(second.journal.handles) == 1
    numbers = [int(h.handle_id.removeprefix("result-")) for h in first.journal.handles]
    assert all(
        int(h.handle_id.removeprefix("result-")) > max(numbers) for h in second.journal.handles
    )
