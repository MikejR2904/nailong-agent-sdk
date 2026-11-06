"""Command-line entry point.

nailong                           # interactive session in the current project
nailong "explain this repo"       # interactive, starting with a message
nailong -c                        # resume the latest session
nailong -p "fix the failing test" --output json
"""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import io
import json
import os
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any

from nailong_agent_sdk.foundations.errors import AgentSdkError
from nailong_agent_sdk.starter import Answer, Forge, McpTools, ModelSetup, Session, connect_mcp

from . import __version__, acp, credentials, mcp_serve, schedule, server
from .agent_files import load_agent_files
from .approvals import NOT_SANDBOXED, Approver
from .background import make_background
from .build import (
    EFFORT_LEVELS,
    MissingKeyError,
    build_model,
    choose_sandbox,
    effort_parameters,
    project_root,
)
from .checkpoints import CheckpointStore
from .config import (
    MODES,
    PROJECT_DIRECTORY,
    CliConfig,
    ConfigError,
    MissingSettingsError,
    load_config,
)
from .console import Console
from .cost import BudgetReached, Spending
from .custom_tools import load_custom_tools
from .debug import DebugLog
from .factory import AgentBuilder, Parts
from .forge_review import review_proposal
from .history import enable_history
from .hooks import LifecycleHooks, build_hooks
from .keys import KeySource, system_keys
from .knowledge import KnowledgeBase
from .lsp import LspManager, specs_from_table
from .mcp_config import load_managed, servers_from_table, table_from_json_file
from .memory import load_memory
from .modes import ModeRef, parse_rule
from .notes import NotesStore
from .permissions import PermissionStore, permissions_path
from .plugins import load_plugins, plugin_directories
from .post_edit import PostEdit, checkers_from_table
from .render import Renderer
from .repl import after_plan, command_descriptions, cycle_mode, mode_hint, repl, toolbar_text
from .rules import DEFAULT_RULES, PermissionRules, Rule, rule_from_table
from .setup import SetupCancelled, run_setup
from .shell import run_shell
from .shell_sessions import make_shell_sessions
from .skills import load_skills
from .state import ReplState
from .stream_json import JsonStream
from .telemetry_export import TelemetryExport
from .todo import TodoList
from .tui import PlainReader, choose, load_palette, make_reader
from .turns import PromptRefused, run_turn, switch_agent
from .user_commands import load_commands
from .utilities import SHELLS, completion_script, upgrade_hint
from .wiki import Wiki
from .workflow_commands import MAX_DIFF_CHARS, REVIEW_PROMPT
from .worktree import Worktree, create_worktree, finish_worktree

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_BLOCKED = 2
EXIT_USAGE = 64
EXIT_INTERRUPTED = 130
_STATUS_EXIT = {"completed": EXIT_OK, "failed": EXIT_FAILED, "blocked": EXIT_BLOCKED}

ModelFactory = Callable[..., ModelSetup]


