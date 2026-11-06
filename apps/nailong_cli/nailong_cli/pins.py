"""Pinned rules: short standing instructions that are in every request, whatever else is dropped.

A pin is one sentence in a plain Markdown list, in `.nailong/pinned.md` (this project) or
`~/.config/nailong/pinned.md` (every project). Pins are put at the top of the instructions in full,
before anything else, so conversation history, summaries and the 20,000-character limit on
instruction files cannot lose them. The files can be edited by hand; each bullet is a pin and its id
is derived from its text.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

from nailong_agent_sdk.starter import FunctionTool, tool

from .storage import atomic_write, exclusive

MAX_PIN_CHARS = 500
MAX_PINS = 40
MAX_TOTAL_CHARS = 5_000
HEADER = (
    "# Pinned rules\n\nOne rule per bullet. These are put at the top of every request in full.\n\n"
)
_BULLET = re.compile(r"^\s*[-*]\s+(.*\S)\s*$")
STANDING = re.compile(
    r"\b(always|never|from now on|going forward|must not|must always|do not ever|don't ever|"
    r"remember that|our policy|the policy|policy is|rule is|the rule)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Pin:
    id: str
    scope: str
    text: str


def normalise(text: str) -> str:
    return " ".join(text.split())


def pin_id(text: str) -> str:
    return "p-" + hashlib.sha256(normalise(text).casefold().encode("utf-8")).hexdigest()[:6]


class Pins:
    def __init__(self, project: Path, home: Path) -> None:
        self._files = {
            "project": project / ".nailong" / "pinned.md",
            "user": home / ".config" / "nailong" / "pinned.md",
        }

    def path(self, scope: str) -> Path:
        if scope not in self._files:
            raise ValueError(f'scope must be "project" or "user", not {scope!r}.')
        return self._files[scope]

    def listing(self) -> list[Pin]:
        found: list[Pin] = []
        for scope in ("user", "project"):
            path = self._files[scope]
            if not path.is_file():
                continue
            for line in path.read_text(encoding="utf-8").splitlines():
                match = _BULLET.match(line)
                if match:
                    text = normalise(match.group(1))
                    found.append(Pin(pin_id(text), scope, text))
        return found

    def text(self) -> str:
        pins = self.listing()
        if not pins:
            return ""
        lines = "\n".join(f"- {pin.text}" for pin in pins)
        return (
            "Pinned rules. The user pinned these; they apply to every turn and are not part of "
            f"the conversation, so they are never summarised away:\n{lines}"
        )

    def add(self, text: str, scope: str = "project") -> Pin:
        clean = normalise(text)
        if not clean:
            raise ValueError("A pinned rule cannot be empty.")
        if len(clean) > MAX_PIN_CHARS:
            raise ValueError(
                f"A pin is one short rule; this one is {len(clean)} characters and the limit is "
                f"{MAX_PIN_CHARS}. Put longer guidance in NAILONG.md or .nailong/rules/."
            )
        path = self.path(scope)
        path.parent.mkdir(parents=True, exist_ok=True)
        with exclusive(path.with_suffix(".lock"), wait=5.0, stale=60.0, what="pinned rules"):
            existing = [pin for pin in self.listing() if pin.scope == scope]
            if any(pin.id == pin_id(clean) for pin in self.listing()):
                raise ValueError(f"That rule is already pinned ({pin_id(clean)}).")
            if len(existing) >= MAX_PINS:
                raise ValueError(f"There are already {MAX_PINS} {scope} pins; remove one first.")
            if sum(len(pin.text) for pin in existing) + len(clean) > MAX_TOTAL_CHARS:
                raise ValueError(
                    f"The {scope} pins would pass {MAX_TOTAL_CHARS} characters in all; shorten or "
                    "remove some."
                )
            body = path.read_text(encoding="utf-8") if path.is_file() else HEADER
            atomic_write(path, body.rstrip("\n") + f"\n- {clean}\n")
        return Pin(pin_id(clean), scope, clean)

    def remove(self, wanted: str) -> Pin:
        matches = [pin for pin in self.listing() if pin.id == wanted.strip()]
        if not matches:
            ids = [pin.id for pin in self.listing()]
            raise ValueError(f'There is no pin "{wanted}"; the pins are {ids}.')
        pin = matches[0]
        path = self.path(pin.scope)
        with exclusive(path.with_suffix(".lock"), wait=5.0, stale=60.0, what="pinned rules"):
            kept = []
            removed = False
            for line in path.read_text(encoding="utf-8").splitlines():
                match = _BULLET.match(line)
                if match and not removed and pin_id(normalise(match.group(1))) == pin.id:
                    removed = True
                    continue
                kept.append(line)
            atomic_write(path, "\n".join(kept).rstrip("\n") + "\n")
        return pin

    def tools(self) -> list[FunctionTool]:
        def memory_pin(rule: str, scope: str = "project") -> dict[str, str]:
            """Pin one short standing rule the user asked you to remember, so it is in every
            request from now on. The user approves each pin. Do not pin your own conclusions."""
            pin = self.add(rule, scope)
            return {"id": pin.id, "scope": pin.scope, "rule": pin.text}

        return [tool(memory_pin, needs_approval=True)]


def standing_rules(texts: list[str], pinned: list[Pin], limit: int = 10) -> list[str]:
    """Sentences in the user's messages that read as standing rules and are not pinned."""
    have = {normalise(pin.text).casefold() for pin in pinned}
    found: list[str] = []
    for text in texts:
        for sentence in re.split(r"(?<=[.!?])\s+|\n+", text):
            clean = normalise(sentence)
            if (
                10 <= len(clean) <= 300
                and not clean.endswith("?")
                and STANDING.search(clean)
                and clean.casefold() not in have
                and clean not in found
            ):
                found.append(clean)
    return found[:limit]


KEPT_HEADING = "Kept word for word from the user's messages:"


def kept_from(summary: str) -> list[str]:
    if KEPT_HEADING not in summary:
        return []
    tail = summary.split(KEPT_HEADING, 1)[1]
    return [line[2:].strip() for line in tail.splitlines() if line.startswith("- ")]


def with_kept(summary: str, kept: list[str], cap: int = 20) -> str:
    body = summary.split(KEPT_HEADING, 1)[0].rstrip()
    unique = list(dict.fromkeys(kept))[-cap:]
    if not unique:
        return body
    return body + f"\n\n{KEPT_HEADING}\n" + "\n".join(f"- {item}" for item in unique)
