"""Slash commands for working on a project: init, review, diff, rewind and the agent's extras."""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

from nailong_agent_sdk.foundations.errors import AgentSdkError

from .build import effort_parameters
from .custom_tools import reload_custom_tools, trust
from .directories import add_directory
from .live_commands import LIVE_COMMANDS, LIVE_HELP
from .maintenance_commands import MAINTENANCE_COMMANDS, MAINTENANCE_HELP
from .mcp_config import PRESETS, add_server, remove_server, spec_from_words
from .memory import INIT_PROMPT
from .memory_commands import MEMORY_COMMANDS, MEMORY_HELP
from .model_commands import MODEL_COMMANDS, MODEL_HELP
from .plan_command import plan_command
from .session_tools import export_markdown, search_sessions
from .shell import run_shell
from .skills import create_skill, install_skill, load_skills
from .state import ReplState
from .token_commands import TOKEN_COMMANDS, TOKEN_HELP
from .turns import ask_read_only, switch_agent

MAX_DIFF_CHARS = 30_000
SUGGEST_PROMPT = (
    "Based on this session, suggest three short prompts the user could send next. Reply with "
    "only a numbered list of the three prompts.\n\nSession:\n"
)
REVIEW_PROMPT = (
    "Review these uncommitted changes. Point out bugs, missing tests, risky behaviour and unclear "
    "code, most serious first, naming the file and line. If they look right, say so. Do not "
    "change anything.\n\n"
)


def _git(state: ReplState, arguments: str) -> str:
    record = state.runner.run(run_shell(f"git {arguments}", state.project, timeout_seconds=30))
    return record.output if record.successful else f"git failed: {record.output.strip()[-300:]}"


def _diff(state: ReplState, arguments: list[str]) -> bool:
    stat = _git(state, "diff --stat HEAD")
    body = _git(state, "diff HEAD")
    if not body.strip():
        state.console.line("  No uncommitted changes.")
        return True
    state.console.line(stat.rstrip())
    shown = body if len(body) <= MAX_DIFF_CHARS else body[:MAX_DIFF_CHARS] + "\n...(truncated)"
    state.console.line(shown.rstrip())
    return True


def _review(state: ReplState, arguments: list[str]) -> bool:
    body = _git(state, "diff HEAD")
    if not body.strip() or body.startswith("git failed"):
        state.console.line(body.strip() or "  No uncommitted changes to review.")
        return True
    clipped = body if len(body) <= MAX_DIFF_CHARS else body[:MAX_DIFF_CHARS] + "\n...(truncated)"
    answer = ask_read_only(state, REVIEW_PROMPT + clipped)
    state.console.line(str(answer))
    return True


def _init(state: ReplState, arguments: list[str]) -> bool:
    answer = ask_read_only(state, INIT_PROMPT)
    if not answer.ok:
        state.console.line(str(answer))
        return True
    state.console.line(answer.text)
    target = state.project / "AGENTS.md"
    if not state.console.interactive:
        state.console.line("  Not saved: there is no terminal to confirm.")
        return True
    note = " (this replaces the existing file)" if target.exists() else ""
    if state.console.ask(f"Save as AGENTS.md{note}? [y/N] ").lower() in {"y", "yes"}:
        target.write_text(answer.text, encoding="utf-8")
        state.console.line("  Saved AGENTS.md.")
    return True


def _rewind(state: ReplState, arguments: list[str]) -> bool:
    store = state.parts.checkpoints
    turns = store.turns()
    if not arguments:
        if not turns:
            state.console.line("  Nothing to rewind: the agent has not changed anything yet.")
        for number, files in turns:
            state.console.line(f"  turn {number}: {', '.join(files)}")
        state.console.line("  /rewind N restores everything the agent changed from turn N on.")
        return True
    target = int(arguments[0])
    plan = store.plan(target)
    state.console.line(f"  would restore: {', '.join(plan['restore']) or 'nothing'}")
    state.console.line(f"  would delete (created since): {', '.join(plan['delete']) or 'nothing'}")
    if state.console.interactive:
        answer = state.console.ask(f"Rewind to before turn {target}? [y/N] ")
        if answer.lower() not in {"y", "yes"}:
            return True
    restored = store.rewind(target)
    state.console.line(f"  rewound {len(restored)} file(s).")
    return True


def _redo(state: ReplState, arguments: list[str]) -> bool:
    restored = state.parts.checkpoints.redo()
    state.console.line(f"  put back {len(restored)} file(s) as they were before the rewind.")
    return True


