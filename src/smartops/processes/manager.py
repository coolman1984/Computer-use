"""The lifecycle of an automation, and the gates between its stages.

The rule this module exists to enforce: **nothing runs on a schedule that has
not first proved it works.** A recording becomes a draft process, a draft must
pass a real test run against the real system, only a tested process can be
approved, and only an approved process can be run on demand or picked up by the
scheduler. Every refusal here carries a sentence a non-technical user can act
on, because these refusals are the main thing standing between them and a
broken overnight automation.
"""

from __future__ import annotations

from typing import Any

from ..core.errors import ConcurrencyError, ConfigurationError, PermanentError
from ..domain.enums import (
    TERMINAL_RUN_STATUSES,
    EventType,
    ProcessStatus,
    RunStatus,
    Severity,
    TriggerType,
)
from ..domain.models import Process
from ..recordings.converter import review_plan
from ..workflows.profiles import validate_schedule

# A process can only be edited (plan, rules, name) while it is still in one of
# these states. Once approved, a change has to go through a fresh test, so an
# approved automation always matches the thing that was actually tested.
_EDITABLE = {ProcessStatus.DRAFT, ProcessStatus.TESTED, ProcessStatus.TEST_FAILED}


class ProcessManager:
    """Creates automations from recordings and moves them through their stages."""

    def __init__(self, services: Any) -> None:
        self.services = services

    # ---------- creation ----------

    def create_from_recording(
        self,
        recording_id: str,
        *,
        name: str = "",
        report_key: str = "",
        validation_rules: dict[str, Any] | None = None,
    ) -> Process:
        """Turn a completed recording into a draft automation.

        The recording must already carry a plan (built when its draft was
        created) and that plan must pass review — otherwise the test run would
        fail deep inside the browser for a reason the user cannot decode.
        """
        recording = self.services.recordings.get(recording_id)
        if recording is None:
            raise PermanentError("Recording not found")
        if recording.deleted_at:
            raise PermanentError("This recording is in the trash. Restore it first.")

        plan = recording.automation_draft or {}
        if not plan.get("actions"):
            raise PermanentError(
                "This recording has no reviewable steps yet. Open it and choose "
                "'Build the automation plan' first."
            )
        verdict = review_plan(plan)
        if not verdict["ready"]:
            raise PermanentError(verdict["problems"][0], details={"review": verdict})

        # Confirm the system still exists before creating something that points
        # at it; a process for a deleted system can never run.
        self.services.systems.get(recording.system_key)

        resolved_report = report_key or plan.get("report_key") or "recorded_report"
        version = (
            self.services.processes.latest_version(recording.system_key, resolved_report) + 1
        )
        process = self.services.processes.create(
            name=name.strip() or recording.name,
            system_key=recording.system_key,
            report_key=resolved_report,
            recording_id=recording.id,
            plan={**plan, "report_key": resolved_report},
            validation_rules=validation_rules or _default_rules(),
            version=version,
        )
        self._emit(
            EventType.PROCESS_CREATED,
            process,
            f"Automation created from the recording '{recording.name}'",
        )
        return process

    # ---------- editing ----------

    def update(
        self,
        process_id: str,
        *,
        name: str | None = None,
        validation_rules: dict[str, Any] | None = None,
    ) -> Process:
        process = self.require(process_id)
        if process.status not in _EDITABLE:
            raise PermanentError(
                "An approved automation cannot be edited. Make a new version from its "
                "recording, test it, and approve that instead."
            )
        if name is not None:
            if not name.strip():
                raise PermanentError("The automation needs a name.")
            process.name = name.strip()
        if validation_rules is not None:
            process.validation_rules = validation_rules
        # Any edit invalidates an earlier passing test: what was proven to work
        # is no longer what would run.
        if process.status is ProcessStatus.TESTED:
            process.status = ProcessStatus.DRAFT
            process.last_test_run_id = None
        return self.services.processes.save(process)

    # ---------- the test gate ----------

    def test(self, process_id: str) -> tuple[Process, Any]:
        """Run the automation once for real, against the real system.

        A test is an ordinary run — same engine, same validation, same incident
        on failure — so "it passed the test" means exactly "it produced a valid
        file", not "it looked plausible".
        """
        process = self.require(process_id)
        if process.status is ProcessStatus.RETIRED:
            raise PermanentError("This automation is retired. Create a new one to replace it.")

        run = self.services.runner.create_run(
            "process.replay", params=process.to_run_params(), trigger=TriggerType.MANUAL
        )
        process.status = ProcessStatus.TESTING
        process.last_test_run_id = run.id
        process.error_message = None
        self.services.processes.save(process)
        self._emit(EventType.PROCESS_TEST_STARTED, process, "Test run started", run_id=run.id)

        run = self.services.runner.drive(run.id)
        return self.settle_test(process.id, run.id), run

    def settle_test(self, process_id: str, run_id: str) -> Process:
        """Record the outcome of a test run against the process.

        Kept separate from test() because the run may finish later, on the
        background worker, rather than inline.
        """
        process = self.require(process_id)
        run = self.services.runs.get(run_id)
        if run is None or process.last_test_run_id != run_id:
            return process
        if run.status is RunStatus.SUCCEEDED:
            process.status = ProcessStatus.TESTED
            process.error_message = None
            self.services.processes.save(process)
            self._emit(
                EventType.PROCESS_TESTED,
                process,
                "The test run produced a valid file. This automation can be approved.",
                run_id=run_id,
            )
        elif run.status in (RunStatus.FAILED, RunStatus.CANCELLED):
            process.status = ProcessStatus.TEST_FAILED
            process.error_message = run.error_message
            self.services.processes.save(process)
            self._emit(
                EventType.PROCESS_TEST_FAILED,
                process,
                "The test run failed, so this automation was not approved.",
                severity=Severity.WARNING,
                run_id=run_id,
            )
        return process

    # ---------- the approval gate ----------

    def approve(self, process_id: str) -> Process:
        process = self.require(process_id)
        if process.status is ProcessStatus.APPROVED:
            return process
        if process.status is not ProcessStatus.TESTED:
            raise PermanentError(
                "Run a successful test before approving this automation. Approval means "
                "it is allowed to run on its own, so it has to be proven first.",
                details={"status": process.status.value, "next": "test"},
            )
        process.status = ProcessStatus.APPROVED
        process.approved_at = self.services.clock.now()
        process.error_message = None
        self.services.processes.save(process)
        self._emit(EventType.PROCESS_APPROVED, process, "Automation approved and ready to run")
        return process

    def retire(self, process_id: str) -> Process:
        process = self.require(process_id)
        process.status = ProcessStatus.RETIRED
        process.schedule_enabled = False
        self.services.processes.save(process)
        self._emit(
            EventType.PROCESS_RETIRED,
            process,
            "Automation retired; it will not run again",
            severity=Severity.WARNING,
        )
        return process

    # ---------- running ----------

    def run(self, process_id: str, *, trigger: TriggerType = TriggerType.MANUAL) -> Any:
        """Queue an approved automation. The worker executes it; this returns immediately."""
        process = self.require(process_id)
        if not process.is_runnable:
            raise PermanentError(
                "This automation is not approved yet, so it cannot be run. Test it first, "
                "then approve it.",
                details={"status": process.status.value, "next": _next_action(process)},
            )
        active = self.active_run(process_id)
        if active is not None:
            # Two runs of one automation share a browser session and a target
            # system, and can each half-download the same report. The scheduler
            # already refused to stack them; an impatient second click on "Run it
            # now" could, and produced exactly that.
            raise ConcurrencyError(
                "This automation is already running. Wait for it to finish before starting "
                "it again.",
                details={"run_id": active.id, "status": active.status.value},
            )
        run = self.services.runner.create_run(
            "process.replay", params=process.to_run_params(), trigger=trigger
        )
        process.last_run_id = run.id
        self.services.processes.save(process)
        return run

    def active_run(self, process_id: str, *, lookback: int = 200) -> Any | None:
        """The unfinished run of this automation, if one exists.

        One place answers "is it running", so the manual path and the scheduler
        cannot disagree about it.
        """
        for run in self.services.runs.list(workflow_key="process.replay", limit=lookback):
            if run.params.get("process_id") != process_id:
                continue
            if run.status not in TERMINAL_RUN_STATUSES:
                return run
        return None

    # ---------- scheduling ----------

    def set_schedule(
        self,
        process_id: str,
        *,
        daily_at: str = "",
        every_seconds: float | None = None,
        enabled: bool = True,
    ) -> Process:
        """Attach a schedule. Only an approved automation may be scheduled."""
        process = self.require(process_id)
        if enabled and not process.is_runnable:
            raise PermanentError(
                "Only an approved automation can be scheduled. Test it and approve it first.",
                details={"status": process.status.value, "next": _next_action(process)},
            )
        validate_schedule(
            daily_at=daily_at, every_seconds=every_seconds, context=f"automation {process.name}"
        )
        if enabled and not (daily_at or every_seconds):
            raise PermanentError(
                "Choose when this automation should run: a daily time, or an interval."
            )
        process.schedule_daily_at = daily_at
        process.schedule_every_seconds = every_seconds
        process.schedule_enabled = enabled
        self.services.processes.save(process)
        self._emit(
            EventType.PROCESS_SCHEDULE_CHANGED,
            process,
            "Schedule turned on" if enabled else "Schedule turned off",
        )
        return process

    # ---------- helpers ----------

    def require(self, process_id: str) -> Process:
        process = self.services.processes.get(process_id)
        if process is None:
            raise PermanentError("Automation not found")
        return process

    def _emit(
        self,
        event: EventType,
        process: Process,
        message: str,
        severity: Severity = Severity.INFO,
        run_id: str | None = None,
    ) -> None:
        self.services.events.emit(
            event,
            severity=severity,
            run_id=run_id,
            message=message,
            payload={
                "process_id": process.id,
                "status": process.status.value,
                "system": process.system_key,
                "report": process.report_key,
            },
        )


def _next_action(process: Process) -> str:
    """The single next thing the user must do, by current stage."""
    if process.status in (ProcessStatus.DRAFT, ProcessStatus.TEST_FAILED):
        return "test"
    if process.status is ProcessStatus.TESTED:
        return "approve"
    if process.status is ProcessStatus.TESTING:
        return "wait"
    if process.status is ProcessStatus.RETIRED:
        return "recreate"
    return "run"


def _default_rules() -> dict[str, Any]:
    """Validation a downloaded file must pass before a run counts as successful.

    Deliberately minimal but never empty: a zero-byte file and a repeat of
    yesterday's file are the two failures that most often pass unnoticed, and
    both are caught here without the user configuring anything.
    """
    return {"min_size_bytes": 1, "reject_duplicate_hash": True}
