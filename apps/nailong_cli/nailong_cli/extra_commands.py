from __future__ import annotations

import shutil
import subprocess
import sys
import time
from pathlib import Path

from .config import project_config_path, user_config_path
from .hook_protocol import CLAUDE_NAMES
from .setup import SetupCancelled, run_setup

_WINDOWS_GRAB = (
    "Add-Type -AssemblyName System.Windows.Forms; Add-Type -AssemblyName System.Drawing; "
    "$image = [System.Windows.Forms.Clipboard]::GetImage(); "
    "if ($image -eq $null) { exit 3 }; "
    "$image.Save($args[0], [System.Drawing.Imaging.ImageFormat]::Png)"
)


def grab_clipboard_image(target: Path) -> str | None:
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        if sys.platform == "win32":
            shell = shutil.which("powershell") or shutil.which("pwsh")
            if shell is None:
                return "PowerShell was not found, so the clipboard cannot be read."
            done = subprocess.run(
                [shell, "-NoProfile", "-STA", "-Command", _WINDOWS_GRAB, str(target)],
                capture_output=True,
                timeout=20,
                check=False,
            )
        elif sys.platform == "darwin":
            if shutil.which("pngpaste") is None:
                return "Install pngpaste (brew install pngpaste) to paste images on macOS."
            done = subprocess.run(
                ["pngpaste", str(target)], capture_output=True, timeout=20, check=False
            )
        else:
            if shutil.which("wl-paste"):
                argv = ["wl-paste", "--type", "image/png"]
            elif shutil.which("xclip"):
                argv = ["xclip", "-selection", "clipboard", "-t", "image/png", "-o"]
            else:
                return "Install wl-clipboard or xclip to paste images on Linux."
            done = subprocess.run(argv, capture_output=True, timeout=20, check=False)
            if done.returncode == 0 and done.stdout:
                target.write_bytes(done.stdout)
    except (OSError, subprocess.TimeoutExpired) as error:
        return f"The clipboard could not be read: {type(error).__name__}: {error}"
    if done.returncode != 0 or not target.is_file() or target.stat().st_size == 0:
        target.unlink(missing_ok=True)
        return "There is no image on the clipboard."
    return None


def _image(state, arguments: list[str]) -> bool:
    target = state.project / ".nailong" / "clipboard" / f"clip-{int(time.time())}.png"
    problem = grab_clipboard_image(target)
    if problem:
        state.console.line(f"  {problem}")
        return True
    state.first_images.append(target)
    state.console.line(
        f"  Attached the clipboard image ({target.stat().st_size} bytes) to your next message."
    )
    return True


def _hooks(state, arguments: list[str]) -> bool:
    hooks = state.config.hooks
    if not hooks:
        state.console.line("  No hooks are configured (they live in your user settings).")
    for hook in hooks:
        selector = hook.get("matcher") or hook.get("tool", "*")
        style = hook.get("protocol", "env")
        name = (
            CLAUDE_NAMES.get(hook["event"], hook["event"]) if style == "claude" else hook["event"]
        )
        state.console.line(f"  {name:<18} {selector:<14} [{style}]  {hook['command']}")
    ignored = [key for key in state.config.ignored_project_keys if key in {"hooks", "mcp"}]
    if ignored:
        state.console.line(
            f"  The project settings also define {ignored}; they are not loaded until you "
            "approve them with /trust-project and restart."
        )
    return True


def _config(state, arguments: list[str]) -> bool:
    config = state.config
    rows = [
        ("provider", config.provider),
        ("model", config.model),
        ("api key variable", config.api_key_env or "(none)"),
        ("permission mode", config.permission_mode),
        ("sandbox", config.sandbox),
        ("require_sandbox", config.require_sandbox),
        ("max steps per turn", config.max_steps),
        ("web / search", f"{config.web} / {config.web_search}"),
        ("autocompact", config.autocompact),
        ("offline", config.offline),
        ("hooks", len(config.hooks)),
        ("mcp servers", ", ".join(config.mcp_servers) or "(none)"),
    ]
    for label, value in rows:
        state.console.line(f"  {label:<20} {value}")
    state.console.line(f"  user settings        {user_config_path(state.home)}")
    state.console.line(f"  project settings     {project_config_path(state.project)}")
    if config.ignored_project_keys:
        state.console.line(
            f"  ignored in project   {list(config.ignored_project_keys)} (see /doctor)"
        )
    return True


def _login(state, arguments: list[str]) -> bool:
    if not state.console.interactive:
        state.console.line("  /login asks questions, so it needs a terminal.")
        return True
    try:
        run_setup(state.console, state.home, dict(state.environ))
    except (SetupCancelled, ValueError) as error:
        state.console.line(f"  {error}")
        return True
    state.console.line("  Saved. Restart nailong to use the new settings.")
    return True


def _rename(state, arguments: list[str]) -> bool:
    if len(arguments) != 1:
        state.console.line("  Usage: /rename <new session name>")
        return True
    from nailong_agent_sdk.starter.session_store import SessionStore, validate_session_id

    new = validate_session_id(arguments[0])
    old = state.session.id
    if new == old:
        state.console.line(f"  The session is already called {new}.")
        return True
    if new in state.agent.sessions():
        raise ValueError(f'A session called "{new}" already exists; pick another name.')
    SessionStore(state.agent.services.run_root).fork(old, new, state.agent.name, None)
    state.session = state.agent.resume(new)
    state.agent.delete_session(old)
    state.console.line(f"  Renamed {old} to {new} ({len(state.session.turns)} turn(s) kept).")
    return True


EXTRA_HELP = {
    "hooks": "list the configured hooks and whether the project's are approved",
    "config": "the effective settings and where they come from",
    "login": "choose a model service and save an API key (same as --setup)",
    "image": "attach the image on the clipboard to your next message",
    "rename": "/rename NAME: give this session a name you can resume it by",
}
EXTRA_COMMANDS = {
    "hooks": _hooks,
    "config": _config,
    "login": _login,
    "image": _image,
    "rename": _rename,
}
