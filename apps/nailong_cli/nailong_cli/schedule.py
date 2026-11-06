"""Scheduled prompts: saved in a file, run unattended by `nailong --run-schedules`."""

from __future__ import annotations

import json
import os
import re
import time
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

SCHEDULE_FILE = "schedules.json"
LOCK_FILE = "schedules.lock"
LOCK_STALE_SECONDS = 3_600
UNATTENDED_MODES = ("plan", "accept-edits")
MAX_SCHEDULES = 50
_INTERVAL = re.compile(r"^every\s+(\d+)\s*([mhd])$")
_DAILY = re.compile(r"^(daily|weekdays)\s+(\d{1,2}):(\d{2})$")
_LOOKBACK_MINUTES = 7 * 24 * 60


@dataclass
class Scheduled:
    id: str
    when: str
    prompt: str
    project: str
    mode: str
    created: str
    last_run: str | None = None
    last_status: str | None = None
    enabled: bool = True


def schedule_path(home: Path) -> Path:
    return home / ".config" / "nailong" / SCHEDULE_FILE


def load(home: Path) -> list[Scheduled]:
    path = schedule_path(home)
    if not path.is_file():
        return []
    try:
        return [Scheduled(**item) for item in json.loads(path.read_text(encoding="utf-8"))]
    except (OSError, ValueError, TypeError) as error:
        raise ValueError(
            f"The schedule file {path} cannot be read: {type(error).__name__}: {error}. Fix or "
            "delete it."
        ) from error


def save(home: Path, items: list[Scheduled]) -> None:
    path = schedule_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps([asdict(item) for item in items], indent=2), encoding="utf-8")
    os.replace(temporary, path)


def check_when(when: str) -> str:
    text = " ".join(when.split()).lower()
    if _INTERVAL.match(text):
        count = int(_INTERVAL.match(text).group(1))  # type: ignore[union-attr]
        if count < 1:
            raise ValueError("The interval must be at least 1.")
        return text
    daily = _DAILY.match(text)
    if daily:
        hour, minute = int(daily.group(2)), int(daily.group(3))
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError(f'"{when}" has a time outside 00:00 to 23:59.')
        return f"{daily.group(1)} {hour:02d}:{minute:02d}"
    fields = text.split()
    if len(fields) == 5:
        for field, low, high in zip(fields, (0, 0, 1, 1, 0), (59, 23, 31, 12, 6), strict=True):
            _cron_values(field, low, high, when)
        return text
    raise ValueError(
        f'"{when}" is not a schedule. Use "every 30m", "every 2h", "every 1d", "daily 09:00", '
        '"weekdays 09:00" or five cron fields such as "0 9 * * 1-5".'
    )


def _cron_values(field: str, low: int, high: int, original: str) -> set[int]:
    values: set[int] = set()
    for part in field.split(","):
        step = 1
        if "/" in part:
            part, _, step_text = part.partition("/")
            if not step_text.isdigit() or int(step_text) < 1:
                raise ValueError(f'"{original}": step "{step_text}" must be a positive number.')
            step = int(step_text)
        if part == "*":
            start, end = low, high
        elif "-" in part:
            first, _, last = part.partition("-")
            start, end = _number(first, original), _number(last, original)
        else:
            start = _number(part, original)
            end = high if step > 1 else start
        if not (low <= start <= end <= high):
            raise ValueError(f'"{original}": {part or field} is outside {low} to {high}.')
        values.update(range(start, end + 1, step))
    return values


def _number(text: str, original: str) -> int:
    if not text.isdigit():
        raise ValueError(f'"{original}": "{text}" is not a number.')
    return int(text)


def is_due(item: Scheduled, now: datetime) -> bool:
    reference = datetime.fromisoformat(item.last_run or item.created)
    if now.tzinfo is not None and reference.tzinfo is None:
        reference = reference.astimezone()
    text = item.when
    interval = _INTERVAL.match(text)
    if interval:
        unit = {"m": "minutes", "h": "hours", "d": "days"}[interval.group(2)]
        return now >= reference + timedelta(**{unit: int(interval.group(1))})
    return _next_match(text, reference, now) is not None


