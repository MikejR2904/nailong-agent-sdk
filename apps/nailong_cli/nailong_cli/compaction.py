"""/compact: have the model write a summary of the earlier turns and keep it with the session."""

from __future__ import annotations

from .state import ReplState
from .turns import ask_read_only

MAX_TURN_CHARS = 3_000
SUMMARY_PROMPT = (
    "Summarise this conversation so that work can continue from the summary alone. Keep: what "
    "the user wants, decisions made and why, files and commands that matter, what is done, what "
    "is still open. Use under 300 words and plain text.{focus}\n\nConversation:\n{turns}"
)


def compact_session(state: ReplState, focus: str = "") -> tuple[int, int]:
    pending = state.session.uncompacted_turns()
    if not pending:
        raise ValueError("There is nothing new to compact since the last summary.")
    summary = state.session.summary
    parts = [f"Earlier summary:\n{summary['text']}"] if summary else []
    for turn in pending:
        parts.append(
            f"Turn {turn['number']} ({turn['status']}):\nUser: {turn['prompt'][:MAX_TURN_CHARS]}\n"
            f"Assistant: {turn['answer'][:MAX_TURN_CHARS]}"
        )
    note = f" Pay particular attention to: {focus}." if focus else ""
    answer = ask_read_only(state, SUMMARY_PROMPT.format(focus=note, turns="\n\n".join(parts)))
    if not answer.ok or not answer.text.strip():
        raise ValueError(f"The summary could not be written: {answer}")
    covered = state.session.compact(answer.text)
    return covered, len(answer.text)
