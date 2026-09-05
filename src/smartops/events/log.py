"""The event log: the backbone of traceability. Every meaningful step leaves a trace here."""

from __future__ import annotations

from typing import Any

from ..core.clock import Clock, SystemClock
from ..core.ids import new_id
from ..domain.enums import EventType, Severity
from ..domain.models import Event
from ..storage.repositories import EventRepository
from .bus import EventBus


class EventLog:
    def __init__(
        self,
        repository: EventRepository,
        bus: EventBus | None = None,
        clock: Clock | None = None,
    ) -> None:
        self.repository = repository
        self.bus = bus or EventBus()
        self.clock = clock or SystemClock()

    def emit(
        self,
        event_type: EventType,
        *,
        run_id: str | None = None,
        step_name: str | None = None,
        severity: Severity = Severity.INFO,
        message: str = "",
        payload: dict[str, Any] | None = None,
    ) -> Event:
        event = Event(
            id=new_id("evt"),
            type=event_type,
            severity=severity,
            created_at=self.clock.now(),
            run_id=run_id,
            step_name=step_name,
            message=message,
            payload=payload or {},
        )
        self.repository.append(event)
        self.bus.publish(event)
        return event

    def timeline(self, run_id: str, *, limit: int = 500) -> list[Event]:
        return self.repository.list(run_id=run_id, limit=limit)

    def recent(self, *, limit: int = 200) -> list[Event]:
        return self.repository.list(limit=limit)
