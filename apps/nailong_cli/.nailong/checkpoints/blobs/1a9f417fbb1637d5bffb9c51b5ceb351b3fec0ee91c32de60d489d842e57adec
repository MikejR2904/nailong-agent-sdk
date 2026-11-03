"""Showing a model-written tool to a person and getting a decision they typed."""

from __future__ import annotations

from nailong_agent_sdk.starter import ToolProposal

from .console import Console

_SOURCE_LINES = 60
_RISKY_KEYS = ("network_modules", "process_or_file_modules", "dynamic_calls")


def review_proposal(console: Console, proposal: ToolProposal) -> bool | str:
    if not console.interactive:
        return "no terminal is attached, so the proposal was not reviewed"
    console.line()
    console.line(
        console.style(f"Tool proposal {proposal.id}: {proposal.name} v{proposal.version}", "bold")
    )
    console.line(f"  {proposal.description}")
    if proposal.reason:
        console.line(f"  why: {proposal.reason}")
    console.line(_shown(proposal.diff or proposal.source))
    console.line("  reaches for: " + _report(proposal))
    console.line(f"  tests: {len(proposal.tests)}")
    for case in proposal.tests:
        console.line(f"    {case['arguments']} -> {case['expect']!r}")
    risky = any(proposal.report.get(key) for key in _RISKY_KEYS)
    if risky:
        answer = console.ask(
            "This code touches the network, files, processes or dynamic code.\n"
            'Type "approve" to accept it: '
        )
        return True if answer == "approve" else "the reviewer did not type approve"
    answer = console.ask("Approve? [y/N] ").lower()
    return True if answer in {"y", "yes"} else "the reviewer declined it"


def _shown(text: str) -> str:
    lines = text.splitlines()
    if len(lines) > _SOURCE_LINES:
        lines = [
            *lines[:_SOURCE_LINES],
            f"... {len(text.splitlines()) - _SOURCE_LINES} more line(s)",
        ]
    return "\n".join("  | " + line for line in lines)


def _report(proposal: ToolProposal) -> str:
    report = proposal.report
    found = [
        f"{label}: {', '.join(report[key])}"
        for key, label in (
            ("network_modules", "network"),
            ("process_or_file_modules", "process/file"),
            ("dynamic_calls", "dynamic"),
        )
        if report.get(key)
    ]
    return "; ".join(found) or "no network, process, file or dynamic calls found"
