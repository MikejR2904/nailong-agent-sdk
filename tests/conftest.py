import pytest

from tests.support.processes import run_python


@pytest.fixture(autouse=True)
def git_discovery_ceiling(monkeypatch, tmp_path):
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path.parent))


@pytest.fixture
def run_py():
    return run_python
