"""Slash commands about the session, the tools and the settings."""

from __future__ import annotations

import dataclasses
from pathlib import Path

from nailong_agent_sdk.foundations.errors import AgentSdkError

from . import diagnostics
from .approvals import NOT_SANDBOXED
from .commandline import is_read_only_command
from .compaction import compact_session
from .cost import BudgetReached
from .custom_tools import create_tool_file, reload_custom_tools
from .modes import MODE_HELP, parse_rule
from .shell import run_shell
from .skills import find_skill, invocation_prompt
from .state import ReplState
from .turns import PromptRefused, run_turn, switch_agent
from .user_commands import fill_injections, injected_commands
from .workflow_commands import WORKFLOW_COMMANDS, WORKFLOW_HELP


def handle(state: ReplState, line: str) -> bool:
    name, *arguments = line[1:].split()
    handler = COMMANDS.get(name)
    try:
        if handler is None and name in state.user_commands:
            return run_user_command(state, state.user_commands[name], " ".join(arguments))
        if handler is None:
            skill = find_skill(state.parts.skills, name)
            if skill is not None:
                return run_skill(state, skill, " ".join(arguments))
            state.console.line(f"Unknown command /{name}. Type /help for the list.")
            return True
        return handler(state, arguments)
    except (AgentSdkError, ValueError, OSError, BudgetReached, PromptRefused) as error:
        state.console.error(str(error))
        return True


def _injected_output(state: ReplState, command: str) -> str:
    console = state.console
    if not is_read_only_command(command):
        if not console.interactive:
            raise ValueError(
                f"The command file runs {command!r} to build its prompt. That is not a "
                "read-only command, and approving it needs a terminal."
            )
        answer = console.ask(
            f"  The command file wants to run `{command}` to build its prompt. Run it? [y/N] "
        )
        if answer.lower() not in {"y", "yes"}:
            raise ValueError(f"You did not approve running {command!r}, so the command stopped.")
    record = state.runner.run(run_shell(command, state.project))
    suffix = "" if record.successful else f"\n(exit {record.return_code})"
    return record.output + suffix


def run_user_command(state: ReplState, command, arguments: str) -> bool:
    options = command.options()
    text = command.render(arguments)
    found = injected_commands(text)
    if found:
        text = fill_injections(text, {item: _injected_output(state, item) for item in found})
    if options.agent:
        if options.agent not in state.config.agents:
            raise ValueError(
                f'The command file {command.path} names the agent "{options.agent}", but the '
                f"subagents are {sorted(state.config.agents)}."
            )
        text = f"Hand this to the agent_{options.agent} tool and report what it returns:\n\n{text}"
    if not options.model:
        return _guarded_turn(state, text)
    if state.parts.model_for is None:
        raise ValueError("This session cannot switch models.")
    original = state.setup
    switch_agent(state, setup=state.parts.model_for(options.model))
    try:
        return _guarded_turn(state, text)
    finally:
        switch_agent(state, setup=original)


def run_skill(state: ReplState, skill, arguments: str) -> bool:
    state.parts.skill_scope.activate(skill)
    try:
        return _guarded_turn(state, invocation_prompt(skill, arguments))
    finally:
        state.parts.skill_scope.clear()


def _guarded_turn(state: ReplState, text: str) -> bool:
    try:
        run_turn(state, text)
    except (BudgetReached, PromptRefused) as error:
        state.console.error(str(error))
    return True


def _help(state: ReplState, arguments: list[str]) -> bool:
    for name, text in HELP.items():
        state.console.line(f"  /{name:<12} {text}")
    for name, command in sorted(state.user_commands.items()):
        state.console.line(f"  /{name:<12} your command ({command.path.name})")
    state.console.line("  @path        attach a file or directory to your message")
    state.console.line("  !command     run a shell command yourself")
    return True


def _tools(state: ReplState, arguments: list[str]) -> bool:
    if arguments[:1] == ["new"]:
        names = [word for word in arguments[1:] if word != "user"]
        if len(names) != 1:
            state.console.line("Usage: /tools new <name> [user]")
            return True
        path = create_tool_file(state.project, state.home, names[0], user="user" in arguments)
        trusted = "user" in arguments
        state.console.line(
            f"  created {path}; edit it" + ("" if trusted else ", then /trust-tools")
        )
        if trusted:
            reload_custom_tools(state)
        return True
    custom = state.parts.custom_tools.sources if state.parts.custom_tools is not None else {}
    for definition in state.agent.definition.tools:
        where = f"  [{custom[definition.name]}]" if definition.name in custom else ""
        state.console.line(f"  {definition.name}: {definition.description[:80]}{where}")
    return True


