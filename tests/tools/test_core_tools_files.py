import asyncio
import json
import subprocess
import sys
import time

import pytest

from nailong_agent_sdk.tools.core.services import CoreToolDispatcher, CoreToolServices
from nailong_agent_sdk.tools.policy import CapabilityGrant, CapabilityPolicy, SideEffectClass
from tests.support.extended_paths import resolve_files_in_extended_spelling, windows_only
from tests.support.processes import child_environment
from tests.support.tools import build, call, mkjunction, run


def test_read_file_roundtrip_and_pagination(tmp_path):
    executor, ctx = build(tmp_path)
    (ctx.run_root / "a.txt").write_text("\n".join(f"line {i}" for i in range(1, 11)), "utf-8")
    result = run(call(executor, "read_file", path="a.txt", offset=2, limit=3))
    assert result.status == "succeeded"
    assert [row["line"] for row in result.output["lines"]] == [3, 4, 5]
    assert result.output["lines"][0]["text"] == "line 3"
    assert result.output["truncated"] is True
    assert result.output["path"] == "a.txt"
    last = run(call(executor, "read_file", path="a.txt", offset=8, limit=50))
    assert last.output["truncated"] is False
    assert len(last.output["lines"]) == 2


@pytest.mark.parametrize(
    "path",
    ["../outside.txt", "..\\outside.txt", "sub/../../outside.txt", "ABS"],
)
def test_read_file_rejects_traversal_and_absolute(tmp_path, path):
    executor, ctx = build(tmp_path)
    outside = tmp_path / "outside.txt"
    outside.write_text("OUTSIDE-SECRET", "utf-8")
    (ctx.run_root / "sub").mkdir()
    target = str(outside) if path == "ABS" else path
    result = run(call(executor, "read_file", path=target))
    assert result.status == "failed"
    assert "OUTSIDE-SECRET" not in json.dumps(result.model_dump(mode="json"))
    assert result.error


def test_directory_junction_escape_is_rejected_everywhere(tmp_path):
    executor, ctx = build(tmp_path)
    outside = tmp_path / "outside_dir"
    outside.mkdir()
    (outside / "data.txt").write_text("JUNCTION-SECRET", "utf-8")
    if not mkjunction(ctx.run_root / "link", outside):
        pytest.skip("cannot create junction/symlink in this environment")
    read = run(call(executor, "read_file", path="link/data.txt"))
    assert read.status == "failed" and "JUNCTION-SECRET" not in json.dumps(
        read.model_dump(mode="json")
    )
    globbed = run(call(executor, "glob", pattern="link/*"))
    assert globbed.output["matches"] == []
    globbed_all = run(call(executor, "glob", pattern="**/*"))
    assert not any("data.txt" in match for match in globbed_all.output["matches"])
    grepped = run(call(executor, "grep", pattern="JUNCTION-SECRET"))
    assert grepped.output["matches"] == []


def test_read_file_binary_oversize_directory_messages(tmp_path):
    executor, ctx = build(tmp_path)
    (ctx.run_root / "bin.dat").write_bytes(b"\xff\xfe\x00\x80binary")
    (ctx.run_root / "big.txt").write_text("x" * 600_000, "utf-8")
    (ctx.run_root / "dir").mkdir()
    binary = run(call(executor, "read_file", path="bin.dat"))
    assert binary.status == "failed" and "not valid UTF-8" in binary.error
    big = run(call(executor, "read_file", path="big.txt"))
    assert big.status == "failed" and "read-byte limit" in big.error
    folder = run(call(executor, "read_file", path="dir"))
    assert folder.status == "failed" and "regular file" in folder.error
    missing = run(call(executor, "read_file", path="nope.txt"))
    assert missing.status == "failed" and "does not exist" in missing.error


def test_line_numbers_match_newline_delimited_lines(tmp_path):
    executor, ctx = build(tmp_path)
    (ctx.run_root / "ff.txt").write_text("alpha\x0cbeta\nGAMMA\u2028delta\nend", "utf-8")
    result = run(call(executor, "read_file", path="ff.txt"))
    newline_lines = "alpha\x0cbeta\nGAMMA\u2028delta\nend".split("\n")
    shown = [row["text"] for row in result.output["lines"]]
    assert len(shown) == len(newline_lines)


def test_glob_rules_and_limits(tmp_path):
    executor, ctx = build(tmp_path)
    for name in ("b.txt", "a.txt", "sub/c.txt", "sub/deep/d.md"):
        target = ctx.run_root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("x", "utf-8")
    ok = run(call(executor, "glob", pattern="**/*.txt"))
    assert ok.output["matches"] == sorted(ok.output["matches"])
    assert {m.replace("\\", "/") for m in ok.output["matches"]} == {"a.txt", "b.txt", "sub/c.txt"}
    for bad in (
        "../*",
        "/etc/*",
        "sub/../../*",
        "C:\\Windows\\*",
        "D:data/*",
        "\\\\server\\share\\*",
        "sub\\..\\..\\*",
    ):
        result = run(call(executor, "glob", pattern=bad))
        assert result.status == "failed", bad
        assert f'Glob pattern "{bad}" must remain below the configured run root' in result.error
    limited = run(call(executor, "glob", pattern="**/*", limit=2))
    assert len(limited.output["matches"]) == 2
    empty_pattern = run(call(executor, "glob", pattern=""))
    assert empty_pattern.status == "failed" and "non-empty string" in empty_pattern.error


