import json
import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from nailong_agent_sdk.developer_tools.catalog import (
    build_public_api_catalog,
    write_public_api_catalog,
)
from nailong_agent_sdk.developer_tools.inspect import inspect_run
from nailong_agent_sdk.developer_tools.validate import (
    supported_contract_types,
    validate_contract_file,
)
from nailong_agent_sdk.observability.audit_log import AuditTranscriptStore
from nailong_agent_sdk.observability.telemetry_store import TelemetryStore
from tests.support.agents import (
    FnExecutor,
    agent,
    arun,
    definition,
    final,
    ok,
    task,
    tool,
    tool_call,
)
from tests.support.paths import REPO_ROOT
from tests.support.processes import child_environment


def locate_ruff():
    names = ("ruff.exe", "ruff") if os.name == "nt" else ("ruff",)
    for name in names:
        candidate = Path(sys.executable).parent / name
        if candidate.exists():
            return candidate
    located = shutil.which("ruff")
    return Path(located) if located else None


RUFF = locate_ruff()
needs_ruff = pytest.mark.skipif(RUFF is None, reason="ruff is not installed")


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


def outcome_of(result):
    lines = [line for line in result.stderr.strip().splitlines() if line.strip()]
    return {
        "code": result.returncode,
        "traceback": "Traceback" in result.stderr,
        "last_line": lines[-1][:160] if lines else "",
    }


def record_run(tmp_path, run_id, turns=1):
    telemetry = TelemetryStore(tmp_path)
    audit = AuditTranscriptStore(tmp_path)
    try:
        script = [tool_call(f"c{i}", "echo") for i in range(turns)] + [final()]
        subject, _ = agent(
            script,
            executor=FnExecutor(lambda t, c: ok({"v": 1})),
            definition_=definition(tools=[tool("echo")], max_iterations=turns + 10),
            telemetry=telemetry,
            audit_logs=audit,
        )
        arun(subject.run(task(run_id)), timeout=600)
    finally:
        telemetry.close()


def test_public_api_catalog_matches_all_and_is_deterministic(tmp_path):
    import nailong_agent_sdk as package

    names = list(package.__all__)
    assert len(names) == len(set(names)), "duplicate names in __all__"
    missing = [name for name in names if not hasattr(package, name)]
    assert missing == []
    catalog = build_public_api_catalog()
    assert [s.name for s in catalog.symbols] == sorted(names)
    first = write_public_api_catalog(tmp_path / "out" / "api.json")
    second = write_public_api_catalog(tmp_path / "out" / "api.json")
    assert (
        first == second
        and json.loads((tmp_path / "out" / "api.json").read_text("utf-8"))["package_name"]
        == "nailong-agent-sdk"
    )
    namespace: dict = {}
    exec("from nailong_agent_sdk import *", namespace)
    assert set(names) <= set(namespace)


def test_validate_contract_files(tmp_path):
    good_definition = definition().model_dump(mode="json")
    (tmp_path / "def.json").write_text(json.dumps(good_definition), "utf-8")
    assert validate_contract_file(tmp_path / "def.json", "agent-definition").valid is True
    (tmp_path / "bad.yaml").write_text(yaml.safe_dump({**good_definition, "identity": ""}), "utf-8")
    report = validate_contract_file(tmp_path / "bad.yaml", "agent-definition")
    assert report.valid is False and report.issues[0].path == "identity"
    (tmp_path / "task.yml").write_text(yaml.safe_dump(task().model_dump(mode="json")), "utf-8")
    assert validate_contract_file(tmp_path / "task.yml", "scoped-task").valid
    with pytest.raises(ValueError, match="Unsupported artifact type"):
        validate_contract_file(tmp_path / "task.yml", "nope")
    with pytest.raises(FileNotFoundError):
        validate_contract_file(tmp_path / "missing.json", "plan")
    (tmp_path / "x.txt").write_text("{}", "utf-8")
    with pytest.raises(ValueError, match=r"\.json, \.yaml, or \.yml"):
        validate_contract_file(tmp_path / "x.txt", "plan")
    (tmp_path / "list.yaml").write_text("- 1\n- 2\n", "utf-8")
    assert validate_contract_file(tmp_path / "list.yaml", "plan").valid is False
    assert supported_contract_types() == (
        "agent-definition",
        "plan",
        "scoped-task",
        "specification-manifest",
    )


def test_validate_accepts_a_json_file_saved_with_a_utf8_bom(tmp_path):
    (tmp_path / "bom.json").write_bytes(
        b"\xef\xbb\xbf" + json.dumps(definition().model_dump(mode="json")).encode()
    )
    assert validate_contract_file(tmp_path / "bom.json", "agent-definition").valid is True
    (tmp_path / "bom.yaml").write_bytes(
        b"\xef\xbb\xbf" + yaml.safe_dump(task().model_dump(mode="json")).encode()
    )
    assert validate_contract_file(tmp_path / "bom.yaml", "scoped-task").valid is True


