import json
import os

import pytest

from nailong_agent_sdk.observability.audit_log import AuditTranscriptStore
from tests.support.processes import run_workers


def test_the_rendered_transcript_counts_only_entries_before_a_break_as_verified(tmp_path):
    store = AuditTranscriptStore(tmp_path)
    for i in range(6):
        store.append("run-b", "evt", {"i": i})
    store.close()
    path = tmp_path / ".agent-audit-logs" / "run-b.jsonl"
    lines = path.read_text(encoding="utf-8").splitlines()
    entry = json.loads(lines[3])
    entry["payload"] = {"i": "tampered"}
    lines[3] = json.dumps(entry)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    reopened = AuditTranscriptStore(tmp_path)
    text = reopened.render_markdown("run-b").read_text(encoding="utf-8")
    assert "Integrity chain valid: `False`" in text
    assert "Verified transcript entries: `3`" in text
    assert "Verified through sequence: `3`" in text
    assert "Rendered entries: `6`" in text
    reopened.close()


def test_audit_chain_paging_render_and_verify(tmp_path):
    store = AuditTranscriptStore(tmp_path)
    entries = [store.append("run-a", "evt", {"i": i}, task_id="t", iteration=i) for i in range(12)]
    assert [e.sequence for e in entries] == list(range(1, 13))
    assert store.verify("run-a") and store.snapshot_sequence("run-a") == 12
    assert len(store.list_entries("run-a", limit=5)) == 5
    with pytest.raises(ValueError):
        store.list_entries("run-a", limit=0)
    path = store.render_markdown("run-a")
    text = path.read_text(encoding="utf-8")
    assert "Integrity chain valid: `True`" in text and "Verified transcript entries: `12`" in text
    store.close()
    again = AuditTranscriptStore(tmp_path)
    assert again.append("run-a", "evt", {"i": 12}).sequence == 13 and again.verify("run-a")
    again.close()


def test_audit_multiprocess_append_is_serialized(tmp_path):
    script = """
    import sys
    from pathlib import Path
    from nailong_agent_sdk.observability.audit_log import AuditTranscriptStore
    store = AuditTranscriptStore(Path(sys.argv[2]))
    for i in range(30):
        store.append("shared", "evt", {"w": sys.argv[1], "i": i})
    store.close()
    """
    outputs = run_workers(script, 5, str(tmp_path))
    assert all(code == 0 for code, _, _ in outputs), outputs
    store = AuditTranscriptStore(tmp_path)
    entries = store.list_entries("shared", limit=1000)
    assert [e.sequence for e in entries] == list(range(1, 151)) and store.verify("shared")
    store.close()


def test_audit_more_runs_than_handle_cache_keeps_every_chain_valid(tmp_path):
    store = AuditTranscriptStore(tmp_path, max_open_handles=4)
    for round_ in range(3):
        for run in range(12):
            store.append(f"run-{run}", "evt", {"round": round_})
    assert all(
        store.verify(f"run-{run}") and store.snapshot_sequence(f"run-{run}") == 3
        for run in range(12)
    )
    store.close()


def test_audit_distinct_run_ids_never_share_a_transcript(tmp_path):
    store = AuditTranscriptStore(tmp_path)
    ids = ["a/b", "a:b", "a b", "a_b"]
    for run_id in ids:
        store.append(run_id, "evt", {"who": run_id})
    for run_id in ids:
        entries = store.list_entries(run_id)
        assert [e.run_id for e in entries] == [run_id], (
            f"run id {run_id!r} shares a transcript: {[e.run_id for e in entries]}"
        )
    store.close()


@pytest.mark.skipif(os.name != "nt", reason="Windows reserved device names")
@pytest.mark.parametrize("run_id", ["nul", "con", "aux", "com1"])
def test_audit_reserved_windows_device_names_as_run_ids(tmp_path, run_id):
    store = AuditTranscriptStore(tmp_path)
    store.append(run_id, "evt", {"x": 1})
    assert store.snapshot_sequence(run_id) == 1 and store.verify(run_id)
    store.close()


def test_audit_entry_larger_than_tail_window_does_not_break_later_appends(tmp_path):
    store = AuditTranscriptStore(tmp_path, max_payload_chars=300_000)
    store.append("big", "evt", {"blob": "x" * 120_000})
    second = store.append("big", "evt", {"blob": "y"})
    assert second.sequence == 2 and store.verify("big")
    store.close()


def test_audit_hidden_reasoning_and_secrets(tmp_path):
    store = AuditTranscriptStore(tmp_path)
    with pytest.raises(ValueError):
        store.append("r", "evt", {"scratchpad": "plan"})
    entry = store.append(
        "r", "evt", {"password": "hunter2hunter2", "text": "Bearer abcdefghijklmnopqrstuv"}
    )
    assert (
        entry.payload["password"] == "[REDACTED]"
        and "abcdefghijklmnopqr" not in entry.payload["text"]
    )
    store.close()


def test_audit_oversized_payload_is_bounded_with_hash(tmp_path):
    store = AuditTranscriptStore(tmp_path)
    entry = store.append("r", "evt", {"blob": "z" * 50_000})
    assert entry.payload["truncated"] is True and entry.payload["original_chars"] > 50_000
    assert len(entry.payload["preview"]) == 8192
    store.close()


def test_audit_unicode_edge_cases_round_trip(tmp_path):
    store = AuditTranscriptStore(tmp_path)
    for text in ["emoji 😀 ok", "nul\x00byte", "line\u2028sep", "zero​width", "ñandú"]:
        store.append("u", "evt", {"t": text})
    assert store.verify("u")
    assert [e.payload["t"] for e in store.list_entries("u")][0] == "emoji 😀 ok"
    store.close()


def test_audit_lone_surrogate_in_payload(tmp_path):
    store = AuditTranscriptStore(tmp_path)
    try:
        store.append("s", "evt", {"t": "broken \ud83d pair"})
    except Exception as error:
        pytest.fail(f"lone surrogate in a payload raised {type(error).__name__}: {error}")
    assert store.verify("s")
    store.close()
