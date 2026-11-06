"""The prompt: plain input, or a prompt_toolkit prompt laid out like a modern coding terminal."""

from __future__ import annotations

import json
import shutil
import sys
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

from .console import Console

SKIPPED_DIRECTORIES = frozenset(
    {".git", ".venv", "node_modules", "__pycache__", ".mypy_cache", ".pytest_cache", ".ruff_cache"}
)
MAX_PATH_SUGGESTIONS = 40
PROMPT_MARK = "> "
KEY_ACTIONS = {
    "cycle_mode": ["s-tab"],
    "expand_output": ["c-o"],
    "newline": ["escape enter", "c-j"],
}
PALETTE_KEYS = ("rule", "hint", "mode", "menu")
_PRETTY = {"c": "Ctrl+", "s": "Shift+", "escape": "Alt+"}


def load_palette(name: str, project: Path, home: Path) -> dict[str, str]:
    if name in THEMES:
        return THEMES[name]
    for folder in (project / ".nailong" / "themes", home / ".config" / "nailong" / "themes"):
        path = folder / f"{name}.json"
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise ValueError(f"Theme file {path} cannot be read: {error}") from error
        unknown = (
            sorted(set(data) - set(PALETTE_KEYS)) if isinstance(data, dict) else ["(not an object)"]
        )
        if unknown or not all(isinstance(value, str) for value in data.values()):
            raise ValueError(
                f"Theme file {path} must be a JSON object with text values for "
                f"{list(PALETTE_KEYS)}; it has {unknown or 'a non-text value'}."
            )
        return {**THEMES["default"], **data}
    available = sorted(THEMES)
    raise ValueError(
        f'There is no theme "{name}". Built in: {available}; or put NAME.json in '
        ".nailong/themes/ or ~/.config/nailong/themes/."
    )


def key_chords(action: str, configured: dict[str, Any] | None) -> list[tuple[str, ...]]:
    value = (configured or {}).get(action, KEY_ACTIONS[action])
    chords = [value] if isinstance(value, str) else list(value)
    if not chords or any(not isinstance(item, str) or not item.strip() for item in chords):
        raise ValueError(f'keybinds.{action} must be a key such as "c-o" or a list of them.')
    return [tuple(item.split()) for item in chords]


def pretty_chord(chord: tuple[str, ...]) -> str:
    parts = []
    for key in chord:
        prefix, _, rest = key.partition("-")
        if prefix in _PRETTY and rest:
            parts.append(_PRETTY[prefix] + (rest.upper() if len(rest) == 1 else rest.title()))
        else:
            parts.append(key.title())
    return " ".join(parts)


THEMES = {
    "default": {
        "rule": "#585858",
        "hint": "#8a8a8a",
        "mode": "#d7af5f",
        "menu": "bg:#303030 #d0d0d0",
    },
    "dark": {"rule": "#3a3a3a", "hint": "#6c6c6c", "mode": "#8fbcbb", "menu": "bg:#101010 #8fbcbb"},
    "light": {
        "rule": "#b2b2b2",
        "hint": "#6c6c6c",
        "mode": "#875f00",
        "menu": "bg:#e5e5e5 #202020",
    },
    "mono": {"rule": "", "hint": "", "mode": "bold", "menu": ""},
}


class PlainReader:
    def __init__(self, console: Console) -> None:
        self._console = console

    def read(self, prompt: str, default: str = "") -> str:
        return self._console.ask(prompt)


def slash_matches(text: str, commands: dict[str, str]) -> list[tuple[str, str]]:
    if not text.startswith("/") or " " in text:
        return []
    return [(name, help_) for name, help_ in sorted(commands.items()) if name.startswith(text)]


def mention_matches(fragment: str, project: Path) -> list[str]:
    directory_part, _, name_part = fragment.rpartition("/")
    base = (project / directory_part) if directory_part else project
    try:
        base = base.resolve()
        base.relative_to(project.resolve())
        entries = sorted(base.iterdir(), key=lambda path: (not path.is_dir(), path.name.lower()))
    except (OSError, ValueError):
        return []
    found = []
    for entry in entries:
        if entry.name in SKIPPED_DIRECTORIES or not entry.name.lower().startswith(
            name_part.lower()
        ):
            continue
        if entry.name.startswith(".") and not name_part.startswith("."):
            continue
        prefix = f"{directory_part}/" if directory_part else ""
        found.append(f"{prefix}{entry.name}{'/' if entry.is_dir() else ''}")
        if len(found) >= MAX_PATH_SUGGESTIONS:
            break
    return found


