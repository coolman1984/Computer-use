"""Schedule collect.report runs from the system definitions (F-08).

No new schema: due-ness is computed from the last recorded run for each
(system, report) pair, inside the existing runs table. The target scale is
small (a handful of systems and reports), so filtering happens in Python
instead of a SQL query over JSON1 — simpler, and enough for the current scale.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from .core.clock import Clock, SystemClock
from .domain.enums import TERMINAL_RUN_STATUSES, TriggerType
from .domain.models import Process, Run
from .workflows.profiles import ScheduleProfile

logger = logging.getLogger("smartops.scheduler")


def is_due(schedule: ScheduleProfile, last_run_at: datetime | None, now: datetime) -> bool:
    """Decide whether a (system, report) pair is due per its schedule, with no I/O involved."""
    if schedule.every_seconds is not None:
        if last_run_at is None:
            return True
        return (now - last_run_at).total_seconds() >= schedule.every_seconds

    if schedule.daily_at:
        hour, minute = (int(part) for part in schedule.daily_at.split(":"))
        local_now = now.astimezone()
        slot = local_now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if local_now < slot:
            return False
        if last_run_at is None:
            return True
        return last_run_at.astimezone() < slot

    return False


class Scheduler:
    """Checks the system definitions on every tick and creates the due collect.report runs."""

    def __init__(self, services: Any, *, clock: Clock | None = None, lookback: int = 500) -> None:
        self.services = services
        self.clock = clock or SystemClock()
        self.lookback = lookback

    def tick(self, *, now: datetime | None = None) -> list[Run]:
        """One scheduling pass over both kinds of scheduled work.

        Two sources, one scheduler: reports defined in a system's YAML, and
        automations recorded and approved in the app. Both end up as ordinary
        runs, so everything downstream — the worker, retries, incidents,
        history — stays identical no matter which one produced the run.
        """
        moment = now or self.clock.now()
        created: list[Run] = []
        created.extend(self._tick_reports(moment))
        created.extend(self._tick_processes(moment))
        return created

    def _tick_reports(self, moment: datetime) -> list[Run]:
        created: list[Run] = []
        for system, report in self.services.systems.iter_scheduled():
            try:
                run = self._maybe_create_run(system.key, report.key, report.schedule, moment)
            except Exception:
                logger.exception(
                    "Schedule check failed for %s/%s — the scheduler skips this pair and continues",
                    system.key,
                    report.key,
                )
                continue
            if run is not None:
                created.append(run)
        return created

    def _tick_processes(self, moment: datetime) -> list[Run]:
        """Queue any approved automation whose schedule is due.

        Only approved ones are ever visible here (repository-level filter), so
        an untested automation cannot reach the scheduler even if something
        upstream went wrong.
        """
        created: list[Run] = []
        for process in self.services.processes.scheduled():
            try:
                run = self._maybe_create_process_run(process, moment)
            except Exception:
                logger.exception(
                    "Schedule check failed for automation %s — the scheduler continues",
                    process.id,
                )
                continue
            if run is not None:
                created.append(run)
        return created

    def _maybe_create_process_run(self, process: Process, now: datetime) -> Run | None:
        schedule = ScheduleProfile(
            daily_at=process.schedule_daily_at,
            every_seconds=process.schedule_every_seconds,
            enabled=process.schedule_enabled,
        )
        recent = self.services.runs.list(workflow_key="process.replay", limit=self.lookback)
        process_runs = [r for r in recent if r.params.get("process_id") == process.id]
        if any(r.status not in TERMINAL_RUN_STATUSES for r in process_runs):
            return None  # still running from last time — never stack a second copy
        last_run_at = process_runs[0].created_at if process_runs else None
        if not is_due(schedule, last_run_at, now):
            return None
        run = self.services.runner.create_run(
            "process.replay", params=process.to_run_params(), trigger=TriggerType.SCHEDULE
        )
        process.last_run_id = run.id
        self.services.processes.save(process)
        return run

    def _maybe_create_run(
        self, system_key: str, report_key: str, schedule: ScheduleProfile, now: datetime
    ) -> Run | None:
        recent = self.services.runs.list(workflow_key="collect.report", limit=self.lookback)
        pair_runs = [
            r
            for r in recent
            if r.params.get("system") == system_key and r.params.get("report") == report_key
        ]
        if any(r.status not in TERMINAL_RUN_STATUSES for r in pair_runs):
            return None  # an earlier run for the same pair is still running or waiting — don't duplicate it

        last_run_at = pair_runs[0].created_at if pair_runs else None
        if not is_due(schedule, last_run_at, now):
            return None

        params = self.services.systems.run_params(system_key, report_key)
        return self.services.runner.create_run(
            "collect.report", params=params, trigger=TriggerType.SCHEDULE
        )
