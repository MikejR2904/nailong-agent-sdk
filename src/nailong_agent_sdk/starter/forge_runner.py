# Copyright (c) 2026 David Michael Indraputra

"""Standalone script a forged tool runs under: import its source, call run(), print the result."""

import importlib.util
import json
import sys

MARKER = "@@NAILONG-RESULT@@ "


def main(source_path: str, arguments_json: str) -> int:
    spec = importlib.util.spec_from_file_location("forged_tool", source_path)
    if spec is None or spec.loader is None:
        sys.stderr.write(f"Cannot load {source_path} as a Python module.\n")
        return 2
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    run = getattr(module, "run", None)
    if not callable(run):
        sys.stderr.write("The tool source does not define a callable run().\n")
        return 2
    result = run(**json.loads(arguments_json))
    sys.stdout.write("\n" + MARKER + json.dumps(result, allow_nan=False) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2]))