def _skills(state: ReplState, arguments: list[str]) -> bool:
    console = state.console
    if arguments[:1] == ["new"]:
        return _skill_new(state, arguments[1:])
    if arguments[:1] == ["install"]:
        return _skill_install(state, arguments[1:])
    for item in state.parts.skills:
        flags = []
        if item.manual_only:
            flags.append("manual only")
        if item.allowed_tools:
            flags.append("tools: " + " ".join(item.allowed_tools))
        if item.files:
            flags.append(f"{len(item.files)} file(s)")
        extra = f"  [{'; '.join(flags)}]" if flags else ""
        hint = f" {item.argument_hint}" if item.argument_hint else ""
        console.line(f"  /{item.name}{hint}  {item.description[:70]}{extra}")
        console.line(console.style(f"      {item.source}: {item.path}", "dim"))
    if not state.parts.skills:
        console.line("  No skills. /skills new NAME makes one; see the guide, section 12.")
    return True


def _skill_new(state: ReplState, arguments: list[str]) -> bool:
    if len(arguments) != 1:
        state.console.line("Usage: /skills new <name>")
        return True
    path = create_skill(state.project, arguments[0])
    reload_skills(state)
    state.console.line(f"  created {path}; edit its description and steps")
    return True


def _skill_install(state: ReplState, arguments: list[str]) -> bool:
    if len(arguments) != 1:
        state.console.line("Usage: /skills install <folder with a SKILL.md>")
        return True
    path = install_skill(state.project, Path(arguments[0]).expanduser())
    reload_skills(state)
    state.console.line(f"  installed into {path}")
    return True


def reload_skills(state: ReplState) -> None:
    plugin = {item.name: item for item in state.parts.skills if item.source == "plugin"}
    state.parts.skills[:] = load_skills(state.project, state.home, plugin)
    state.mode_dirty = True


def _agents(state: ReplState, arguments: list[str]) -> bool:
    console = state.console
    for name, spec in state.config.agents.items():
        model = f", model {spec['model']}" if spec.get("model") else ""
        console.line(
            f"  agent_{name} ({spec.get('tools', 'read-only')}{model}): {spec['description']}"
        )
        if spec.get("source"):
            console.line(console.style(f"      {spec['source']}", "dim"))
    if not state.config.agents:
        console.line("  No subagents. Put Markdown files in .nailong/agents/ (see the guide).")
    runs = state.parts.bg_agents.listing() if state.parts.bg_agents is not None else []
    for line in runs:
        console.line(f"  {line}")
    return True


def _mcp(state: ReplState, arguments: list[str]) -> bool:
    console = state.console
    action = arguments[0] if arguments else "list"
    if action == "add" and len(arguments) >= 3:
        spec = PRESETS[arguments[2]] if arguments[2] in PRESETS and len(arguments) == 3 else None
        add_server(state.home, arguments[1], spec or spec_from_words(arguments[2:]))
        console.line(f"  added {arguments[1]}; it connects the next time nailong starts")
        return True
    if action == "preset" and len(arguments) == 2 and arguments[1] in PRESETS:
        add_server(state.home, arguments[1], PRESETS[arguments[1]])
        console.line(
            f"  added {arguments[1]} (npx runs its package); it connects at the next start"
        )
        return True
    if action == "remove" and len(arguments) == 2:
        remove_server(state.home, arguments[1])
        console.line(f"  removed {arguments[1]}; it stays connected until nailong restarts")
        return True
    if action not in {"list", "tools"}:
        console.line(
            "Usage: /mcp [list | tools | add NAME COMMAND... | add NAME URL | preset browser | "
            "remove NAME]"
        )
        return True
    for name, spec in state.config.mcp_servers.items():
        where = spec.get("url") or " ".join([spec["command"], *spec.get("args", [])])
        limits = []
        if spec.get("allow_tools"):
            limits.append("only " + ", ".join(spec["allow_tools"]))
        if spec.get("deny_tools"):
            limits.append("never " + ", ".join(spec["deny_tools"]))
        extra = f"  [{'; '.join(limits)}]" if limits else ""
        console.line(f"  {name}: {where}{extra}")
    if not state.config.mcp_servers:
        console.line("  No MCP servers. /mcp add NAME COMMAND, or [mcp.NAME] in your settings.")
    for group in state.parts.mcp_groups:
        for definition in group.definitions:
            console.line(f"    {definition.name}")
    return True


def _todos(state: ReplState, arguments: list[str]) -> bool:
    if not state.parts.todos.items:
        state.console.line("  No task list yet.")
    state.parts.todos.render(state.console)
    return True


