"""A small chat client with an on-disk cache and per-method cost accounting."""

from __future__ import annotations

import contextlib
import contextvars
import hashlib
import json
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

BASE_URL = "https://api.fireworks.ai/inference/v1/chat/completions"
CACHE = Path("results/corpus_llm_cache")
MAX_ATTEMPTS = 5
DEFAULT_MAX_TOKENS = 8_000
RETRY_MAX_TOKENS = 24_000

REPEAT: contextvars.ContextVar[int] = contextvars.ContextVar("repeat", default=0)


@dataclass
class Cost:
    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    seconds: float = 0.0

    def add(self, prompt: int, completion: int, seconds: float) -> None:
        self.calls += 1
        self.prompt_tokens += prompt
        self.completion_tokens += completion
        self.seconds += seconds


_CURRENT: contextvars.ContextVar[Cost | None] = contextvars.ContextVar("cost", default=None)


@contextlib.contextmanager
def track() -> Iterator[Cost]:
    cost = Cost()
    token = _CURRENT.set(cost)
    try:
        yield cost
    finally:
        _CURRENT.reset(token)


class Usage:
    def __init__(self) -> None:
        self.prompt = 0
        self.completion = 0
        self.calls = 0
        self.cached = 0
        self.truncated = 0
        self._lock = threading.Lock()

    def add(self, prompt: int, completion: int, cached: bool) -> None:
        with self._lock:
            self.calls += 1
            if cached:
                self.cached += 1
            else:
                self.prompt += prompt
                self.completion += completion


USAGE = Usage()


def ask(
    model: str,
    prompt: str,
    key: str,
    *,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    temperature: float = 0.0,
) -> str:
    digest = hashlib.sha256(
        json.dumps([model, prompt, max_tokens, temperature, REPEAT.get()]).encode("utf-8")
    ).hexdigest()
    path = CACHE / f"{digest}.json"
    cost = _CURRENT.get()
    if path.is_file():
        stored = json.loads(path.read_text("utf-8"))
        USAGE.add(0, 0, True)
        if cost is not None:
            cost.add(
                int(stored.get("prompt_tokens", 0)),
                int(stored.get("completion_tokens", 0)),
                float(stored.get("seconds", 0.0)),
            )
        return str(stored["text"])
    body = json.dumps(
        {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
    ).encode("utf-8")
    last = ""
    started = time.perf_counter()
    for attempt in range(1, MAX_ATTEMPTS + 1):
        request = urllib.request.Request(
            BASE_URL,
            data=body,
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=300) as response:
                data: dict[str, Any] = json.load(response)
            break
        except urllib.error.HTTPError as error:
            last = f"HTTP {error.code}"
            if error.code not in {429, 500, 502, 503, 504}:
                raise RuntimeError(
                    f"The provider refused the request for {model}: HTTP {error.code}: "
                    f"{error.read().decode('utf-8', errors='replace')[:200]}"
                ) from error
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            last = f"{type(error).__name__}: {error}"
        time.sleep(min(30, 2**attempt))
    else:
        raise RuntimeError(f"The provider did not answer for {model} after {MAX_ATTEMPTS}: {last}")
    seconds = time.perf_counter() - started
    choice = data["choices"][0]
    text = str(choice["message"].get("content") or "")
    usage = data.get("usage") or {}
    prompt_tokens = int(usage.get("prompt_tokens", 0))
    completion_tokens = int(usage.get("completion_tokens", 0))
    USAGE.add(prompt_tokens, completion_tokens, False)
    if cost is not None:
        cost.add(prompt_tokens, completion_tokens, seconds)
    if (
        not text.strip()
        and choice.get("finish_reason") == "length"
        and max_tokens < RETRY_MAX_TOKENS
    ):
        USAGE.truncated += 1
        return ask(model, prompt, key, max_tokens=RETRY_MAX_TOKENS, temperature=temperature)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "text": text,
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "seconds": seconds,
            }
        ),
        encoding="utf-8",
    )
    return text


def json_object(text: str) -> dict[str, Any] | None:
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        found = json.loads(text[start : end + 1])
    except ValueError:
        return None
    return found if isinstance(found, dict) else None


def json_array(text: str) -> list[Any] | None:
    start, end = text.find("["), text.rfind("]")
    if start < 0 or end <= start:
        return None
    try:
        found = json.loads(text[start : end + 1])
    except ValueError:
        return None
    return found if isinstance(found, list) else None