def parser() -> argparse.ArgumentParser:
    cli = argparse.ArgumentParser(prog="nailong", description=__doc__.strip().splitlines()[0])
    cli.add_argument("initial", nargs="?", help="A first message for the interactive session.")
    cli.add_argument("-p", "--print", dest="prompt", metavar="PROMPT", help="Run once and exit.")
    cli.add_argument("--output", choices=["text", "json", "stream-json"], default="text")
    cli.add_argument("--project", type=Path, default=None, help="Project directory.")
    cli.add_argument("--add-dir", action="append", default=[], type=Path, metavar="DIR")
    cli.add_argument("--worktree", nargs="?", const="", default=None, metavar="NAME")
    cli.add_argument("--provider", choices=["anthropic", "openai-compatible"])
    cli.add_argument("--model")
    cli.add_argument("--effort", choices=list(EFFORT_LEVELS))
    cli.add_argument("--param", action="append", default=[], metavar="KEY=JSON")
    cli.add_argument("--permission-mode", choices=list(MODES))
    cli.add_argument("--allow", action="append", default=[], metavar="TOOL[:PATTERN]")
    cli.add_argument("--disallow", action="append", default=[], metavar="TOOL[:PATTERN]")
    cli.add_argument("--max-turns", type=int, metavar="N", help="Most model steps per turn.")
    cli.add_argument("--max-budget-usd", type=float, metavar="USD")
    cli.add_argument("--system-prompt-append", metavar="TEXT")
    cli.add_argument("--json-schema", type=Path, metavar="FILE", help="Shape the answer as JSON.")
    cli.add_argument("--image", action="append", default=[], type=Path, metavar="FILE")
    cli.add_argument("--mcp-config", action="append", default=[], type=Path, metavar="FILE")
    cli.add_argument("--plugin-dir", action="append", default=[], type=Path, metavar="DIR")
    cli.add_argument("--web", action="store_true", help="Let the agent fetch public web pages.")
    cli.add_argument(
        "--setup", action="store_true", help="Choose a model service and save settings."
    )
    cli.add_argument(
        "--fallback-model",
        action="append",
        default=[],
        metavar="NAME",
        help="Try this model when the main one fails (repeatable, in order).",
    )
    cli.add_argument(
        "--otlp-endpoint",
        metavar="URL",
        help="Send recorded runs to this OpenTelemetry collector (OTLP/HTTP).",
    )
    cli.add_argument(
        "--no-reasoning",
        action="store_true",
        help="Do not show the model's reasoning while it streams.",
    )
    cli.add_argument(
        "--forget-key",
        metavar="VARIABLE",
        help="Delete the saved key for this variable name from the credential store.",
    )
    cli.add_argument(
        "--offline",
        action="store_true",
        help="Refuse any setting that would reach beyond your own network.",
    )
    cli.add_argument("--search", action="store_true", help="Let the agent search the web.")
    cli.add_argument("--autocompact", action="store_true")
    cli.add_argument("-c", "--continue", dest="continue_", action="store_true")
    cli.add_argument("-r", "--resume", metavar="ID", help="Resume a saved session.")
    cli.add_argument("--fork", metavar="ID", help="Branch a saved session.")
    cli.add_argument("--fork-session", action="store_true", help="With -c or -r: branch instead.")
    cli.add_argument("--at", type=int, metavar="TURN", help="With a fork: branch after this turn.")
    cli.add_argument("-n", "--name", metavar="NAME", help="Name for a new session.")
    cli.add_argument("--review", nargs="?", const="HEAD", default=None, metavar="BASE")
    cli.add_argument("--forge", action="store_true", help="Let the agent propose tools for review.")
    cli.add_argument("--allow-unsandboxed", action="store_true")
    cli.add_argument(
        "--acp",
        action="store_true",
        help="Speak the Agent Client Protocol on stdio, for editors that can run an agent.",
    )
    cli.add_argument(
        "--completion",
        choices=list(SHELLS),
        help="Print a shell completion script for bash, zsh, fish or powershell, then exit.",
    )
    cli.add_argument(
        "--upgrade", action="store_true", help="Say how to upgrade nailong where it is installed."
    )
    cli.add_argument(
        "--serve",
        action="store_true",
        help="Run the agent behind a small HTTP service (token in NAILONG_SERVER_TOKEN).",
    )
    cli.add_argument("--port", type=int, default=server.DEFAULT_PORT, help="Port for --serve.")
    cli.add_argument("--hostname", default=server.DEFAULT_HOST, help="Address for --serve.")
    cli.add_argument("--attach", metavar="URL", help="Talk to a nailong --serve at this address.")
    cli.add_argument(
        "--run-schedules",
        action="store_true",
        help="Run the scheduled prompts that are due (for cron or Task Scheduler), then exit.",
    )
    cli.add_argument(
        "--mcp-serve",
        action="store_true",
        help="Offer this project's file and command tools to another program over MCP (stdio).",
    )
    cli.add_argument("--verbose", action="store_true", help="Show full tool arguments and results.")
    cli.add_argument("--debug-file", type=Path, metavar="FILE")
    cli.add_argument("--no-color", action="store_true")
    cli.add_argument("--version", action="version", version=f"nailong {__version__}")
    return cli


