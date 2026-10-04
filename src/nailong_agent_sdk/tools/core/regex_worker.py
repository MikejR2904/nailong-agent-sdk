# Copyright (c) 2026 David Michael Indraputra

"""Standalone regex worker: runs untrusted patterns in an isolated, killable interpreter."""

from __future__ import annotations

import json
import re
import sys


def main() -> None:
    try:
        request = json.loads(sys.stdin.read())
        flags = 0 if request["case_sensitive"] else re.IGNORECASE
        expression = re.compile(request["pattern"], flags)
        limit = request["limit"]
        matches: list[dict[str, object]] = []
        for relative, lines in request["documents"]:
            for index, line in enumerate(lines, start=1):
                if expression.search(line):
                    matches.append({"path": relative, "line": index, "text": line[:1_000]})
                    if len(matches) >= limit:
                        _emit({"matches": matches, "truncated": True})
                        return
        _emit({"matches": matches, "truncated": False})
    except Exception as error:
        message = str(error) if isinstance(error, re.error) else f"{type(error).__name__}: {error}"
        _emit({"error": message})


def _emit(outcome: dict[str, object]) -> None:
    sys.stdout.write(json.dumps(outcome))
    sys.stdout.flush()


if __name__ == "__main__":
    main()
