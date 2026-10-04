import ast
import importlib
import subprocess
import sys
from pathlib import Path

import nailong_agent_sdk as package
from tests.support.processes import child_environment

ROOT_FILE = Path(package.__file__)


def run_python(code):
    completed = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        env=child_environment(),
        timeout=120,
    )
    assert completed.returncode == 0, completed.stderr
    return completed.stdout.strip().splitlines()


def type_checking_names():
    tree = ast.parse(ROOT_FILE.read_text("utf-8"))
    block = next(
        node
        for node in tree.body
        if isinstance(node, ast.If) and ast.unparse(node.test) == "TYPE_CHECKING"
    )
    return {
        alias.name: "." + statement.module
        for statement in block.body
        if isinstance(statement, ast.ImportFrom)
        for alias in statement.names
    }


def test_the_lazy_export_table_matches_the_static_import_block_and_all():
    static = type_checking_names()
    assert package._EXPORTS == static
    assert package.__all__ == list(package._EXPORTS)
    assert len(package.__all__) == len(set(package.__all__)) == 369


def test_every_export_resolves_to_the_object_its_module_defines():
    for name, module_path in package._EXPORTS.items():
        module = importlib.import_module(module_path, package.__name__)
        assert getattr(package, name) is getattr(module, name), name


def test_unknown_names_raise_attribute_error_naming_the_module():
    try:
        package.definitely_not_exported
    except AttributeError as error:
        assert str(error) == "module 'nailong_agent_sdk' has no attribute 'definitely_not_exported'"
    else:
        raise AssertionError("an unknown attribute was resolved")
    assert not hasattr(package, "_EXPORTS_MISSING")


def test_subpackages_and_exports_are_listed_by_dir():
    listing = dir(package)
    assert set(package.__all__) <= set(listing)
    assert {"agent", "mcp", "tools", "state"} <= set(listing)
    assert listing == sorted(listing)


def test_the_package_ships_a_py_typed_marker():
    assert (ROOT_FILE.parent / "py.typed").is_file()


def test_importing_the_root_loads_neither_the_mcp_stack_nor_the_runtime():
    lines = run_python(
        "import sys\n"
        "import nailong_agent_sdk\n"
        "loaded = sorted(m for m in ('mcp', 'starlette', 'uvicorn', 'httpx', 'pydantic', "
        "'jsonschema', 'sqlite3') if m in sys.modules)\n"
        "agent = [m for m in sys.modules if m.startswith('nailong_agent_sdk.agent')]\n"
        "print(loaded)\n"
        "print(len(sys.modules))\n"
        "print(agent)\n"
    )
    assert lines[0] == "[]", lines
    assert int(lines[1]) < 150, lines
    assert lines[2] == "[]", lines


def test_using_the_agent_runtime_does_not_import_the_mcp_server_stack():
    lines = run_python(
        "import sys\n"
        "from nailong_agent_sdk import BaseAgent, StateGraph\n"
        "print(sorted(m for m in ('mcp', 'starlette', 'uvicorn') if m in sys.modules))\n"
        "from nailong_agent_sdk import create_mcp_server\n"
        "print(sorted(m for m in ('mcp', 'starlette', 'uvicorn') if m in sys.modules))\n"
    )
    assert lines[0] == "[]", lines
    assert "mcp" in lines[1] and "starlette" in lines[1], lines


def test_star_import_resolves_the_whole_public_surface():
    namespace = {}
    exec("from nailong_agent_sdk import *", namespace)
    assert set(package.__all__) <= set(namespace)


def test_every_module_imports_first_in_a_fresh_interpreter():
    from concurrent.futures import ThreadPoolExecutor

    from tests.support.paths import SRC_ROOT

    names = []
    for path in sorted((SRC_ROOT / "nailong_agent_sdk").rglob("*.py")):
        parts = list(path.relative_to(SRC_ROOT).with_suffix("").parts)
        if parts[-1] == "__init__":
            parts = parts[:-1]
        names.append(".".join(parts))

    def attempt(module):
        completed = subprocess.run(
            [sys.executable, "-c", f"import {module}"],
            capture_output=True,
            text=True,
            env=child_environment(),
            timeout=120,
        )
        tail = completed.stderr.strip().splitlines()[-1:]
        return module, completed.returncode, tail

    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(attempt, names))
    failures = {module: tail for module, code, tail in results if code != 0}
    assert len(results) >= 120
    assert failures == {}, failures
