import json

import pytest

from nailong_agent_sdk.foundations.errors import AgentSdkError
from nailong_agent_sdk.observability.run_checkpoints import FileCheckpointSink, HmacSha256Signer
from nailong_agent_sdk.tools.artifact_anchor import (
    ArtifactAnchorVerdict,
    anchor_artifact_log,
    verify_artifact_anchor,
)
from nailong_agent_sdk.tools.artifacts import ArtifactStore
from tests.tools.test_artifact_history_integrity import log_lines, put_lines, reseal, written

KEY = "k" * 40
NAME = "run-1"


def signer(key=KEY):
    return HmacSha256Signer(key, signer_id="test")


def verdict(store, directory, key=KEY):
    return verify_artifact_anchor(
        ArtifactStore(store.root, read_only=True), FileCheckpointSink(directory), NAME, signer(key)
    )


def anchored(tmp_path, count=6):
    root = tmp_path / "ws"
    store = written(root, count)
    sink_dir = tmp_path / "anchors"
    anchor_artifact_log(store, FileCheckpointSink(sink_dir), NAME, signer())
    return root, store, sink_dir


def test_an_untouched_anchored_log_matches_and_a_longer_one_is_advanced(tmp_path):
    root, store, sink_dir = anchored(tmp_path)
    assert verdict(store, sink_dir).verdict is ArtifactAnchorVerdict.MATCHES
    store.write_text("a.txt", "later\n")
    result = verdict(store, sink_dir)
    assert result.verdict is ArtifactAnchorVerdict.ADVANCED and result.ok
    anchor_artifact_log(store, FileCheckpointSink(sink_dir), NAME, signer())
    assert verdict(store, sink_dir).verdict is ArtifactAnchorVerdict.MATCHES


@pytest.mark.parametrize("start", [0, 2, 5])
def test_a_resealed_log_is_diverged_from_its_anchor(tmp_path, start):
    root, store, sink_dir = anchored(tmp_path)
    put_lines(root, _forged(root, start))
    assert store.verify_history().ok
    result = verdict(store, sink_dir)
    assert result.verdict is ArtifactAnchorVerdict.DIVERGED
    assert "rewritten after it was anchored" in result.detail


def _forged(root, start):
    lines = log_lines(root)
    body = json.loads(lines[start])
    body["manifest"] = {**(body.get("manifest") or {}), "agent_identity": "forged"}
    lines[start] = json.dumps(body, separators=(",", ":"))
    return reseal(lines, start)


@pytest.mark.parametrize("keep", [0, 3, 5])
def test_a_truncated_or_tail_deleted_log_is_reported_with_the_count_removed(tmp_path, keep):
    root, store, sink_dir = anchored(tmp_path)
    put_lines(root, log_lines(root)[:keep])
    assert ArtifactStore(root, read_only=True).verify_history().ok
    result = verdict(store, sink_dir)
    assert result.verdict is ArtifactAnchorVerdict.TRUNCATED
    assert f"{6 - keep} newest writes were removed" in result.detail


def test_an_anchor_signed_with_another_key_or_edited_is_refused(tmp_path):
    root, store, sink_dir = anchored(tmp_path)
    assert verdict(store, sink_dir, key="z" * 40).verdict is ArtifactAnchorVerdict.SIGNATURE_INVALID
    path = next(sink_dir.glob("*.jsonl"))
    path.write_text(path.read_text("utf-8").replace('"records":6', '"records":2'), "utf-8")
    assert verdict(store, sink_dir).verdict is ArtifactAnchorVerdict.SIGNATURE_INVALID


def test_a_missing_anchor_says_the_log_is_only_tamper_evident(tmp_path):
    store = written(tmp_path / "ws", 3)
    result = verdict(store, tmp_path / "none")
    assert result.verdict is ArtifactAnchorVerdict.NO_ANCHOR and not result.ok
    assert "tamper-evident only" in result.detail


def test_a_broken_chain_is_never_signed_and_is_reported_against_an_existing_anchor(tmp_path):
    root, store, sink_dir = anchored(tmp_path)
    lines = log_lines(root)
    lines[2] = lines[2].replace("a.txt", "c.txt")
    put_lines(root, lines)
    assert verdict(store, sink_dir).verdict is ArtifactAnchorVerdict.CHAIN_BROKEN
    with pytest.raises(AgentSdkError, match="is not intact and was not anchored"):
        anchor_artifact_log(
            ArtifactStore(root, read_only=True), FileCheckpointSink(sink_dir), NAME, signer()
        )


def test_an_empty_log_anchors_as_zero_writes_and_detects_later_insertions_as_advanced(tmp_path):
    store = ArtifactStore(tmp_path / "ws")
    sink_dir = tmp_path / "anchors"
    anchor_artifact_log(store, FileCheckpointSink(sink_dir), NAME, signer())
    assert verdict(store, sink_dir).verdict is ArtifactAnchorVerdict.MATCHES
    store.write_text("a.txt", "x\n")
    assert verdict(store, sink_dir).verdict is ArtifactAnchorVerdict.ADVANCED
