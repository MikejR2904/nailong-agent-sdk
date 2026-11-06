from __future__ import annotations

import re

from nailong_agent_sdk.foundations.errors import AgentSdkError

AUTOMATIC_ID = re.compile(r"session-[0-9a-f]{8}")


def turn_count(agent, session_id: str) -> int | None:
    try:
        return len(agent.resume(session_id).turns)
    except (AgentSdkError, ValueError):
        return None


def discard_if_unused(agent, session_id: str) -> bool:
    if not AUTOMATIC_ID.fullmatch(session_id) or turn_count(agent, session_id) != 0:
        return False
    try:
        agent.delete_session(session_id)
    except AgentSdkError:
        return False
    return True


def latest_with_turns(agent) -> str | None:
    latest = agent.latest_session()
    if latest is None or turn_count(agent, latest) != 0:
        return latest
    used = [i for i in agent.sessions() if turn_count(agent, i)]
    if not used:
        return None
    return max(used, key=lambda i: agent.resume(i).turns[-1].get("at", ""))
