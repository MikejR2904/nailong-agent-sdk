"""Open tailored resumes in Overleaf through its public "Open in Overleaf" API.

Overleaf's developer API (https://www.overleaf.com/devs) imports LaTeX by a form
POST to https://www.overleaf.com/docs: ``encoded_snip`` carries URL-encoded
source, ``snip_name`` names the file, and ``engine`` picks the compiler. Overleaf
then creates a project in the user's logged-in browser session and compiles it.
It returns no PDF to the caller, so it complements, and cannot replace, the
local compile that enforces the page limit. This module writes static HTML
launchers; opening one and clicking a button sends that resume to Overleaf.
"""

from __future__ import annotations

import html
import urllib.parse
from dataclasses import dataclass
from pathlib import Path

OVERLEAF_DOCS_URL = "https://www.overleaf.com/docs"


@dataclass(frozen=True)
class OverleafEntry:
    title: str
    subtitle: str
    tex: str
    file_name: str = "resume.tex"
    pdf_href: str = ""
    posting_url: str = ""


def overleaf_form(entry: OverleafEntry, *, engine: str = "pdflatex") -> str:
    fields = {
        "encoded_snip": urllib.parse.quote(entry.tex, safe=""),
        "snip_name": entry.file_name,
        "engine": engine,
    }
    inputs = "".join(
        f'<input type="hidden" name="{name}" value="{html.escape(value, quote=True)}">'
        for name, value in fields.items()
    )
    return (
        f'<form action="{OVERLEAF_DOCS_URL}" method="post" target="_blank">{inputs}'
        '<button type="submit">Open in Overleaf</button></form>'
    )


def launcher_html(entries: list[OverleafEntry], *, heading: str) -> str:
    rows = []
    for entry in entries:
        links = []
        if entry.pdf_href:
            links.append(f'<a href="{html.escape(entry.pdf_href)}">Local PDF</a>')
        if entry.posting_url:
            links.append(f'<a href="{html.escape(entry.posting_url)}">Job posting</a>')
        rows.append(
            "<li><div><strong>"
            f"{html.escape(entry.title)}</strong><span>{html.escape(entry.subtitle)}</span>"
            f"<nav>{' · '.join(links)}</nav></div>{overleaf_form(entry)}</li>"
        )
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(heading)}</title>
<style>
  :root {{ color-scheme: light dark; --fg: #1d1d1f; --bg: #fafafa; --card: #fff;
          --muted: #6b6b70; --accent: #138a36; --line: #e3e3e6; }}
  @media (prefers-color-scheme: dark) {{
    :root {{ --fg: #ececf0; --bg: #161618; --card: #202023; --muted: #a0a0a8;
            --accent: #3fbf63; --line: #333338; }}
  }}
  body {{ font: 15px/1.5 system-ui, sans-serif; color: var(--fg); background: var(--bg);
         max-width: 760px; margin: 0 auto; padding: 24px 16px; }}
  h1 {{ font-size: 20px; margin: 0 0 4px; }} p {{ color: var(--muted); margin: 0 0 20px; }}
  ul {{ list-style: none; padding: 0; margin: 0; display: grid; gap: 10px; }}
  li {{ display: flex; gap: 12px; align-items: center; justify-content: space-between;
       background: var(--card); border: 1px solid var(--line); border-radius: 10px;
       padding: 12px 14px; }}
  li span {{ display: block; color: var(--muted); font-size: 13px; }}
  nav {{ font-size: 13px; }} a {{ color: var(--accent); }}
  button {{ background: var(--accent); color: #fff; border: 0; border-radius: 8px;
           padding: 8px 12px; font: inherit; cursor: pointer; white-space: nowrap; }}
  @media (max-width: 520px) {{ li {{ flex-direction: column; align-items: stretch; }} }}
</style></head><body>
<h1>{html.escape(heading)}</h1>
<p>Each button opens that resume as a new Overleaf project (log in to Overleaf first).</p>
<ul>{"".join(rows)}</ul>
</body></html>
"""


def write_launcher(path: Path, entries: list[OverleafEntry], *, heading: str) -> Path:
    path.write_text(launcher_html(entries, heading=heading), encoding="utf-8")
    return path
