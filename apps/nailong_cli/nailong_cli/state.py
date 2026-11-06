"""Everything one running session holds, passed to commands and the loop."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from nailong_agent_sdk.starter import Agent, Answer, ModelSetup, Session

from .config import CliConfig
from .console import Console
from .factory import AgentBuilder, Parts
from .live import LiveWork
from .paste import PasteStore
from .permissions import PermissionStore
from .user_commands import UserCommand


@dataclass
class ReplState:
    builder: AgentBuilder
    agent: Agent
    session: Session
    setup: ModelSetup
    runner: asyncio.Runner
    config: CliConfig
    sandbox_label: str
    home: Path
    environ: Mapping[str, str]
    model_factory: Callable[..., ModelSetup]
    store: PermissionStore
    user_commands: dict[str, UserCommand] = field(default_factory=dict)
    keys: Any = None
    plugin_names: list[str] = field(default_factory=list)
    reader: Any = None
    last_answer: Answer | None = None
    turns_run: int = 0
    mode_dirty: bool = False
    first_images: list[Path] = field(default_factory=list)
    typed_leftover: str = ""
    variant: str | None = None
    pastes: PasteStore = field(default_factory=PasteStore)

    @property
    def live(self) -> LiveWork:
        return self.builder.parts.live

    @property
    def parts(self) -> Parts:
        return self.builder.parts

    @property
    def console(self) -> Console:
        return self.builder.parts.console

    @property
    def project(self) -> Path:
        return self.config.project