def _forge(state: ReplState, arguments: list[str]) -> bool:
    forge = state.parts.forge
    if forge is None:
        state.console.line("The forge is off. Start with --forge to let the agent propose tools.")
        return True
    action = arguments[0] if arguments else "list"
    try:
        if action == "list":
            state.console.line(f"  {forge.listing()}")
        elif action == "approve" and len(arguments) == 2:
            state.console.line(f"  {state.runner.run(forge.approve(arguments[1]))}")
        elif action == "reject" and len(arguments) >= 2:
            state.console.line(f"  {forge.reject(arguments[1], ' '.join(arguments[2:]))}")
        elif action == "rollback" and len(arguments) == 3:
            forge.rollback(arguments[1], int(arguments[2]))
            state.console.line(f"  {arguments[1]} is now version {arguments[2]}")
        else:
            state.console.line(
                "Usage: /forge [list | approve <id> | reject <id> [reason] | "
                "rollback <name> <version>]"
            )
    except AgentSdkError as error:
        state.console.error(str(error))
    return True


def _add_dir(state: ReplState, arguments: list[str]) -> bool:
    if not arguments:
        for name, path in state.parts.extra_dirs.items():
            state.console.line(f"  {name}: {path}")
        state.console.line("  /add-dir PATH [name] gives the agent another directory.")
        return True
    path = Path(arguments[0]).expanduser().resolve()
    name = arguments[1] if len(arguments) > 1 else path.name
    add_directory(state, name, path)
    state.console.line(f"  added {name}: {path}")
    return True


def _jobs(state: ReplState, arguments: list[str]) -> bool:
    background = state.parts.background
    shells = state.parts.shells
    lines = [
        *(background.listing() if background is not None else []),
        *(shells.listing() if shells is not None else []),
    ]
    for line in lines:
        state.console.line(f"  {line}")
    if not lines:
        state.console.line("  No background commands or shells.")
    return True


def _kill(state: ReplState, arguments: list[str]) -> bool:
    background = state.parts.background
    shells = state.parts.shells
    if len(arguments) != 1 or (background is None and shells is None):
        state.console.line("Usage: /kill <job id or shell:name>")
        return True
    if shells is not None and shells.is_shell(arguments[0]) and arguments[0].startswith("shell:"):
        state.console.line(f"  {state.runner.run(shells.stop_named(arguments[0]))}")
        return True
    if background is None:
        state.console.line("Usage: /kill <job id or shell:name>")
        return True
    state.console.line(f"  {state.runner.run(background.stop(arguments[0]))}")
    return True


def _trust_tools(state: ReplState, arguments: list[str]) -> bool:
    console = state.console
    found = state.parts.custom_tools
    waiting = list(found.untrusted) if found is not None else []
    if not waiting:
        console.line("  No project tool files are waiting for your approval.")
        return True
    if not console.interactive:
        console.line("  /trust-tools asks you to read each file, so it needs a terminal.")
        return True
    trusted_any = False
    for path in waiting:
        console.line(console.style(f"  {path}", "bold"))
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines()[:60], 1):
            console.line(console.style(f"  {number:>3}  {line}", "dim"))
        if console.ask(
            "  This runs with your authority when the agent calls it. Trust it? [y/N] "
        ).lower() in {"y", "yes"}:
            trust(state.home, path)
            trusted_any = True
    if trusted_any:
        reload_custom_tools(state)
        for notice in state.parts.custom_tools.notices:
            console.line(console.style(f"  {notice}", "yellow"))
    return True


def _notes(state: ReplState, arguments: list[str]) -> bool:
    store = state.parts.notes
    if store is None:
        state.console.line("  Notes are not available.")
        return True
    result = store.read(arguments[0] if arguments else None)
    if "text" in result:
        state.console.line(result["text"].rstrip())
    elif result["notes"]:
        for title in result["notes"]:
            state.console.line(f"  {title}")
    else:
        state.console.line("  The agent has not written any notes yet.")
    return True


