import json
import random
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

import nailong_agent_sdk.specifications.git_versioning as gv
from nailong_agent_sdk.specifications.gate_models import (
    DependencyEdge,
    DependencyGraph,
)
from nailong_agent_sdk.specifications.git_models import GitCommandError, VersionBump
from nailong_agent_sdk.specifications.git_versioning import (
    GitRepositoryAdapter,
    SpecificationVersionService,
    structural_specification_diff,
)
from tests.support.git_versions import M, N, P, approval, inputs, lock, sh
from tests.support.processes import child_environment
from tests.support.specs import req, spec, tree


@pytest.fixture
def repo_path(tmp_path):
    path = tmp_path / "repo"
    path.mkdir()
    sh(path, "init", "-q", "-b", "main")
    sh(path, "config", "user.email", "audit@example.com")
    sh(path, "config", "user.name", "audit")
    (path / "spec.txt").write_text("v1\n", "utf-8")
    sh(path, "add", ".")
    sh(path, "commit", "-q", "-m", "initial")
    return path


def test_state_clean_dirty_branch_tags_and_toplevel(repo_path, tmp_path):
    repo = GitRepositoryAdapter(repo_path)
    state = repo.state()
    assert state.branch == "main" and state.clean and state.tags == []
    assert state.head_commit == sh(repo_path, "rev-parse", "HEAD")
    assert state.tree_id == sh(repo_path, "rev-parse", "HEAD^{tree}")
    (repo_path / "new.txt").write_text("x", "utf-8")
    assert repo.state().clean is False
    (repo_path / "new.txt").unlink()
    for tag in ("v1.9.0", "v1.10.0", "v1.2.0"):
        sh(repo_path, "tag", tag)
    assert repo.state().tags == ["v1.2.0", "v1.9.0", "v1.10.0"]
    sh(repo_path, "checkout", "-q", "--detach")
    assert repo.state().branch is None
    sub = repo_path / "sub"
    sub.mkdir()
    with pytest.raises(ValueError, match="actual repository top-level"):
        GitRepositoryAdapter(sub).state()
    plain = tmp_path / "plain"
    plain.mkdir()
    with pytest.raises(GitCommandError, match="not a git repository"):
        GitRepositoryAdapter(plain).state()


def test_unicode_branch_name_survives_state(repo_path):
    sh(repo_path, "checkout", "-q", "-b", "feature-\u00fcn\u00ef")
    got = GitRepositoryAdapter(repo_path).state().branch
    assert got == "feature-\u00fcn\u00ef"


def test_resolve_commit_and_misc_adapter_rules(repo_path):
    repo = GitRepositoryAdapter(repo_path)
    head = sh(repo_path, "rev-parse", "HEAD")
    assert repo.resolve_commit("HEAD") == head and repo.resolve_commit("main") == head
    for bad in ("", "--output=evil", "-h"):
        with pytest.raises(ValueError, match="non-option"):
            repo.resolve_commit(bad)
    with pytest.raises(GitCommandError):
        repo.resolve_commit("does-not-exist")
    assert repo.diff_names() == []
    (repo_path / "spec.txt").write_text("v2\n", "utf-8")
    assert repo.diff_names() == ["M\tspec.txt"]
    sh(repo_path, "commit", "-aq", "-m", "second")
    sh(repo_path, "tag", "v0.0.1", "HEAD~1")
    assert repo.diff_names("v0.0.1") == ["M\tspec.txt"]
    assert repo.tag_exists("v0.0.1") and not repo.tag_exists("v9.9.9")
    assert not repo.tag_exists("v0.0.*")


def test_worktrees_listing_handles_spaces(repo_path, tmp_path):
    repo = GitRepositoryAdapter(repo_path)
    target = tmp_path / "work tree with spaces"
    repo.create_worktree(target, "wt-branch", "HEAD")
    listed = repo.worktrees()
    assert len(listed) == 2 and any("work tree with spaces" in item["worktree"] for item in listed)
    with pytest.raises(GitCommandError):
        repo.create_worktree(tmp_path / "other", "wt-branch", "HEAD")


