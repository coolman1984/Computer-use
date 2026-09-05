"""Bringing the platform back to a truthful state after a crash or a restart.

A non-technical user will never run a repair command, so anything a restart
leaves half-finished has to be found and settled automatically — otherwise the
platform quietly lies: a run that says "running" but is not, an automation stuck
mid-test with all its buttons gone, a recording that claims to be capturing when
its browser died with the process.

Every case here follows the same rule: **never guess that interrupted work
succeeded.** Recovery either re-queues something that is safe to repeat, or
marks it failed with a reason the user can act on. It never marks anything done.
"""

from __future__ import annotations

import logging
from typing import Any

from .domain.enums import EventType, ProcessStatus, RunStatus, Severity
from .domain.models import Process, Run

logger = logging.getLogger("smartops.recovery")


class RecoveryService:
    """Finds work stranded by an interruption and settles it, at startup and on demand."""

    def __init__(self, services: Any) -> None:
        self.services = services
        # Set once startup recovery has run, so health and tests can tell that a
        # restart really did clear stranded work rather than merely being able to.
        self.ran_at_startup = False

    # ---------- entry point ----------

    def recover_all(self) -> dict[str, int]:
        """Every kind of stranded work, in one pass. Safe to call repeatedly."""
        summary = {
            "runs": len(self.recover_stranded_runs()),
            "processes": len(self.recover_stranded_processes()),
            "recordings": self.services.recording_manager.recover(),
        }
        self.ran_at_startup = True
        if any(summary.values()):
            logger.info("Recovered stranded work at startup: %s", summary)
        return summary

    # ---------- runs ----------

    def recover_stranded_runs(self) -> list[Run]:
        """Re-queue runs whose worker died mid-execution.

        Re-queuing rather than failing is right because the engine is resumable:
        every step that already succeeded is recorded, so the run picks up where
        it stopped instead of repeating work. The one thing that must not happen
        is leaving it RUNNING, which is neither finished nor runnable.
        """
        recovered: list[Run] = []
        for run in self.services.runs.stranded():
            run.status = RunStatus.QUEUED
            run.resume_at = None
            # Deliberately not touching started_at: the run did start, and the
            # history should say so.
            self.services.runs.update(run)
            self.services.events.emit(
                EventType.RUN_RESUMED,
                run_id=run.id,
                severity=Severity.WARNING,
                message="This run was interrupted and has been queued to continue",
                payload={"recovered": True, "workflow": run.workflow_key},
            )
            recovered.append(run)
        return recovered

    # ---------- automations ----------

    def recover_stranded_processes(self) -> list[Process]:
        """Settle automations left mid-test by an interruption.

        A test whose run finished while the server was down is settled from that
        run's real outcome. A test whose run is gone or itself stranded goes back
        to draft: not approved, not silently passed, and with its buttons back so
        the user can simply test again.
        """
        recovered: list[Process] = []
        for process in self.services.processes.list(limit=1000):
            if process.status is not ProcessStatus.TESTING:
                continue
            run = (
                self.services.runs.get(process.last_test_run_id)
                if process.last_test_run_id
                else None
            )
            if run is not None and run.status in (RunStatus.SUCCEEDED, RunStatus.FAILED):
                # The run did finish; take its verdict rather than inventing one.
                self.services.process_manager.settle_test(process.id, run.id)
                recovered.append(self.services.processes.get(process.id))
                continue

            process.status = ProcessStatus.TEST_FAILED
            process.error_message = (
                "The test was interrupted before it finished, so it did not pass. "
                "Run the test again."
            )
            self.services.processes.save(process)
            self.services.events.emit(
                EventType.PROCESS_TEST_FAILED,
                severity=Severity.WARNING,
                message="A test was interrupted and did not complete",
                payload={"process_id": process.id, "recovered": True},
            )
            recovered.append(process)
        return recovered
