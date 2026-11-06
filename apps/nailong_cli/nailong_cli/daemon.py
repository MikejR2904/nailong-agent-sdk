"""`nailong --daemon`: one resident process that runs scheduled prompts when they fall due.

The scheduler already runs from cron or Task Scheduler (`--run-schedules`); this keeps a single
process alive instead, for machines where installing a timer is not wanted. It checks the schedule
every interval, runs what is due exactly as `--run-schedules` would, and writes a small status file
so `--daemon-status` can say whether it is alive and what it did last. Only one runs at a time;
`--daemon-stop` asks it to finish its current check and exit.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from .storage import atomic_write, pid_alive

MIN_INTERVAL_SECONDS = 10
DEFAULT_INTERVAL_SECONDS = 60
KEEP_LINES = 20


class DaemonError(ValueError):
    pass


class Daemon:
    def __init__(
        self,
        home: Path,
        interval: float,
        tick: Callable[[], list[str]],
        *,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if interval < MIN_INTERVAL_SECONDS:
            raise DaemonError(
                f"The interval is {interval:g}s; the shortest allowed is {MIN_INTERVAL_SECONDS}s."
            )
        self._directory = home / ".local" / "state" / "nailong"
        self._interval = interval
        self._tick = tick
        self._sleep = sleep

    @property
    def _status_file(self) -> Path:
        return self._directory / "daemon.json"

    @property
    def _stop_file(self) -> Path:
        return self._directory / "daemon.stop"

    def status(self) -> dict[str, Any]:
        if not self._status_file.is_file():
            return {"running": False, "note": "no daemon has run here"}
        data = json.loads(self._status_file.read_text(encoding="utf-8"))
        data["running"] = pid_alive(int(data.get("pid", 0)))
        return data

    def request_stop(self) -> bool:
        status = self.status()
        if not status["running"]:
            return False
        self._directory.mkdir(parents=True, exist_ok=True)
        self._stop_file.write_text("stop", encoding="utf-8")
        return True

    def _write(self, **values: Any) -> None:
        atomic_write(self._status_file, json.dumps(values, indent=1))

    def run(self, max_ticks: int | None = None) -> int:
        if self.status()["running"] and self.status().get("pid") != os.getpid():
            raise DaemonError(f"A daemon is already running (process {self.status()['pid']}).")
        self._directory.mkdir(parents=True, exist_ok=True)
        self._stop_file.unlink(missing_ok=True)
        started = datetime.now().astimezone().isoformat(timespec="seconds")
        ticks = 0
        recent: list[str] = []
        try:
            while True:
                lines = self._tick()
                ticks += 1
                recent = [*recent, *lines][-KEEP_LINES:]
                self._write(
                    pid=os.getpid(),
                    started=started,
                    interval=self._interval,
                    ticks=ticks,
                    last_tick=datetime.now().astimezone().isoformat(timespec="seconds"),
                    recent=recent,
                )
                if self._stop_file.exists() or (max_ticks is not None and ticks >= max_ticks):
                    break
                self._sleep(self._interval)
        finally:
            self._stop_file.unlink(missing_ok=True)
            if self._status_file.is_file():
                data = json.loads(self._status_file.read_text(encoding="utf-8"))
                data["pid"] = 0
                data["stopped"] = datetime.now().astimezone().isoformat(timespec="seconds")
                self._write(**data)
        return ticks
