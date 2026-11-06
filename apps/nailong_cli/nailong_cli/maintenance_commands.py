"""/telemetry and /cleanup: the SDK's OpenTelemetry export and old-run retention."""

from __future__ import annotations

import sys

from nailong_agent_sdk.agent.retention import RetentionPolicy, RetentionReport
from nailong_agent_sdk.foundations.errors import AgentSdkError

from . import ci, schedule
from .state import ReplState
from .utilities import editor_command, stats_command

SECONDS_PER_DAY = 86_400
DEFAULT_DAYS = 30.0
KEEP_RECENT = 3
MEGABYTE = 1_048_576


def _telemetry(state: ReplState, arguments: list[str]) -> bool:
    console = state.console
    exporter = state.parts.exporter
    if exporter is None or not exporter.enabled:
        console.line("  Telemetry export is off.")
        console.line(
            "  Set otlp_endpoint in your user settings (or pass --otlp-endpoint URL) to send "
            "recorded runs to an OpenTelemetry collector over OTLP/HTTP. Put any credentials in "
            "OTEL_EXPORTER_OTLP_HEADERS."
        )
        return True
    if arguments[:1] == ["now"]:
        status = exporter.export_now()
    elif arguments:
        console.error("Usage: /telemetry [now]")
        return True
    else:
        status = exporter.last
    console.line(f"  collector   {exporter.host}")
    console.line(f"  sending     {', '.join(exporter.include)}")
    if status is None:
        console.line("  nothing has been sent yet; it runs after each turn, or use /telemetry now")
    else:
        mark = "ok" if status.ok else "failed"
        console.line(f"  last export {status.at}  {mark}: {status.detail}")
    return True


def _schedule(state: ReplState, arguments: list[str]) -> bool:
    console = state.console
    action = arguments[0] if arguments else "list"
    if action == "list":
        items = schedule.load(state.home)
        for item in items:
            console.line(f"  {schedule.describe(item)}")
        if not items:
            console.line(
                "  No schedules. /schedule add daily 09:00 -- summarise yesterday's commits"
            )
        return True
    if action == "add":
        rest = arguments[1:]
        if "--" not in rest:
            console.line("Usage: /schedule add <when> [mode=plan|accept-edits] -- <prompt>")
            return True
        split = rest.index("--")
        words, prompt = rest[:split], " ".join(rest[split + 1 :])
        mode = "plan"
        for word in list(words):
            if word.startswith("mode="):
                mode = word.removeprefix("mode=")
                words.remove(word)
        item = schedule.add(state.home, " ".join(words), prompt, state.project, mode)
        console.line(f"  added {schedule.describe(item)}")
        console.line(
            "  It runs when `nailong --run-schedules` is run; /schedule install shows how."
        )
        return True
    if action == "remove" and len(arguments) == 2:
        schedule.remove(state.home, arguments[1])
        console.line(f"  removed {arguments[1]}")
        return True
    if action == "install":
        for line in schedule.install_hint(sys.executable):
            console.line(f"  {line}")
        return True
    console.line("Usage: /schedule [list | add <when> -- <prompt> | remove ID | install]")
    return True


def _connect(state: ReplState, arguments: list[str]) -> bool:
    from .config import load_config
    from .setup import SetupCancelled, run_setup
    from .turns import switch_agent

    try:
        result = run_setup(state.console, state.home, state.environ)
    except SetupCancelled as error:
        raise ValueError(str(error)) from error
    environ = state.environ
    if hasattr(environ, "update"):
        environ.update(result.session_keys)
    state.config = load_config(state.project, home=state.home, environ=state.environ)
    state.parts.config = state.config
    switch_agent(state, setup=state.model_factory(state.config, state.environ))
    state.console.line(f"  now using {state.config.model} ({state.config.provider})")
    return True


def _kb(state: ReplState, arguments: list[str]) -> bool:
    console = state.console
    base = state.parts.knowledge
    action = arguments[0] if arguments else "list"
    if action == "add" and len(arguments) == 2:
        result = base.add(arguments[1])
        console.line(f"  added {result['added']} file(s); {result['files']} in the knowledge base")
        for line in result["skipped"]:
            console.line(console.style(f"  skipped {line}", "yellow"))
    elif action == "remove" and len(arguments) == 2:
        console.line(f"  removed {base.remove(arguments[1])} file(s)")
    elif action == "refresh":
        result = base.refresh()
        console.line(
            f"  refreshed {result['updated']} file(s); dropped {result['dropped'] or 'none'}"
        )
    elif action == "search" and len(arguments) >= 2:
        for item in base.search(" ".join(arguments[1:])):
            console.line(f"  {item.path} ({item.location})")
            console.line(console.style("    " + " ".join(item.text.split())[:140], "dim"))
    elif action == "list":
        lines = base.listing()
        for line in lines:
            console.line(f"  {line}")
        if not lines:
            console.line("  Empty. /kb add PATH adds a file or a folder of documents.")
    else:
        console.line("Usage: /kb [add PATH | list | remove PATH | refresh | search TEXT]")
    return True


