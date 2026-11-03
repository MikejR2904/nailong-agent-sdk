"""Looking across saved sessions: export one as markdown, search them all."""

from __future__ import annotations

from nailong_agent_sdk.starter import Agent


def export_markdown(agent: Agent, session_id: str) -> str:
    session = agent.resume(session_id)
    lines = [f"# Session {session_id}", ""]
    forked = session.forked_from
    if forked:
        lines += [f"Forked from {forked['session']} at turn {forked['turn']}.", ""]
    summary = session.summary
    if summary:
        lines += [f"## Summary of turns 1 to {summary['upto']}", "", summary["text"], ""]
    for turn in session.turns:
        lines += [
            f"## Turn {turn['number']} ({turn['status']})",
            "",
            "**User**",
            "",
            turn["prompt"],
            "",
        ]
        lines += ["**Assistant**", "", turn["answer"], ""]
        if turn.get("tools"):
            lines += ["Tools used:", *[f"- {item}" for item in turn["tools"]], ""]
    return "\n".join(lines)


def search_sessions(agent: Agent, text: str, limit: int = 20) -> list[str]:
    needle = text.lower()
    found: list[str] = []
    for session_id in agent.sessions():
        try:
            turns = agent.resume(session_id).turns
        except Exception:
            continue
        for turn in turns:
            for label, body in (("prompt", turn["prompt"]), ("answer", turn["answer"])):
                position = body.lower().find(needle)
                if position >= 0:
                    start = max(0, position - 30)
                    snippet = " ".join(body[start : position + 60].split())
                    found.append(f"{session_id} turn {turn['number']} {label}: ...{snippet}...")
                    break
            if len(found) >= limit:
                return found
    return found
