# Contributing

## Set up

```bash
uv sync
cd apps/nailong_cli && uv sync
```

The CLI has its own environment in `apps/nailong_cli/.venv`; run its tests from that folder.

## Checks that CI runs

```bash
python -m ruff check src tests apps/nailong_cli
python -m ruff format --check src tests apps/nailong_cli
python -m mypy --platform linux nailong_cli      # from apps/nailong_cli
python -m pytest -m "not docker"                 # SDK, from the repository root
python -m pytest                                 # CLI, from apps/nailong_cli
```

Run the type check for `--platform win32` as well when you touch platform-specific code.

## Conventions

- Source files under `src/` carry no comments and no narrative docstrings; the code and the names
  say what it does. Put explanations in `docs/`.
- Every failure message names the exact field, status or exception, not a generic wrapper.
- Keep test modules topical and under about 500 lines; shared fixtures go in the folder's
  `conftest.py`, helpers in `tests/support`.
- Do not change the package version in a feature change.
- Repository files use CRLF line endings on Windows; do not rewrite whole files to change them.

## Tests that need a key

Tests that call a real model are marked `live` and skip without `FIREWORKS_API_KEY`.