def _wiki(state: ReplState, arguments: list[str]) -> bool:
    console = state.console
    wiki = state.parts.wiki
    if arguments[:1] == ["search"] and len(arguments) >= 2:
        for hit in wiki.search(" ".join(arguments[1:])):
            console.line(f"  {hit['page']}: {hit['snippet']}")
        return True
    if arguments:
        console.line(wiki.read(arguments[0])["text"].rstrip())
        return True
    names = wiki.names()
    for name in names:
        console.line(f"  {name}")
    if not names:
        console.line("  No pages yet. The agent adds them with wiki_write as it learns.")
    return True


def _ci(state: ReplState, arguments: list[str]) -> bool:
    if not arguments or arguments[0] not in {"github", "github-review", "gitlab"}:
        state.console.line("Usage: /ci github|github-review|gitlab [write]")
        return True
    text, relative = ci.template(arguments[0])
    if arguments[1:] == ["write"]:
        path = ci.write_template(state.project, arguments[0])
        state.console.line(f"  wrote {path}")
        state.console.line(
            "  Commit .nailong/config.toml and add your key as a secret or variable."
        )
        return True
    state.console.line(f"  {relative}:")
    for line in text.splitlines():
        state.console.line(f"    {line}")
    state.console.line("  /ci " + arguments[0] + " write  saves it into the project.")
    return True


def _describe(report: RetentionReport, days: float) -> list[str]:
    if not report.runs:
        return [f"  No finished runs older than {days:g} day(s) to remove."]
    size = report.bytes / MEGABYTE
    lines = [
        f"  {len(report.runs)} run(s) older than {days:g} day(s), {size:.1f} MB of audit and "
        "telemetry records:"
    ]
    for item in report.runs[:8]:
        lines.append(
            f"    {item.run_id}  last activity {item.last_event_at}  {item.events} event(s)"
        )
    if len(report.runs) > 8:
        lines.append(f"    ... and {len(report.runs) - 8} more")
    kept = ", ".join(f"{count} {reason}" for reason, count in report.kept.items())
    if kept:
        lines.append(f"  kept: {kept}")
    return lines


def _cleanup(state: ReplState, arguments: list[str]) -> bool:
    console = state.console
    try:
        days = float(arguments[0]) if arguments else DEFAULT_DAYS
        if days <= 0:
            raise ValueError
    except ValueError:
        console.error(f"Usage: /cleanup [days]  (a positive number, not {arguments[0]!r})")
        return True
    policy = RetentionPolicy(
        older_than_seconds=days * SECONDS_PER_DAY, keep_most_recent=KEEP_RECENT
    )
    retention = state.agent.services.retention()
    try:
        preview = retention.prune(policy, dry_run=True)
        for line in _describe(preview, days):
            console.line(line)
        if not preview.runs:
            return True
        console.line(
            "  Sessions you can /resume are not touched; the removed runs leave a tombstone "
            "record that says they existed."
        )
        if not console.interactive:
            console.line("  Not removed: there is no terminal to confirm.")
            return True
        if console.ask("Remove these runs? [y/N] ").lower() not in {"y", "yes"}:
            console.line("  Nothing removed.")
            return True
        done = retention.prune(policy, dry_run=False)
    except (AgentSdkError, ValueError, OSError) as error:
        console.error(f"Cleanup failed: {error}")
        return True
    console.line(f"  Removed {len(done.runs)} run(s), {done.bytes / MEGABYTE:.1f} MB.")
    for problem in done.problems:
        console.line(f"  could not remove {problem.run_id}: {problem.reason}")
    return True


MAINTENANCE_HELP = {
    "telemetry": "/telemetry [now]: the OpenTelemetry export (collector, last result, send now)",
    "cleanup": "/cleanup [days]: remove old runs' audit and telemetry records (asks first)",
    "schedule": "/schedule [list|add|remove|install]: prompts that run on a timetable",
    "kb": "/kb [add PATH|list|remove PATH|refresh|search TEXT]: files the agent can cite",
    "wiki": "/wiki [PAGE|search TEXT]: the pages the agent keeps about this project",
    "connect": "choose a model service and save settings, then switch to it now",
    "ci": "/ci github|github-review|gitlab [write]: a workflow for comments, PR reviews or CI",
    "editor": "/editor [text]: write a long prompt in your editor ($VISUAL or $EDITOR)",
    "stats": "/stats [days]: runs, statuses and busiest events over the last days",
}
MAINTENANCE_COMMANDS = {
    "telemetry": _telemetry,
    "cleanup": _cleanup,
    "schedule": _schedule,
    "editor": editor_command,
    "connect": _connect,
    "kb": _kb,
    "wiki": _wiki,
    "ci": _ci,
    "stats": stats_command,
}
