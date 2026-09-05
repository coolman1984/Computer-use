from __future__ import annotations

from smartops.domain.enums import EventType, RunStatus
from smartops.storage.db import Database


def test_migrations_are_idempotent() -> None:
    db = Database(":memory:")
    assert db.migrate() == 2
    assert db.migrate() == 2


def test_run_lock_prevents_double_execution(services) -> None:
    run = services.runs.create("platform.selfcheck")
    assert services.runs.claim(run.id, "worker-a") is True
    assert services.runs.claim(run.id, "worker-b") is False
    services.runs.release(run.id, "worker-a")
    assert services.runs.claim(run.id, "worker-b") is True


def test_events_keep_insertion_order(services) -> None:
    run = services.runs.create("platform.selfcheck")
    for _ in range(5):
        services.events.emit(EventType.RUN_STARTED, run_id=run.id)
        services.events.emit(EventType.STEP_STARTED, run_id=run.id)
    types = [e.type for e in services.events.timeline(run.id)]
    assert types == [EventType.RUN_STARTED, EventType.STEP_STARTED] * 5


def test_due_runs_include_queued(services) -> None:
    run = services.runs.create("platform.selfcheck")
    due = services.runs.due()
    assert run.id in [r.id for r in due]
    run.status = RunStatus.SUCCEEDED
    services.runs.update(run)
    assert run.id not in [r.id for r in services.runs.due()]
