"""Claim verification: every bullet on a tailored resume must be backed by a quote.

Bullets copied verbatim from the base resume are trusted. Every other bullet is shown to
a verifier model together with the cited evidence, and a "supported" verdict only counts
when its quote really occurs in that evidence (checked here, not by the model).
"""

from __future__ import annotations

import re
from typing import Any

from .latex import latex_to_text

_BULLET = re.compile(r"\\item\b(.*?)(?=\\item\b|\\end\{|\Z)", re.S)
MIN_QUOTE_CHARS = 12


def _normal(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def bullets(tex: str) -> list[str]:
    body = tex.split(r"\begin{document}", 1)[-1]
    return [
        text
        for match in _BULLET.finditer(body)
        if (text := " ".join(latex_to_text(match.group(1)).split()))
    ]


def claims_to_verify(base_tex: str, tailored_tex: str) -> list[dict[str, str]]:
    trusted = {_normal(text) for text in bullets(base_tex)}
    return [
        {"id": f"b{index}", "text": text}
        for index, text in enumerate(bullets(tailored_tex), start=1)
        if _normal(text) not in trusted
    ]


def quote_in_sources(quote: str, sources: str) -> bool:
    needle = _normal(quote)
    return len(quote.strip()) >= MIN_QUOTE_CHARS and needle != "" and needle in _normal(sources)


def unsupported_claims(
    claims: list[dict[str, str]], verdicts: list[dict[str, Any]], sources: str
) -> list[str]:
    """Descriptions of claims without a verified supporting quote."""

    by_id = {verdict.get("id"): verdict for verdict in verdicts}
    problems = []
    for claim in claims:
        verdict = by_id.get(claim["id"])
        if (
            verdict is None
            or verdict.get("verdict") != "supported"
            or not quote_in_sources(str(verdict.get("quote", "")), sources)
        ):
            note = (verdict or {}).get("reason", "no supporting quote found in the cited evidence")
            problems.append(f'"{claim["text"][:140]}": {note}')
    return problems
