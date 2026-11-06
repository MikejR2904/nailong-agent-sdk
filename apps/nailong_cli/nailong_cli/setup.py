"""First-run setup: pick a service, name a model, give a key, and write the settings file."""

from __future__ import annotations

import getpass
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from nailong_agent_sdk.starter import OPENAI_COMPATIBLE_BASE_URLS

from . import credentials
from .config import user_config_path
from .console import Console
from .network import host_of, is_loopback

QUOTE = chr(34)


class SetupCancelled(Exception):
    pass


@dataclass(frozen=True)
class Choice:
    label: str
    provider: str
    base_url: str | None
    key_variable: str | None
    local: bool = False


CHOICES = (
    Choice(
        "OpenRouter  (hosted, many models)",
        "openai-compatible",
        "openrouter",
        "OPENROUTER_API_KEY",
    ),
    Choice("Anthropic", "anthropic", None, "ANTHROPIC_API_KEY"),
    Choice("OpenAI", "openai-compatible", "openai", "OPENAI_API_KEY"),
    Choice("Google Gemini", "openai-compatible", "gemini", "GEMINI_API_KEY"),
    Choice("xAI", "openai-compatible", "xai", "XAI_API_KEY"),
    Choice("Groq", "openai-compatible", "groq", "GROQ_API_KEY"),
    Choice("Together AI", "openai-compatible", "together", "TOGETHER_API_KEY"),
    Choice("Fireworks AI", "openai-compatible", "fireworks", "FIREWORKS_API_KEY"),
    Choice("DeepSeek", "openai-compatible", "deepseek", "DEEPSEEK_API_KEY"),
    Choice("Mistral", "openai-compatible", "mistral", "MISTRAL_API_KEY"),
    Choice("Cerebras", "openai-compatible", "cerebras", "CEREBRAS_API_KEY"),
    Choice("Ollama  (on this machine)", "openai-compatible", "ollama", None, True),
    Choice("vLLM  (on this machine)", "openai-compatible", "vllm", None, True),
    Choice("LM Studio  (on this machine)", "openai-compatible", "lmstudio", None, True),
    Choice("llama.cpp server  (on this machine)", "openai-compatible", "llamacpp", None, True),
    Choice("Another OpenAI-compatible server  (you give the URL)", "openai-compatible", "", None),
)


@dataclass
class SetupResult:
    path: Path
    session_keys: dict[str, str] = field(default_factory=dict)


class Dialog(Protocol):
    def select(self, title: str, options: list[str]) -> int: ...

    def text(self, prompt: str, *, default: str = "", secret: bool = False) -> str: ...


class PlainDialog:
    def __init__(self, console: Console) -> None:
        self._console = console

    def select(self, title: str, options: list[str]) -> int:
        self._console.line(title)
        for number, option in enumerate(options, 1):
            self._console.line(f"  {number}. {option}")
        while True:
            answer = self._console.ask("Number: ")
            if answer.isdigit() and 1 <= int(answer) <= len(options):
                return int(answer) - 1
            self._console.line(f"Please enter a number from 1 to {len(options)}.")

    def text(self, prompt: str, *, default: str = "", secret: bool = False) -> str:
        suffix = f" [{default}]" if default else ""
        if secret and self._console.stdin is sys.stdin and sys.stdin.isatty():
            return getpass.getpass(f"{prompt}: ").strip() or default
        return self._console.ask(f"{prompt}{suffix}: ").strip() or default


class ToolkitDialog:
    def __init__(self, console: Console) -> None:
        self._console = console

    def select(self, title: str, options: list[str]) -> int:
        from .tui import choose

        picked = choose(title, options)
        if picked is None:
            raise SetupCancelled("Setup was cancelled.")
        return picked

    def text(self, prompt: str, *, default: str = "", secret: bool = False) -> str:
        from prompt_toolkit import prompt as ask

        try:
            return ask(f"{prompt}: ", default=default, is_password=secret).strip()
        except (EOFError, KeyboardInterrupt) as error:
            raise SetupCancelled("Setup was cancelled.") from error