def test_grep_basic_flags_limit_and_errors(tmp_path):
    executor, ctx = build(tmp_path)
    (ctx.run_root / "a.txt").write_text("Needle one\nnothing\nneedle two\n", "utf-8")
    (ctx.run_root / "sub").mkdir()
    (ctx.run_root / "sub" / "c.md").write_text("a needle here\n", "utf-8")
    sensitive = ctx.run_root / ".aws"
    sensitive.mkdir()
    (sensitive / "credentials").write_text("needle-secret\n", "utf-8")
    case = run(call(executor, "grep", pattern="needle"))
    paths = sorted((m["path"].replace("\\", "/"), m["line"]) for m in case.output["matches"])
    assert paths == [("a.txt", 3), ("sub/c.md", 1)]
    insensitive = run(call(executor, "grep", pattern="needle", case_sensitive=False))
    assert len(insensitive.output["matches"]) == 3
    limited = run(call(executor, "grep", pattern="needle", case_sensitive=False, limit=1))
    assert len(limited.output["matches"]) == 1 and limited.output["truncated"] is True
    scoped = run(call(executor, "grep", pattern="needle", file_glob="sub/*.md"))
    assert [m["path"].replace("\\", "/") for m in scoped.output["matches"]] == ["sub/c.md"]
    invalid = run(call(executor, "grep", pattern="("))
    assert invalid.status == "failed" and "GREP_REGEX_INVALID" in invalid.error
    assert "unterminated subpattern" in invalid.error or "missing )" in invalid.error


def test_grep_catastrophic_regex_is_bounded_and_named(tmp_path):
    (tmp_path / "run").mkdir()
    services = CoreToolServices(
        root=tmp_path / "run",
        artifacts=build(tmp_path)[1].artifacts,
        declared_output_paths=(),
        max_grep_seconds=3.0,
    )
    (tmp_path / "run" / "evil.txt").write_text("a" * 40 + "b\n", "utf-8")
    dispatcher = CoreToolDispatcher(services)
    started = time.monotonic()
    result = run(dispatcher.execute("grep", {"pattern": "(a+)+$"}))
    elapsed = time.monotonic() - started
    assert result.status == "failed" and "GREP_REGEX_TIMEOUT" in result.error
    assert elapsed < 8


def test_grep_latency_and_parallel_batch(tmp_path):
    executor, ctx = build(tmp_path)
    (ctx.run_root / "a.txt").write_text("hello world\n", "utf-8")
    singles = []
    for _ in range(3):
        started = time.monotonic()
        result = run(call(executor, "grep", pattern="hello"))
        singles.append(time.monotonic() - started)
        assert result.status == "succeeded"

    async def batch(width):
        started = time.monotonic()
        results = await asyncio.gather(
            *(call(executor, "grep", pattern="hello") for _ in range(width))
        )
        return time.monotonic() - started, results

    durations = {}
    failures = {}
    for width in (4, 8, 16):
        elapsed, results = run(batch(width), timeout=180)
        durations[width] = elapsed
        failures[width] = [r.error for r in results if r.status != "succeeded"]
    for width, errors in failures.items():
        assert errors == [], f"parallel width {width}: {errors[:2]}"


@pytest.mark.parametrize(
    "arguments, fragment",
    [
        ({"path": 123}, '"path"'),
        ({"path": ""}, '"path"'),
        ({"path": "   "}, "non-empty"),
        ({"path": "a.txt", "offset": 1.0}, '"offset"'),
        ({"path": "a.txt", "offset": "1"}, '"offset"'),
        ({"path": "a.txt", "offset": True}, '"offset"'),
        ({"path": "a.txt", "offset": -1}, '"offset"'),
        ({"path": "a.txt", "limit": 0}, '"limit"'),
        ({"path": "a.txt", "limit": 2001}, '"limit"'),
        ({"path": "a.txt", "limit": None}, '"limit"'),
        ({"path": "a\x00b.txt"}, ""),
    ],
)
def test_read_file_argument_type_confusion_is_rejected_cleanly(tmp_path, arguments, fragment):
    executor, ctx = build(tmp_path)
    (ctx.run_root / "a.txt").write_text("hello", "utf-8")
    result = run(call(executor, "read_file", **arguments))
    assert result.status == "failed"
    assert result.error and fragment in result.error, result.error


