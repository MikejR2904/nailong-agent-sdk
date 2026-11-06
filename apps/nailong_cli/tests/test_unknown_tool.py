from __future__ import annotations

import json

from tests.conftest import call, final, run_cli


def test_a_tool_that_is_not_offered_is_an_error_the_model_can_recover_from(project, home):
    code, out, _, setup = run_cli(
        project, home, ["-p", "x"], call("no_such_tool", x=1), final("recovered")
    )
    assert code == 0 and "recovered" in out
    sent = json.dumps(setup.model.calls[1].observations, default=str)
    assert "is not available in this session" in sent and "read_file" in sent


def test_a_write_the_model_remembers_after_plan_mode_is_refused_not_fatal(project, home):
    code, out, _, _ = run_cli(
        project,
        home,
        ["-p", "x", "--permission-mode", "plan"],
        call("write_file", path="a.txt", content="x"),
        final("carried on"),
    )
    assert code == 0 and not (project / "a.txt").exists()
