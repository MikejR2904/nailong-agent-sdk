import json
import subprocess

import pytest
from trust_bench import cli, lock
from trust_bench.cli import main
from trust_bench.confirmatory import VALIDATION_SEEDS, confirmatory_plan
from trust_bench.lock import LockError


def git(root, *args):
    subprocess.run(
        ["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@t", *args],
        capture_output=True,
        text=True,
        check=True,
    )


def write(root, relative, text):
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-q")
    write(root, "src/nailong_agent_sdk/core.py", "VALUE = 1\n")
    write(root, "benchmarks/trust/trust_bench/score.py", "RATE = 0.5\n")
    write(root, "benchmarks/trust/PREREGISTRATION.md", "# plan\n")
    write(root, "tests/trust_bench/test_x.py", "def test_x(): pass\n")
    write(root, "tests/support/trust_runs.py", "X = 1\n")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "baseline")
    return root


PLAN = {"seeds": "1000000-1000099"}


def test_a_clean_committed_tree_can_be_locked_and_the_lock_names_what_it_pins(repo):
    made = lock.create_lock(repo, PLAN)
    stored = json.loads((repo / lock.LOCK_FILE).read_text(encoding="utf-8"))
    assert stored == made and stored["test_plan"] == PLAN
    assert len(stored["commit"]) == 40 and len(stored["harness_sha256"]) == 64
    assert "python" in stored["versions"]


def test_a_lock_is_refused_while_a_pinned_file_has_uncommitted_changes(repo):
    write(repo, "src/nailong_agent_sdk/core.py", "VALUE = 2\n")
    with pytest.raises(LockError, match=r"1 path\(s\).*uncommitted changes \(.*core.py\)"):
        lock.create_lock(repo, PLAN)


def test_a_lock_is_refused_without_a_preregistration(repo):
    git(repo, "rm", "-q", "-f", "benchmarks/trust/PREREGISTRATION.md")
    git(repo, "commit", "-q", "-m", "remove")
    with pytest.raises(LockError, match="PREREGISTRATION.md does not exist"):
        lock.create_lock(repo, PLAN)


def test_a_locked_pre_registration_is_never_rewritten(repo):
    lock.create_lock(repo, PLAN)
    with pytest.raises(LockError, match="LOCK.json already exists"):
        lock.create_lock(repo, PLAN)


def test_committing_the_lock_file_itself_does_not_break_verification(repo):
    made = lock.create_lock(repo, PLAN)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "lock")
    assert lock.verify_lock(repo)["commit"] == made["commit"]


def test_verification_needs_a_lock(repo):
    with pytest.raises(LockError, match="LOCK.json does not exist"):
        lock.verify_lock(repo)