def test_lock_lifecycle_and_rejections(repo_path, tmp_path):
    repo = GitRepositoryAdapter(repo_path)
    service = SpecificationVersionService(tmp_path / "locks")
    base = [req("R1"), req("R2", deps=["R1"])]
    record = lock(service, repo, "1.0.0", base, M)
    assert (
        record.tag_name == "v1.0.0" and record.classification.recommended_bump is VersionBump.MAJOR
    )
    assert sh(repo_path, "cat-file", "-t", "v1.0.0") == "tag"
    assert record.tag_object_id == sh(repo_path, "rev-parse", "v1.0.0^{tag}")
    assert (tmp_path / "locks" / ".agent-git-locks" / "1.0.0.lock.json").is_file()
    assert (
        tmp_path / "locks" / ".agent-git-locks" / "specification-snapshots" / "v1.0.0.snapshot.json"
    ).is_file()
    with pytest.raises(ValueError, match="already exists"):
        lock(service, repo, "1.0.0", base, M)
    more = base + [req("R3")]
    for version, kind, message in (
        ("1.0.1", N, "does not satisfy"),
        ("1.1.0", M, "does not match"),
        ("1.1.0", P, "does not match"),
        ("2.0.0", N, "does not satisfy"),
        ("1.0.0", N, "already exists"),
    ):
        with pytest.raises(ValueError, match=message):
            lock(service, repo, version, more, kind)
    with pytest.raises(ValueError, match="does not match supplied specification"):
        lock(service, repo, "1.1.0", more, N, metadata_update={"unified_specification_hash": "bad"})
    with pytest.raises(ValueError, match="soft-lock metadata"):
        lock(service, repo, "1.1.0", more, N, metadata_update={"soft_locked": False})
    with pytest.raises(
        ValueError,
        match=r'Version metadata version "1.1.1" must match the unified specification '
        r'version "1.1.0"',
    ):
        lock(service, repo, "1.1.0", more, N, metadata_update={"version": "1.1.1"})
    _, _, report, _ = inputs("1.1.0", more, N)
    with pytest.raises(
        ValueError,
        match=r'Gap report document_version "0.9.0" must match the unified specification '
        r'version "1.1.0"',
    ):
        lock(
            service,
            repo,
            "1.1.0",
            more,
            N,
            report=report.model_copy(update={"document_version": "0.9.0"}),
        )
    with pytest.raises(ValueError, match="create-specification-lock"):
        lock(service, repo, "1.1.0", more, N, approval=approval(approved=False))
    with pytest.raises(ValueError, match="create-specification-lock"):
        lock(service, repo, "1.1.0", more, N, approval=approval(action="other"))
    for bad_version in ("1.0", "v1.1.0", "1.1.0-rc1", "01.1.0", "1.1.0 ", "1.1.0\n"):
        with pytest.raises(ValueError, match="MAJOR.MINOR.PATCH"):
            service.classify(
                repo,
                bad_version,
                spec(version="1.1.0", reqs=more, trees=[tree()]),
                DependencyGraph(),
            )
    (repo_path / "dirty.txt").write_text("x", "utf-8")
    with pytest.raises(ValueError, match="must be clean"):
        lock(service, repo, "1.1.0", more, N)
    (repo_path / "dirty.txt").unlink()
    minor = lock(service, repo, "1.1.0", more, N)
    assert minor.classification.previous_tag == "v1.0.0"
    assert minor.classification.structural_diff.added_requirement_ids == ["R3"]
    patch = lock(service, repo, "1.1.1", more, P)
    assert patch.classification.recommended_bump is VersionBump.PATCH


def test_breaking_changes_require_major(repo_path, tmp_path):
    repo = GitRepositoryAdapter(repo_path)
    service = SpecificationVersionService(tmp_path / "locks")
    base = [req("R1"), req("R2", deps=["R1"])]
    lock(service, repo, "1.0.0", base, M)
    edge_added = [req("R1"), req("R2", deps=["R1"]), req("R3", deps=["R1"])]
    with pytest.raises(ValueError, match="does not match the deterministic structural"):
        lock(service, repo, "1.1.0", edge_added, N)
    text_changed = [req("R1", text="changed"), req("R2", deps=["R1"])]
    with pytest.raises(ValueError, match="does not match"):
        lock(service, repo, "1.1.0", text_changed, N)
    removed = [req("R1")]
    record = lock(service, repo, "2.0.0", removed, M)
    assert record.classification.structural_diff.removed_requirement_ids == ["R2"]
    edge_only = [req("R1", deps=["R1x"]), req("R9")]
    with pytest.raises(ValueError):
        lock(service, repo, "2.1.0", edge_only, N)


def test_snapshot_tamper_and_missing_snapshot_are_detected(repo_path, tmp_path):
    repo = GitRepositoryAdapter(repo_path)
    service = SpecificationVersionService(tmp_path / "locks")
    base = [req("R1")]
    lock(service, repo, "1.0.0", base, M)
    snapshot = (
        tmp_path / "locks" / ".agent-git-locks" / "specification-snapshots" / "v1.0.0.snapshot.json"
    )
    payload = json.loads(snapshot.read_text("utf-8"))
    payload["specification"]["requirements"][0]["text"] = "tampered"
    snapshot.write_text(json.dumps(payload), "utf-8")
    with pytest.raises(ValueError, match="specification snapshot digest does not match"):
        lock(service, repo, "1.0.1", base, P)
    snapshot.unlink()
    with pytest.raises(
        ValueError, match='"v1.0.0" has no persisted structured specification snapshot'
    ):
        lock(service, repo, "1.0.1", base, P)


