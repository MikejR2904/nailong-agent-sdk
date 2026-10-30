import sys
import time

from nailong_agent_sdk.tools.supervisor import CommandTemplate
from tests.support.tools import build, call, run


def test_run_registered_command_result_shapes(tmp_path):
    templates = [
        CommandTemplate(
            name="ok", command=[sys.executable, "-c", "print('hi')"], timeout_seconds=30
        ),
        CommandTemplate(
            name="fail",
            command=[sys.executable, "-c", "import sys; print('boom'); sys.exit(3)"],
            timeout_seconds=30,
        ),
        CommandTemplate(
            name="slow",
            command=[sys.executable, "-c", "import time; time.sleep(60)"],
            timeout_seconds=1,
        ),
        CommandTemplate(name="missing", command=["definitely-not-a-binary-xyz"], timeout_seconds=5),
        CommandTemplate(
            name="big",
            command=[sys.executable, "-c", "print('x' * 1_000_000)"],
            timeout_seconds=30,
            max_output_bytes=1000,
        ),
    ]
    executor, ctx = build(tmp_path, templates=templates, approve=("process.execute",))
    ok = run(call(executor, "run_registered_command", template_name="ok"))
    assert ok.status == "succeeded" and ok.output["exit_kind"] == "succeeded"
    fail = run(call(executor, "run_registered_command", template_name="fail"))
    assert fail.status == "failed" and fail.output["return_code"] == 3
    assert "3" in fail.error, fail.error
    slow_started = time.monotonic()
    slow = run(call(executor, "run_registered_command", template_name="slow"))
    assert time.monotonic() - slow_started < 15
    assert slow.status == "failed" and slow.output["timed_out"] is True
    assert "PROCESS_TIMEOUT" in slow.error
    missing = run(call(executor, "run_registered_command", template_name="missing"))
    assert missing.status == "failed"
    assert "definitely-not-a-binary-xyz" in missing.error, missing.error
    big = run(call(executor, "run_registered_command", template_name="big"))
    assert big.status == "succeeded"
    assert big.output["output_bytes_retained"] == 1000
    assert big.output["output_bytes_total"] >= 1_000_000
    unknown = run(call(executor, "run_registered_command", template_name="nope"))
    assert unknown.status == "failed" and '"nope"' in unknown.error


def test_sleep_and_brief_bounds(tmp_path):
    executor, _ = build(tmp_path)
    for bad, fragment in (
        (-1, "between 0 and 30"),
        (31, "between 0 and 30"),
        (True, "numeric"),
        ("1", "numeric"),
        (float("nan"), "between 0 and 30"),
        (float("inf"), "between 0 and 30"),
    ):
        result = run(call(executor, "sleep", seconds=bad))
        assert result.status == "failed" and fragment in result.error, (bad, result.error)
    quick = run(call(executor, "sleep", seconds=0.05))
    assert quick.status == "succeeded"
    brief = run(call(executor, "brief", text="x" * 5000, max_chars=100))
    assert len(brief.output["text"]) == 100 and brief.output["truncated"] is True
    low = run(call(executor, "brief", text="abc", max_chars=19))
    assert low.status == "failed" and "between 20 and 2000" in low.error
    run(call(executor, "brief", text=""))


def test_web_search_and_human_question_paths(tmp_path):
    from nailong_agent_sdk.tools.core.services import SearchResult

    class FakeClient:
        async def search(self, query, limit):
            if query == "boom":
                raise RuntimeError("search backend exploded")
            return [
                SearchResult(title=f"t{i}", url=f"https://e.test/{i}", snippet="s")
                for i in range(20)
            ]

    async def human(question):
        return None if question == "silent" else f"answer to {question}"

    executor, _ = build(tmp_path, search_client=FakeClient(), ask_human=human)
    found = run(call(executor, "web_search", query="q", max_results=3))
    assert len(found.output["results"]) == 3 and found.output["untrusted_content"] is True
    boom = run(call(executor, "web_search", query="boom"))
    assert boom.status == "failed" and "search backend exploded" in boom.error
    zero = run(call(executor, "web_search", query="q", max_results=0))
    assert zero.status == "failed" and "between 1 and 10" in zero.error
    answered = run(call(executor, "ask_human_question", question="why"))
    assert answered.output == {"answer": "answer to why", "answered": True}
    silent = run(call(executor, "ask_human_question", question="silent"))
    assert silent.output == {"answer": None, "answered": False}
    bare, _ = build(tmp_path / "bare")
    missing = run(call(bare, "ask_human_question", question="x"))
    assert missing.status == "failed" and "No interactive human-question responder" in missing.error