def test_cli_validate_exit_codes_for_valid_and_invalid_contracts(tmp_path):
    good = tmp_path / "def.json"
    good.write_text(json.dumps(definition().model_dump(mode="json")), "utf-8")
    ok_run = cli("validate", "agent-definition", good)
    assert ok_run.returncode == 0 and json.loads(ok_run.stdout)["valid"] is True
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"identity": ""}), "utf-8")
    bad_run = cli("validate", "agent-definition", bad)
    assert bad_run.returncode == 1 and json.loads(bad_run.stdout)["valid"] is False
    unknown_type = cli("validate", "nonsense", good)
    assert unknown_type.returncode == 2 and "invalid choice" in unknown_type.stderr


def test_cli_operational_errors_are_reported_without_tracebacks_and_with_a_distinct_status(
    tmp_path,
):
    good = tmp_path / "def.json"
    good.write_text(json.dumps(definition().model_dump(mode="json")), "utf-8")
    garbage = tmp_path / "garbage.json"
    garbage.write_text("{not json", "utf-8")
    wrong_suffix = tmp_path / "def.txt"
    wrong_suffix.write_text("{}", "utf-8")
    invalid = tmp_path / "bad.json"
    invalid.write_text(json.dumps({"identity": ""}), "utf-8")
    empty_root = tmp_path / "empty-root"
    empty_root.mkdir()
    scenarios = {
        "contract_invalid": cli("validate", "agent-definition", invalid),
        "missing_file": cli("validate", "agent-definition", tmp_path / "nope.json"),
        "invalid_json": cli("validate", "agent-definition", garbage),
        "wrong_suffix": cli("validate", "agent-definition", wrong_suffix),
        "inspect_unknown_run": cli("inspect-run", tmp_path / "no-such-root", "ghost"),
        "quality_wrong_root": cli("quality", empty_root),
    }
    outcomes = {name: outcome_of(result) for name, result in scenarios.items()}
    crashes = {
        name: o for name, o in outcomes.items() if name != "contract_invalid" and o["traceback"]
    }
    same_code_as_invalid = [
        name
        for name, o in outcomes.items()
        if name != "contract_invalid" and o["code"] == outcomes["contract_invalid"]["code"]
    ]
    assert not crashes, f"raw tracebacks: {sorted(crashes)}"
    assert not same_code_as_invalid, (
        f"same exit status as an invalid contract: {same_code_as_invalid}"
    )


def test_cli_catalog_writes_a_file(tmp_path):
    target = tmp_path / "api.json"
    result = cli("catalog", "--output", target)
    assert result.returncode == 0 and target.is_file()
    assert json.loads(result.stdout)["package_name"] == "nailong-agent-sdk"


def test_inspect_run_reports_chain_health_and_unknown_runs(tmp_path):
    record_run(tmp_path, "inspected")
    report = inspect_run(tmp_path, "inspected")
    assert report.telemetry_chain_valid and report.audit_chain_valid and report.event_count > 5
    assert report.event_types["agent.terminated"] == 1 and report.audit_entry_count >= 3
    with pytest.raises(ValueError, match='Telemetry run "ghost" is unknown'):
        inspect_run(tmp_path, "ghost")
    database = tmp_path / ".agent-telemetry" / "telemetry.sqlite3"
    connection = sqlite3.connect(database)
    row = connection.execute(
        "SELECT event_json FROM events WHERE run_id='inspected' AND sequence=2"
    ).fetchone()
    payload = json.loads(row[0])
    payload["status"] = "tampered"
    connection.execute(
        "UPDATE events SET event_json=? WHERE run_id='inspected' AND sequence=2",
        (json.dumps(payload),),
    )
    connection.commit()
    connection.close()
    tampered = inspect_run(tmp_path, "inspected")
    assert tampered.telemetry_chain_valid is False and tampered.telemetry_chain_failure is not None
    result = cli("inspect-run", tmp_path, "inspected")
    assert result.returncode == 1 and json.loads(result.stdout)["telemetry_chain_valid"] is False


