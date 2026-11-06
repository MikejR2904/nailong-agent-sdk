"""Long pasted text shows as a short label in the prompt; the model still gets all of it.

When a paste is long (more than 10 lines or 1,000 characters), the prompt box holds
`[Pasted text #1 +37 lines]` instead of the text, so the line stays readable. The full text is kept
here. When the message is sent the label is replaced by the text. `/paste` lists the pastes and
shows one; a key (Alt+E by default) opens the label under the cursor back into its text in the
prompt box before you send.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

COLLAPSE_LINES = 10
COLLAPSE_CHARS = 1_000
MAX_PASTES = 100
MAX_TOTAL_CHARS = 20_000_000
LABEL = re.compile(r"\[Pasted text #(\d+) \+(\d+) lines?\]")
PREVIEW_CHARS = 70


@dataclass(frozen=True)
class Paste:
    number: int
    text: str

    @property
    def lines(self) -> int:
        return self.text.count("\n") + 1

    @property
    def label(self) -> str:
        return f"[Pasted text #{self.number} +{self.lines} lines]"

    @property
    def preview(self) -> str:
        flat = " ".join(self.text.split())
        return flat if len(flat) <= PREVIEW_CHARS else flat[: PREVIEW_CHARS - 1] + "…"


class PasteStore:
    def __init__(self) -> None:
        self._pastes: dict[int, Paste] = {}
        self._next = 1

    def should_collapse(self, text: str) -> bool:
        too_big = (
            len(self._pastes) >= MAX_PASTES
            or sum(len(item.text) for item in self._pastes.values()) + len(text) > MAX_TOTAL_CHARS
        )
        long = len(text) > COLLAPSE_CHARS or text.count("\n") + 1 > COLLAPSE_LINES
        return long and not too_big

    def add(self, text: str) -> str:
        paste = Paste(self._next, text)
        self._pastes[paste.number] = paste
        self._next += 1
        return paste.label

    def get(self, number: int) -> Paste:
        if number not in self._pastes:
            raise ValueError(
                f"There is no paste #{number}; the pastes are {sorted(self._pastes) or 'none yet'}."
            )
        return self._pastes[number]

    def listing(self) -> list[Paste]:
        return list(self._pastes.values())

    def expand(self, text: str) -> str:
        """Replace every label that names a paste held here; any other text is left alone."""

        def put_back(found: re.Match[str]) -> str:
            paste = self._pastes.get(int(found.group(1)))
            return (
                paste.text
                if paste is not None and paste.label == found.group(0)
                else found.group(0)
            )

        return LABEL.sub(put_back, text)

    def open_at(self, text: str, cursor: int) -> tuple[str, int] | None:
        """The text with the label at or just before the cursor opened, and the new cursor."""
        chosen = None
        for found in LABEL.finditer(text):
            if found.start() <= cursor <= found.end():
                chosen = found
                break
            if found.end() <= cursor:
                chosen = found
        if chosen is None:
            return None
        paste = self._pastes.get(int(chosen.group(1)))
        if paste is None or paste.label != chosen.group(0):
            return None
        return text[: chosen.start()] + paste.text + text[chosen.end() :], chosen.start() + len(
            paste.text
        )