def main(
    argv: Sequence[str] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    console: Console | None = None,
    model_factory: ModelFactory = build_model,
    home: Path | None = None,
    cwd: Path | None = None,
    keys: KeySource | None = None,
) -> int:
    args = parser().parse_args(argv)
    if args.completion:
        sys.stdout.write(completion_script(args.completion, parser()))
        return EXIT_OK
    if args.upgrade:
        sys.stdout.write("\n".join(upgrade_hint()) + "\n")
        return EXIT_OK
    if console is None:
        _tolerate_encoding()
    console = console or Console(
        sys.stdin, sys.stdout, sys.stderr, color=False if args.no_color else None
    )
    environ = dict(environ if environ is not None else os.environ)
    home = home or Path.home()
    with asyncio.Runner() as runner:
        worktree: Worktree | None = None
        try:
            project = project_root(args.project or cwd or Path.cwd())
            if args.worktree is not None:
                worktree = runner.run(create_worktree(project, args.worktree or None))
            if args.forget_key:
                forgotten = credentials.forget(args.forget_key)
                console.line(
                    f"Deleted the saved key for {args.forget_key}."
                    if forgotten
                    else f"No saved key for {args.forget_key} was found."
                )
                return EXIT_OK
            if args.setup:
                return _only_setup(console, home, environ)
            if args.acp:
                return acp.serve_acp(
                    lambda inner, quiet: main(
                        inner,
                        environ=environ,
                        console=quiet,
                        model_factory=model_factory,
                        home=home,
                    ),
                    server.strip_server_flags(
                        [
                            item
                            for item in (argv if argv is not None else sys.argv[1:])
                            if item != "--acp"
                        ]
                    ),
                    project=project,
                    mode=args.permission_mode or "plan",
                    stdin=sys.stdin,
                    stdout=sys.stdout,
                )
            if args.attach:
                return server.attach(
                    args.attach,
                    args.prompt,
                    environ=environ,
                    console=console,
                    mode=args.permission_mode or "plan",
                )
            if args.serve:
                return server.serve(
                    lambda inner, quiet: main(
                        inner,
                        environ=environ,
                        console=quiet,
                        model_factory=model_factory,
                        home=home,
                    ),
                    list(argv if argv is not None else sys.argv[1:]),
                    project=project,
                    hostname=args.hostname,
                    port=args.port,
                    environ=environ,
                    console=console,
                    version=__version__,
                )
            if args.run_schedules:
                return _run_schedules(console, environ, home, model_factory)
            if args.mcp_serve:
                return _serve_mcp(runner, args, console, home, project)
            built = _first_run(args, console, environ, home, project, worktree, model_factory)
        except (ConfigError, MissingKeyError, NotADirectoryError, ValueError, OSError) as error:
            console.error(str(error))
            return EXIT_USAGE
        config, setup, parts, user_commands, plugin_names = built
        builder = AgentBuilder(parts)
        try:
            parts.mcp_groups = _connect(runner, config)
        except (ConnectionError, ValueError, AgentSdkError) as error:
            console.error(str(error))
            return EXIT_USAGE
        try:
            agent = builder.build(setup)
            session = _session(args, agent)
        except (AgentSdkError, ValueError) as error:
            console.error(str(error))
            _shutdown(runner, builder, parts, worktree, console)
            return EXIT_USAGE
        parts.repeat_guard.attach(console)
        parts.renderer.announce(session.id)
        state = ReplState(
            builder,
            agent,
            session,
            setup,
            runner,
            config,
            _label(config, parts),
            home,
            environ,
            model_factory,
            PermissionStore(permissions_path(home)),
            user_commands,
            plugin_names,
            first_images=list(args.image),
        )
        try:
            state.reader = make_reader(
                console,
                project=project,
                history_file=home / ".local" / "state" / "nailong" / "history",
                editing_mode=config.editing_mode,
                theme=config.theme,
                keybinds=config.keybinds,
                palette=load_palette(config.theme, project, home),
                toolbar=lambda: toolbar_text(state),
                cycle_mode=lambda: cycle_mode(state),
                commands=lambda: command_descriptions(state),
                mode_hint=lambda: mode_hint(state),
                on_expand=lambda: _show_last_output(state),
            )
        except ValueError as error:
            console.error(str(error))
            _shutdown(runner, builder, parts, worktree, console)
            return EXIT_USAGE
        console.fancy = console.fancy or (
            not isinstance(state.reader, PlainReader) and console.interactive
        )
        if console.fancy:
            console.chooser = console.chooser or choose
            state.keys = keys if keys is not None else system_keys()
        save_history = (
            enable_history(home)
            if console.interactive and isinstance(state.reader, PlainReader)
            else (lambda: None)
        )
        try:
            _lifecycle(state, "session_start")
            if args.review is not None:
                return _review(state, args)
            if args.prompt is not None:
                return _once(state, args)
            if not console.interactive:
                console.error(not_a_terminal_notice(environ))
            _banner(state)
            _custom_notices(state)
            if args.initial:
                _first_message(state, args.initial)
            repl(state)
            return EXIT_OK
        finally:
            save_history()
            _lifecycle(state, "session_end")
            _shutdown(runner, builder, parts, worktree, console)