def test_forged_event_columns_are_not_reported_while_the_chain_verifies(tmp_path):
    record_run(tmp_path, "columns")
    before = inspect_run(tmp_path, "columns")
    database = tmp_path / ".agent-telemetry" / "telemetry.sqlite3"
    connection = sqlite3.connect(database)
    connection.execute(
        "UPDATE events SET status='FORGED', event_type='forged.type', payload_json=? "
        "WHERE run_id='columns'",
        ('{"forged":true}',),
    )
    connection.commit()
    connection.close()
    store = TelemetryStore(tmp_path)
    try:
        summary = next(s for s in store.list_runs() if s.run_id == "columns")
        chain_valid = store.chain_break("columns") is None
    finally:
        store.close()
    assert not chain_valid or summary.statuses == before.statuses, (
        f"list_runs reports {summary.statuses} while the chain verifies"
    )


def test_inspect_run_is_read_only_and_does_not_create_directories(tmp_path):
    typo = tmp_path / "typo-run-root"
    try:
        inspect_run(typo, "x")
    except ValueError:
        pass
    created = sorted(p.name for p in typo.rglob("*")) if typo.exists() else []
    assert not typo.exists(), f"inspect_run created {created} for a path that did not exist"


def test_inspect_run_counts_every_event_of_a_long_run(tmp_path):
    record_run(tmp_path, "long-inspected", turns=150)
    store = TelemetryStore(tmp_path)
    try:
        actual = sum(1 for _ in store.iter_events("long-inspected"))
    finally:
        store.close()
    report = inspect_run(tmp_path, "long-inspected")
    assert report.event_count == actual, (report.event_count, actual)


