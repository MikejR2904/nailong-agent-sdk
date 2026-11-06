"""Assembling the agent from the settings, the mode and everything the session holds."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from nailong_agent_sdk import ContextProjectionPolicy
from nailong_agent_sdk.starter import (
    Agent,
    Forge,
    FunctionTool,
    McpTools,
    ModelSetup,
    SkillDefinition,
    ToolGroup,
    core_tools,
    dev_tools,
    notebook_tools,
)

from .agent_files import BackgroundAgents, ScopedApprover
from .apply_patch import patch_tool
from .approvals import Approver
from .background import BackgroundJobs
from .checkpoints import CheckpointStore
from .config import PROJECT_DIRECTORY, CliConfig
from .console import Console
from .cost import Spending, price_table
from .custom_tools import CustomTools
from .debug import DebugLog
from .documents import VISION_INSTRUCTIONS, DocumentReader, document_tools
from .environment import environment_note
from .hooks import LifecycleHooks
from .knowledge import KnowledgeBase
from .live import LiveWork
from .lsp import navigation_tools
from .mcp_config import refusal_for
from .memory import MemoryFile, memory_text, nested_note
from .modes import ModeRef, parse_rule
from .notes import NotesStore
from .post_edit import PostEdit
from .repeat_guard import RepeatGuard
from .rules import PermissionRules, Rule, rules_hook
from .shell_sessions import ShellSessions
from .skills import SkillScope, skill_tool, skills_index
from .telemetry_export import TelemetryExport
from .todo import TodoList, todo_tool
from .wiki import Wiki

DEFAULT_INSTRUCTIONS = (
    "You are a coding and research assistant working in the user's project directory. Read the "
    "relevant files before changing anything, make small focused edits, run the project's tests "
    "or checks after a change, and stop when the request is done. Say plainly what you did and "
    "what you could not.\n\n"
    "How to work:\n"
    "- Before a tool call, say in a sentence what you are looking for and why.\n"
    "- After a result, note only what is new in it and what you will do next. Do not restate "
    "what you already established; build on it.\n"
    "- If only the user can decide something, ask with ask_human_question instead of guessing.\n"
    "- Never repeat a call you have already made. If a search returned nothing or a page was "
    "not useful, change the query or the source instead of retrying the same thing.\n"
    "- Independent lookups can go in one step; do not run several web searches at once.\n"
    "- Stop gathering as soon as the evidence answers the question, then answer once: lead with "
    "the answer, give the supporting facts with their source URLs or file paths, and say what "
    "is uncertain or could not be confirmed. Do not draft the answer before the final reply."
)
PLAN_NOTE = (
    "You are in plan mode: you can read and search but you cannot change anything or run "
    "commands. Investigate, then finish with a short numbered plan of exactly what you would "
    "change and why."
)
EXPLORE_NOTE = (
    "You can read, search and fetch pages but you cannot change anything or run commands. "
    "Do the task you are given and report what you found, with sources."
)
PROTECTED = (PROJECT_DIRECTORY, "NAILONG.md")


@dataclass
class Parts:
    config: CliConfig
    console: Console
    mode: ModeRef
    approver: Approver
    renderer: Any
    checkpoints: CheckpointStore
    todos: TodoList
    memory: list[MemoryFile]
    skills: list[SkillDefinition]
    sandbox_kwargs: dict[str, Any]
    spending: Spending
    forge: Forge | None = None
    mcp_groups: list[McpTools] = field(default_factory=list)
    hooks_before: list[Any] = field(default_factory=list)
    hooks_after: list[Any] = field(default_factory=list)
    lifecycle: LifecycleHooks | None = None
    background: BackgroundJobs | None = None
    shells: ShellSessions | None = None
    live: LiveWork = field(default_factory=LiveWork)
    model_for: Callable[..., ModelSetup] | None = None
    bg_agents: BackgroundAgents | None = None
    custom_tools: CustomTools | None = None
    permission_rules: PermissionRules | None = None
    post_edit: PostEdit | None = None
    knowledge: KnowledgeBase | None = None
    wiki: Wiki | None = None
    current_agent: Any = None
    notes: NotesStore | None = None
    debug: DebugLog | None = None
    extra_dirs: dict[str, Path] = field(default_factory=dict)
    json_schema: dict[str, Any] | None = None
    system_append: str | None = None
    repeat_guard: RepeatGuard = field(default_factory=RepeatGuard)
    fallbacks: list[ModelSetup] = field(default_factory=list)
    exporter: TelemetryExport | None = None
    skill_scope: SkillScope = field(default_factory=SkillScope)


def context_policy(config: CliConfig) -> ContextProjectionPolicy:
    return ContextProjectionPolicy(
        context_token_budget=config.context_tokens,
        episode_token_budget=config.context_tokens * 3 // 4,
        tool_result_preview_chars=config.tool_result_chars,
        compacted_summary_chars=400,
        compacted_stub_token_budget=min(3_000, config.context_tokens // 8),
    )


class AgentBuilder:
    def __init__(self, parts: Parts) -> None:
        self.parts = parts
        self._built: list[Agent] = []
        self.setup_in_use: ModelSetup | None = None

    def build(self, setup: ModelSetup, mode: str | None = None) -> Agent:
        parts = self.parts
        active = mode or parts.mode.value
        before = [
            parts.repeat_guard.before_tool,
            parts.renderer.before_tool,
            parts.checkpoints.before_tool,
            parts.skill_scope.before_tool,
            *(
                [rules_hook(parts.permission_rules, parts.approver)]
                if parts.permission_rules
                else []
            ),
            lambda event: refusal_for(parts.config.mcp_servers, event.tool),
            *parts.hooks_before,
        ]
        after = [parts.repeat_guard.after_tool, parts.renderer.after_tool, *parts.hooks_after]
        if parts.post_edit is not None and parts.post_edit.active:
            after.append(parts.post_edit.after_tool)
        if parts.debug is not None:
            before.append(parts.debug.before_tool)
            after.append(parts.debug.after_tool)
        agent = Agent(
            model=setup,
            context=context_policy(parts.config),
            fallbacks=parts.fallbacks,
            instructions=self.instructions_for(active),
            tools=self._tools(setup, active),
            approve=parts.approver,
            recoverable_refusals=True,
            before_tool=before,
            after_tool=after,
            on_stream=self._on_stream(),
            output=parts.json_schema,
            prices=price_table(parts.config, setup),
            max_steps=parts.config.max_steps,
            invalid_output_retries=parts.config.invalid_output_retries,
            model_turn_timeout_seconds=parts.config.model_timeout_seconds or None,
            tool_timeout_seconds=parts.config.tool_timeout_seconds or None,
            deadline_seconds=parts.config.run_deadline_seconds or None,
            workspace=parts.config.workspace,
            name="nailong",
        )
        self._built.append(agent)
        parts.current_agent = agent
        if parts.exporter is not None:
            parts.exporter.watch(agent.services)
        return agent

    async def _ask_user(self, question: str) -> str | None:
        console = self.parts.console
        if not console.interactive:
            return None

        def read() -> str:
            console.line()
            console.line(console.style(f"? {question}", "bold"))
            return console.ask("> ")

        try:
            answer = await asyncio.to_thread(read)
        except EOFError:
            return None
        return answer or None

    def close(self) -> None:
        for agent in self._built:
            agent.close()
        self._built.clear()

    def instructions_for(self, mode: str) -> str:
        parts = self.parts
        pieces = [parts.config.instructions or DEFAULT_INSTRUCTIONS]
        pieces.append(environment_note(parts.config.project, sandboxed=bool(parts.sandbox_kwargs)))
        if parts.shells is not None:
            pieces.append(
                "For work that needs state to carry over (a directory, variables, a login to a "
                "server), open a persistent shell with shell_open and use shell_run, instead of "
                "repeating run_command or logging in again for every step."
            )
        if any(
            spec.get("tools", "read-only") == "read-only" for spec in parts.config.agents.values()
        ):
            pieces.append(
                "Read-only subagents can work in parallel: call several agent_* tools in one step, "
                "or start one with agent_start and collect its answer later with agent_result "
                "while you do something else."
            )
        pieces.append(
            "Local PDF, Word, Excel and CSV files are read with read_document (a page or sheet at "
            "a "
            "time; search_document finds the pages first); look at a diagram or scanned page with "
            "interpret_page. Cite page numbers."
        )
        if parts.knowledge is not None and parts.knowledge.trees():
            pieces.append(
                "A knowledge base of project files is available: use knowledge_search before "
                "answering questions about them, and cite the path and location of each passage."
            )
        if parts.wiki is not None:
            pieces.append(
                "The project wiki holds what earlier work learned: check wiki_search first, and "
                "record durable findings as short linked pages with wiki_write."
            )
        if parts.notes is not None:
            pieces.append(
                "For a long task, keep notes with notes_write (goal, facts learned, what is done, "
                "what is next) and reread them with notes_read when you resume."
            )
        if parts.system_append:
            pieces.append(parts.system_append)
        if parts.memory:
            pieces.append(memory_text(parts.memory))
        nested = nested_note(parts.config.project)
        if nested:
            pieces.append(nested)
        if parts.extra_dirs:
            listing = "\n".join(f"- {name}: {path}" for name, path in parts.extra_dirs.items())
            pieces.append(
                "You can also use these added directories; name one in the root argument of the "
                f"file tools:\n{listing}"
            )
        index = skills_index(parts.skills)
        if index:
            pieces.append(index)
        if mode == "plan":
            pieces.append(PLAN_NOTE)
        elif mode == "explore":
            pieces.append(EXPLORE_NOTE)
        return "\n\n".join(pieces)

    def _on_stream(self):
        parts = self.parts
        if parts.debug is None:
            return parts.renderer.on_stream

        def fan_out(event):
            parts.renderer.on_stream(event)
            parts.debug.on_stream(event)

        return fan_out

    def _tools(self, setup: ModelSetup, mode: str) -> list[FunctionTool | ToolGroup]:
        parts = self.parts
        project = parts.config.project
        planning = mode in {"plan", "explore"}
        tools: list[Any] = [
            *dev_tools(
                project,
                allow_writes=not planning,
                allow_commands=not planning,
                protected=PROTECTED,
                extra_roots=parts.extra_dirs or None,
                **parts.sandbox_kwargs,
            ),
            todo_tool(parts.todos, parts.console),
            core_tools(project, only=("ask_human_question",), ask_human=self._ask_user),
        ]
        tools.extend(
            document_tools(
                DocumentReader(self._roots(), parts.config.workspace),
                self._vision(setup),
                fetchable=parts.config.web,
            )
        )
        if (
            parts.post_edit is not None
            and parts.post_edit.lsp is not None
            and parts.post_edit.lsp.active
        ):
            tools.extend(navigation_tools(parts.post_edit.lsp, project))
        if parts.knowledge is not None:
            tools.extend(parts.knowledge.tools())
        if parts.wiki is not None:
            tools.extend(
                item for item in parts.wiki.tools() if not planning or item.name != "wiki_write"
            )
        loader = skill_tool(parts.skills, parts.skill_scope)
        if loader is not None:
            tools.append(loader)
        if parts.config.web:
            tools.append(core_tools(project, only=("web_fetch",), needs_approval=True))
        if parts.config.web_search:
            tools.append(core_tools(project, only=("web_search",), needs_approval=True))
        if not planning:
            tools.append(notebook_tools(project, protected=PROTECTED))
            tools.append(patch_tool(project, PROTECTED))
            tools.extend(parts.mcp_groups)
            if parts.forge is not None:
                tools.append(parts.forge)
            if parts.background is not None:
                tools.extend(parts.background.tools())
            if parts.shells is not None:
                tools.extend(parts.shells.tools())
        if parts.notes is not None:
            tools.extend(parts.notes.tools())
        if not planning and parts.custom_tools is not None:
            tools.extend(self._custom(tools))
        tools.extend(self._subagents(setup))
        if not planning:
            tools.extend(self._background_agents(setup).tools())
        return tools

    def _helper(
        self, name: str, spec: dict[str, Any], setup: ModelSetup, *, background: bool = False
    ) -> Agent:
        parts = self.parts
        level = spec.get("tools", "read-only")
        extra = {"temperature": spec["temperature"]} if "temperature" in spec else None
        chosen = setup
        if parts.model_for is not None and (spec.get("model") or extra):
            chosen = parts.model_for(spec.get("model") or parts.config.model, extra)
        denied = PermissionRules(
            [
                Rule("deny", (tool,), pattern, f"agent {name}")
                for tool, pattern in (parse_rule(text) for text in spec.get("deny", []))
            ]
        )
        return Agent(
            model=chosen,
            instructions=spec["instructions"],
            tools=dev_tools(
                parts.config.project,
                allow_writes=level in {"edit", "all"},
                allow_commands=level == "all",
                protected=PROTECTED,
                extra_roots=parts.extra_dirs or None,
                **(parts.sandbox_kwargs if level == "all" else {}),
            ),
            approve=ScopedApprover(name, spec.get("allow", []))
            if background and level != "read-only"
            else parts.approver,
            recoverable_refusals=True,
            before_tool=[rules_hook(denied, parts.approver)] if denied.rules else [],
            max_steps=spec.get("steps") or parts.config.max_steps,
            workspace=parts.config.workspace,
            name=f"sub-{name}",
        )

    def _background_agents(self, setup: ModelSetup) -> BackgroundAgents:
        parts = self.parts
        agents = parts.config.agents
        readonly = {
            name
            for name, spec in agents.items()
            if spec.get("tools", "read-only") == "read-only" or spec.get("allow")
        }
        if parts.bg_agents is None:
            parts.bg_agents = BackgroundAgents(
                lambda name: self._helper(
                    name, parts.config.agents[name], self.setup_in_use, background=True
                ),
                parts.live,
                readonly,
            )
        self.setup_in_use = setup
        return parts.bg_agents

    def _roots(self) -> dict[str, Path]:
        return {"project": self.parts.config.project, **self.parts.extra_dirs}

    def _vision(self, setup: ModelSetup):
        parts = self.parts

        async def look(image: Path, question: str) -> str:
            helper = Agent(
                model=setup,
                instructions=VISION_INSTRUCTIONS,
                max_steps=3,
                workspace=parts.config.workspace,
                name="vision",
            )
            try:
                answer = await helper.run(question, images=[image])
            finally:
                helper.close()
            if not answer.ok:
                raise ValueError(
                    f"The model {parts.config.model} could not read the image: {answer.reason}. "
                    "Use a model that accepts images."
                )
            return answer.text

        return look

    def _custom(self, existing: list[Any]) -> list[FunctionTool]:
        assert self.parts.custom_tools is not None
        names: set[str] = set()
        for item in existing:
            names.update(
                [item.name] if hasattr(item, "name") else [d.name for d in item.definitions]
            )
        kept = []
        for item in self.parts.custom_tools.tools:
            if item.name in names:
                self.parts.console.line(
                    self.parts.console.style(
                        f'  custom tool "{item.name}" is skipped: a built-in tool has that name',
                        "yellow",
                    )
                )
                continue
            kept.append(item)
        return kept

    def _subagents(self, setup: ModelSetup) -> list[FunctionTool]:
        parts = self.parts
        tools: list[FunctionTool] = []
        for name, spec in parts.config.agents.items():
            level = spec.get("tools", "read-only")
            helper = self._helper(name, spec, setup)
            self._built.append(helper)
            tools.append(
                helper.as_tool(
                    name=f"agent_{name}",
                    description=spec["description"],
                    read_only=level == "read-only",
                )
            )
        return tools
