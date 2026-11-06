from __future__ import annotations

import re

import pytest

from nailong_cli.config import ConfigError
from tests.conftest import call, final
from tests.test_sdk_features import user_only_project
from tests.test_terminal_ui import run_fancy

BYPASS = ["--permission-mode", "bypass", "--allow-unsandboxed"]


def test_command_output_appears_as_the_command_prints_not_only_in_the_summary(project, home):
    command = "python -c \"import sys; [print(f'row {n}') for n in range(3)]\""
    code, out, _ = run_fancy(
        project,
        home,
        call("run_command", command=command),
        final("done"),
        lines="Run it\n/exit\n",
        argv=BYPASS,
    )
    assert code == 0
    assert out.count("row 0") == 1 and "row 2" in out
    assert out.index("row 0") < out.index("exit 0")


def test_a_flood_of_output_is_cut_live_with_a_note_and_stays_available_in_expand(project, home):
    command = "python -c \"[print(f'line {n}') for n in range(100)]\""
    code, out, _ = run_fancy(
        project,
        home,
        call("run_command", command=command),
        final("done"),
        lines="Run it\n/expand\n/exit\n",
        argv=BYPASS,
    )
    assert code == 0
    live = out.split("/expand")[0]
    assert "more output is not shown live" in out
    assert len(re.findall(r"line \d+", live)) < 60
    assert "line 99" in out


def test_the_time_limits_have_defaults_and_can_be_changed_or_switched_off(project, home):
    config = user_only_project(project, home, "")
    assert (config.model_timeout_seconds, config.tool_timeout_seconds) == (300.0, 600.0)
    assert config.run_deadline_seconds == 0
    custom = user_only_project(
        project, home, "model_timeout_seconds = 0\nrun_deadline_seconds = 90\n"
    )
    assert custom.model_timeout_seconds == 0 and custom.run_deadline_seconds == 90


@pytest.mark.parametrize("value", ["-1", '"soon"', "true"])
def test_a_bad_time_limit_names_the_setting(project, home, value):
    with pytest.raises(ConfigError, match="tool_timeout_seconds must be 0"):
        user_only_project(project, home, f"tool_timeout_seconds = {value}\n")
