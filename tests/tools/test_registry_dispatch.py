import sys

from nailong_agent_sdk.tools.policy import CapabilityGrant, SideEffectClass
from nailong_agent_sdk.tools.registry import HarnessToolRegistry, RegisteredTool
from nailong_agent_sdk.tools.supervisor import CommandTemplate
from tests.support.tools import build, call, run


def template(name, code):
    return CommandTemplate(name=name, command=[sys.executable, "-c", code], timeout_seconds=30)


def test_a_command_template_tool_runs_its_template_and_names_the_tool_when_it_fails(tmp_path):
    templates = [
        template("verilator", "print('lint clean')"),
        template("yosys", "import sys; print('synthesis failed'); sys.exit(2)"),
    ]
    executor, _ = build(tmp_path, templates=templates, approve=("rtl.verilator", "rtl.yosys"))
    clean = run(call(executor, "run_verilator"))
    assert clean.status == "succeeded"
    assert clean.output["template_name"] == "verilator" and "lint clean" in clean.output["output"]
    failed = run(call(executor, "run_yosys"))
    assert failed.status == "failed" and failed.output["return_code"] == 2
    assert failed.error == (
        'PROCESS_EXIT_NONZERO: registered command "run_yosys" exited with code 2.'
    )


def test_a_command_template_tool_without_a_registered_template_names_it(tmp_path):
    executor, _ = build(tmp_path, templates=[], approve=("physical.openroad",))
    result = run(call(executor, "run_openroad"))
    assert result.status == "failed" and '"openroad"' in result.error


def test_grep_artifact_reports_the_matching_lines_of_an_authorized_artifact(tmp_path):
    executor, context = build(tmp_path)
    record = context.artifacts.write_text("notes.txt", "alpha\nbeta needle\ngamma\nneedle again\n")
    stranger = context.artifacts.write_text("stranger.txt", "unrelated\n")
    context.plan_task.authorized_artifact_ids.append(record.artifact_id)
    found = run(call(executor, "grep_artifact", artifact_id=record.artifact_id, needle="needle"))
    assert found.status == "succeeded"
    assert found.output == {"artifact_id": record.artifact_id, "matches": [2, 4]}
    missing = run(call(executor, "grep_artifact", artifact_id=record.artifact_id))
    assert (
        missing.status == "failed"
        and 'Argument "needle" must be a non-empty string' in missing.error
    )
    no_base = run(
        call(
            executor,
            "diff_declared_artifacts",
            base_artifact_id=stranger.artifact_id,
            draft_artifact_id=record.artifact_id,
        )
    )
    assert no_base.status == "failed" and "Base artifact is not authorized" in no_base.error


def test_a_registered_tool_with_neither_handler_nor_template_says_so(tmp_path):
    registry = HarnessToolRegistry(
        [RegisteredTool("orphan", "orphan.use", SideEffectClass.READ_ONLY)]
    )
    grants = [CapabilityGrant(role="worker", capabilities=["orphan.use"], allowed_paths=["."])]
    executor, _ = build(tmp_path, grants=grants, registry=registry, approve=())
    result = run(call(executor, "orphan"))
    assert result.status == "failed"
    assert 'raised ValueError: No implementation is registered for "orphan".' in result.error