def test_a_committed_change_to_the_harness_after_the_lock_is_caught_by_its_hash(repo):
    lock.create_lock(repo, PLAN)
    write(repo, "benchmarks/trust/trust_bench/score.py", "RATE = 0.9\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "tweak the scorer")
    with pytest.raises(LockError, match="harness_sha256 is .* but the lock holds"):
        lock.verify_lock(repo)


def test_a_committed_change_to_the_system_under_test_is_caught_by_its_hash(repo):
    lock.create_lock(repo, PLAN)
    write(repo, "src/nailong_agent_sdk/core.py", "VALUE = 3\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "fix")
    with pytest.raises(LockError, match="system_sha256 is .* but the lock holds"):
        lock.verify_lock(repo)


def test_an_edit_to_the_pre_registration_after_the_lock_is_caught(repo):
    lock.create_lock(repo, PLAN)
    write(repo, "benchmarks/trust/PREREGISTRATION.md", "# changed\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "amend")
    with pytest.raises(LockError, match="preregistration_sha256 is .* but the lock holds"):
        lock.verify_lock(repo)


def test_an_unrelated_file_does_not_disturb_the_lock(repo):
    lock.create_lock(repo, PLAN)
    write(repo, "benchmarks/trust/reports/dev.md", "notes\n")
    write(repo, "docs/notes.md", "x\n")
    assert lock.verify_lock(repo)["commit"]


def test_a_rewritten_history_without_the_locked_commit_is_refused(repo):
    lock.create_lock(repo, PLAN)
    stored = json.loads((repo / lock.LOCK_FILE).read_text(encoding="utf-8"))
    stored["commit"] = "0" * 40
    (repo / lock.LOCK_FILE).write_text(json.dumps(stored), encoding="utf-8")
    with pytest.raises(LockError, match="not in the history of HEAD"):
        lock.verify_lock(repo)


def test_a_test_job_runs_once_and_a_repeat_is_refused_with_the_first_offender_named(repo):
    first = lock.eval_job_key(("doc", 1_000_000, "sdk", None, "test", frozenset()))
    second = lock.eval_job_key(("doc", 1_000_000, "sdk", "limit", "test", frozenset()))
    third = lock.eval_job_key(("doc", 1_000_001, "sdk", None, "test", frozenset()))
    lock.register_test_jobs(repo, [first], "run 1")
    lock.register_test_jobs(repo, [second], "run 2")
    with pytest.raises(LockError, match=r"1 of 2 test jobs were already run \(first: .*1000000"):
        lock.register_test_jobs(repo, [first, third], "run 3")
    lock.register_test_jobs(repo, [third], "run 4")


def test_a_repeat_with_a_reason_is_allowed_and_leaves_the_deviation_on_record(repo):
    key = lock.tamper_job_key(("doc", 1_000_000, "sdk"))
    lock.register_test_jobs(repo, [key], "first")
    with pytest.raises(LockError, match="pass a rerun reason"):
        lock.register_test_jobs(repo, [key], "second")
    lock.register_test_jobs(repo, [key], "second", rerun_reason="the harness crashed at job 12")
    lines = (repo / lock.REGISTRY).read_text(encoding="utf-8").splitlines()
    entry = json.loads(lines[-1])
    assert entry["rerun_reason"] == "the harness crashed at job 12" and entry["repeated"] == 1


def test_the_tree_digest_changes_with_content_and_with_names(tmp_path):
    write(tmp_path, "pkg/a.py", "x = 1\n")
    first = lock.tree_digest(tmp_path, ["pkg"])
    write(tmp_path, "pkg/a.py", "x = 2\n")
    second = lock.tree_digest(tmp_path, ["pkg"])
    (tmp_path / "pkg" / "a.py").rename(tmp_path / "pkg" / "b.py")
    assert len({first, second, lock.tree_digest(tmp_path, ["pkg"])}) == 3


def validation_plan():
    return confirmatory_plan(VALIDATION_SEEDS, VALIDATION_SEEDS)


def committed_lock(repo):
    lock.create_lock(repo, validation_plan())
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "lock")


def slice_args(out, seeds="1900000"):
    return [
        "run",
        "--split",
        "test",
        "--families",
        "data",
        "--seeds",
        seeds,
        "--arms",
        "langgraph",
        "--workers",
        "1",
        "--out",
        str(out),
    ]


def test_the_lock_command_pins_the_tree_and_verify_lock_confirms_it(repo, capsys):
    assert main(["lock"], repo=repo) == 0
    assert "locked at commit" in capsys.readouterr().out
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "lock")
    assert main(["verify-lock"], repo=repo) == 0
    write(repo, "benchmarks/trust/trust_bench/score.py", "RATE = 0.7\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "tweak")
    with pytest.raises(LockError, match="harness_sha256"):
        main(["verify-lock"], repo=repo)


def test_the_test_split_is_refused_until_a_lock_exists(repo, tmp_path):
    out = tmp_path / "r.jsonl"
    with pytest.raises(LockError, match="LOCK.json does not exist"):
        main(slice_args(out), repo=repo)
    assert not out.exists()


def test_a_locked_test_slice_runs_once_and_a_recorded_rerun_is_allowed(repo, tmp_path):
    committed_lock(repo)
    args = slice_args(tmp_path / "r.jsonl")
    assert main(args, repo=repo) == 0
    assert len((tmp_path / "r.jsonl").read_text(encoding="utf-8").splitlines()) >= 7
    with pytest.raises(LockError, match="already run"):
        main(args, repo=repo)
    assert main([*args, "--rerun-reason", "demonstration"], repo=repo) == 0


def test_a_run_outside_the_locked_plan_is_refused_before_anything_runs(repo, tmp_path):
    committed_lock(repo)
    out = tmp_path / "r.jsonl"
    with pytest.raises(LockError, match=r"seeds \[1900500\] are outside the locked plan"):
        main(slice_args(out, "1900500"), repo=repo)
    tamper = ["tamper", "--split", "test", "--families", "doc", "--seeds", "1900050"]
    with pytest.raises(LockError, match=r"outside the locked plan \[1900000, 1900049\]"):
        main([*tamper, "--arms", "sdk", "--out", str(out)], repo=repo)
    assert not out.exists()


def test_the_entry_point_reports_a_refusal_and_exits_with_two(repo, monkeypatch, capsys):
    monkeypatch.setattr(cli, "REPO", repo)
    monkeypatch.setattr("sys.argv", ["trust_bench", "verify-lock"])
    assert cli.entry() == 2
    assert "refused: benchmarks/trust/LOCK.json does not exist" in capsys.readouterr().err


def test_the_dev_split_never_asks_for_a_lock(repo, tmp_path):
    out = tmp_path / "dev.jsonl"
    args = ["run", "--split", "dev", "--families", "data", "--seeds", "1", "--arms", "langgraph"]
    assert main([*args, "--workers", "1", "--out", str(out)], repo=repo) == 0
    assert out.exists() and not (repo / lock.REGISTRY).exists()
