# Copyright (c) 2026 David Michael Indraputra

"""Comparison of Windows extended-length path spellings as one location.
Windows has 2 special path syntaxes:
- Extended drive paths: ``\\\\?\\C:\\path`` (or ``\\\\?\\UNC\\server\\share\\path``)
  The \\\\?\\ prefix tells Windows APIs to bypass the usual MAX_PATH (260-character) limit.
- UNC paths: ``\\\\server\\share\\path\\to\\file``
  which is the same idea as the extended drive path, but for network shares.
These spellings are valid, but they are not normalized for comparison.
For example, ``\\\\?\\C:\\path`` and ``C:\\path`` refer to the same file,
but they are not equal as strings. This module provides a way to normalize for comparison.
"""

from __future__ import annotations

import re
from pathlib import PurePath, PureWindowsPath

_EXTENDED_DRIVE = re.compile(r"\\\\\?\\[A-Za-z]:")
_EXTENDED_UNC = re.compile(r"\\\\\?\\UNC\\", re.IGNORECASE)
_EXTENDED_PREFIX_LENGTH = 4


def strip_extended_prefix[P: PurePath](path: P) -> P:
    if not isinstance(path, PureWindowsPath):
        return path
    text = str(path)
    unc = _EXTENDED_UNC.match(text)
    if unc is not None:
        # Convert extended UNC (\\?\UNC\server\share) to normal UNC (\\server\share)
        return type(path)("\\\\" + text[unc.end() :])
    if _EXTENDED_DRIVE.match(text) is not None:
        # Strip \\?\ prefix from extended drive paths (\\?\C:\...)
        return type(path)(text[_EXTENDED_PREFIX_LENGTH:])
    return path


def relative_to_base[P: PurePath](path: P, base: PurePath) -> P:
    # Normalize both paths by stripping extended prefixes before computing relativity
    return strip_extended_prefix(path).relative_to(strip_extended_prefix(base))