def _run_schedules(console, environ, home, model_factory) -> int:
    moment = datetime.now().astimezone()

    def run_one(item):
        out, err = io.StringIO(), io.StringIO()
        quiet = Console(io.StringIO(), out, err, color=False, interactive=False)
        code = main(
            ["-p", item.prompt, "--project", item.project, "--permission-mode", item.mode],
            environ=environ,
            console=quiet,
            model_factory=model_factory,
            home=home,
        )
        status = "ok" if code == 0 else f"failed (exit {code})"
        text = out.getvalue() + err.getvalue()
        path = schedule.record_run(item, status, text, moment)
        return status, f"{schedule.summary_of(text)} -> {path}"

    try:
        for line in schedule.run_due(home, run_one, moment):
            console.line(line)
    except ValueError as error:
        console.error(str(error))
        return EXIT_USAGE
    return EXIT_OK


def _serve_mcp(runner, args, console, home, project) -> int:
    overrides = {"permission_mode": args.permission_mode, "offline": True if args.offline else None}
    try:
        config = load_config(project, home=home, overrides=overrides)
    except MissingSettingsError:
        config = load_config(
            project, home=home, overrides={**overrides, "provider": "anthropic", "model": "unused"}
        )
    extra_dirs = _extra_dirs([*config.add_dirs, *map(str, args.add_dir)])
    sandbox_kwargs, label = choose_sandbox(config, extra_dirs)
    refused = mcp_serve.refusal(config.permission_mode, label, args.allow_unsandboxed)
    if refused:
        console.error(refused)
        return EXIT_USAGE
    table = mcp_serve.offered_tools(
        config.project, config.permission_mode, sandbox_kwargs, extra_dirs
    )
    mcp_serve.announce(mcp_serve.describe(config.permission_mode, table))
    runner.run(mcp_serve.serve_stdio(table, version=__version__, project=config.project))
    return EXIT_OK


def _tolerate_encoding() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(errors="replace")


def not_a_terminal_notice(environ: Mapping[str, str]) -> str:
    notice = (
        "Input or output is not a terminal, so nailong is using plain line input (no menus, "
        "spinner, Esc or colours)."
    )
    if environ.get("MSYSTEM"):
        notice += (
            " You are in Git Bash, which does not give programs a console: run "
            "`winpty nailong`, or use PowerShell, cmd or Windows Terminal."
        )
    return notice


def _only_setup(console: Console, home: Path, environ: dict[str, str]) -> int:
    if not console.interactive:
        console.error("nailong --setup asks questions, so it needs a terminal.")
        return EXIT_USAGE
    try:
        run_setup(console, home, environ)
    except (SetupCancelled, ValueError) as error:
        console.error(str(error))
        return EXIT_USAGE
    console.line("Run nailong to start.")
    return EXIT_OK


