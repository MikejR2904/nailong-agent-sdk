import asyncio
import subprocess
import sys
from pathlib import Path

import pytest

from nailong_agent_sdk.tools.worktrees import AgentWorktreeManager, validate_worktree_slug
from tests.support.git_repos import git
from tests.support.tools import run


@pytest.mark.parametrize(
    "slug",
    ["a", "a-b", "a.b", "a_b", "A1", "a/b", "a/b/c", "x" * 64],
)
def test_valid_slugs_pass(slug):
    assert validate_worktree_slug(slug) == slug


@pytest.mark.parametrize(
    "slug",
    [
        "",
        "x" * 65,
        "/abs",
        "\\abs",
        "a/../b",
        "..",
        ".",
        "a/./b",
        "a//b",
        "a b",
        "a:b",
        "a+b",
        "a\\b",
        "ü",
        "a/",
        "a\n",
        "a\nb",
    ],
)
def test_invalid_slugs_fail(slug):
    with pytest.raises(ValueError):
        validate_worktree_slug(slug)


def test_worktree_create_list_remove_roundtrip(repo, tmp_path):
    manager = AgentWorktreeManager(tmp_path / "wt")

    async def scenario():
        info = await manager.create_worktree(repo, "agent/one", agent_id="agent-1")
        again = await manager.create_worktree(repo, "agent/one")
        assert again is info
        listed = manager.list_worktrees()
        removed = await manager.remove_worktree("agent/one")
        missing = await manager.remove_worktree("agent/one")
        return info, listed, removed, missing

    info, listed, removed, missing = run(scenario(), timeout=120)
    assert Path(info.path).name == "agent+one" and info.branch == "agent-worktree-agent+one"
    assert info.agent_id == "agent-1" and listed == [info] and removed is True and missing is False
    assert not Path(info.path).exists()
    assert manager.get_worktree("agent/one") is None


def test_not_a_repository_error_names_git_output(tmp_path, monkeypatch):
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))
    manager = AgentWorktreeManager(tmp_path / "wt")
    plain = tmp_path / "plain"
    plain.mkdir()
    with pytest.raises(RuntimeError) as excinfo:
        run(manager.create_worktree(plain, "w"), timeout=60)
    assert "git worktree add failed" in str(excinfo.value) and "not a git repository" in str(
        excinfo.value
    )


def test_recreating_a_slug_discards_the_previous_runs_commits(repo, tmp_path):
    manager = AgentWorktreeManager(tmp_path / "wt")

    async def first():
        return await manager.create_worktree(repo, "w1")

    info = run(first(), timeout=60)
    worktree = Path(info.path)
    (worktree / "agent_work.txt").write_text("valuable agent output\n", "utf-8")
    git(worktree, "config", "user.email", "agent@example.com")
    git(worktree, "config", "user.name", "agent")
    git(worktree, "add", ".")
    git(worktree, "commit", "-q", "-m", "agent commit")
    agent_commit = git(worktree, "rev-parse", "HEAD")
    run(manager.remove_worktree("w1"), timeout=60)
    assert git(repo, "rev-parse", info.branch) == agent_commit

    recreated = run(manager.create_worktree(repo, "w1"), timeout=60)
    assert (Path(recreated.path) / "agent_work.txt").read_text("utf-8") == "valuable agent output\n"
    git(repo, "rev-parse", info.branch)
    reachable = (
        subprocess.run(
            ["git", "merge-base", "--is-ancestor", agent_commit, info.branch],
            cwd=str(repo),
            capture_output=True,
        ).returncode
        == 0
    )
    assert reachable, (
        "agent commit is no longer reachable from its branch after the slug was reused"
    )


def test_explicit_branch_name_resets_an_existing_unmerged_branch(repo, tmp_path):
    git(repo, "checkout", "-q", "-b", "feature")
    (repo / "feature.txt").write_text("unmerged feature work\n", "utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "feature work")
    feature_commit = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "-q", "main")
    manager = AgentWorktreeManager(tmp_path / "wt")
    run(manager.create_worktree(repo, "w2", branch="feature"), timeout=60)
    after = git(repo, "rev-parse", "feature")
    assert after == feature_commit, "existing branch 'feature' was force-reset by create_worktree"


def test_concurrent_creates_for_the_same_slug_return_one_worktree(repo, tmp_path):
    manager = AgentWorktreeManager(tmp_path / "wt")

    async def scenario():
        return await asyncio.gather(
            manager.create_worktree(repo, "same"),
            manager.create_worktree(repo, "same"),
            return_exceptions=True,
        )

    results = run(scenario(), timeout=120)
    assert all(not isinstance(r, Exception) for r in results), results


def test_repository_path_inside_another_repository_is_rejected(repo, tmp_path):
    nested = repo / "src"
    nested.mkdir()
    manager = AgentWorktreeManager(tmp_path / "wt")
    with pytest.raises(ValueError, match="is not the root of a git repository"):
        run(manager.create_worktree(nested, "w"), timeout=60)
    assert manager.list_worktrees() == []
    assert git(repo, "branch", "--list", "agent-worktree-w") == ""


def test_concurrent_creates_for_distinct_slugs(repo, tmp_path):
    manager = AgentWorktreeManager(tmp_path / "wt")

    async def scenario():
        return await asyncio.gather(
            *(manager.create_worktree(repo, f"slug-{i}") for i in range(8)),
            return_exceptions=True,
        )

    results = run(scenario(), timeout=180)
    errors = [str(r) for r in results if isinstance(r, Exception)]
    assert errors == [], errors[:2]


def test_worktree_commands_of_one_repository_never_run_at_the_same_time(
    repo, tmp_path, monkeypatch
):
    from nailong_agent_sdk.tools import worktrees

    real = worktrees._run_git
    active = {"now": 0, "most": 0}

    async def observed(*args, cwd):
        if args[0] != "worktree":
            return await real(*args, cwd=cwd)
        active["now"] += 1
        active["most"] = max(active["most"], active["now"])
        try:
            await asyncio.sleep(0.05)
            return await real(*args, cwd=cwd)
        finally:
            active["now"] -= 1

    monkeypatch.setattr(worktrees, "_run_git", observed)
    manager = AgentWorktreeManager(tmp_path / "wt")

    async def scenario():
        created = await asyncio.gather(
            *(manager.create_worktree(repo, f"slug-{i}") for i in range(6)),
            return_exceptions=True,
        )
        removed = await asyncio.gather(
            *(manager.remove_worktree(f"slug-{i}") for i in range(6)), return_exceptions=True
        )
        return created, removed

    created, removed = run(scenario(), timeout=180)
    assert [item for item in created + removed if isinstance(item, Exception)] == []
    assert removed == [True] * 6
    assert active["most"] == 1


def test_case_variant_slug_message_is_accurate(repo, tmp_path):
    if sys.platform != "win32":
        pytest.skip("case-insensitive filesystem behaviour")
    manager = AgentWorktreeManager(tmp_path / "wt")
    run(manager.create_worktree(repo, "Foo"), timeout=60)
    with pytest.raises(Exception) as excinfo:
        run(manager.create_worktree(repo, "foo"), timeout=60)
    assert "not tracked" not in str(excinfo.value), str(excinfo.value)


def test_trailing_newline_slug_cannot_reach_git(repo, tmp_path):
    AgentWorktreeManager(tmp_path / "wt")
    try:
        validate_worktree_slug("a\n")
        passed = True
    except ValueError:
        passed = False
    assert not passed