def _completer(commands: Callable[[], dict[str, str]], project: Path) -> Any:
    from prompt_toolkit.completion import Completer, Completion

    class PromptCompleter(Completer):
        def get_completions(self, document: Any, complete_event: Any) -> Iterable[Any]:
            before = document.text_before_cursor
            for name, help_ in slash_matches(before, commands()):
                yield Completion(
                    name, start_position=-len(before), display=name, display_meta=help_
                )
            if before.startswith("/") and " " not in before:
                return
            token = before.rsplit(None, 1)[-1] if before.split() else ""
            if token.startswith("@") and not before.endswith(" "):
                fragment = token[1:]
                for path in mention_matches(fragment, project):
                    yield Completion(
                        f"@{path}", start_position=-len(token), display=path, display_meta=""
                    )

    return PromptCompleter()


class ToolkitReader:
    def __init__(
        self,
        *,
        project: Path,
        history_file: Path,
        editing_mode: str,
        theme: str,
        toolbar: Callable[[], str],
        cycle_mode: Callable[[], None],
        commands: Callable[[], dict[str, str]],
        mode_hint: Callable[[], str] | None = None,
        on_expand: Callable[[], None] | None = None,
        input: Any = None,
        output: Any = None,
        keybinds: dict[str, Any] | None = None,
        palette: dict[str, str] | None = None,
    ) -> None:
        from prompt_toolkit import PromptSession
        from prompt_toolkit.history import FileHistory
        from prompt_toolkit.key_binding import KeyBindings
        from prompt_toolkit.styles import Style

        colours = palette or THEMES.get(theme, THEMES["default"])
        self._colours = colours
        self._toolbar = toolbar
        self._mode_hint = mode_hint or (lambda: "")
        bindings = KeyBindings()

        def _cycle(event: Any) -> None:
            cycle_mode()
            event.app.invalidate()

        def _expand(event: Any) -> None:
            if on_expand is not None:
                event.app.run_in_terminal(on_expand)

        @bindings.add("enter")
        def _submit(event: Any) -> None:
            buffer = event.current_buffer
            if buffer.text.endswith("\\"):
                buffer.delete_before_cursor(1)
                buffer.insert_text("\n")
            elif buffer.complete_state is not None and buffer.complete_state.current_completion:
                buffer.apply_completion(buffer.complete_state.current_completion)
            else:
                buffer.validate_and_handle()

        def _newline(event: Any) -> None:
            event.current_buffer.insert_text("\n")

        for action, handler in (
            ("cycle_mode", _cycle),
            ("expand_output", _expand),
            ("newline", _newline),
        ):
            for chord in key_chords(action, keybinds):
                try:
                    bindings.add(*chord)(handler)
                except ValueError as error:
                    raise ValueError(
                        f'keybinds.{action}: "{" ".join(chord)}" is not a key: {error}'
                    ) from error
        self._expand_label = pretty_chord(key_chords("expand_output", keybinds)[0])

        history_file.parent.mkdir(parents=True, exist_ok=True)
        style = {
            "rule": colours["rule"],
            "hint": colours["hint"],
            "mode": colours["mode"],
            "bottom-toolbar": "noreverse",
            "completion-menu": colours["menu"],
            "completion-menu.completion.current": "reverse",
            "completion-menu.meta.completion": colours["menu"] + " italic",
            "completion-menu.meta.completion.current": "reverse italic",
        }
        self._session = PromptSession(
            history=FileHistory(str(history_file)),
            key_bindings=bindings,
            bottom_toolbar=self._bottom,
            vi_mode=editing_mode == "vi",
            completer=_completer(commands, project),
            complete_while_typing=True,
            multiline=True,
            prompt_continuation=lambda width, line, wrap: "  ",
            style=Style.from_dict({key: value for key, value in style.items() if value}),
            input=input,
            output=output,
        )

    def _width(self) -> int:
        return max(20, shutil.get_terminal_size((80, 20)).columns)

    def _bottom(self) -> Any:
        from prompt_toolkit.formatted_text import FormattedText

        rule = "─" * self._width()
        return FormattedText(
            [
                ("class:rule", rule + "\n"),
                ("class:mode", " " + self._mode_hint() + "\n"),
                (
                    "class:hint",
                    " "
                    + self._toolbar()
                    + f"  ·  / commands  @ files  {self._expand_label} output  \\⏎ new line",
                ),
            ]
        )

    def read(self, prompt: str, default: str = "") -> str:
        from prompt_toolkit.formatted_text import FormattedText

        message = FormattedText([("class:rule", "─" * self._width() + "\n"), ("bold", prompt)])
        return self._session.prompt(message, default=default).strip()