def _sessions(state: ReplState, arguments: list[str]) -> bool:
    if arguments[:1] == ["import"]:
        if len(arguments) not in {2, 3}:
            state.console.line("Usage: /sessions import <transcript.jsonl> [new session name]")
            return True
        from nailong_agent_sdk.starter.session_store import SessionStore

        from .session_import import import_transcript

        session_id, source, count, skipped = import_transcript(
            SessionStore(state.agent.services.run_root),
            state.agent.name,
            Path(arguments[1]).expanduser(),
            arguments[2] if len(arguments) == 3 else None,
        )
        tail = f" ({skipped} older turn(s) left out)" if skipped else ""
        state.console.line(
            f"  imported {count} turn(s) from a {source} transcript as {session_id}{tail}; "
            f"/resume {session_id} to continue it"
        )
        return True
    if arguments[:1] == ["delete"]:
        if len(arguments) != 2:
            state.console.line("Usage: /sessions delete <session id>")
            return True
        target = arguments[1]
        if target == state.session.id:
            raise ValueError("That is the session you are in; /new first, then delete it.")
        if target not in state.agent.sessions():
            raise ValueError(
                f'Session "{target}" does not exist; the sessions are {state.agent.sessions()}.'
            )
        if state.console.interactive:
            reply = state.console.ask(f"Delete session {target} for good? [y/N] ")
            if reply.lower() not in {"y", "yes"}:
                return True
        state.agent.delete_session(target)
        state.console.line(f"  deleted {target}")
        return True
    for session_id in state.agent.sessions():
        mark = "*" if session_id == state.session.id else " "
        state.console.line(f" {mark} {session_id}  {_last_prompt(state, session_id)}")
    return True


def _resume(state: ReplState, arguments: list[str]) -> bool:
    if len(arguments) == 1:
        chosen = arguments[0]
    elif state.console.interactive and state.agent.sessions():
        ids = state.agent.sessions()
        for number, session_id in enumerate(ids, 1):
            state.console.line(f"  {number}. {session_id}  {_last_prompt(state, session_id)}")
        answer = state.console.ask("Resume which? (number) > ")
        if not answer.isdigit() or not 1 <= int(answer) <= len(ids):
            state.console.line("No session chosen.")
            return True
        chosen = ids[int(answer) - 1]
    else:
        state.console.line("Usage: /resume <session id>")
        return True
    state.session = state.agent.resume(chosen)
    state.console.line(_session_line(state, "resumed"))
    return True


def _fork(state: ReplState, arguments: list[str]) -> bool:
    at_turn = int(arguments[0]) if arguments else None
    state.session = state.session.fork(at_turn=at_turn)
    state.console.line(_session_line(state, "forked"))
    return True


def _new(state: ReplState, arguments: list[str]) -> bool:
    state.session = state.agent.session()
    state.parts.todos.items.clear()
    state.console.line(_session_line(state, "started"))
    return True


def _cost(state: ReplState, arguments: list[str]) -> bool:
    for line in state.parts.spending.lines(state.last_answer):
        state.console.line(line)
    return True


def _sandbox(state: ReplState, arguments: list[str]) -> bool:
    label = state.sandbox_label
    state.console.line(f"  commands run in: {label}")
    if label == NOT_SANDBOXED:
        state.console.line("  A command can read any file you can and use the network.")
    return True


def _verify(state: ReplState, arguments: list[str]) -> bool:
    state.console.line(
        f"  session {state.session.id}: {state.session.verify()} turn(s), chain intact"
    )
    if state.parts.forge is not None:
        state.console.line(f"  forge: {state.parts.forge.verify()}")
    return True


def _mode(state: ReplState, arguments: list[str]) -> bool:
    if not arguments:
        for name, text in MODE_HELP.items():
            mark = "*" if name == state.parts.mode.value else " "
            state.console.line(f" {mark} {name:<13} {text}")
        return True
    switch_agent(state, mode=arguments[0])
    state.console.line(f"  mode is now {arguments[0]}")
    return True


