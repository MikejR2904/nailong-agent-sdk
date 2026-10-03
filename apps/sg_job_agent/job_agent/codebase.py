"""Reading the actual code of a GitHub repository.

`analyze_archive` is deterministic: it walks the repository archive and computes facts
(languages by lines, tests, dependencies, entry points, module docstrings, largest
files). A model later writes higher-level commentary from those facts plus excerpts,
and `verified_claims` keeps only claims whose cited files really exist in the archive.
"""

from __future__ import annotations

import ast
import io
import json
import re
import tarfile
import tomllib
from collections import Counter
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any

SKIP_DIRS = frozenset(
    ".git node_modules dist build venv .venv env __pycache__ vendor third_party target "
    ".next site-packages coverage .idea .vscode .tox .mypy_cache .pytest_cache out bin obj "
    "__snapshots__ migrations".split()
)
SKIP_SUFFIXES = (".lock", ".min.js", ".min.css", ".map", ".svg", ".csv", ".json.gz", ".snap")
SKIP_NAMES = frozenset(
    {"package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock", "uv.lock"}
)
LANGUAGES = {
    ".py": "Python",
    ".ts": "TypeScript",
    ".tsx": "TypeScript",
    ".js": "JavaScript",
    ".jsx": "JavaScript",
    ".java": "Java",
    ".kt": "Kotlin",
    ".cpp": "C++",
    ".cc": "C++",
    ".cxx": "C++",
    ".hpp": "C++",
    ".c": "C",
    ".h": "C/C++",
    ".rs": "Rust",
    ".go": "Go",
    ".cs": "C#",
    ".sv": "SystemVerilog",
    ".v": "Verilog",
    ".vhd": "VHDL",
    ".cu": "CUDA",
    ".swift": "Swift",
    ".rb": "Ruby",
    ".sh": "Shell",
    ".sql": "SQL",
    ".mlir": "MLIR",
    ".scala": "Scala",
    ".php": "PHP",
    ".dart": "Dart",
    ".lua": "Lua",
    ".r": "R",
    ".ipynb": "Jupyter",
    ".html": "HTML",
    ".css": "CSS",
    ".tex": "LaTeX",
}
MAX_FILE_BYTES = 300_000
MAX_ARCHIVE_BYTES = 40_000_000
_TEST_PATH = re.compile(
    r"(^|/)(tests?|__tests__|spec)(/|$)|(^|/)test_[^/]*|_test\.|\.test\.|\.spec\."
)


@dataclass
class CodeFacts:
    full_name: str
    file_count: int = 0
    total_lines: int = 0
    lines_by_language: dict[str, int] = field(default_factory=dict)
    top_directories: dict[str, int] = field(default_factory=dict)
    test_files: int = 0
    has_ci: bool = False
    has_license: bool = False
    dependencies: dict[str, list[str]] = field(default_factory=dict)
    entry_points: list[str] = field(default_factory=list)
    largest_files: list[tuple[str, int]] = field(default_factory=list)
    module_docs: dict[str, str] = field(default_factory=dict)
    python_classes: int = 0
    python_functions: int = 0
    file_paths: list[str] = field(default_factory=list)

    def to_text(self) -> str:
        languages = ", ".join(
            f"{name} {lines:,}"
            for name, lines in sorted(self.lines_by_language.items(), key=lambda pair: -pair[1])[:6]
        )
        parts = [
            f"Code facts computed from the repository archive of {self.full_name}:",
            f"{self.file_count} source/text files, {self.total_lines:,} lines ({languages}).",
            f"{self.test_files} test files. CI workflows: {'yes' if self.has_ci else 'no'}."
            f" License file: {'yes' if self.has_license else 'no'}.",
        ]
        if self.python_classes or self.python_functions:
            parts.append(
                f"Python: {self.python_classes} classes, {self.python_functions} functions."
            )
        if self.top_directories:
            tree = ", ".join(f"{name}/ ({count})" for name, count in self.top_directories.items())
            parts.append(f"Top-level layout: {tree}.")
        for manifest, names in self.dependencies.items():
            parts.append(f"Dependencies ({manifest}): {', '.join(names[:25])}.")
        if self.entry_points:
            parts.append("Entry points: " + ", ".join(self.entry_points[:8]) + ".")
        if self.largest_files:
            parts.append(
                "Largest source files: "
                + ", ".join(f"{path} ({lines} lines)" for path, lines in self.largest_files[:8])
                + "."
            )
        if self.module_docs:
            parts.append("Module docstrings:")
            parts += [f"- {path}: {doc}" for path, doc in list(self.module_docs.items())[:12]]
        return "\n".join(parts)


def _is_text(data: bytes) -> bool:
    return b"\x00" not in data[:2048]


