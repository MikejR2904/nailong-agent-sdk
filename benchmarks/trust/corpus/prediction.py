"""What a method returns for one case."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Prediction:
    node: str | None
    call_id: str | None
    note: str = ""
    ranked: tuple[str, ...] = ()