def test_foreign_v_tag_breaks_classification_with_unnamed_error(repo_path, tmp_path):
    repo = GitRepositoryAdapter(repo_path)
    service = SpecificationVersionService(tmp_path / "locks")
    base = [req("R1")]
    lock(service, repo, "1.0.0", base, M)
    sh(repo_path, "tag", "v2-beta")
    try:
        lock(service, repo, "1.0.1", base, P)
        outcome = "locked"
    except Exception as error:
        outcome = f"{type(error).__name__}: {error}"
    assert outcome == "locked" or "v2-beta" in outcome, outcome


def test_lock_inside_the_repository_does_not_block_the_next_lock(repo_path):
    repo = GitRepositoryAdapter(repo_path)
    service = SpecificationVersionService(repo_path)
    base = [req("R1")]
    lock(service, repo, "1.0.0", base, M)
    try:
        lock(service, repo, "1.0.1", base, P)
        outcome = "second lock ok"
    except Exception as error:
        outcome = f"{type(error).__name__}: {error}"
    assert outcome == "second lock ok", outcome


def test_failed_persistence_rolls_back_tag_and_files(repo_path, tmp_path, monkeypatch):
    repo = GitRepositoryAdapter(repo_path)
    service = SpecificationVersionService(tmp_path / "locks")
    base = [req("R1")]
    original = gv._atomic_json

    def failing(target, value):
        if target.name.endswith(".lock.json"):
            raise OSError("disk full (simulated)")
        original(target, value)

    monkeypatch.setattr(gv, "_atomic_json", failing)
    with pytest.raises(OSError, match="disk full"):
        lock(service, repo, "1.0.0", base, M)
    assert not repo.tag_exists("v1.0.0")
    snapshots = tmp_path / "locks" / ".agent-git-locks" / "specification-snapshots"
    assert list(snapshots.glob("*.json")) == []
    monkeypatch.setattr(gv, "_atomic_json", original)
    assert lock(service, repo, "1.0.0", base, M).tag_name == "v1.0.0"


def test_crash_between_tag_and_lock_files_is_recoverable(repo_path, tmp_path):
    locks = tmp_path / "locks"
    script = textwrap.dedent(
        f"""
        import os, sys
        from pathlib import Path
        import nailong_agent_sdk.specifications.git_versioning as gv
        from tests.support.specs import req
        from tests.support.git_versions import lock, inputs, M
        from nailong_agent_sdk.specifications.git_versioning import (
            GitRepositoryAdapter,
            SpecificationVersionService,
        )
        service = SpecificationVersionService(Path(r"{locks}"))
        repo = GitRepositoryAdapter(Path(r"{repo_path}"))
        gv.SpecificationVersionService._persist_snapshot = lambda self, **kw: os._exit(3)
        lock(service, repo, "1.0.0", [req("R1")], M)
        """
    )
    env = child_environment({"GIT_CEILING_DIRECTORIES": str(tmp_path)})
    crashed = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, env=env, timeout=120
    )
    assert crashed.returncode == 3, crashed.stderr[-400:]
    repo = GitRepositoryAdapter(repo_path)
    assert repo.tag_exists("v1.0.0")
    service = SpecificationVersionService(locks)
    try:
        lock(service, repo, "1.0.0", [req("R1")], M)
        outcome = "recovered"
    except Exception as error:
        outcome = f"{type(error).__name__}: {error}"
    assert outcome == "recovered", outcome


def test_two_processes_locking_the_same_version_race(repo_path, tmp_path):
    locks = tmp_path / "locks"
    script = textwrap.dedent(
        f"""
        import sys, json
        from pathlib import Path
        from tests.support.specs import req
        from tests.support.git_versions import lock, M
        from nailong_agent_sdk.specifications.git_versioning import (
            GitRepositoryAdapter,
            SpecificationVersionService,
        )
        service = SpecificationVersionService(Path(r"{locks}"))
        repo = GitRepositoryAdapter(Path(r"{repo_path}"))
        try:
            lock(service, repo, "1.0.0", [req("R1")], M)
            print("OK")
        except Exception as error:
            print("ERR", type(error).__name__, str(error)[:160])
        """
    )
    env = child_environment({"GIT_CEILING_DIRECTORIES": str(tmp_path)})
    procs = [
        subprocess.Popen(
            [sys.executable, "-c", script],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
        )
        for _ in range(3)
    ]
    outputs = [p.communicate(timeout=180)[0].strip() for p in procs]
    assert sum(o == "OK" for o in outputs) == 1, outputs
    repo = GitRepositoryAdapter(repo_path)
    assert repo.tag_exists("v1.0.0")
    lock_files = list((locks / ".agent-git-locks").glob("*.lock.json"))
    assert len(lock_files) == 1


