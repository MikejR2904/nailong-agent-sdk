import os

from nailong_agent_sdk.observability.run_checkpoints import FileCheckpointSink, HmacSha256Signer
from nailong_agent_sdk.tools import artifacts as artifact_module
from nailong_agent_sdk.tools.artifact_anchor import anchor_artifact_log
from nailong_agent_sdk.tools.artifacts import ArtifactStore

KEY = "k" * 40


class FsyncSpy:
    def __init__(self, monkeypatch):
        self.paths = []
        real = os.fsync

        def spy(descriptor):
            try:
                self.paths.append(os.readlink(f"/proc/self/fd/{descriptor}"))
            except OSError:
                self.paths.append(descriptor)
            real(descriptor)

        monkeypatch.setattr(artifact_module.os, "fsync", spy)
        monkeypatch.setattr("nailong_agent_sdk.foundations.atomic_io.os.fsync", spy)


def test_a_default_store_does_not_sync_so_a_power_loss_can_drop_the_log_tail(tmp_path, monkeypatch):
    spy = FsyncSpy(monkeypatch)
    ArtifactStore(tmp_path).write_text("a.txt", "x\n")
    assert spy.paths == []


def test_a_durable_store_syncs_the_blob_the_manifests_and_the_log_before_it_returns(
    tmp_path, monkeypatch
):
    spy = FsyncSpy(monkeypatch)
    ArtifactStore(tmp_path, durable=True).write_text("a.txt", "x\n")
    assert len(spy.paths) >= 4


def test_a_durable_write_syncs_the_blob_before_the_log_line_and_the_log_after_it(
    tmp_path, monkeypatch
):
    order = []
    real_fsync, real_append = os.fsync, artifact_module._append_line

    def fsync(descriptor):
        order.append("fsync")
        real_fsync(descriptor)

    def append(path, line, *args, **kwargs):
        order.append("append")
        return real_append(path, line, *args, **kwargs)

    monkeypatch.setattr(artifact_module.os, "fsync", fsync)
    monkeypatch.setattr("nailong_agent_sdk.foundations.atomic_io.os.fsync", fsync)
    monkeypatch.setattr(artifact_module, "_append_line", append)
    ArtifactStore(tmp_path, durable=True).write_text("a.txt", "x\n")
    where = order.index("append")
    assert "fsync" in order[:where] and "fsync" in order[where:]


def test_anchoring_syncs_the_log_before_it_reads_the_head(tmp_path):
    calls = []

    class Spy(ArtifactStore):
        def sync_log(self):
            calls.append("sync")
            return super().sync_log()

        def log_hashes(self):
            calls.append("hashes")
            return super().log_hashes()

    store = Spy(tmp_path)
    store.write_text("a.txt", "x\n")
    anchor_artifact_log(
        store, FileCheckpointSink(tmp_path / "anchors"), "r", HmacSha256Signer(KEY, signer_id="t")
    )
    assert calls == ["sync", "hashes"]
