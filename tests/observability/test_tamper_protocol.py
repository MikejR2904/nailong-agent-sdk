import sys

import pytest

pytest.importorskip("cryptography")

from tests.support.paths import REPO_ROOT

BENCHMARK = str(REPO_ROOT / "benchmarks" / "trust")
if BENCHMARK not in sys.path:
    sys.path.insert(0, BENCHMARK)

import tamper_protocol as protocol  # noqa: E402

from nailong_agent_sdk.observability.recorder import spawn_recorder  # noqa: E402


@pytest.fixture(scope="module")
def recorder(tmp_path_factory):
    handle = spawn_recorder(tmp_path_factory.mktemp("protocol-recorder") / "home", head_every=10)
    yield handle
    handle.stop()


def trial(arm, klass, target, recorder, seed=1):
    return protocol.run_trial(arm, klass, target, seed, recorder)


def test_a_resealed_chain_passes_the_chain_and_the_end_anchor_but_not_the_recorder(recorder):
    for klass, target in (("edit-reseal-audit", "recent"), ("edit-reseal-telemetry", "older")):
        for arm in ("chain-only", "end-anchor"):
            row = trial(arm, klass, target, recorder)
            assert row["effective"] and not row["detected"], (arm, klass)
        row = trial("recorder-sync", klass, target, recorder)
        assert row["effective"] and row["detected"], klass
        assert row["recorder"] != {"audit": "matches", "telemetry": "matches"}


def test_removing_the_newest_events_passes_the_chain_and_the_anchor_but_not_the_recorder(recorder):
    for arm in ("chain-only", "end-anchor"):
        row = trial(arm, "delete-tail", "recent", recorder)
        assert row["effective"] and not row["detected"]
    row = trial("recorder-sync", "delete-tail", "recent", recorder)
    assert row["effective"] and row["detected"]
    assert "truncated" in row["recorder"].values() or row["inline"]


def test_an_edit_that_was_not_resealed_breaks_the_chain_and_the_checkpoint_refuses_to_sign(
    recorder,
):
    row = trial("end-anchor", "edit-audit", "recent", recorder)
    assert row["effective"] and row["detected"]
    assert row["chain"] and row["checkpoint_refused"] == "RUN_CHECKPOINT_CHAIN_BROKEN"


def test_an_untouched_run_raises_no_alarm_on_any_arm(recorder):
    for arm in protocol.ARMS:
        row = trial(arm, "none", "all", recorder)
        assert not row["effective"] and not row["detected"], arm
        assert row["status"] == "completed"


def test_a_ledger_the_operating_system_will_not_let_go_is_recorded_as_blocked(recorder):
    row = trial("chain-only", "delete-audit-file", "all", recorder)
    if sys.platform == "win32":
        assert row["blocked"] == "PermissionError" and not row["effective"]
    else:
        assert row["effective"]