def test_variant_worktree_rules(repo_path, tmp_path):
    repo = GitRepositoryAdapter(repo_path)
    service = SpecificationVersionService(tmp_path / "locks")
    lock(service, repo, "1.0.0", [req("R1")], M)
    (repo_path / "spec.txt").write_text("v2\n", "utf-8")
    sh(repo_path, "commit", "-aq", "-m", "later")
    other = approval("create-variant-worktree")

    def variant(**overrides):
        arguments = dict(
            name="var-a",
            branch="variant/a",
            base_ref="v1.0.0",
            specification_tag="v1.0.0",
            purpose="try",
            approval=other,
        )
        arguments.update(overrides)
        return service.create_variant_worktree(repo, **arguments)

    record = variant()
    assert Path(record.path).is_dir() and record.base_commit == sh(
        repo_path, "rev-parse", "v1.0.0^{commit}"
    )
    assert (Path(record.path) / "spec.txt").read_text("utf-8") == "v1\n"
    with pytest.raises(ValueError, match="path already exists"):
        variant(branch="variant/b")
    for bad_branch in ("-x", "a..b", "", "a b", "/a"):
        with pytest.raises(ValueError, match="branch name is invalid"):
            variant(name="var-b", branch=bad_branch)
    for bad_name in ("", "-x", "a b", "../x", "a/b"):
        with pytest.raises(ValueError, match="name is invalid"):
            variant(name=bad_name, branch="variant/c")
    with pytest.raises(ValueError, match="existing specification tag"):
        variant(name="var-c", branch="variant/c", specification_tag="v7.7.7")
    with pytest.raises(ValueError, match="must resolve to the approved specification tag commit"):
        variant(name="var-c", branch="variant/c", base_ref="HEAD")
    with pytest.raises(ValueError, match="create-variant-worktree"):
        variant(name="var-c", branch="variant/c", approval=approval())


def test_variant_failure_after_creation_leaves_no_orphans(repo_path, tmp_path, monkeypatch):
    repo = GitRepositoryAdapter(repo_path)
    service = SpecificationVersionService(tmp_path / "locks")
    lock(service, repo, "1.0.0", [req("R1")], M)
    original = gv._atomic_json

    def failing(target, value):
        if target.parent.name == "worktrees":
            raise OSError("disk full (simulated)")
        original(target, value)

    monkeypatch.setattr(gv, "_atomic_json", failing)
    with pytest.raises(OSError):
        service.create_variant_worktree(
            repo,
            name="orph",
            branch="variant/orph",
            base_ref="v1.0.0",
            specification_tag="v1.0.0",
            purpose="p",
            approval=approval("create-variant-worktree"),
        )
    leftovers = [item["branch"] for item in repo.worktrees() if "orph" in item.get("worktree", "")]
    assert leftovers == []


def test_structural_diff_property_matches_oracle():
    rng = random.Random(3)
    for _ in range(200):
        ids = [f"R{i}" for i in range(rng.randint(1, 6))]

        def build(subset):
            requirements = []
            for rid in subset:
                requirements.append(
                    req(
                        rid,
                        text=rng.choice(["a", "b"]),
                        deps=[d for d in subset if d != rid and rng.random() < 0.3],
                    )
                )
            return requirements

        prev_reqs = build(rng.sample(ids, rng.randint(1, len(ids))))
        cur_reqs = build(rng.sample(ids, rng.randint(1, len(ids))))

        def graph(requirements):
            return DependencyGraph(
                nodes=sorted(r.id for r in requirements),
                edges=[
                    DependencyEdge(source_id=r.id, target_id=d)
                    for r in requirements
                    for d in r.dependencies
                ],
            )

        prev, cur = spec(reqs=prev_reqs, trees=[tree()]), spec(reqs=cur_reqs, trees=[tree()])
        diff = structural_specification_diff(prev, graph(prev_reqs), cur, graph(cur_reqs))
        pm, cm = {r.id: r for r in prev_reqs}, {r.id: r for r in cur_reqs}
        assert diff.added_requirement_ids == sorted(set(cm) - set(pm))
        assert diff.removed_requirement_ids == sorted(set(pm) - set(cm))
        assert diff.modified_requirement_ids == sorted(
            i for i in pm if i in cm and pm[i].text != cm[i].text
        )
        pe = {(r.id, d) for r in prev_reqs for d in r.dependencies}
        ce = {(r.id, d) for r in cur_reqs for d in r.dependencies}
        assert set(map(tuple, diff.added_dependency_edges)) == ce - pe
        assert set(map(tuple, diff.removed_dependency_edges)) == pe - ce
        assert diff.has_breaking_change == bool(
            diff.removed_requirement_ids or diff.modified_requirement_ids or ce != pe
        )
