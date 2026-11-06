"""/models: ask the model service which models it offers, then pick one."""

from __future__ import annotations

import dataclasses
import json
import urllib.error
import urllib.request

from .network import host_of, is_private_host
from .state import ReplState

ANTHROPIC_MODELS_URL = "https://api.anthropic.com/v1/models?limit=100"
ANTHROPIC_VERSION = "2023-06-01"
MAX_RESPONSE_BYTES = 5 * 1024 * 1024
SHOWN = 40
TIMEOUT_SECONDS = 15.0


def models_request(provider: str, base_url: str | None, key: str | None):
    if provider == "anthropic":
        url = base_url.rstrip("/") + "/models?limit=100" if base_url else ANTHROPIC_MODELS_URL
        headers = {"anthropic-version": ANTHROPIC_VERSION}
        if key:
            headers["x-api-key"] = key
    else:
        if not base_url:
            raise ValueError("This provider has no base_url, so there is no service to ask.")
        url = base_url.rstrip("/") + "/models"
        headers = {"Authorization": f"Bearer {key}"} if key else {}
    return url, headers


def fetch_models(url: str, headers: dict[str, str]) -> list[str]:
    request = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            raw = response.read(MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as error:
        raise ValueError(f"{url} answered HTTP {error.code} {error.reason}.") from None
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise ValueError(f"Could not reach {url}: {type(error).__name__}: {error}") from None
    if len(raw) > MAX_RESPONSE_BYTES:
        raise ValueError(f"{url} sent more than {MAX_RESPONSE_BYTES} bytes.")
    try:
        body = json.loads(raw)
    except json.JSONDecodeError as error:
        raise ValueError(f"{url} did not send JSON ({error.msg}).") from None
    items = body.get("data") if isinstance(body, dict) else body
    if not isinstance(items, list):
        raise ValueError(f'{url} sent no "data" list of models.')
    names = [item.get("id") if isinstance(item, dict) else item for item in items]
    return sorted({name for name in names if isinstance(name, str) and name})


def _models(state: ReplState, arguments: list[str]) -> bool:
    from .turns import switch_agent

    config, console = state.config, state.console
    key = state.environ.get(config.api_key_env) if config.api_key_env else None
    url, headers = models_request(config.provider, config.base_url, key)
    if config.offline and not is_private_host(host_of(url)):
        raise ValueError(
            f"offline is on and {host_of(url)} is not on your network, so the model list was not "
            "requested."
        )
    wanted = " ".join(arguments).lower()
    found = [name for name in fetch_models(url, headers) if wanted in name.lower()]
    if not found:
        console.line(f"  The service lists no model matching {wanted!r}." if wanted else "  None.")
        return True
    shown = found[:SHOWN]
    if console.chooser is not None:
        picked = console.chooser(f"{len(found)} model(s); choose one", shown)
        chosen = shown[picked] if picked is not None else None
    else:
        for number, name in enumerate(shown, 1):
            marker = "*" if name == config.model else " "
            console.line(f" {marker} {number:>2}  {name}")
        if len(found) > SHOWN:
            console.line(f"  ...and {len(found) - SHOWN} more; add text to narrow the list.")
        console.line("  Switch with /model NAME.")
        chosen = None
    if chosen is not None and chosen != config.model:
        state.config = dataclasses.replace(config, model=chosen)
        state.parts.config = state.config
        switch_agent(state, setup=state.model_factory(state.config, state.environ))
        console.line(f"  model is now {chosen}")
    return True


MODEL_HELP = {"models": "/models [text]: list the models the service offers and switch to one"}
MODEL_COMMANDS = {"models": _models}


def _web(state: ReplState, arguments: list[str]) -> bool:
    from .turns import switch_agent

    config, console = state.config, state.console
    words = [word.lower() for word in arguments]
    if not words:
        console.line(f"  web access (fetch pages and documents): {'on' if config.web else 'off'}")
        console.line(f"  web search: {'on' if config.web_search else 'off'}")
        console.line("  /web on|off  and  /web search on|off  change this session only.")
        return True
    field = "web_search" if words[0] == "search" else "web"
    value = words[-1]
    if value not in {"on", "off"} or len(words) > (2 if field == "web_search" else 1):
        raise ValueError("Usage: /web on|off   or   /web search on|off")
    if value == "on" and config.offline:
        raise ValueError(
            "offline is on in your settings, which forbids web access; set offline = false first."
        )
    wanted = value == "on"
    if getattr(config, field) == wanted:
        console.line(f"  {field} is already {value}")
        return True
    state.config = (
        dataclasses.replace(config, web_search=wanted)
        if field == "web_search"
        else dataclasses.replace(config, web=wanted)
    )
    state.parts.config = state.config
    switch_agent(state)
    console.line(f"  {field} is now {value} for this session")
    return True


MODEL_HELP["web"] = "/web [on|off] and /web search [on|off]: web access for this session"
MODEL_COMMANDS["web"] = _web