def _first_run(args, console, environ, home, project, worktree, model_factory):
    try:
        return _context(args, console, environ, home, project, worktree, model_factory)
    except MissingSettingsError as error:
        if not console.interactive or args.prompt is not None:
            raise ConfigError(
                f"{error} Run nailong --setup in a terminal to create them."
            ) from error
        console.line("No settings found, so nailong does not know which model to use yet.")
        if console.ask("Set it up now? [Y/n] ").lower() in {"n", "no"}:
            raise
        try:
            environ.update(run_setup(console, home, environ).session_keys)
        except SetupCancelled as cancelled:
            raise ConfigError(str(cancelled)) from cancelled
        return _context(args, console, environ, home, project, worktree, model_factory)


def _context(
    args: argparse.Namespace,
    console: Console,
    environ: Mapping[str, str],
    home: Path,
    project: Path,
    worktree: Worktree | None,
    model_factory: ModelFactory,
):
    parameters = _parameters(args)
    config = load_config(
        project,
        home=home,
        environ=environ,
        overrides={
            "provider": args.provider,
            "model": args.model,
            "permission_mode": args.permission_mode,
            "max_steps": args.max_turns,
            "max_budget_usd": args.max_budget_usd,
            "web": True if args.web else None,
            "web_search": True if args.search else None,
            "autocompact": True if args.autocompact else None,
            "show_reasoning": False if args.no_reasoning else None,
            "offline": True if args.offline else None,
            "fallback_models": list(args.fallback_model) or None,
            "otlp_endpoint": args.otlp_endpoint,
            "parameters": parameters or None,
        },
    )
    bundle = load_plugins(plugin_directories(home, args.plugin_dir))
    mcp = {**load_managed(home), **config.mcp_servers, **bundle.mcp}
    for path in args.mcp_config:
        mcp.update(table_from_json_file(path))
    extra_dirs = _extra_dirs([*config.add_dirs, *map(str, args.add_dir)])
    config = dataclasses.replace(
        config,
        mcp_servers=mcp,
        agents={**load_agent_files(config.project, home), **bundle.agents, **config.agents},
        hooks=(*config.hooks, *bundle.hooks),
        project=worktree.path if worktree else config.project,
    )
    setup = model_factory(config, environ)
    sandbox_kwargs, label = choose_sandbox(config, extra_dirs)
    mode = ModeRef(config.permission_mode)
    store = PermissionStore(permissions_path(home))
    permission_rules = PermissionRules(
        [
            *DEFAULT_RULES,
            *(
                rule_from_table(item, item["source"], index)
                for index, item in enumerate(config.rules, 1)
            ),
            *(
                Rule("allow", (tool,), pattern, "--allow")
                for tool, pattern in (parse_rule(text) for text in args.allow)
            ),
        ]
    )
    approver = Approver(
        console,
        store,
        config.project,
        label,
        mode=mode,
        allow_unsandboxed=args.allow_unsandboxed,
        allow_rules=[parse_rule(text) for text in args.allow],
        deny_rules=[parse_rule(text) for text in args.disallow],
        rules=permission_rules,
    )
    before, after = build_hooks(config.hooks, config.project, console)
    schema = json.loads(args.json_schema.read_text(encoding="utf-8")) if args.json_schema else None
    renderer = (
        JsonStream(console)
        if args.output == "stream-json"
        else Renderer(console, verbose=args.verbose, show_reasoning=config.show_reasoning)
    )
    parts = Parts(
        config=config,
        console=console,
        mode=mode,
        approver=approver,
        renderer=renderer,
        checkpoints=CheckpointStore(
            config.project, config.project / PROJECT_DIRECTORY / "checkpoints", extra_dirs
        ),
        todos=TodoList(),
        memory=load_memory(config.project, home, config.instruction_files),
        skills=load_skills(config.project, home, bundle.skills),
        sandbox_kwargs=sandbox_kwargs,
        spending=Spending(config.max_budget_usd),
        forge=_forge(args, console, config.project / PROJECT_DIRECTORY / "forge"),
        hooks_before=before,
        hooks_after=after,
        lifecycle=LifecycleHooks(config.hooks, config.project),
        background=make_background(sandbox_kwargs, config.project),
        custom_tools=load_custom_tools(config.project, home),
        permission_rules=permission_rules,
        knowledge=KnowledgeBase(
            config.project / ".nailong" / "knowledge",
            {"project": config.project},
            config.workspace,
        ),
        wiki=Wiki(config.project / ".nailong" / "wiki"),
        shells=make_shell_sessions(sandbox_kwargs, config.project, config.ssh_hosts),
        notes=NotesStore(config.workspace / "notes"),
        debug=DebugLog(args.debug_file) if args.debug_file else None,
        extra_dirs=extra_dirs,
        json_schema=schema,
        system_append=args.system_prompt_append,
        fallbacks=[
            model_factory(replace(config, model=name), environ) for name in config.fallback_models
        ],
        model_for=lambda name, parameters=None: model_factory(
            replace(config, model=name, parameters={**config.parameters, **(parameters or {})}),
            environ,
        ),
        exporter=TelemetryExport(
            console, config.workspace, config.otlp_endpoint, environ, include=config.otlp_include
        ),
    )
    approver.lifecycle = parts.lifecycle
    parts.post_edit = PostEdit(
        config.project,
        console,
        formatters=checkers_from_table(config.formatters, "formatters"),
        diagnostics=checkers_from_table(config.diagnostics, "diagnostics"),
        builtin_format=config.format_on_edit,
        builtin_diagnostics=config.diagnostics_on_edit,
        current_agent=lambda: parts.current_agent,
        lsp=LspManager(config.project, specs_from_table(config.lsp), builtin=config.lsp_on_edit),
    )
    commands = {**load_commands(config.project, home), **bundle.commands}
    return config, setup, parts, commands, bundle.names


