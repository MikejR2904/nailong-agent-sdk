# Copyright (c) 2026 David Michael Indraputra

"""Edit a cell of a Jupyter notebook anywhere in a project, without running it."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

from ..tools.artifacts import ArtifactStore
from ..tools.core.services import CoreToolDispatcher, CoreToolServices
from .tools import FunctionTool, tool

NOTEBOOK_SUFFIX = ".ipynb"


def notebook_tools(
    root: Path, *, protected: Sequence[str] = (), needs_approval: bool = True
) -> FunctionTool:
    base = root.resolve()
    guarded = tuple(protected)

    async def notebook_edit(
        path: str, new_source: str, cell_index: int = 0, mode: str = "replace"
    ) -> dict[str, Any]:
        target = (base / path).resolve()
        try:
            relative = target.relative_to(base)
        except ValueError as error:
            raise ValueError(f'The notebook "{path}" is outside the project.') from error
        if target.suffix != NOTEBOOK_SUFFIX:
            raise ValueError(f'"{path}" is not a notebook; only {NOTEBOOK_SUFFIX} files.')
        if not target.is_file():
            raise ValueError(f'The notebook "{path}" does not exist.')
        if relative.parts and relative.parts[0] in guarded:
            raise ValueError(f'"{path}" is protected; the agent may not edit it.')
        name = relative.as_posix()
        services = CoreToolServices(
            root=base,
            artifacts=ArtifactStore(base, read_only=False),
            declared_output_paths=(name,),
        )
        result = await CoreToolDispatcher(services).execute(
            "notebook_edit",
            {"path": name, "new_source": new_source, "cell_index": cell_index, "mode": mode},
        )
        if result.status != "succeeded":
            raise ValueError(str(result.error or result.status))
        return result.output if isinstance(result.output, dict) else {"result": result.output}

    return tool(
        notebook_edit,
        name="notebook_edit",
        description=(
            "Replace (mode replace) or append to (mode append) one cell of a Jupyter notebook "
            "in the project by cell_index, without running it."
        ),
        needs_approval=needs_approval,
    )
