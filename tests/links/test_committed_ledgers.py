import shutil

import pytest

from nailong_agent_sdk.foundations.identifiers import file_safe_name
from nailong_agent_sdk.observability.audit_log import AuditTranscriptStore
from nailong_agent_sdk.observability.run_checkpoints import TerminalLinkState
from nailong_agent_sdk.observability.telemetry_store import TelemetryStore
from tests.support.ledger_checks import ledger_summary
from tests.support.paths import REPO_ROOT

EXAMPLES = REPO_ROOT / "example_runs"
SCENARIOS = (
    sorted(path.name for path in EXAMPLES.iterdir() if (path / ".agent-telemetry").is_dir())
    if EXAMPLES.is_dir()
    else []
)


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_the_ledgers_committed_with_a_real_run_are_intact_and_complete(scenario, tmp_path):
    root = tmp_path / "run"
    root.mkdir()
    for name in (".agent-telemetry", ".agent-audit-logs"):
        source = EXAMPLES / scenario / name
        if source.is_dir():
            shutil.copytree(source, root / name)
    telemetry = TelemetryStore(root, read_only=True)
    audit = AuditTranscriptStore(root, read_only=True)
    try:
        runs = [summary.run_id for summary in telemetry.list_runs(limit=1_000)]
        audited = [
            run_id
            for run_id in runs
            if (root / ".agent-audit-logs" / f"{file_safe_name(run_id)}.jsonl").is_file()
        ]
        assert audited, f"{scenario} committed telemetry runs {runs} and no audit transcript"
        for run_id in audited:
            summary = ledger_summary(telemetry, audit, run_id)
            assert summary.terminal_link in {TerminalLinkState.ABSENT, TerminalLinkState.LINKED}
            assert summary.tool_requests == summary.tool_completions == summary.tool_results
            assert summary.turns_started >= summary.model_turns >= 1
    finally:
        telemetry.close()
        audit.close()
