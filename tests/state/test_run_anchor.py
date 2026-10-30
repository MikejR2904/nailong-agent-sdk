import json
import shutil

import pytest

from nailong_agent_sdk.agent.run_checkpointer import RunHeadCheckpointer
from nailong_agent_sdk.observability.run_checkpoints import FileCheckpointSink, HmacSha256Signer
from nailong_agent_sdk.state.coordination_records import _hash_run
from nailong_agent_sdk.state.harness_coordinator import HarnessCoordinator
from nailong_agent_sdk.state.run_anchor import (
    RunAnchorVerdict,
    anchor_run_record,
    run_anchor_name,
    verify_run_anchor,
)
from nailong_agent_sdk.state.run_state_store import RunStateStore
from tests.support.plans import AGENT, arun, chain_plan, run_ok

SIGNER = HmacSha256Signer(b"run-anchor-key-0123456789abcdef!", signer_id="run-anchor")


def sink_of(tmp_path):
    return FileCheckpointSink(tmp_path / "elsewhere")


def executed(tmp_path, plan=None):
    coordinator = HarnessCoordinator(
        tmp_path / "root", anchorer=RunHeadCheckpointer(SIGNER, sink_of(tmp_path))
    )
    started = coordinator.start_run(plan or chain_plan(4))
    arun(coordinator.execute_run(started.run_id, {AGENT: run_ok}))
    return coordinator, started.run_id


def verdict(tmp_path, run_id, key=SIGNER):
    store = RunStateStore(tmp_path / "root", read_only=True)
    return verify_run_anchor(store, sink_of(tmp_path), run_anchor_name(run_id), run_id, key)


def rewrite_fresh(tmp_path, run_id, change):
    """What an attacker with write access to the run root can do: re-save a changed record into
    an empty store so that its history hash and run hash are consistent, then swap the files."""

    source = RunStateStore(tmp_path / "root", read_only=True)
    record = source.load(run_id)
    changed = change(record)
    changed = changed.model_copy(
        update={
            "run_hash": _hash_run(
                changed.run_id,
                changed.plan_id,
                changed.graph,
                changed.plan_validation,
                changed.cancelled,
            )
        }
    )
    scratch = tmp_path / "scratch"
    shutil.rmtree(scratch, ignore_errors=True)
    forged = RunStateStore(scratch)
    forged.save(changed)
    for suffix in (".json", ".history.jsonl"):
        shutil.copy(
            scratch / ".agent-runs" / f"{run_id}{suffix}",
            tmp_path / "root" / ".agent-runs" / f"{run_id}{suffix}",
        )


def test_an_untouched_run_matches_its_anchor_after_execution(tmp_path):
    _, run_id = executed(tmp_path)
    result = verdict(tmp_path, run_id)
    assert result.verdict is RunAnchorVerdict.MATCHES, result.detail
    assert result.anchored_entries == result.current_entries > 0


def test_a_run_that_kept_going_after_the_anchor_is_advanced_not_matched(tmp_path):
    coordinator = HarnessCoordinator(tmp_path / "root")
    started = coordinator.start_run(chain_plan(4))
    store = RunStateStore(tmp_path / "root")
    anchor_run_record(store, sink_of(tmp_path), started.run_id, SIGNER)
    arun(coordinator.execute_run(started.run_id, {AGENT: run_ok}))
    result = verdict(tmp_path, started.run_id)
    assert result.verdict is RunAnchorVerdict.ADVANCED and result.ok


def test_a_resealed_history_passes_the_stores_own_check_and_fails_the_anchor(tmp_path):
    _, run_id = executed(tmp_path)

    def tamper(record):
        graph = json.loads(json.dumps(record.graph))
        graph["events"][1]["reason"] = "rewritten after the fact"
        return record.model_copy(update={"graph": graph})

    rewrite_fresh(tmp_path, run_id, tamper)
    RunStateStore(tmp_path / "root", read_only=True).load(run_id)
    result = verdict(tmp_path, run_id)
    assert result.verdict is RunAnchorVerdict.DIVERGED, result.detail
    assert "rewritten after it was anchored" in result.detail


def test_a_truncated_history_passes_the_stores_own_check_and_fails_the_anchor(tmp_path):
    _, run_id = executed(tmp_path)

    def tamper(record):
        graph = json.loads(json.dumps(record.graph))
        graph["events"] = graph["events"][:2]
        return record.model_copy(update={"graph": graph})

    rewrite_fresh(tmp_path, run_id, tamper)
    RunStateStore(tmp_path / "root", read_only=True).load(run_id)
    result = verdict(tmp_path, run_id)
    assert result.verdict is RunAnchorVerdict.TRUNCATED, result.detail
    assert result.current_entries < result.anchored_entries


def test_an_edit_that_does_not_reseal_breaks_the_stores_chain_first(tmp_path):
    _, run_id = executed(tmp_path)
    history = tmp_path / "root" / ".agent-runs" / f"{run_id}.history.jsonl"
    lines = history.read_text("utf-8").splitlines()
    lines[2] = lines[2].replace("completed", "failed", 1)
    history.write_text("\n".join(lines) + "\n", "utf-8")
    assert verdict(tmp_path, run_id).verdict is RunAnchorVerdict.CHAIN_BROKEN


def test_a_forged_or_missing_anchor_is_named(tmp_path):
    _, run_id = executed(tmp_path)
    other = HmacSha256Signer(b"another-key-0123456789abcdef-012!", signer_id="run-anchor")
    assert verdict(tmp_path, run_id, other).verdict is RunAnchorVerdict.SIGNATURE_INVALID
    store = RunStateStore(tmp_path / "root", read_only=True)
    none = verify_run_anchor(store, sink_of(tmp_path), "unanchored.run", run_id, SIGNER)
    assert none.verdict is RunAnchorVerdict.NO_ANCHOR and not none.ok


def test_a_broken_run_is_never_anchored(tmp_path):
    _, run_id = executed(tmp_path)
    snapshot = tmp_path / "root" / ".agent-runs" / f"{run_id}.json"
    snapshot.write_text(snapshot.read_text("utf-8").replace("completed", "failed", 1), "utf-8")
    store = RunStateStore(tmp_path / "root")
    with pytest.raises(Exception, match="not anchored|integrity"):
        anchor_run_record(store, sink_of(tmp_path), run_id, SIGNER)
