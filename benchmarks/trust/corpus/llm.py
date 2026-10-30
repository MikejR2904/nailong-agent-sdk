"""A small chat client with an on-disk cache and token accounting, for the LLM-based methods."""

from __future__ import annotations

import hashlib
import json
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

BASE_URL = "https://api.fireworks.ai/inference/v1/chat/completions"
CACHE = Path("results/corpus_llm_cache")
MAX_ATTEMPTS = 5


class Usage:
    def __init__(self) -> None:
        self.prompt = 0
        self.completion = 0
        self.calls = 0
        self.cached = 0
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
    model: str, prompt: str, key: str, *, max_tokens: int = 1_500, temperature: float = 0.0
) -> str:
    digest = hashlib.sha256(
        json.dumps([model, prompt, max_tokens, temperature]).encode("utf-8")
    ).hexdigest()
    path = CACHE / f"{digest}.json"
    if path.is_file():
        USAGE.add(0, 0, True)
        return str(json.loads(path.read_text("utf-8"))["text"])
    body = json.dumps(
        {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
    ).encode("utf-8")
    last = ""
    for attempt in range(1, MAX_ATTEMPTS + 1):
        request = urllib.request.Request(
            BASE_URL,
            data=body,
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
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
    text = str(data["choices"][0]["message"].get("content") or "")
    usage = data.get("usage") or {}
    USAGE.add(int(usage.get("prompt_tokens", 0)), int(usage.get("completion_tokens", 0)), False)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"text": text}), encoding="utf-8")
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
