# Copyright (c) 2026 David Michael Indraputra

"""Signed checkpoints taken while a run is going, not only when it ends.

A hash chain shows an edit in the middle of a record; only a record kept elsewhere shows that
the newest entries were cut. A checkpoint taken once at the end of a run leaves everything a
crash interrupts uncovered. A run that is killed keeps the checkpoints taken before it died, so
anything cut from before the newest of them is detected, and only the entries after it are not.
"""

from __future__ import annotations

import threading
from types import TracebackType

from ..foundations.logging import get_logger
from ..observability.telemetry_models import TelemetryContext
from ..tools.artifact_anchor import anchor_artifact_log, artifact_anchor_name
from ..tools.artifacts import ArtifactStore
from .runtime import AgentRuntimeServices

_logger = get_logger("periodic-checkpoints")
_STOP_WAIT_SECONDS = 30.0


class PeriodicCheckpoints:
    """A thread that checkpoints one run, and anchors its artifact log, every few seconds."""

    def __init__(
        self,
        services: AgentRuntimeServices,
        run_id: str,
        interval_seconds: float,
        *,
        artifacts: ArtifactStore | None = None,
        context: TelemetryContext | None = None,
    ) -> None:
        if interval_seconds <= 0:
            raise ValueError(f"interval_seconds must be above 0, got {interval_seconds!r}.")
        if services.checkpointer is None:
            raise ValueError(
                "periodic checkpoints need AgentRuntimeServices.open(checkpointer=...); "
                "this service has none."
            )
        self._services = services
        self._run_id = run_id
        self._interval = interval_seconds
        self._artifacts = artifacts
        self._context = context
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._loop, name=f"checkpoints-{run_id}", daemon=True
        )
        self.taken = 0
        self.failures = 0

    def start(self) -> PeriodicCheckpoints:
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        if self._thread.is_alive():
            self._thread.join(_STOP_WAIT_SECONDS)

    def __enter__(self) -> PeriodicCheckpoints:
        return self.start()

    def __exit__(
        self,
        kind: type[BaseException] | None,
        error: BaseException | None,
        trace: TracebackType | None,
    ) -> None:
        self.stop()

    def _loop(self) -> None:
        while not self._stop.wait(self._interval):
            self._take()

    def _take(self) -> None:
        checkpointer = self._services.checkpointer
        assert checkpointer is not None
        try:
            self._services.checkpoint_run(self._run_id)
            if self._artifacts is not None:
                anchor_artifact_log(
                    self._artifacts,
                    checkpointer.sink,
                    artifact_anchor_name(self._run_id),
                    checkpointer.signer,
                )
            self.taken += 1
        except Exception as error:
            self.failures += 1
            _logger.warning(
                'A periodic checkpoint of run "%s" failed: %s: %s',
                self._run_id,
                type(error).__name__,
                error,
            )
            self._services.record_checkpoint_failure(self._run_id, self._context, error, "periodic")
