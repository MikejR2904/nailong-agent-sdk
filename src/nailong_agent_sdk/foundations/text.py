# Copyright (c) 2026 David Michael Indraputra

"""Newline-only line splitting and UTF-8 well-formedness helpers for durable text.
This is similar to str.splitlines() but stricter, i.e.,
- We don't consider other Unicode line separators (U+2028, U+2029) as line breaks.
- If the text ends with a newline, we pop the trailing empty string;
  str.splitlines() would keep the empty string in the result.
Examples:
- text = "a\\r\\nb\\nc\\r"
  text.splitlines() -> ["a", "b", "c", ""]
  split_lines(text) -> ["a", "b", "c"]
- text = "a\\u\\u2028b"
  text.splitlines() -> ["a", "b"]
  split_lines(text) -> ["a\\u2028b"]
"""

from __future__ import annotations

import re

_NEWLINE = re.compile(r"\r\n|\r|\n")  # matches Windows \r\n, Mac \r, and Unix \n
_LINE_WITH_TERMINATOR = re.compile(r"[^\r\n]*(?:\r\n|\r|\n)|[^\r\n]+\Z")
_SURROGATE = re.compile("[\ud800-\udfff]")  # UTF-16, not for UTF-8


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
    # Replace unpaired code with U+FFFD replacement character
    return _SURROGATE.sub("\N{REPLACEMENT CHARACTER}", text)


# A way to validate that a string is well-formed UTF-8
def assert_well_formed_text(value: str, field: str) -> str:
    match = _SURROGATE.search(value)
    if match is not None:
        raise ValueError(
            f"{field} contains the unpaired surrogate code point U+{ord(match.group()):04X} at "
            f"index {match.start()}, which cannot be encoded as UTF-8."
        )
    return value
