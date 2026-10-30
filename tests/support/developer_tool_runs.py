import os
import subprocess
import sys

from tests.support.paths import REPO_ROOT
from tests.support.processes import child_environment


def cli(*args, cwd=None, extra_path=None, timeout=300):
    env = child_environment()
    if extra_path:
        env["PATH"] = str(extra_path) + os.pathsep + env["PATH"]
    return subprocess.run(
        [sys.executable, "-m", "nailong_agent_sdk.developer_tools.cli", *map(str, args)],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(cwd or REPO_ROOT),
        timeout=timeout,
    )