@needs_ruff
def test_cli_quality_agrees_with_ruff_on_this_checkout():
    result = cli("quality", REPO_ROOT, extra_path=RUFF.parent)
    payload = json.loads(result.stdout)
    check = subprocess.run(
        [RUFF, "check", "src", "--select", "F401,F811,E,F,I,UP"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
    )
    fmt = subprocess.run(
        [RUFF, "format", "--check", "src"], cwd=str(REPO_ROOT), capture_output=True, text=True
    )
    flagged = [
        line for line in (fmt.stdout + fmt.stderr).splitlines() if line.startswith("Would reformat")
    ]
    assert (check.returncode == 0 and fmt.returncode == 0) == payload["passed"]
    assert payload["passed"], f"the SDK's own quality gate fails on its own source: {flagged}"


@needs_ruff
def test_cli_quality_decodes_non_ascii_diagnostics(tmp_path):
    package = tmp_path / "src" / "nailong_agent_sdk"
    package.mkdir(parents=True)
    (package / "x.py").write_text("import os  # \u00c1 \u65e5\u672c\n", "utf-8")
    result = cli("quality", tmp_path, extra_path=RUFF.parent)
    outcome = outcome_of(result)
    assert not outcome["traceback"], outcome
    payload = json.loads(result.stdout)
    assert payload["passed"] is False and "\u00c1" in payload["diagnostics"], payload[
        "diagnostics"
    ][:200]


def test_cli_quality_without_ruff_names_the_cause(tmp_path):
    (tmp_path / "src" / "nailong_agent_sdk").mkdir(parents=True)
    env = child_environment({"PATH": str(tmp_path / "nowhere")})
    result = subprocess.run(
        [sys.executable, "-m", "nailong_agent_sdk.developer_tools.cli", "quality", str(tmp_path)],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(tmp_path),
    )
    assert "Ruff is unavailable" in result.stderr


def test_cli_operational_errors_exit_with_status_3_and_name_the_input_and_cause(tmp_path):
    garbage = tmp_path / "garbage.json"
    garbage.write_text("{not json", "utf-8")
    broken_yaml = tmp_path / "broken.yaml"
    broken_yaml.write_text("a: [1, 2", "utf-8")
    wrong_suffix = tmp_path / "def.txt"
    wrong_suffix.write_text("{}", "utf-8")
    empty_root = tmp_path / "empty-root"
    empty_root.mkdir()
    cases = [
        (cli("validate", "agent-definition", tmp_path / "nope.json"), "does not exist"),
        (cli("validate", "agent-definition", garbage), "is not valid JSON"),
        (cli("validate", "agent-definition", broken_yaml), "is not valid YAML"),
        (cli("validate", "agent-definition", wrong_suffix), 'got ".txt"'),
        (cli("inspect-run", tmp_path / "no-such-root", "ghost"), "does not exist or is not a dir"),
        (cli("inspect-run", empty_root, "ghost"), "has no telemetry database"),
        (cli("quality", empty_root), "must contain src/nailong_agent_sdk"),
    ]
    for result, expected in cases:
        assert result.returncode == 3, (expected, result.stderr)
        assert result.stdout == "" and "Traceback" not in result.stderr
        assert result.stderr.startswith("nailong-agent-sdk-dev ")
        assert expected in result.stderr, (expected, result.stderr)
    assert "garbage.json" in cases[1][0].stderr and "broken.yaml" in cases[2][0].stderr


def test_inspect_run_never_creates_the_audit_directory_of_a_telemetry_only_root(tmp_path):
    telemetry = TelemetryStore(tmp_path)
    try:
        subject, _ = agent([final()], telemetry=telemetry)
        arun(subject.run(task("telemetry-only")), timeout=600)
    finally:
        telemetry.close()
    assert not (tmp_path / ".agent-audit-logs").exists()
    report = inspect_run(tmp_path, "telemetry-only")
    assert report.telemetry_chain_valid and report.audit_chain_valid
    assert report.audit_entry_count == 0
    assert not (tmp_path / ".agent-audit-logs").exists()


def record_evidence(root, project="p1", count=3):
    from nailong_agent_sdk.foundations.contracts import ToolCall, ToolExecutionResult
    from nailong_agent_sdk.memory.context_projection import FileToolResultJournal
    from nailong_agent_sdk.state.project_state_engine import ProjectStateReducer
    from nailong_agent_sdk.state.project_state_models import StageStateSchema
    from nailong_agent_sdk.state.project_state_store import FileProjectStateStore

    journal = FileToolResultJournal(root)
    store = FileProjectStateStore(root)
    store.ensure(project, StageStateSchema(schema_id="s-v1", stage="design"))
    handles = []
    for index in range(count):
        call = ToolCall(id=f"c{index}", name="echo", arguments={"i": index})
        result = ToolExecutionResult(status="succeeded", output={"v": index})
        handle = journal.record(call, result)
        store.apply(project, ProjectStateReducer.tool_transition(call, result, handle))
        handles.append(handle)
    return handles


def test_verify_project_evidence_accepts_untouched_journals(tmp_path):
    from nailong_agent_sdk.developer_tools.inspect import verify_project_evidence

    record_evidence(tmp_path)
    report = verify_project_evidence(tmp_path, "p1")
    assert report.verified is True and report.checked == 3 and report.mismatches == []


def test_verify_project_evidence_reports_overwritten_missing_and_corrupt_results(tmp_path):
    from nailong_agent_sdk.developer_tools.inspect import verify_project_evidence

    handles = record_evidence(tmp_path)
    journal = tmp_path / ".agent-tool-results"
    (journal / f"{handles[0].handle_id}.json").write_text("{not json", "utf-8")
    (journal / f"{handles[1].handle_id}.json").write_text('{"call":{},"result":{}}', "utf-8")
    (journal / f"{handles[2].handle_id}.json").unlink()
    report = verify_project_evidence(tmp_path, "p1")
    assert report.verified is False and report.checked == 3
    by_id = {m.evidence_id: m for m in report.mismatches}
    assert set(by_id) == {handle.handle_id for handle in handles}
    assert "is corrupt" in by_id[handles[0].handle_id].reason
    changed = by_id[handles[1].handle_id]
    assert changed.recorded_hash == handles[1].content_hash
    assert changed.journal_hash and changed.journal_hash != changed.recorded_hash
    assert changed.recorded_hash in changed.reason and changed.journal_hash in changed.reason
    assert 'project "p1" at revision 2' in changed.reason
    assert "has no journal file" in by_id[handles[2].handle_id].reason


def test_verify_project_evidence_is_read_only_and_names_unknown_inputs(tmp_path):
    from nailong_agent_sdk.developer_tools.inspect import verify_project_evidence

    with pytest.raises(ValueError, match="does not exist or is not a directory"):
        verify_project_evidence(tmp_path / "no-such-root", "p1")
    assert not (tmp_path / "no-such-root").exists()
    with pytest.raises(ValueError, match='Project state "p1" is unknown'):
        verify_project_evidence(tmp_path, "p1")
    assert not (tmp_path / ".agent-project-state").exists()
    record_evidence(tmp_path)
    with pytest.raises(ValueError, match='Project state "ghost" is unknown'):
        verify_project_evidence(tmp_path, "ghost")


def test_cli_verify_evidence_uses_the_verdict_exit_status(tmp_path):
    handles = record_evidence(tmp_path)
    clean = cli("verify-evidence", tmp_path, "p1")
    assert clean.returncode == 0 and json.loads(clean.stdout)["verified"] is True
    (tmp_path / ".agent-tool-results" / f"{handles[0].handle_id}.json").write_text("{}", "utf-8")
    tampered = cli("verify-evidence", tmp_path, "p1")
    assert tampered.returncode == 1 and json.loads(tampered.stdout)["verified"] is False
    unknown = cli("verify-evidence", tmp_path, "ghost")
    assert unknown.returncode == 3 and 'Project state "ghost" is unknown' in unknown.stderr
