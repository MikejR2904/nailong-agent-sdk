import sys
from pathlib import Path

import pytest

EXTENDED_PREFIX = "\\\\?\\"

windows_only = pytest.mark.skipif(
    sys.platform != "win32", reason="the extended-length spelling exists only on Windows"
)


def resolve_files_in_extended_spelling(monkeypatch):
    original = Path.resolve

    def resolve(self, strict=False):
        resolved = original(self, strict=strict)
        if resolved.is_dir() or str(resolved).startswith(EXTENDED_PREFIX):
            return resolved
        return Path(EXTENDED_PREFIX + str(resolved))

    monkeypatch.setattr(Path, "resolve", resolve)
