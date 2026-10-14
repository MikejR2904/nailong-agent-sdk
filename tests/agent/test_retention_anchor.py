import json
from datetime import UTC, datetime, timedelta

import pytest

from nailong_agent_sdk.agent.retention import (
    RetentionPolicy,
    build_anchor,
    export_tombstone_anchor,
    prune_run_root,
    read_tombstones,
    tombstone_chain_break,
    tombstone_hash,
    tombstone_path,
    verify_tombstone_anchor,
)
from nailong_agent_sdk.agent.retention_anchor import (
    AnchorVerdict,
    TombstoneAnchor,
    read_anchor,
)
from nailong_agent_sdk.agent.runtime import AgentRuntimeServices
from nailong_agent_sdk.developer_tools.cli import main
from nailong_agent_sdk.foundations.errors import AgentSdkError
from tests.support.retention_runs import finished_run

POLICY = RetentionPolicy(older_than_seconds=3_600)


def later():
    return datetime.now(UTC) + timedelta(days=2)


@pytest.fixture
def roots(tmp_path):
    run_root = tmp_path / "run"
    run_root.mkdir()
    services = AgentRuntimeServices.open(run_root)
    for name in ("a", "b", "c", "d"):
        finished_run(services, name)
    services.audit_logs.close()
    services.telemetry.close()
    return run_root, tmp_path / "anchors" / "tombstones.anchor.json"


def prune(run_root, *, max_runs=None, anchor_path=None, dry_run=False):
    policy = RetentionPolicy(older_than_seconds=3_600, max_runs=max_runs)
    return prune_run_root(run_root, policy, dry_run=dry_run, now=later(), anchor_path=anchor_path)


def rewrite_log(run_root, change):
    path = tombstone_path(run_root)
    entries = read_tombstones(run_root)
    lines = []
    previous = None
    for index, entry in enumerate(entries):
        draft = change(index, entry).model_copy(update={"previous_hash": previous})
        sealed = draft.model_copy(update={"integrity_hash": tombstone_hash(draft)})
        previous = sealed.integrity_hash
        lines.append(json.dumps(sealed.model_dump(mode="json"), sort_keys=True))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def drop_newest(run_root, count):
    path = tombstone_path(run_root)
    lines = path.read_text(encoding="utf-8").splitlines()
    path.write_text("".join(f"{line}\n" for line in lines[:-count]), encoding="utf-8")


def test_an_anchor_records_the_length_and_head_of_the_log_and_checks_its_own_hash(roots):
    run_root, destination = roots
    prune(run_root, max_runs=2)
    anchor = export_tombstone_anchor(run_root, destination)
    entries = read_tombstones(run_root)
    assert anchor.entries == 2 and anchor.head_hash == entries[-1].integrity_hash
    assert anchor.intact() and read_anchor(destination) == anchor
    assert verify_tombstone_anchor(run_root, destination).verdict is AnchorVerdict.MATCHES


def test_a_log_that_grew_since_it_was_anchored_is_reported_as_advanced(roots):
    run_root, destination = roots
    prune(run_root, max_runs=2)
    export_tombstone_anchor(run_root, destination)
    prune(run_root)
    verification = verify_tombstone_anchor(run_root, destination)
    assert verification.verdict is AnchorVerdict.ADVANCED and verification.ok
    assert (verification.anchored_entries, verification.current_entries) == (2, 4)
    assert "anchor it again" in verification.detail
    export_tombstone_anchor(run_root, destination)
    assert verify_tombstone_anchor(run_root, destination).verdict is AnchorVerdict.MATCHES


def test_removing_the_newest_entries_leaves_a_valid_chain_but_not_a_valid_anchor(roots):
    run_root, destination = roots
    prune(run_root)
    anchor = export_tombstone_anchor(run_root, destination)
    assert anchor.entries == 4
    drop_newest(run_root, 2)
    assert tombstone_chain_break(run_root) is None
    verification = verify_tombstone_anchor(run_root, destination)
    assert verification.verdict is AnchorVerdict.TRUNCATED and not verification.ok
    assert (verification.anchored_entries, verification.current_entries) == (4, 2)
    assert "2 newest entries were removed" in verification.detail


def test_a_log_rewritten_with_a_consistent_chain_diverges_from_its_anchor(roots):
    run_root, destination = roots
    prune(run_root)
    export_tombstone_anchor(run_root, destination)
    rewrite_log(
        run_root,
        lambda index, entry: (
            entry.model_copy(update={"report_path": "forged.json"}) if index == 1 else entry
        ),
    )
    assert tombstone_chain_break(run_root) is None
    verification = verify_tombstone_anchor(run_root, destination)
    assert verification.verdict is AnchorVerdict.DIVERGED
    assert "was rewritten after it was anchored" in verification.detail


def test_a_broken_chain_is_reported_as_such_against_any_anchor(roots):
    run_root, destination = roots
    prune(run_root, max_runs=2)
    anchor = build_anchor(run_root)
    log = tombstone_path(run_root)
    log.write_text(log.read_text("utf-8").replace('"run_id": "a"', '"run_id": "z"'), "utf-8")
    verification = verify_tombstone_anchor(run_root, anchor)
    assert verification.verdict is AnchorVerdict.CHAIN_BROKEN
    assert "RETENTION_TOMBSTONES_BROKEN" in verification.detail or "fail verification" in (
        verification.detail
    )


