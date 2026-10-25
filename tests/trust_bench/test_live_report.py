import argparse

from trust_bench.v2 import commands
from trust_bench.v2.report import live_section


def row(scenario, tool_calls, **extra):
    return {
        "scenario": scenario,
        "model": "m",
        "status": "completed",
        "checks": {"telemetry_chain": "intact", "audit_chain": "intact", "anchor": "matches"},
        "cut_back": {
            "events-3_detected": True,
            "audit-2_detected": True,
            "both_detected": True,
        },
        "event_types": {"agent.tool-requested": tool_calls},
        "files_outside_declared_output": [],
        **extra,
    }


def test_no_live_rows_are_reported_as_not_run():
    assert "NOT RUN" in "\n".join(live_section([]))


def test_a_model_that_never_attempted_the_forbidden_write_is_said_to_leave_the_boundary_untested():
    text = "\n".join(live_section([row("injected", 2), row("escape-instructed", 2)]))
    assert "attempted it in 0 of 2 runs" in text and "not exercised by a model" in text


def test_an_attempted_write_is_counted():
    text = "\n".join(live_section([row("injected", 3), row("escape-instructed", 2)]))
    assert "attempted it in 1 of 2 runs" in text


def test_the_live_command_refuses_to_call_a_model_without_the_confirmation_flag(capsys):
    args = argparse.Namespace(
        command="live",
        confirm_model_calls=False,
        reps=1,
        model="m",
        scenarios="",
        out=None,
    )
    assert commands.dispatch(args) == 2
    assert "--confirm-model-calls" in capsys.readouterr().out
