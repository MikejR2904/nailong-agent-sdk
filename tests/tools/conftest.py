import pytest

from tests.support.git_repos import git


@pytest.fixture
def repo(tmp_path):
    path = tmp_path / "repo"
    path.mkdir()
    git(path, "init", "-q", "-b", "main")
    git(path, "config", "user.email", "audit@example.com")
    git(path, "config", "user.name", "audit")
    (path / "f.txt").write_text("one\n", "utf-8")
    git(path, "add", ".")
    git(path, "commit", "-q", "-m", "init")
    return path
