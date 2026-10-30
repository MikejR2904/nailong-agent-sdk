import shutil
import subprocess

import pytest

from tests.support.paths import REPO_ROOT

MAX_DATA_BYTES = 500_000
MAX_FILE_BYTES = 5_000_000
DATA_SUFFIXES = (".json", ".jsonl")
RECORDED_LEDGERS = "example_runs/"


def tracked_files():
    if shutil.which("git") is None or not (REPO_ROOT / ".git").exists():
        pytest.skip("not a git checkout")
    out = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "ls-files", "-z"], capture_output=True, check=True
    ).stdout
    return [name for name in out.decode("utf-8").split("\0") if name]


def sizes():
    return {
        name: (REPO_ROOT / name).stat().st_size
        for name in tracked_files()
        if (REPO_ROOT / name).is_file()
    }


def test_no_tracked_data_file_is_large_outside_the_recorded_ledgers():
    large = {
        name: size
        for name, size in sizes().items()
        if name.endswith(DATA_SUFFIXES)
        and size > MAX_DATA_BYTES
        and not name.startswith(RECORDED_LEDGERS)
    }
    assert large == {}, (
        f"tracked JSON files above {MAX_DATA_BYTES:,} bytes: keep them out of git "
        "(add them to .gitignore and run git rm --cached)"
    )


def test_no_tracked_file_exceeds_the_hard_limit():
    large = {name: size for name, size in sizes().items() if size > MAX_FILE_BYTES}
    assert large == {}, f"tracked files above {MAX_FILE_BYTES:,} bytes: {sorted(large)}"


def test_per_seed_benchmark_dumps_stay_untracked():
    dumps = [
        name
        for name in tracked_files()
        if name.startswith("benchmarks/dfl/results/")
        and name.rsplit("/", 1)[-1].startswith("seed")
        and name.endswith(".json")
    ]
    assert dumps == []
