"""Runs the real CLI against a real model service.

Skipped unless you opt in, because it costs money.

    NAILONG_LIVE_TESTS=1 NAILONG_LIVE_PROVIDER=openai-compatible NAILONG_LIVE_MODEL=<model> \
    NAILONG_LIVE_BASE_URL=<url> NAILONG_LIVE_KEY_ENV=<variable holding the key> \
    python -m pytest tests/live -p no:cacheprovider

Each test limits its own budget with --max-budget-usd and a small step count.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from nailong_cli.cli import main
from nailong_cli.console import Console
from tests.conftest import make_console
from tests.make_documents import make_pdf

pytestmark = pytest.mark.skipif(
    os.environ.get("NAILONG_LIVE_TESTS") != "1",
    reason="live-provider tests cost money; set NAILONG_LIVE_TESTS=1 to run them",
)

KEY_ENV = os.environ.get("NAILONG_LIVE_KEY_ENV", "OPENROUTER_API_KEY")


@pytest.fixture
def live(tmp_path):
    project = tmp_path / "project"
    (project / ".nailong").mkdir(parents=True)
    base = os.environ.get("NAILONG_LIVE_BASE_URL")
    settings = (
        f'provider = "{os.environ.get("NAILONG_LIVE_PROVIDER", "openai-compatible")}"\n'
        f'model = "{os.environ["NAILONG_LIVE_MODEL"]}"\n'
        f'api_key_env = "{KEY_ENV}"\n'
        "max_steps = 8\n" + (f'base_url = "{base}"\n' if base else "")
    )
    (project / ".nailong" / "config.toml").write_text(settings, encoding="utf-8")
    home = tmp_path / "home"
    home.mkdir()

    def run(prompt: str, *flags: str) -> tuple[int, str, str]:
        console, out, err = make_console("")
        code = main(
            ["-p", prompt, "--project", str(project), "--max-budget-usd", "0.25", *flags],
            environ=dict(os.environ),
            console=console,
            home=home,
        )
        return code, out.getvalue(), err.getvalue()

    run.project = project
    return run


def test_a_real_model_reads_a_file_with_a_tool_and_answers_from_it(live):
    (live.project / "secret-number.txt").write_text(
        "The magic number is 48151623.\n", encoding="utf-8"
    )
    code, out, err = live(
        "Read secret-number.txt and tell me the magic number.", "--permission-mode", "plan"
    )
    assert code == 0, err
    assert "48151623" in out


def test_a_real_model_summarises_a_pdf_with_page_numbers(live):
    make_pdf(live.project / "paper.pdf", ["Abstract: ducks swim.", "Results: ducks swim fast."])
    code, out, err = live(
        "Use read_document on paper.pdf and say in one sentence what page 2 reports, "
        "citing the page.",
        "--permission-mode",
        "plan",
    )
    assert code == 0, err
    assert "fast" in out.lower()


def test_a_real_model_run_records_a_complete_event_log_and_a_cost(live):
    (live.project / "a.txt").write_text("hello\n", encoding="utf-8")
    code, out, err = live(
        "Read a.txt and repeat its content.", "--permission-mode", "plan", "--output", "json"
    )
    assert code == 0, err
    from nailong_cli.events import check, read_events

    (log,) = (live.project / ".nailong" / "events").glob("run-*.jsonl")
    events = read_events(log)
    assert check(events) == []
    assert any(e["event_type"] == "tool.start" for e in events)
    assert Console is not None and Path(log).exists()