def _model(state: ReplState, arguments: list[str]) -> bool:
    if not arguments:
        state.console.line(f"  {state.config.model} ({state.config.provider})")
        return True
    state.config = dataclasses.replace(state.config, model=arguments[0])
    state.parts.config = state.config
    switch_agent(state, setup=state.model_factory(state.config, state.environ))
    state.console.line(f"  model is now {arguments[0]}")
    return True


def _permissions(state: ReplState, arguments: list[str]) -> bool:
    if arguments[:1] == ["remove"] and len(arguments) == 2:
        tool, pattern = state.store.remove(state.project, int(arguments[1]))
        state.console.line(f"  removed {tool}: {pattern}")
        return True
    if arguments[:1] == ["allow"] and len(arguments) == 2:
        tool, pattern = parse_rule(arguments[1])
        state.store.add(state.project, tool, pattern)
        state.console.line(f"  saved: allow {tool} matching {pattern}")
        return True
    state.console.line(f"  mode: {state.parts.mode.value}")
    for number, (tool, pattern) in enumerate(state.store.rules(state.project), 1):
        state.console.line(f"  {number}. allow {tool}: {pattern}")
    if state.parts.approver.session_tools:
        state.console.line(f"  this session: {sorted(state.parts.approver.session_tools)}")
    state.console.line("  /permissions allow tool:pattern | /permissions remove N")
    return True


def _status(state: ReplState, arguments: list[str]) -> bool:
    for line in diagnostics.status_lines(state):
        state.console.line("  " + line)
    return True


def _doctor(state: ReplState, arguments: list[str]) -> bool:
    for line in diagnostics.doctor_lines(state):
        state.console.line(line)
    return True


def _context(state: ReplState, arguments: list[str]) -> bool:
    instructions = state.builder.instructions_for(state.parts.mode.value)
    digest = state.session.history()
    state.console.line(f"  instructions + memory: {len(instructions)} characters")
    state.console.line(f"  tools: {len(state.agent.definition.tools)}")
    state.console.line(f"  skills: {sum(len(s.body) for s in state.parts.skills)} characters")
    state.console.line(
        f"  earlier turns sent: {len(digest['turns'])}, left out: {digest['omitted_turns']}"
    )
    return True


def _compact(state: ReplState, arguments: list[str]) -> bool:
    covered, size = compact_session(state, " ".join(arguments))
    state.console.line(f"  Summarised turns 1 to {covered} in {size} characters.")
    return True


def _exit(state: ReplState, arguments: list[str]) -> bool:
    return False


def _last_prompt(state: ReplState, session_id: str) -> str:
    try:
        turns = state.agent.resume(session_id).turns
    except (AgentSdkError, ValueError):
        return "(unreadable)"
    return turns[-1]["prompt"][:50] if turns else "(empty)"


def _session_line(state: ReplState, verb: str) -> str:
    return f"  {verb} session {state.session.id} ({len(state.session.turns)} earlier turn(s))"


HELP = {
    "help": "this list",
    "tools": "the tools the agent has",
    "mode": "show or set the permission mode",
    "permissions": "saved approval rules (allow tool:pattern, remove N)",
    "model": "show or change the model",
    "status": "what is configured",
    "doctor": "check the setup",
    "context": "what is sent with each turn",
    "compact": "/compact [focus]: have the model summarise the session so far",
    "sessions": "saved sessions",
    "resume": "continue a saved session (pick from a list)",
    "fork": "/fork [turn]: branch this session",
    "new": "start an empty session (also /clear)",
    "cost": "cost of the last turn",
    "sandbox": "where commands run and what they can reach",
    "verify": "check the session and forge hash chains",
    **WORKFLOW_HELP,
    "exit": "leave",
}
COMMANDS = {
    "help": _help,
    "tools": _tools,
    "mode": _mode,
    "permissions": _permissions,
    "model": _model,
    "status": _status,
    "doctor": _doctor,
    "context": _context,
    "compact": _compact,
    "sessions": _sessions,
    "resume": _resume,
    "fork": _fork,
    "new": _new,
    "clear": _new,
    "cost": _cost,
    "sandbox": _sandbox,
    "verify": _verify,
    "exit": _exit,
    "quit": _exit,
    **WORKFLOW_COMMANDS,
}
