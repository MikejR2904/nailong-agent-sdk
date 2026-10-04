# `(root)/` - the package root: one lazy public import surface

`nailong_agent_sdk/__init__.py` is the only file at the package root. It holds no logic beyond lazy name resolution; it exposes the supported public names so consumers write `from nailong_agent_sdk import BaseAgent`, and importing the package itself loads nothing but the standard library.

| File | Lines | Role |
|---|---:|---|
| [`__init__.py`](#__init__py---the-public-api-surface-lazy-re-exports) | 782 | the public API surface (lazy re-exports) |

---

### `__init__.py` - the public API surface (lazy re-exports)

*782 lines · depends on: nothing at import time (the `if TYPE_CHECKING:` block names 67 deep modules for type checkers and editors) · used by: no other module (entry point or re-exported only)*

**Role in the workflow.** `_EXPORTS` maps each of the 369 public names to the deep module that defines it and `__all__` is that table's key list; the developer catalogue (`developer_tools/catalog.py`) reads exactly `__all__`, so a name that is not in it is not public API. `__getattr__` imports a name's module on first use and caches the value, and also imports a sub-package (`nailong_agent_sdk.tools`, `nailong_agent_sdk.mcp`, ...) when it is accessed as an attribute. The `if TYPE_CHECKING:` block repeats the same 70 `from .module import (...)` statements so type checkers and IDEs resolve every name; `tests/foundations/test_package_root.py` keeps the table, that block and `__all__` identical. Importing the package costs about 10 ms and 58 modules and loads neither `pydantic` nor `mcp`; `from nailong_agent_sdk import BaseAgent` loads the agent runtime without the MCP server stack, and only the MCP client and server names (`McpClientManager`, `create_mcp_server`, ...) import `mcp`, `httpx` and `starlette`. Nothing depends on the root's import order any more: every module imports cleanly as the first import of a fresh interpreter, including the two folder-level cycles (`tools` with `state`, and `agent` with `integrations`).

**Contents**

- `__getattr__(name: str) -> Any` - Module hook (PEP 562): resolves a public name through `_EXPORTS` (importing its module and caching the value in the module namespace) or a sub-package name, otherwise raises `AttributeError: module 'nailong_agent_sdk' has no attribute '<name>'`.
- `__dir__() -> list[str]` - Sorted union of the namespace, the public names and the sub-packages, so `dir()` and tab completion show the whole surface.

**Algorithms & invariants.** The package ships a `py.typed` marker, which together with the `TYPE_CHECKING` block lets downstream type checkers treat the exports as typed.

*Module-level names:* `_EXPORTS`, `_SUBPACKAGES`, `__all__`