def toolkit_available() -> bool:
    try:
        import prompt_toolkit  # noqa: F401
    except ImportError:
        return False
    return True


def make_reader(
    console: Console,
    *,
    project: Path,
    history_file: Path,
    editing_mode: str,
    theme: str,
    toolbar: Callable[[], str],
    cycle_mode: Callable[[], None],
    commands: Callable[[], dict[str, str]],
    mode_hint: Callable[[], str] | None = None,
    on_expand: Callable[[], None] | None = None,
    keybinds: dict[str, Any] | None = None,
    palette: dict[str, str] | None = None,
) -> PlainReader | ToolkitReader:
    if console.interactive and console.stdin is sys.stdin and toolkit_available():
        return ToolkitReader(
            project=project,
            history_file=history_file,
            editing_mode=editing_mode,
            theme=theme,
            toolbar=toolbar,
            cycle_mode=cycle_mode,
            commands=commands,
            mode_hint=mode_hint,
            on_expand=on_expand,
            keybinds=keybinds,
            palette=palette,
        )
    return PlainReader(console)


def choose(
    title: str,
    options: list[str],
    *,
    detail: str = "",
    input: Any = None,
    output: Any = None,
) -> int | None:
    from prompt_toolkit.application import Application
    from prompt_toolkit.formatted_text import FormattedText
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.layout import Layout
    from prompt_toolkit.layout.containers import Window
    from prompt_toolkit.layout.controls import FormattedTextControl

    selected = [0]
    bindings = KeyBindings()

    def move(step: int) -> None:
        selected[0] = (selected[0] + step) % len(options)

    @bindings.add("up")
    @bindings.add("k")
    def _up(event: Any) -> None:
        move(-1)

    @bindings.add("down")
    @bindings.add("j")
    @bindings.add("tab")
    def _down(event: Any) -> None:
        move(1)

    @bindings.add("enter")
    def _accept(event: Any) -> None:
        event.app.exit(result=selected[0])

    @bindings.add("escape", eager=True)
    @bindings.add("c-c")
    def _cancel(event: Any) -> None:
        event.app.exit(result=None)

    for number in range(1, min(len(options), 9) + 1):

        @bindings.add(str(number))
        def _number(event: Any, number: int = number) -> None:
            event.app.exit(result=number - 1)

    def text() -> Any:
        parts: list[tuple[str, str]] = [("bold", title + "\n")]
        if detail:
            parts.append(("", detail.rstrip("\n") + "\n"))
        for index, label in enumerate(options):
            if index == selected[0]:
                parts.append(("class:selected", f"❯ {index + 1}. {label}\n"))
            else:
                parts.append(("", f"  {index + 1}. {label}\n"))
        parts.append(("class:hint", "  ↑/↓ choose · Enter confirm · 1-9 pick · Esc = no"))
        return FormattedText(parts)

    from prompt_toolkit.styles import Style

    application: Application[int | None] = Application(
        layout=Layout(Window(FormattedTextControl(text), always_hide_cursor=True)),
        key_bindings=bindings,
        style=Style.from_dict({"selected": "bold #5fafd7", "hint": "#8a8a8a"}),
        full_screen=False,
        erase_when_done=True,
        input=input,
        output=output,
    )
    return application.run(handle_sigint=False)