def _parameters(args: argparse.Namespace) -> dict[str, Any]:
    parameters: dict[str, Any] = {}
    provider = args.provider or "anthropic"
    if args.effort:
        parameters.update(effort_parameters(provider, args.effort))
    for item in args.param:
        key, separator, raw = item.partition("=")
        if not separator or not key.strip():
            raise ValueError(
                f'--param "{item}" must look like KEY=JSON, for example temperature=0.2.'
            )
        try:
            parameters[key.strip()] = json.loads(raw)
        except json.JSONDecodeError:
            parameters[key.strip()] = raw
    return parameters


def _extra_dirs(entries: Sequence[str]) -> dict[str, Path]:
    found: dict[str, Path] = {}
    for entry in entries:
        path = Path(entry).expanduser().resolve()
        if not path.is_dir():
            raise ValueError(f"The added directory {path} does not exist.")
        name, number = path.name or "root", 2
        while name in found or name == "project":
            name, number = f"{path.name}-{number}", number + 1
        found[name] = path
    return found


def _label(config: CliConfig, parts: Parts) -> str:
    return choose_sandbox(config, parts.extra_dirs)[1]


def _connect(runner: asyncio.Runner, config: CliConfig) -> list[McpTools]:
    if not config.mcp_servers:
        return []
    return [runner.run(connect_mcp(servers_from_table(config.mcp_servers)))]


def _shutdown(
    runner: asyncio.Runner,
    builder: AgentBuilder,
    parts: Parts,
    worktree: Worktree | None,
    console: Console,
) -> None:
    builder.close()
    if parts.exporter is not None:
        parts.exporter.finish()
    if parts.background is not None:
        runner.run(parts.background.close())
    if parts.bg_agents is not None:
        parts.bg_agents.close()
    if parts.post_edit is not None:
        parts.post_edit.close()
    if parts.shells is not None:
        runner.run(parts.shells.close())
    for group in parts.mcp_groups:
        runner.run(group.aclose())
    if worktree is not None:
        console.line(runner.run(finish_worktree(worktree)))


def _lifecycle(state: ReplState, event: str) -> None:
    hooks = state.parts.lifecycle
    if hooks is not None and hooks.has(event):
        state.runner.run(hooks.run(event, session=state.session.id))


def _forge(args: argparse.Namespace, console: Console, directory: Path) -> Forge | None:
    if not args.forge:
        return None
    return Forge(directory, reviewer=lambda proposal: review_proposal(console, proposal))


