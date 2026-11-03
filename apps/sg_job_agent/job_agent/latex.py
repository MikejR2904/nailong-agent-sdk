"""LaTeX resume helpers: plain-text extraction, integrity checks, and compilation.

The integrity check is the deterministic guard behind the tailoring agents. A
tailored resume may reorder, cut, and reword the base resume, but it must not
introduce metrics, dates, or employers that the base resume does not contain.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path

_COMMENT = re.compile(r"(?<!\\)%.*$", re.M)
# Only the environment name and optional argument: required arguments such as
# \begin{twocolentry}{Aug 2026 -- Present} carry resume content (dates).
_ENV = re.compile(r"\\(?:begin|end)\{[^}]*\}(?:\[[^\]]*\])?")
# Spacing/size commands and bare dimensions are layout, not resume facts.
_LAYOUT_CMD = re.compile(
    r"\\(?:vspace|hspace|kern|fontsize|linespread|setlength|setcolumnwidth|titlespacing)\*?"
    r"(?:\{[^{}]*\})*"
)
_DIMENSION = re.compile(r"-?\d*\.?\d+\s*(?:cm|mm|pt|em|ex|bp)\b")
_CMD_WITH_ARG = re.compile(r"\\[a-zA-Z@]+\*?(?:\[[^\]]*\])?\{([^{}]*)\}")
_BARE_CMD = re.compile(r"\\[a-zA-Z@]+\*?(?:\[[^\]]*\])?")
_ESCAPED = re.compile(r"\\([%&$#_{}])")
# Numbers with an optional unit; thousands separators are normalised away.
_NUMBER = re.compile(r"(?<![\w.])(\d+(?:[.,]\d+)*)\s*(%|x|k|m|b|bn|\+)?(?![\w])", re.I)
_MONTH_YEAR = re.compile(
    r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+(\d{4})\b", re.I
)
_PLACEHOLDER = re.compile(r"\b(TODO|TBD|FIXME|XXX|lorem ipsum|\[company\]|\[role\])", re.I)
_WORD = re.compile(r"[A-Za-z][\w.+#-]*[\w+#]|[A-Za-z]")
# Capitalised or symbol-bearing words in a JD are the skill/tool names
# (Kubernetes, PyTorch, C++, AWS); lowercase prose words are not.
_SKILL_SHAPED = re.compile(r"^(?:[A-Z].*|.*[+#\d].*)$")


def latex_to_text(tex: str) -> str:
    """Return a readable approximation of the text a LaTeX document renders."""

    body = tex
    if "\\begin{document}" in body:
        body = body.split("\\begin{document}", 1)[1]
    body = body.split("\\end{document}", 1)[0]
    body = _COMMENT.sub("", body)
    body = _DIMENSION.sub(" ", _LAYOUT_CMD.sub(" ", body))
    body = _ENV.sub(" ", body)
    previous = None
    while previous != body:  # unwrap nested \cmd{...} from the inside out
        previous = body
        body = _CMD_WITH_ARG.sub(r" \1 ", body)
    body = _ESCAPED.sub(r"\1", body)
    body = _BARE_CMD.sub(" ", body)
    body = body.replace("{", " ").replace("}", " ").replace("~", " ").replace("$", "")
    body = body.replace("--", "-").replace("\\\\", "\n")
    return re.sub(r"[ \t]+", " ", re.sub(r"\n\s*\n+", "\n", body)).strip()


def numbers_in(text: str) -> set[str]:
    found = set()
    for value, unit in _NUMBER.findall(text):
        found.add(value.replace(",", "") + (unit or "").lower())
    return found


def month_years_in(text: str) -> set[str]:
    return {f"{month.lower()} {year}" for month, year in _MONTH_YEAR.findall(text)}


def _bare_numbers(numbers: set[str]) -> set[str]:
    return {re.sub(r"[^\d.]", "", item) for item in numbers}


def words_in(text: str) -> set[str]:
    return {word.lower() for word in _WORD.findall(text)}


def skill_terms_in(text: str) -> set[str]:
    return {word.lower() for word in _WORD.findall(text) if _SKILL_SHAPED.match(word)}


@dataclass
class ResumeCheck:
    """Outcome of comparing a tailored resume to its base resume."""

    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    new_numbers: list[str] = field(default_factory=list)
    unsupported_terms: list[str] = field(default_factory=list)
    compiled: bool | None = None
    pdf_path: str | None = None
    page_count: int | None = None
    compile_log_tail: str = ""

    @property
    def passed(self) -> bool:
        return not self.errors

    def to_dict(self) -> dict:
        return {**asdict(self), "passed": self.passed}


def check_tailored_resume(
    base_tex: str,
    tailored_tex: str,
    *,
    job_description: str = "",
    evidence_text: str = "",
) -> ResumeCheck:
    """Static integrity checks; compilation is a separate step.

    ``evidence_text`` is the text of the experience-bank items the tailor cited:
    numbers, dates, and skills found there are as legitimate as the base resume's.
    """

    check = ResumeCheck()
    if "\\begin{document}" not in tailored_tex or "\\end{document}" not in tailored_tex:
        check.errors.append("Tailored resume is not a complete LaTeX document.")
    if tailored_tex.count("{") != tailored_tex.count("}"):
        check.errors.append("Unbalanced braces in tailored LaTeX.")
    base_text = latex_to_text(base_tex)
    tailored_text = latex_to_text(tailored_tex)
    allowed_text = f"{base_text}\n{evidence_text}"
    placeholders = sorted({m.group(0) for m in _PLACEHOLDER.finditer(tailored_text)})
    if placeholders:
        check.errors.append(f"Leftover placeholders: {placeholders}")

    base_numbers = numbers_in(allowed_text)
    base_bare = _bare_numbers(base_numbers)
    new_numbers = sorted(
        number
        for number in numbers_in(tailored_text) - base_numbers
        if re.sub(r"[^\d.]", "", number) not in base_bare
    )
    if new_numbers:
        check.new_numbers = new_numbers
        check.errors.append(
            "Numbers/metrics not present in the base resume or the cited evidence (possible "
            f"fabrication): {new_numbers}. Use figures verbatim from a source and cite its id."
        )

    new_dates = sorted(month_years_in(tailored_text) - month_years_in(allowed_text))
    if new_dates:
        check.errors.append(
            f"Dates not present in the base resume or the cited evidence: {new_dates}. "
            "Keep every date verbatim from a cited source."
        )

    # Skill-like terms taken from the JD that the base resume never mentions are
    # the classic keyword-stuffing failure: surface them for removal or review.
    jd_terms = skill_terms_in(job_description) if job_description else set()
    unsupported = sorted((words_in(tailored_text) - words_in(allowed_text)) & jd_terms)
    if unsupported:
        check.unsupported_terms = unsupported
        check.warnings.append(
            "Skill terms copied from the job description that neither the base resume nor "
            f"the cited evidence mentions: {unsupported}. Remove them unless they are a "
            "faithful rename of something a source shows."
        )
    if len(tailored_text) < 0.4 * len(base_text):
        check.warnings.append("Tailored resume is less than 40% of the base resume's length.")
    return check


TEXLIVE_NET = "https://texlive.net/cgi-bin/latexcgi"


def find_latex_engine(preference: str = "auto") -> str | None:
    if preference == "none":
        return None
    if preference == "texlive_net":
        return preference
    order = ["tectonic", "latexmk", "pdflatex"] if preference == "auto" else [preference]
    for engine in order:
        if shutil.which(engine):
            return engine
    return None


def compile_latex(
    tex_path: Path, *, engine: str | None, timeout_seconds: float = 120.0
) -> tuple[bool, Path | None, str]:
    """Compile ``tex_path`` in its own directory. Returns (ok, pdf_path, log_tail)."""

    if engine is None:
        return False, None, "No LaTeX engine available (install tectonic or TeX Live)."
    if engine == "texlive_net":
        return _compile_texlive_net(tex_path, timeout_seconds)
    workdir = tex_path.parent
    name = tex_path.name
    commands = {
        "tectonic": [["tectonic", "--keep-logs", name]],
        "latexmk": [["latexmk", "-g", "-pdf", "-interaction=nonstopmode", "-halt-on-error", name]],
        # Twice so cross-references and page counts settle.
        "pdflatex": [["pdflatex", "-interaction=nonstopmode", "-halt-on-error", name]] * 2,
    }[engine]
    output = ""
    for command in commands:
        try:
            completed = subprocess.run(
                command,
                cwd=workdir,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return False, None, f"{engine} timed out after {timeout_seconds:.0f}s."
        output = (completed.stdout or "") + (completed.stderr or "")
        if completed.returncode != 0:
            return False, None, output[-3000:]
    pdf = tex_path.with_suffix(".pdf")
    return pdf.is_file(), (pdf if pdf.is_file() else None), output[-1500:]


def _compile_texlive_net(tex_path: Path, timeout_seconds: float) -> tuple[bool, Path | None, str]:
    import httpx

    source = tex_path.read_text(encoding="utf-8")

    def request(kind: str) -> httpx.Response:
        return httpx.post(
            TEXLIVE_NET,
            data={"engine": "pdflatex", "return": kind, "filename[]": "document.tex"},
            files={"filecontents[]": (None, source)},
            timeout=timeout_seconds,
            follow_redirects=True,
        )

    try:
        response = request("pdf")
    except httpx.HTTPError as error:
        return False, None, f"texlive.net request failed: {type(error).__name__}: {error}"
    if response.status_code != 200 or not response.content.startswith(b"%PDF"):
        detail = (
            response.text[-3000:] if response.status_code == 200 else f"HTTP {response.status_code}"
        )
        return False, None, f"texlive.net did not return a PDF: {detail}"
    pdf = tex_path.with_suffix(".pdf")
    pdf.write_bytes(response.content)
    try:
        tex_path.with_suffix(".log").write_text(request("log").text, encoding="utf-8")
    except httpx.HTTPError:
        tex_path.with_suffix(".log").unlink(missing_ok=True)
    return True, pdf, ""


_OVERFULL = re.compile(r"Overfull \\hbox \(([\d.]+)pt too wide\) .*?lines? (\d+)")


def overfull_lines(log_path: Path, tolerance_pt: float = 1.0) -> list[tuple[int, float]]:
    """(source line, points too wide) for every line that visibly overruns the margin."""

    if not log_path.is_file():
        return []
    text = log_path.read_text(encoding="utf-8", errors="replace")
    return [
        (int(line), float(width))
        for width, line in _OVERFULL.findall(text)
        if float(width) > tolerance_pt
    ]


def pdf_page_count(pdf_path: Path) -> int:
    from pypdf import PdfReader

    return len(PdfReader(str(pdf_path)).pages)


_PROJECT_SECTION = re.compile(r"\\section\*?\{[^}]*project[^}]*\}", re.I)
_ANY_SECTION = re.compile(r"\\section\*?\{")
_LIST_BLOCK = re.compile(r"\\begin\{(highlights|itemize)\}(.*?)\\end\{\1\}", re.S)
_ENTRY_TITLE = re.compile(r"\\textbf\{((?:[^{}]|\{[^{}]*\})*)\}")
_ITEM = re.compile(r"\\item\b")


def project_bullet_counts(tex: str) -> list[tuple[str, int]]:
    """(entry title, bullet count) for every list in the Projects section."""

    body = _COMMENT.sub("", tex)
    start = _PROJECT_SECTION.search(body)
    if not start:
        return []
    rest = body[start.end() :]
    following = _ANY_SECTION.search(rest)
    section = rest[: following.start()] if following else rest
    counts = []
    for block in _LIST_BLOCK.finditer(section):
        titles = _ENTRY_TITLE.findall(section[: block.start()])
        counts.append((titles[-1] if titles else "project", len(_ITEM.findall(block.group(2)))))
    return counts