def _manifest_dependencies(path: str, text: str) -> list[str]:
    name = PurePosixPath(path).name
    try:
        if name == "pyproject.toml":
            project = tomllib.loads(text).get("project", {})
            deps = list(project.get("dependencies", []))
            return [re.split(r"[<>=!~\[ ;]", dep, maxsplit=1)[0] for dep in deps]
        if name.startswith("requirements") and name.endswith(".txt"):
            lines = [line.strip() for line in text.splitlines()]
            return [
                re.split(r"[<>=!~\[ ;]", line, maxsplit=1)[0]
                for line in lines
                if line and not line.startswith(("#", "-"))
            ]
        if name == "package.json":
            data = json.loads(text)
            return list(data.get("dependencies", {})) + list(data.get("devDependencies", {}))
        if name == "Cargo.toml":
            return list(tomllib.loads(text).get("dependencies", {}))
        if name == "go.mod":
            return re.findall(r"^\s*(?:require\s+)?([\w./-]+\.[\w./-]+)\s+v[\w.-]+", text, re.M)
        if name == "pom.xml":
            return re.findall(r"<artifactId>([^<]+)</artifactId>", text)
        if name == "CMakeLists.txt":
            return re.findall(r"find_package\(\s*([\w+-]+)", text, re.I)
    except (ValueError, tomllib.TOMLDecodeError):
        return []
    return []


def _python_stats(text: str) -> tuple[int, int, str]:
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return 0, 0, ""
    classes = sum(isinstance(node, ast.ClassDef) for node in ast.walk(tree))
    functions = sum(
        isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) for node in ast.walk(tree)
    )
    doc = (ast.get_docstring(tree) or "").strip().splitlines()
    return classes, functions, doc[0][:160] if doc else ""


def analyze_archive(data: bytes, full_name: str) -> tuple[CodeFacts, dict[str, str]]:
    """Return (facts, source texts by path) for a repository tarball."""

    facts = CodeFacts(full_name=full_name)
    sources: dict[str, str] = {}
    lines_by_language: Counter[str] = Counter()
    directories: Counter[str] = Counter()
    sizes: list[tuple[str, int]] = []
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as archive:
        for member in archive:
            if not member.isfile():
                continue
            parts = PurePosixPath(member.name).parts[1:]
            if not parts or any(part in SKIP_DIRS for part in parts[:-1]):
                continue
            path = "/".join(parts)
            name = parts[-1]
            if name in SKIP_NAMES or name.endswith(SKIP_SUFFIXES) or member.size > MAX_FILE_BYTES:
                continue
            if path.startswith(".github/workflows/"):
                facts.has_ci = True
            if re.match(r"(?i)licen[cs]e", name):
                facts.has_license = True
            handle = archive.extractfile(member)
            raw = handle.read() if handle else b""
            if not raw or not _is_text(raw):
                continue
            text = raw.decode("utf-8", errors="replace")
            facts.file_paths.append(path)
            facts.file_count += 1
            directories[parts[0] if len(parts) > 1 else "."] += 1
            suffix = PurePosixPath(name).suffix.lower()
            dependencies = _manifest_dependencies(path, text)
            if dependencies:
                facts.dependencies[path] = dependencies
            language = LANGUAGES.get(suffix)
            if language is None:
                continue
            if _TEST_PATH.search(path):
                facts.test_files += 1
            line_count = text.count("\n") + 1
            lines_by_language[language] += line_count
            facts.total_lines += line_count
            sources[path] = text
            if not _TEST_PATH.search(path):
                sizes.append((path, line_count))
            if suffix == ".py":
                classes, functions, doc = _python_stats(text)
                facts.python_classes += classes
                facts.python_functions += functions
                if doc and not name.startswith("test_"):
                    facts.module_docs[path] = doc
                if name in ("__main__.py", "cli.py", "main.py", "app.py", "server.py"):
                    facts.entry_points.append(path)
            elif name in (
                "main.go",
                "main.rs",
                "main.c",
                "main.cpp",
                "index.js",
                "index.ts",
                "Main.java",
            ):
                facts.entry_points.append(path)
    facts.lines_by_language = dict(lines_by_language)
    facts.top_directories = dict(directories.most_common(10))
    facts.largest_files = sorted(sizes, key=lambda pair: -pair[1])[:10]
    return facts, sources


def key_excerpts(
    facts: CodeFacts, sources: dict[str, str], *, budget: int = 18_000
) -> dict[str, str]:
    """First lines of the largest non-test source files, up to a character budget."""

    excerpts: dict[str, str] = {}
    used = 0
    for path, _lines in facts.largest_files:
        snippet = "\n".join(sources[path].splitlines()[:70])[:2_500]
        if used + len(snippet) > budget:
            break
        excerpts[path] = snippet
        used += len(snippet)
    return excerpts


def verified_claims(claims: list[dict[str, Any]], facts: CodeFacts) -> list[dict[str, Any]]:
    """Keep claims that cite at least one file, all of which exist in the archive."""

    known = set(facts.file_paths)
    kept = []
    for claim in claims:
        paths = [str(path) for path in claim.get("evidence_paths", [])]
        if paths and all(path in known for path in paths) and claim.get("claim"):
            kept.append({"claim": str(claim["claim"]), "evidence_paths": paths})
    return kept