def test_unicode_and_spaced_paths(tmp_path):
    executor, ctx = build(tmp_path, declared=("dir with space/résumé 文件.md",))
    wrote = run(call(executor, "write_draft", path="dir with space/résumé 文件.md", content="ok"))
    assert wrote.status == "succeeded"
    read = run(call(executor, "read_file", path="dir with space/résumé 文件.md"))
    assert read.output["lines"][0]["text"] == "ok"
    matched = run(call(executor, "glob", pattern="dir with space/*"))
    assert matched.output["matches"] == ["dir with space\\résumé 文件.md"] or matched.output[
        "matches"
    ] == ["dir with space/résumé 文件.md"]


@windows_only
def test_policy_scopes_hold_when_a_file_resolves_in_the_extended_spelling(tmp_path, monkeypatch):
    policy = CapabilityPolicy(
        [CapabilityGrant(role="r", capabilities=["draft.write"], allowed_paths=["out"])]
    )
    resolve_files_in_extended_spelling(monkeypatch)

    def evaluate(requested):
        return policy.evaluate(
            role="r",
            capability="draft.write",
            side_effect=SideEffectClass.MUTATING,
            run_root=tmp_path,
            requested_paths=[requested],
        )

    inside = evaluate("out/a.txt")
    assert inside.allowed is False and inside.approval_required is True
    for requested in ("elsewhere/a.txt", "out/../../x", "out_evil/a.txt"):
        denied = evaluate(requested)
        assert denied.allowed is False and denied.approval_required is False, requested
        assert "outside the declared capability scope" in denied.reason, requested


@windows_only
def test_core_tools_work_when_a_file_resolves_in_the_extended_spelling(tmp_path, monkeypatch):
    executor, ctx = build(tmp_path, declared=("out/draft.md",))
    (ctx.run_root / "a.txt").write_text("alpha\n", "utf-8")
    (tmp_path / "outside.txt").write_text("OUTSIDE-SECRET", "utf-8")
    resolve_files_in_extended_spelling(monkeypatch)
    read = run(call(executor, "read_file", path="a.txt"))
    assert read.status == "succeeded" and read.output["lines"][0]["text"] == "alpha"
    wrote = run(call(executor, "write_draft", path="out/draft.md", content="drafted"))
    assert wrote.status == "succeeded", wrote.error
    assert (ctx.run_root / "out" / "draft.md").read_text("utf-8") == "drafted"
    assert run(call(executor, "glob", pattern="*.txt")).output["matches"] == ["a.txt"]
    nested = run(call(executor, "glob", pattern="out/*.md")).output["matches"]
    assert [match.replace("\\", "/") for match in nested] == ["out/draft.md"]
    escape = run(call(executor, "read_file", path="../outside.txt"))
    assert escape.status in {"failed", "blocked"}
    assert "OUTSIDE-SECRET" not in json.dumps(escape.model_dump(mode="json"))


def test_grep_parallel_width_threshold(tmp_path):
    executor, ctx = build(tmp_path)
    (ctx.run_root / "a.txt").write_text("hello world\n", "utf-8")

    async def batch(width):
        started = time.monotonic()
        results = await asyncio.gather(
            *(call(executor, "grep", pattern="hello") for _ in range(width))
        )
        return time.monotonic() - started, [r.error for r in results if r.status != "succeeded"]

    report = {}
    for width in (32, 64):
        elapsed, errors = run(batch(width), timeout=400)
        report[width] = {"seconds": elapsed, "failed": len(errors), "sample": errors[:1]}
    assert all(entry["failed"] == 0 for entry in report.values()), report


def test_grep_line_numbers_follow_newlines(tmp_path):
    executor, ctx = build(tmp_path)
    (ctx.run_root / "ff.txt").write_text("alpha\x0cbeta\nGAMMA delta\nend", "utf-8")
    result = run(call(executor, "grep", pattern="delta"))
    assert [(m["path"], m["line"]) for m in result.output["matches"]] == [("ff.txt", 2)]


def test_grep_does_not_rerun_a_host_script_that_lacks_a_main_guard(tmp_path):
    marker = tmp_path / "runs.txt"
    script = tmp_path / "host.py"
    script.write_text(
        "\n".join(
            [
                "import asyncio, pathlib",
                "from nailong_agent_sdk.tools.artifacts import ArtifactStore",
                "from nailong_agent_sdk.tools.core.services import CoreToolDispatcher, "
                "CoreToolServices",
                f"root = pathlib.Path(r'{tmp_path}') / 'run'",
                "root.mkdir(exist_ok=True)",
                "(root / 'a.txt').write_text('hello' + chr(10), 'utf-8')",
                f"open(r'{marker}', 'a').write('run' + chr(10))",
                "services = CoreToolServices(root=root, artifacts=ArtifactStore(root), "
                "declared_output_paths=())",
                "dispatcher = CoreToolDispatcher(services)",
                "print(asyncio.run(dispatcher.execute('grep', {'pattern': 'hello'})).status)",
            ]
        ),
        "utf-8",
    )
    completed = subprocess.run(
        [sys.executable, str(script)],
        capture_output=True,
        text=True,
        env=child_environment(),
        timeout=120,
    )
    assert completed.stdout.strip() == "succeeded", completed.stderr[-600:]
    assert marker.read_text("utf-8").splitlines() == ["run"]