def make_dialog(console: Console) -> Dialog:
    if console.interactive and console.stdin is sys.stdin:
        try:
            import prompt_toolkit  # noqa: F401
        except ImportError:
            return PlainDialog(console)
        return ToolkitDialog(console)
    return PlainDialog(console)


def run_setup(
    console: Console,
    home: Path,
    environ: Mapping[str, str],
    dialog: Dialog | None = None,
) -> SetupResult:
    dialog = dialog or make_dialog(console)
    path = user_config_path(home)
    console.box(
        [
            "✻ Welcome to nailong",
            "",
            "A coding agent for your terminal. Three quick questions, then you are in.",
            f"Settings are saved to {path}",
        ]
    )
    if path.exists():
        title = f"{path} already exists. Replace it?"
        if dialog.select(title, ["No, keep it", "Yes, replace"]) == 0:
            raise ValueError(f"Setup stopped: {path} was left as it is.")
    labels = [option.label for option in CHOICES]
    choice = CHOICES[dialog.select("1/3  Which model service will you use?", labels)]
    lines = [f'provider = "{choice.provider}"']
    base_url = choice.base_url
    if base_url == "":
        base_url = _ask_url(console, dialog)
    if base_url:
        lines.append(f'base_url = "{base_url}"')
    resolved = OPENAI_COMPATIBLE_BASE_URLS.get(base_url or "", base_url or "")
    if resolved.startswith("http://") and not is_loopback(host_of(resolved)):
        lines.append("allow_insecure_http = true")
    model = _ask_required(console, dialog, "2/3  Model name, as the service lists it")
    lines.append(f'model = "{model}"')
    result = SetupResult(path)
    variable = _key(console, dialog, choice, environ, result)
    if variable:
        lines.append(f'api_key_env = "{variable}"')
    if choice.local or resolved.startswith("http://"):
        lines.append("timeout_seconds = 300")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    console.line(console.style(f"✓ Saved {path}", "green"))
    return result


def _clean(text: str) -> str:
    return text.replace(QUOTE, "").strip()


def _ask_required(console: Console, dialog: Dialog, prompt: str) -> str:
    while True:
        answer = _clean(dialog.text(prompt))
        if answer:
            return answer
        console.line("This cannot be empty.")


def _ask_url(console: Console, dialog: Dialog) -> str:
    while True:
        url = _clean(dialog.text("Server URL, for example http://gpu-box:8000/v1"))
        if url.startswith(("http://", "https://")):
            return url
        console.line("The URL must start with http:// or https://.")


def _key(
    console: Console,
    dialog: Dialog,
    choice: Choice,
    environ: Mapping[str, str],
    result: SetupResult,
) -> str | None:
    if choice.key_variable is None:
        if dialog.select("3/3  Does the server need an API key?", ["No", "Yes"]) == 0:
            console.line("No key needed.")
            return None
        variable = "NAILONG_API_KEY"
    else:
        variable = choice.key_variable
    if environ.get(variable) or credentials.lookup(variable):
        console.line(f"Using the key already available as {variable}.")
        return variable
    key = _ask_key(console, dialog)
    if not key:
        console.line(
            console.style(
                f"No key entered. Set {variable} in your shell before the next run, or run "
                "nailong --setup again to save one.",
                "yellow",
            )
        )
        return variable
    result.session_keys[variable] = key
    try:
        credentials.store(variable, key)
    except credentials.CredentialError as error:
        message = f"{error} It will be used for this session only."
        console.line(console.style(message, "yellow"))
    else:
        where = credentials.backend_name()
        saved = f"✓ Key saved in the system credential store ({where})."
        console.line(console.style(saved, "green"))
    return variable


def _ask_key(console: Console, dialog: Dialog) -> str:
    while True:
        key = dialog.text("3/3  Paste your API key (hidden; Enter to skip)", secret=True)
        if not any(character.isspace() for character in key):
            return key
        console.line("A key has no spaces; paste only the key.")