def _export(state: ReplState, arguments: list[str]) -> bool:
    session_id = state.session.id
    target = (
        Path(arguments[0])
        if arguments
        else state.project / ".nailong" / "exports" / f"{session_id}.md"
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(export_markdown(state.agent, session_id), encoding="utf-8")
    state.console.line(f"  wrote {target}")
    return True


def _search(state: ReplState, arguments: list[str]) -> bool:
    if not arguments:
        state.console.line("Usage: /search <text>")
        return True
    hits = search_sessions(state.agent, " ".join(arguments))
    for hit in hits:
        state.console.line(f"  {hit}")
    if not hits:
        state.console.line("  No matches.")
    return True


def _suggest(state: ReplState, arguments: list[str]) -> bool:
    digest = json.dumps(state.session.history(), default=str)[:6_000]
    answer = ask_read_only(state, SUGGEST_PROMPT + digest, small=True)
    state.console.line(str(answer))
    return True


def _variant(state: ReplState, arguments: list[str]) -> bool:
    console = state.console
    variants = state.config.variants
    if not arguments:
        listed = (
            ", ".join(sorted(variants)) or "none defined (add [variants.NAME] to your settings)"
        )
        console.line(f"  now: {state.variant or 'default'}; variants: {listed}")
        return True
    name = arguments[0]
    if name != "off" and name not in variants:
        raise ValueError(f'There is no variant "{name}"; variants: {sorted(variants)}.')
    parameters = dict(state.config.parameters)
    if name != "off":
        settings = dict(variants[name])
        effort = settings.pop("effort", None)
        if effort:
            parameters.update(effort_parameters(state.config.provider, str(effort)))
        parameters.update(settings)
    configured = dataclasses.replace(state.config, parameters=parameters)
    switch_agent(state, setup=state.model_factory(configured, state.environ))
    state.variant = None if name == "off" else name
    console.line(f"  variant is now {name}")
    return True


def _plugins(state: ReplState, arguments: list[str]) -> bool:
    for name in state.plugin_names:
        state.console.line(f"  {name}")
    if not state.plugin_names:
        state.console.line(
            "  No plugins. Put one in ~/.config/nailong/plugins/ or use --plugin-dir."
        )
    return True


def _reasoning(state: ReplState, arguments: list[str]) -> bool:
    renderer = state.parts.renderer
    if not hasattr(renderer, "show_reasoning"):
        state.console.line("  This output format has no reasoning display.")
        return True
    if arguments[:1] in (["on"], ["off"]):
        renderer.show_reasoning = arguments[0] == "on"
    elif arguments:
        state.console.error("Usage: /reasoning [on|off]")
        return True
    state.console.line(f"  reasoning display is {'on' if renderer.show_reasoning else 'off'}")
    return True


def _expand(state: ReplState, arguments: list[str]) -> bool:
    try:
        number = int(arguments[0]) if arguments else None
        text = state.parts.renderer.expand(number)
    except ValueError as error:
        state.console.error(str(error))
        return True
    state.console.line(text if text is not None else "  No tool output yet.")
    return True


WORKFLOW_HELP = {
    "plan": "/plan REQUEST: split a request into tasks; run as a team or in one agent",
    "reasoning": "/reasoning [on|off]: show or hide the model's reasoning as it streams",
    "expand": "/expand [N]: show the full output of a tool call (the last one by default)",
    "init": "draft an AGENTS.md for this project (asks before saving)",
    "review": "have the agent review your uncommitted changes (read-only)",
    "diff": "show your uncommitted changes",
    "rewind": "/rewind [N]: undo the agent's file changes from turn N on",
    "redo": "put back what the last /rewind undid (until the next turn)",
    "skills": "skills in use",
    "agents": "subagents the agent can call",
    "mcp": "tools from MCP servers",
    "todos": "the agent's task list",
    "forge": "list, approve, reject or roll back model-written tools",
    "add-dir": "/add-dir PATH [name]: give the agent another directory",
    "jobs": "background commands and persistent shells",
    "kill": "/kill ID: stop a background command or a shell (shell:name)",
    "variant": "/variant [name|off]: switch to a saved set of model parameters",
    "notes": "/notes [title]: the agent's task notes",
    "trust-tools": "read and approve project tool files (Python the agent may call)",
    "export": "/export [file]: this session as markdown",
    "search": "/search TEXT: find text in saved sessions",
    "suggest": "ask for three useful next prompts",
    "plugins": "installed plugins",
    **MAINTENANCE_HELP,
    **MEMORY_HELP,
    **TOKEN_HELP,
    **MODEL_HELP,
    **LIVE_HELP,
}
WORKFLOW_COMMANDS = {
    "plan": plan_command,
    "reasoning": _reasoning,
    "expand": _expand,
    "init": _init,
    "review": _review,
    "diff": _diff,
    "rewind": _rewind,
    "redo": _redo,
    "skills": _skills,
    "agents": _agents,
    "mcp": _mcp,
    "todos": _todos,
    "forge": _forge,
    "add-dir": _add_dir,
    "jobs": _jobs,
    "kill": _kill,
    "notes": _notes,
    "variant": _variant,
    "trust-tools": _trust_tools,
    "export": _export,
    "search": _search,
    "suggest": _suggest,
    "plugins": _plugins,
    **MAINTENANCE_COMMANDS,
    **MEMORY_COMMANDS,
    **TOKEN_COMMANDS,
    **MODEL_COMMANDS,
    **LIVE_COMMANDS,
}
