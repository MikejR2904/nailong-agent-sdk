"""/compact: have the model write a summary of the earlier turns and keep it with the session."""

from __future__ import annotations

from .pins import kept_from, standing_rules, with_kept
from .state import ReplState
from .turns import ask_read_only

MAX_TURN_CHARS = 3_000
SUMMARY_PROMPT = (
    "Summarise this conversation so that work can continue from the summary alone. Keep: what "
    "the user wants, decisions made and why, files and commands that matter, what is done, what "
    "is still open. Use under 300 words and plain text. Leave out any list headed 'Kept word "
    "for word'; it is stored separately.{focus}\n\nConversation:\n{turns}"
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
    answer = ask_read_only(
        state, SUMMARY_PROMPT.format(focus=note, turns="\n\n".join(parts)), small=True
    )
    if not answer.ok or not answer.text.strip():
        raise ValueError(f"The summary could not be written: {answer}")
    prompts = [str(turn["prompt"]) for turn in pending]
    pinned = state.parts.pins.listing() if state.parts.pins is not None else []
    fresh = standing_rules(prompts, pinned)
    previous = kept_from(summary["text"]) if summary else []
    final_text = with_kept(answer.text, [*previous, *fresh])
    covered = state.session.compact(final_text)
    if fresh:
        state.console.line(
            state.console.style(
                f"  {len(fresh)} sentence(s) that read as standing rules were kept word for word "
                "in the summary; /remember pins one for good:",
                "yellow",
            )
        )
        for sentence in fresh:
            state.console.line(f"    {sentence}")
    if state.parts.events is not None:
        state.parts.events.emit("memory.compacted", {"turns": covered})
    lifecycle = state.parts.lifecycle
    if lifecycle is not None and lifecycle.has("compacted"):
        state.runner.run(lifecycle.run("compacted", session=state.session.id, covered=str(covered)))
    return covered, len(final_text)
