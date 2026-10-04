# Copyright (c) 2026 David Michael Indraputra

"""Newline-only line splitting and UTF-8 well-formedness helpers for durable text."""

from __future__ import annotations

import re

_NEWLINE = re.compile(r"\r\n|\r|\n")
_LINE_WITH_TERMINATOR = re.compile(r"[^\r\n]*(?:\r\n|\r|\n)|[^\r\n]+\Z")
_SURROGATE = re.compile("[\ud800-\udfff]")


def split_lines(text: str, *, keepends: bool = False) -> list[str]:
    if keepends:
        return _LINE_WITH_TERMINATOR.findall(text)
    lines = _NEWLINE.split(text)
    if lines and lines[-1] == "":
        lines.pop()
    return lines


def scrub_surrogates(text: str) -> str:
    if text.isascii():
        return text
    return _SURROGATE.sub("\N{REPLACEMENT CHARACTER}", text)


def assert_well_formed_text(value: str, field: str) -> str:
    match = _SURROGATE.search(value)
    if match is not None:
        raise ValueError(
            f"{field} contains the unpaired surrogate code point U+{ord(match.group()):04X} at "
            f"index {match.start()}, which cannot be encoded as UTF-8."
        )
    return value
