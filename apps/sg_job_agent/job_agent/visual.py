"""Seeing the compiled resume: a PNG preview, geometry checks, optional vision review.

Geometry checks read word boxes straight from the PDF, so they are exact. The vision
model review is advisory only: models misread small renders, so it never blocks a resume.
"""

from __future__ import annotations

import base64
import json
import re
from collections import defaultdict
from pathlib import Path

import httpx


def _pymupdf():
    try:
        import pymupdf
    except ImportError:
        return None
    return pymupdf


def render_preview(pdf: Path, png: Path, *, dpi: int = 110) -> Path | None:
    pymupdf = _pymupdf()
    if pymupdf is None:
        return None
    with pymupdf.open(str(pdf)) as document:
        document[0].get_pixmap(dpi=dpi).save(str(png))
    return png


def layout_issues(pdf: Path) -> list[str]:
    """Exact layout problems on the last page: overflow, orphans, dangling icons, empty space."""

    pymupdf = _pymupdf()
    if pymupdf is None:
        return []
    issues: list[str] = []
    with pymupdf.open(str(pdf)) as document:
        page = document[len(document) - 1]
        words = page.get_text("words")
        if not words:
            return ["The last page has no text."]
        left = min(word[0] for word in words)
        right_limit = page.rect.width - left
        lines: dict[tuple[int, int], list[tuple]] = defaultdict(list)
        for word in words:
            lines[(word[5], word[6])].append(word)
        overflow = [word[4] for word in words if word[2] > right_limit + 2]
        if overflow:
            issues.append(f"Text crosses the right margin near: {' '.join(overflow[:4])}")
        rows = sorted(
            (sorted(items, key=lambda word: word[0]) for items in lines.values()),
            key=lambda items: items[0][1],
        )
        text_width = right_limit - left

        def text_start(items: list[tuple]) -> float:
            first = items[1] if len(items[0][4]) <= 2 and len(items) > 1 else items[0]
            return first[0]

        for previous, row in zip(rows, rows[1:], strict=False):
            text = " ".join(word[4] for word in row)
            width = row[-1][2] - row[0][0]
            previous_width = previous[-1][2] - previous[0][0]
            continues = abs(row[0][0] - text_start(previous)) < 3
            if continues and previous_width > 0.8 * text_width:
                if len(row) <= 2 and width < 0.18 * text_width:
                    issues.append(f'Orphan last line "{text}": tighten the bullet above it.')
        uris = [link["uri"] for link in page.get_links() if link.get("uri")]
        for uri in sorted({uri for uri in uris if uris.count(uri) > 1}):
            issues.append(f"The link {uri} wraps onto a second line: shorten its text.")
        bottom = max(word[3] for word in words)
        free = 1 - bottom / page.rect.height
        if len(document) == 1 and free > 0.12:
            issues.append(f"About {free:.0%} of the page is empty at the bottom.")
    return issues


def vision_review(
    png: Path, *, base_url: str, api_key: str, model: str, timeout: float = 120.0
) -> list[str]:
    """Ask an image-capable model for clearly visible layout defects (advisory)."""

    encoded = base64.b64encode(png.read_bytes()).decode()
    prompt = (
        "This is a rendered one-page resume. Report only layout defects you can clearly "
        "see: text clipped or past the margins, overlapping text, a link or icon wrapped "
        "onto its own line, uneven heading styles. Ignore the wording. Do not report "
        "alignment or spacing you are unsure of. Reply with JSON only: "
        '{"issues": ["..."]} and an empty list when the layout looks clean.'
    )
    response = httpx.post(
        f"{base_url.rstrip('/')}/chat/completions",
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=timeout,
        json={
            "model": model,
            "max_tokens": 600,
            "reasoning_effort": "none",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/png;base64,{encoded}"},
                        },
                    ],
                }
            ],
        },
    )
    response.raise_for_status()
    content = response.json()["choices"][0]["message"]["content"] or ""
    match = re.search(r"\{.*\}", content, re.S)
    if not match:
        return []
    try:
        issues = json.loads(match.group(0)).get("issues", [])
    except ValueError:
        return []
    return [str(issue) for issue in issues if issue]
