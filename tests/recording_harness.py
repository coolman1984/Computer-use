"""Drive the real recorder from a test.

The recorder opens a browser and waits for a human. A test has no human, so this
runs the shipped `PlaywrightRecordingWorker` with the shipped `RecordingManager`
callbacks — the same capture script, the same event wiring, the same redaction,
the same persistence — and supplies the hands.

The script has to execute **on the recorder's own thread**. Playwright's sync
objects belong to the greenlet that created them, so a test thread cannot touch
the recorder's page; the worker therefore calls the script itself, once its first
page is loaded, exactly where a person's clicks would arrive.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Callable

from smartops.domain.enums import RecordingStatus
from smartops.recordings.worker import PlaywrightRecordingWorker
from smartops.sessions import session_path


def capture_with_recorder(
    services: Any,
    *,
    start_url: str,
    script: Callable[[Any], None],
    executable_path: str | None = None,
    seconds: float = 6.0,
    system_key: str = "portal",
    name: str = "Recorded task",
) -> list[dict]:
    """Record whatever `script` does to the page, and return the captured steps."""
    manager = services.recording_manager
    record = manager.create(name, system_key)

    failures: list[str] = []

    def drive(page: Any) -> None:
        """Runs on the recorder's thread, once its page is ready."""
        try:
            script(page)
            # Bindings and download handlers are asynchronous relative to the
            # script; give the events time to arrive before the recording ends.
            page.wait_for_timeout(1200)
        except Exception as exc:  # surfaced as a test failure, not swallowed
            failures.append(f"{type(exc).__name__}: {exc}")
        finally:
            worker.stop()

    worker = PlaywrightRecordingWorker(
        record.id,
        Path(record.artifact_dir),
        start_url,
        lambda item: manager._step(record.id, item),
        lambda: manager._heartbeat(record.id),
        lambda error: manager._finished(record.id, error),
        executable_path or "",
        session_path(services.settings.storage.sessions_dir, system_key),
        True,  # headless: this environment has no screen
        drive,
    )
    manager.workers[record.id] = worker

    record.status = RecordingStatus.RECORDING
    services.recordings.save(record)
    worker.start()

    _wait_for_status(
        services, record.id,
        {RecordingStatus.COMPLETED, RecordingStatus.FAILED, RecordingStatus.INTERRUPTED},
        timeout=max(seconds, 10.0) + 25,
    )

    settled = services.recordings.get(record.id)
    assert not failures, f"the recorded script failed: {failures[0]}"
    assert settled.status is RecordingStatus.COMPLETED, (
        f"the recording did not complete: {settled.status.value} — {settled.error_message}"
    )
    return [step.to_dict() for step in services.recordings.steps(record.id)]


def _wait_for_status(services: Any, recording_id: str, wanted: set, *, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        record = services.recordings.get(recording_id)
        if record and record.status in wanted:
            return
        time.sleep(0.1)