def test_an_anchor_that_was_edited_is_unreadable_rather_than_trusted(roots):
    run_root, destination = roots
    prune(run_root, max_runs=2)
    export_tombstone_anchor(run_root, destination)
    destination.write_text(
        destination.read_text("utf-8").replace('"entries": 2', '"entries": 0'), "utf-8"
    )
    verification = verify_tombstone_anchor(run_root, destination)
    assert verification.verdict is AnchorVerdict.UNREADABLE
    assert "fails its own integrity hash" in verification.detail
    with pytest.raises(AgentSdkError) as raised:
        read_anchor(destination)
    assert raised.value.code == "RETENTION_ANCHOR_UNREADABLE"


def test_an_anchor_kept_inside_the_run_root_is_refused(roots):
    run_root, _ = roots
    with pytest.raises(AgentSdkError) as raised:
        export_tombstone_anchor(run_root, run_root / ".agent-retention" / "anchor.json")
    assert raised.value.code == "RETENTION_ANCHOR_INSIDE_RUN_ROOT"
    assert "another volume or host" in str(raised.value)


def test_an_existing_anchor_is_never_moved_backwards_over_a_truncated_log(roots):
    run_root, destination = roots
    prune(run_root)
    export_tombstone_anchor(run_root, destination)
    before = destination.read_text("utf-8")
    drop_newest(run_root, 1)
    with pytest.raises(AgentSdkError) as raised:
        export_tombstone_anchor(run_root, destination)
    assert raised.value.code == "RETENTION_ANCHOR_MISMATCH"
    assert "truncated" in str(raised.value) and destination.read_text("utf-8") == before


def test_an_empty_log_can_be_anchored_and_later_extended(roots):
    run_root, destination = roots
    anchor = export_tombstone_anchor(run_root, destination)
    assert anchor.entries == 0 and anchor.head_hash is None
    assert verify_tombstone_anchor(run_root, destination).verdict is AnchorVerdict.MATCHES
    prune(run_root, max_runs=1)
    assert verify_tombstone_anchor(run_root, destination).verdict is AnchorVerdict.ADVANCED


def test_an_applied_prune_writes_its_anchor_and_reports_it(roots):
    run_root, destination = roots
    first = prune(run_root, max_runs=2, anchor_path=destination)
    assert first.anchor is not None and first.anchor.verdict == "absent"
    assert first.anchor.written is True and first.anchor.entries == 2
    assert read_anchor(destination).head_hash == first.anchor.head_hash
    second = prune(run_root, anchor_path=destination)
    assert second.anchor is not None and second.anchor.verdict == "matches"
    assert second.anchor.entries == 4 and read_anchor(destination).entries == 4


def test_a_prune_refuses_to_continue_over_a_log_that_lost_its_newest_entries(roots):
    run_root, destination = roots
    prune(run_root, max_runs=3, anchor_path=destination)
    drop_newest(run_root, 1)
    with pytest.raises(AgentSdkError) as raised:
        prune(run_root, anchor_path=destination)
    assert raised.value.code == "RETENTION_ANCHOR_MISMATCH"
    assert "truncated" in str(raised.value) and "Nothing was pruned" in str(raised.value)
    assert len(read_tombstones(run_root)) == 2


def test_a_dry_run_reports_a_mismatch_as_a_problem_instead_of_raising(roots):
    run_root, destination = roots
    prune(run_root, max_runs=3, anchor_path=destination)
    drop_newest(run_root, 1)
    report = prune(run_root, anchor_path=destination, dry_run=True)
    assert report.anchor is not None and report.anchor.verdict == "truncated"
    assert report.clean is False
    assert any(problem.run_id == "(tombstone log)" for problem in report.problems)
    assert len(read_tombstones(run_root)) == 2


def test_the_command_line_anchors_verifies_and_prunes_under_an_anchor(roots, capsys):
    run_root, destination = roots
    assert (
        main(
            [
                "prune-runs",
                str(run_root),
                "--older-than-seconds",
                "0.001",
                "--max-runs",
                "2",
                "--apply",
                "--anchor",
                str(destination),
            ]
        )
        == 0
    )
    printed = json.loads(capsys.readouterr().out)
    assert printed["anchor"]["written"] is True and printed["anchor"]["entries"] == 2
    assert main(["verify-tombstones", str(run_root), str(destination)]) == 0
    assert json.loads(capsys.readouterr().out)["verdict"] == "matches"
    drop_newest(run_root, 1)
    assert main(["verify-tombstones", str(run_root), str(destination)]) == 1
    assert json.loads(capsys.readouterr().out)["verdict"] == "truncated"
    other = destination.with_name("second.anchor.json")
    drop_newest(run_root, 1)
    assert main(["anchor-tombstones", str(run_root), str(other)]) == 0
    assert json.loads(capsys.readouterr().out)["entries"] == 0


def test_an_anchor_is_a_plain_json_document_with_no_secrets(roots):
    run_root, destination = roots
    prune(run_root, max_runs=1)
    export_tombstone_anchor(run_root, destination)
    document = json.loads(destination.read_text("utf-8"))
    assert set(document) == {
        "schema_version",
        "run_root_label",
        "entries",
        "head_hash",
        "anchored_at_utc",
        "integrity_hash",
    }
    assert TombstoneAnchor.model_validate(document).intact()
    assert str(run_root) not in destination.read_text("utf-8")