def _next_match(text: str, after: datetime, now: datetime) -> datetime | None:
    daily = _DAILY.match(text)
    if daily:
        hour, minute = int(daily.group(2)), int(daily.group(3))
        weekdays = daily.group(1) == "weekdays"
        candidate = after.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if candidate <= after:
            candidate += timedelta(days=1)
        while weekdays and candidate.weekday() >= 5:
            candidate += timedelta(days=1)
        return candidate if candidate <= now else None
    minute_set, hour_set, day_set, month_set, weekday_set = (
        _cron_values(field, low, high, text)
        for field, low, high in zip(text.split(), (0, 0, 1, 1, 0), (59, 23, 31, 12, 6), strict=True)
    )
    moment = after.replace(second=0, microsecond=0) + timedelta(minutes=1)
    steps = min(_LOOKBACK_MINUTES, int((now - moment).total_seconds() // 60) + 1)
    for _ in range(max(steps, 0)):
        if (
            moment.minute in minute_set
            and moment.hour in hour_set
            and moment.day in day_set
            and moment.month in month_set
            and (moment.isoweekday() % 7) in weekday_set
        ):
            return moment
        moment += timedelta(minutes=1)
    return None


def add(home: Path, when: str, prompt: str, project: Path, mode: str) -> Scheduled:
    if mode not in UNATTENDED_MODES:
        raise ValueError(
            f"Scheduled work runs with nobody to ask, so mode must be one of {UNATTENDED_MODES}, "
            f"not {mode!r}."
        )
    if not prompt.strip():
        raise ValueError("A scheduled prompt cannot be empty.")
    items = load(home)
    if len(items) >= MAX_SCHEDULES:
        raise ValueError(f"{MAX_SCHEDULES} schedules exist already; remove one first.")
    item = Scheduled(
        id=f"sch-{uuid.uuid4().hex[:6]}",
        when=check_when(when),
        prompt=prompt.strip(),
        project=str(project.resolve()),
        mode=mode,
        created=datetime.now().astimezone().isoformat(timespec="seconds"),
    )
    save(home, [*items, item])
    return item


def remove(home: Path, schedule_id: str) -> None:
    items = load(home)
    kept = [item for item in items if item.id != schedule_id]
    if len(kept) == len(items):
        raise ValueError(
            f'There is no schedule "{schedule_id}"; schedules: {[i.id for i in items]}.'
        )
    save(home, kept)


def describe(item: Scheduled) -> str:
    state = "" if item.enabled else " (off)"
    last = f", last run {item.last_run} {item.last_status}" if item.last_run else ", never run"
    return f"{item.id}{state}: {item.when} [{item.mode}] {item.prompt[:60]}  ({item.project}{last})"


def install_hint(python: str) -> list[str]:
    command = f'"{python}" -m nailong_cli --run-schedules'
    if os.name == "nt":
        return [
            "Run this once in a terminal to check due schedules every five minutes:",
            f'  schtasks /Create /SC MINUTE /MO 5 /TN "nailong schedules" /TR "{command}"',
            'Remove it later with:  schtasks /Delete /TN "nailong schedules"',
        ]
    return [
        "Add this line with `crontab -e` to check due schedules every five minutes:",
        f"  */5 * * * * {command}",
    ]


class _Lock:
    def __init__(self, home: Path) -> None:
        self._path = schedule_path(home).with_name(LOCK_FILE)
        self._held = False

    def __enter__(self) -> bool:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        if self._path.exists() and time.time() - self._path.stat().st_mtime > LOCK_STALE_SECONDS:
            self._path.unlink(missing_ok=True)
        try:
            descriptor = os.open(self._path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            return False
        os.write(descriptor, str(os.getpid()).encode())
        os.close(descriptor)
        self._held = True
        return True

    def __exit__(self, *exc: object) -> None:
        if self._held:
            self._path.unlink(missing_ok=True)


def run_due(
    home: Path,
    run_one: Callable[[Scheduled], tuple[str, str]],
    now: datetime | None = None,
) -> list[str]:
    moment = now or datetime.now().astimezone()
    lines: list[str] = []
    with _Lock(home) as acquired:
        if not acquired:
            return ["Another nailong --run-schedules is already running; nothing done."]
        items = load(home)
        for item in items:
            if not item.enabled or not is_due(item, moment):
                continue
            status, detail = run_one(item)
            item.last_run = moment.isoformat(timespec="seconds")
            item.last_status = status
            lines.append(f"{item.id} {status}: {detail}")
            save(home, items)
    return lines or ["No schedule is due."]


def record_run(item: Scheduled, status: str, output: str, now: datetime) -> Path:
    folder = Path(item.project) / ".nailong" / "schedules" / item.id
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{now.strftime('%Y%m%d-%H%M%S')}.md"
    header = f"# {item.id} {item.when}\n\nPrompt: {item.prompt}\nStatus: {status}\n\n"
    path.write_text(header + output, encoding="utf-8")
    return path


def summary_of(answer_text: Any) -> str:
    text = str(answer_text).strip()
    return text[:120] + ("..." if len(text) > 120 else "")