def _session(args: argparse.Namespace, agent) -> Session:
    if args.fork:
        return agent.resume(args.fork).fork(at_turn=args.at)
    if args.resume:
        base = agent.resume(args.resume)
        return base.fork(at_turn=args.at) if args.fork_session else base
    if args.continue_:
        latest = agent.latest_session()
        if latest:
            base = agent.resume(latest)
            return base.fork(at_turn=args.at) if args.fork_session else base
    return agent.session(args.name)


def _first_message(state: ReplState, text: str) -> None:
    try:
        if run_turn(state, text) is not None:
            after_plan(state)
    except (BudgetReached, PromptRefused) as error:
        state.console.error(str(error))


def _review(state: ReplState, args: argparse.Namespace) -> int:
    record = state.runner.run(
        run_shell(f"git diff {args.review}", state.project, timeout_seconds=30)
    )
    if not record.successful:
        state.console.error(f"git diff failed: {record.output.strip()[-300:]}")
        return EXIT_USAGE
    if not record.output.strip():
        state.console.line("No changes to review.")
        return EXIT_OK
    body = record.output
    if len(body) > MAX_DIFF_CHARS:
        body = body[:MAX_DIFF_CHARS] + "\n...(truncated)"
    switch_agent(state, mode="plan")
    args.prompt = REVIEW_PROMPT + body
    return _once(state, args)


def _once(state: ReplState, args: argparse.Namespace) -> int:
    console = state.console
    prompt = args.prompt
    if not console.interactive:
        piped = console.stdin.read()
        if piped.strip():
            prompt = f"{prompt}\n\n{piped}"
    original = console.stdout
    if args.output == "json":
        console.stdout = _Discard()
    try:
        answer = run_turn(state, prompt)
    except (BudgetReached, PromptRefused) as error:
        console.stdout = original
        console.error(str(error))
        return EXIT_BLOCKED
    finally:
        console.stdout = original
    if answer is None:
        return EXIT_INTERRUPTED
    if args.output == "json":
        console.line(json.dumps(_summary(answer, state.session.id), sort_keys=True))
    return _STATUS_EXIT.get(answer.status, EXIT_FAILED)


def _summary(answer: Answer, session_id: str) -> dict[str, object]:
    return {
        "status": answer.status,
        "answer": answer.text if answer.ok else None,
        "reason": answer.reason,
        "steps": answer.steps,
        "cost": answer.cost,
        "session": session_id,
    }


def _show_last_output(state: ReplState) -> None:
    text = state.parts.renderer.expand(None)
    state.console.line(text if text is not None else "  No tool output yet.")


def _custom_notices(state: ReplState) -> None:
    found = state.parts.custom_tools
    for notice in found.notices if found is not None else []:
        state.console.line(state.console.style(f"  {notice}", "yellow"))


def _banner(state: ReplState) -> None:
    console = state.console
    if console.fancy:
        console.box(
            [
                f"✻ nailong {__version__}",
                f"  {state.config.model}  ·  {state.parts.mode.value} mode  ·  "
                f"session {state.session.id}",
                f"  {state.config.project}",
                "  commands: "
                + (
                    "NOT sandboxed" if state.sandbox_label == NOT_SANDBOXED else state.sandbox_label
                ),
            ],
        )
        console.line(
            console.style(
                " Tips: / for commands · @ to attach a file · ! to run a command yourself · "
                "\⏎ for a new line · Ctrl+D to leave",
                "dim",
            )
        )
        return
    console.line(console.style(f"nailong {__version__}", "bold") + f"  {state.config.project}")
    console.line(
        f"model {state.config.model}  mode {state.parts.mode.value}  session {state.session.id}"
    )
    if state.sandbox_label == NOT_SANDBOXED:
        console.line(console.style("commands are NOT sandboxed (see /sandbox)", "red"))
    else:
        console.line(f"commands run in {state.sandbox_label}")
    if state.config.ignored_project_keys:
        console.line(
            console.style(
                f"ignored {list(state.config.ignored_project_keys)} in project settings "
                "(see /doctor)",
                "yellow",
            )
        )
    console.line("/help for commands, @file to attach, !cmd to run one, Ctrl+D to leave.")


class _Discard:
    def write(self, text: str) -> int:
        return len(text)

    def flush(self) -> None:
        return None
