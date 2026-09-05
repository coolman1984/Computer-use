"""حاوية الخدمات: نقطة التركيب الوحيدة لكل مكونات المنصة."""

from __future__ import annotations

import time
from typing import Any, Callable

from .config import Settings, ensure_directories, load_settings
from .core.clock import Clock, SystemClock
from .engine.registry import StepRegistry, WorkflowRegistry
from .engine.runner import WorkflowRunner
from .events.bus import EventBus
from .events.log import EventLog
from .storage.db import Database
from .storage.repositories import (
    AgentRunRepository,
    EventRepository,
    FileRepository,
    IncidentRepository,
    RunRepository,
    StepRepository,
)


class Services:
    """يجمع الإعدادات وقاعدة البيانات والسجلات والمحرك في كائن واحد."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        db: Database | None = None,
        clock: Clock | None = None,
        sleeper: Callable[[float], None] = time.sleep,
        create_directories: bool = True,
    ) -> None:
        self.settings = settings or load_settings()
        self.clock = clock or SystemClock()
        if create_directories:
            ensure_directories(self.settings)
        self.db = db or Database(self.settings.storage.sqlite_path)
        self.db.migrate()

        self.runs = RunRepository(self.db, self.clock)
        self.steps = StepRepository(self.db, self.clock)
        self.files = FileRepository(self.db, self.clock)
        self.incidents = IncidentRepository(self.db, self.clock)
        self.agent_runs = AgentRunRepository(self.db, self.clock)

        self.bus = EventBus()
        self.events = EventLog(EventRepository(self.db, self.clock), self.bus, self.clock)

        self.step_registry = StepRegistry()
        self.workflows = WorkflowRegistry()
        self.runner = WorkflowRunner(self, clock=self.clock, sleeper=sleeper)

        # محوّلات تُركَّب في مراحل لاحقة (تلتزم بعقود ports/)
        self.browser: Any = None
        self.validator: Any = None
        self.agent_runner: Any = None
        self.notifier: Any = None

        from .workflows.builtin import register_builtins

        register_builtins(self)

    def close(self) -> None:
        self.db.close()


def build_services(**kwargs: Any) -> Services:
    return Services(**kwargs)
