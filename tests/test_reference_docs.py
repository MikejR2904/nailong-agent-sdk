import ast
import re

from tests.support.paths import REPO_ROOT, SRC_ROOT

DOCS = REPO_ROOT / "docs" / "reference"
PACKAGE = SRC_ROOT / "nailong_agent_sdk"
SECTION = re.compile(r"^### `([^`]+)` - ", re.MULTILINE)
CLASS_ENTRY = re.compile(r"^ *- \*\*class `([^`]+)`\*\*", re.MULTILINE)
FUNCTION_ENTRY = re.compile(r"^ *- `([A-Za-z_][\w.]*)\(", re.MULTILINE)
INDEX_ROW = re.compile(r"^\| \[`([^`]+)`\]\(", re.MULTILINE)


def definitions(tree):
    found = {}

    def visit(body, prefix):
        for node in body:
            if isinstance(node, ast.ClassDef):
                found[prefix + node.name] = "class"
                visit(node.body, prefix + node.name + ".")
            elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                found[prefix + node.name] = "function"
                visit(node.body, prefix + node.name + ".")
            else:
                for field in ("body", "orelse", "finalbody"):
                    children = getattr(node, field, None)
                    if isinstance(children, list):
                        visit(children, prefix)
                for handler in getattr(node, "handlers", []):
                    visit(handler.body, prefix)

    visit(tree.body, "")
    return found


def source_definitions():
    result = {}
    for path in sorted(PACKAGE.rglob("*.py")):
        relative = path.relative_to(PACKAGE).as_posix()
        result[relative] = definitions(ast.parse(path.read_text("utf-8")))
    return result


def documented_definitions():
    result = {}
    for page in sorted(DOCS.glob("*.md")):
        if page.name == "README.md":
            continue
        text = page.read_text("utf-8")
        starts = [(match.start(), match.group(1)) for match in SECTION.finditer(text)]
        for index, (start, file) in enumerate(starts):
            end = starts[index + 1][0] if index + 1 < len(starts) else len(text)
            block = text[start:end]
            names = set(CLASS_ENTRY.findall(block)) | set(FUNCTION_ENTRY.findall(block))
            assert file not in result, f"{file} has two sections in the reference"
            result[file] = names
    return result


def test_every_source_file_has_exactly_one_reference_section():
    assert set(documented_definitions()) == set(source_definitions())


def test_every_class_and_function_has_an_entry_and_no_entry_is_stale():
    documented = documented_definitions()
    problems = {}
    for file, items in source_definitions().items():
        missing = sorted(set(items) - documented[file])
        stale = sorted(documented[file] - set(items))
        if missing or stale:
            problems[file] = {"undocumented": missing, "no longer in the source": stale}
    assert problems == {}


def test_the_reference_index_lists_every_file_and_states_the_right_totals():
    source = source_definitions()
    classes = sum(kind == "class" for items in source.values() for kind in items.values())
    functions = sum(kind == "function" for items in source.values() for kind in items.values())
    readme = (DOCS / "README.md").read_text("utf-8")
    listed = set(INDEX_ROW.findall(readme))
    assert set(source) <= listed, sorted(set(source) - listed)
    assert (
        f"covers **{len(source)} files, {classes} classes and {functions:,} functions**" in readme
    )
    assert f"*Coverage: {len(source)} files, {classes} classes and {functions} functions" in readme


def test_the_root_page_states_the_real_name_module_and_line_counts():
    source = (PACKAGE / "__init__.py").read_text("utf-8")
    tree = ast.parse(source)
    block = next(
        node
        for node in tree.body
        if isinstance(node, ast.If) and ast.unparse(node.test) == "TYPE_CHECKING"
    )
    modules = sum(isinstance(statement, ast.ImportFrom) for statement in block.body)
    exports = next(
        node.value
        for node in tree.body
        if isinstance(node, ast.AnnAssign) and getattr(node.target, "id", "") == "_EXPORTS"
    )
    names = len(exports.keys)
    lines = len(source.splitlines())
    page = (DOCS / "root.md").read_text("utf-8")
    index = (DOCS / "README.md").read_text("utf-8")
    expected = {
        "public names": f"each of the {names} public names",
        "deep modules": f"names {modules} deep modules",
        "line count": f"*{lines} lines",
        "index row": f"| {lines} | the public API surface",
    }
    missing = {
        label: phrase
        for label, phrase in expected.items()
        if phrase not in (index if label == "index row" else page)
    }
    assert missing == {}, (
        f"root.md and the README index disagree with __init__.py ({names} names, {modules} "
        f"modules, {lines} lines); update these phrases: {missing}"
    )
