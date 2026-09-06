"""The workflow engine: runs steps as saved state, so any run can resume after any interruption."""

from __future__ import annotations

import time
from datetime import timedelta
from typing import Any, Callable

from ..core.clock import Clock, SystemClock
from ..core.errors import ErrorClass, SmartOpsError, wrap_error
from ..core.ids import new_id
from ..domain.enums import (
    TERMINAL_RUN_STATUSES,
    EventType,
    RunStatus,
    Severity,
    StepStatus,
    TriggerType,
)
from ..domain.models import Run, StepDefinition, StepRecord, WorkflowDefinition
from ..recordings.converter import review_plan
from .contracts import StepContext, StepOutcome, StepResult
from .retry import policy_for

# A delay shorter than this is waited out inline within the same run; a longer one is deferred and resumed later.
INLINE_RETRY_CEILING_SECONDS = 30.0


class WorkflowRunner:
    def __init__(
        self,
        services: Any,
        *,
        clock: Clock | None = None,
        sleeper: Callable[[float], None] = time.sleep,
        inline_retry_ceiling: float = INLINE_RETRY_CEILING_SECONDS,
    ) -> None:
        self.services = services
        self.clock = clock or SystemClock()
        self.sleeper = sleeper
        self.inline_retry_ceiling = inline_retry_ceiling

    # ---------- creation ----------

    def create_run(
        self,
        workflow_key: str,
        *,
        params: dict[str, Any] | None = None,
        trigger: TriggerType = TriggerType.MANUAL,
    ) -> Run:
        definition = self.services.workflows.get(workflow_key)
        run = self.services.runs.create(
            workflow_key=definition.key,
            workflow_version=definition.version,
            params=params or {},
            trigger=trigger,
        )
        self.services.events.emit(
            EventType.RUN_CREATED,
            run_id=run.id,
            message=f"Run created for workflow {definition.title or definition.key}",
            payload={"params": run.params, "workflow": definition.key},
        )
        return run

    # ---------- execution ----------

    def execute(self, run_id: str, *, force: bool = False) -> Run:
        """Execute whatever can run right now, then return the updated run state."""
        run = self.services.runs.get(run_id)
        if run is None:
            raise SmartOpsError(f"Run not found: {run_id}", error_class=ErrorClass.PERMANENT)
        if run.status in TERMINAL_RUN_STATUSES:
            return run
        now = self.clock.now()
        if not force and run.resume_at and run.resume_at > now:
            return run

        token = new_id("lock")
        if not self.services.runs.claim(run.id, token):
            return run
        try:
            try:
                definition: WorkflowDefinition = self.services.workflows.get(run.workflow_key)
            except SmartOpsError as exc:
                # A stale/invalid queued run must become terminal; otherwise a
                # worker would redispatch it forever after every poll.
                run.status = RunStatus.FAILED
                run.finished_at = self.clock.now()
                run.error_class = exc.error_class.value
                run.error_message = exc.message
                self.services.runs.update(run)
                self.services.events.emit(
                    EventType.RUN_FAILED,
                    run_id=run.id,
                    severity=Severity.ERROR,
                    message=exc.message,
                    payload={"error_class": exc.error_class.value},
                )
                raise
            return self._execute_locked(run, definition)
        finally:
            self.services.runs.release(run.id, token)

    def retry(self, run_id: str) -> Run:
        """Start a genuinely new attempt at what a failed run was trying to do.

        The retry button used to call drive() on the failed run itself. execute()
        returns immediately for anything terminal, and FAILED is terminal — so
        the button did nothing at all, silently, forever. Even had it re-entered,
        the engine skips steps already recorded as succeeded, so a "retry" would
        have resumed a finished run rather than repeating the work.

        A retry is therefore a new run with the same workflow and parameters,
        carrying a link back to the attempt it replaces. That keeps every attempt
        in the history as its own record with its own files, which is what makes
        "it worked on the third try" a thing you can actually see.
        """
        original = self.services.runs.get(run_id)
        if original is None:
            raise SmartOpsError(f"Run not found: {run_id}", error_class=ErrorClass.PERMANENT)
        if original.status is RunStatus.SUCCEEDED:
            raise SmartOpsError(
                "This run already succeeded, so there is nothing to try again. "
                "Run the automation again if you want a fresh result.",
                error_class=ErrorClass.PERMANENT,
            )
        if original.status not in TERMINAL_RUN_STATUSES:
            raise SmartOpsError(
                "This run has not finished yet. Wait for it, or let it fail, before trying again.",
                error_class=ErrorClass.PERMANENT,
            )
        if self._contains_unsafe_replay(original):
            raise SmartOpsError(
                "This task contains an unsafe step whose effect may already have happened. "
                "SmartOps will not repeat it blindly; review the result before starting a new run.",
                error_class=ErrorClass.PERMANENT,
            )

        params = {**original.params, "retry_of": original.id}
        retried = self.create_run(original.workflow_key, params=params, trigger=TriggerType.RETRY)
        self.services.events.emit(
            EventType.RUN_CREATED,
            run_id=retried.id,
            message="New attempt created after a failed run",
            payload={"retry_of": original.id},
        )
        return retried

    @staticmethod
    def _contains_unsafe_replay(run: Run) -> bool:
        """Whether repeating this browser task could duplicate an external effect."""
        if run.workflow_key != "process.replay":
            return False
        actions = ((run.params or {}).get("plan") or {}).get("actions") or []
        return any(
            not bool((action.get("retry") or {}).get("safe_to_repeat", False))
            for action in actions
        )

    def drive(self, run_id: str, *, max_cycles: int = 20) -> Run:
        """Resume the run until it finishes or starts waiting on a future time. For CLI use and scheduling."""
        run = self.execute(run_id)
        cycles = 1
        while run.status not in TERMINAL_RUN_STATUSES and cycles < max_cycles:
            if run.resume_at and run.resume_at > self.clock.now():
                break
            run = self.execute(run_id)
            cycles += 1
        return run

    # ---------- internals ----------

    def _execute_locked(self, run: Run, definition: WorkflowDefinition) -> Run:
        # A queued run can survive a restart and predate the API/process gates.
        # Never let a worker or the generic start endpoint turn it into a back
        # door around review and approval.
        if definition.key == "process.replay":
            verdict = review_plan((run.params or {}).get("plan") or {})
            if not verdict["ready"]:
                run.status = RunStatus.FAILED
                run.finished_at = self.clock.now()
                run.error_class = ErrorClass.PERMANENT.value
                run.error_message = verdict["problems"][0]
                self.services.runs.update(run)
                self.services.events.emit(
                    EventType.RUN_FAILED,
                    run_id=run.id,
                    severity=Severity.ERROR,
                    message="Stored replay run rejected by the review gate",
                    payload={"review": verdict},
                )
                return run
        if (
            definition.key == "collect.report"
            and self.services.settings.app.environment == "production"
            and self.services.settings.safety.allow_development_features is not True
        ):
            run.status = RunStatus.FAILED
            run.finished_at = self.clock.now()
            run.error_class = ErrorClass.PERMANENT.value
            run.error_message = (
                "Legacy direct collection is disabled in production. Use an approved recorded process."
            )
            self.services.runs.update(run)
            self.services.events.emit(
                EventType.RUN_FAILED,
                run_id=run.id,
                severity=Severity.ERROR,
                message="Stored legacy collection run rejected in production",
                payload={"workflow": definition.key},
            )
            return run
        first_start = run.started_at is None
        run.status = RunStatus.RUNNING
        run.resume_at = None
        if first_start:
            run.started_at = self.clock.now()
        self.services.runs.update(run)
        self.services.events.emit(
            EventType.RUN_STARTED if first_start else EventType.RUN_RESUMED,
            run_id=run.id,
            message=f"Starting execution of {definition.key}" if first_start else "Resuming execution",
        )

        for seq, step_def in enumerate(definition.steps):
            record = self.services.steps.get(run.id, step_def.name)
            if record and record.status == StepStatus.SUCCEEDED:
                run.state.update(record.output or {})
                continue
            outcome = self._run_step(run, definition, step_def, seq, record)
            if outcome is not None:
                return outcome

        run.status = RunStatus.SUCCEEDED
        run.finished_at = self.clock.now()
        run.error_class = None
        run.error_message = None
        self.services.runs.update(run)
        self.services.events.emit(
            EventType.RUN_SUCCEEDED,
            run_id=run.id,
            message="Run completed successfully",
            payload={"steps": len(definition.steps)},
        )
        return run

    def _run_step(
        self,
        run: Run,
        definition: WorkflowDefinition,
        step_def: StepDefinition,
        seq: int,
        record: StepRecord | None,
    ) -> Run | None:
        """Return None if the step succeeded, or the final run state if execution stopped."""
        step_callable = self.services.step_registry.get(step_def.uses)
        attempts_used = record.attempt if record else 0

        while True:
            attempt_no = attempts_used + 1
            started_at = self.clock.now()
            self.services.steps.save(
                StepRecord(
                    run_id=run.id,
                    name=step_def.name,
                    seq=seq,
                    status=StepStatus.RUNNING,
                    attempt=attempt_no,
                    input=step_def.params,
                    started_at=started_at,
                )
            )
            self.services.events.emit(
                EventType.STEP_STARTED,
                run_id=run.id,
                step_name=step_def.name,
                message=f"Starting step {step_def.title or step_def.name}",
                payload={"attempt": attempt_no},
            )

            ctx = StepContext(
                run_id=run.id,
                step_name=step_def.name,
                attempt=attempt_no,
                params={**run.params, **step_def.params},
                state=run.state,
                services=self.services,
                emit=self._context_emitter(run.id, step_def.name),
            )
            try:
                result = step_callable(ctx)
            except Exception as exc:  # nothing leaves this step unclassified
                result = StepResult.fail(wrap_error(exc))
            if not isinstance(result, StepResult):
                result = StepResult.ok()

            if result.outcome is StepOutcome.OK:
                run.state.update(result.output or {})
                self.services.steps.save(
                    StepRecord(
                        run_id=run.id,
                        name=step_def.name,
                        seq=seq,
                        status=StepStatus.SUCCEEDED,
                        attempt=attempt_no,
                        input=step_def.params,
                        output=result.output,
                        started_at=started_at,
                        finished_at=self.clock.now(),
                    )
                )
                self.services.runs.update(run)
                self.services.events.emit(
                    EventType.STEP_SUCCEEDED,
                    run_id=run.id,
                    step_name=step_def.name,
                    message=f"Step succeeded: {step_def.title or step_def.name}",
                    payload={"attempt": attempt_no, "output_keys": sorted(result.output or {})},
                )
                return None

            if result.outcome is StepOutcome.WAIT:
                # An intentional wait does not consume an attempt.
                self.services.steps.save(
                    StepRecord(
                        run_id=run.id,
                        name=step_def.name,
                        seq=seq,
                        status=StepStatus.WAITING,
                        attempt=attempts_used,
                        input=step_def.params,
                        started_at=started_at,
                    )
                )
                run.status = RunStatus.WAITING
                run.resume_at = self.clock.now() + timedelta(seconds=result.wait_seconds)
                self.services.runs.update(run)
                self.services.events.emit(
                    EventType.RUN_WAITING,
                    run_id=run.id,
                    step_name=step_def.name,
                    message=result.reason or "Temporary wait",
                    payload={"wait_seconds": result.wait_seconds},
                )
                return run

            error = result.error or SmartOpsError("Unspecified failure")
            attempts_used = attempt_no
            policy = policy_for(error.error_class, max_attempts=step_def.max_attempts)
            # Authentication failures need operator intervention; retrying a
            # password automatically can lock the target account.
            can_retry = error.retryable and not error.details.get("no_retry", False) and attempts_used < policy.max_attempts
            if not can_retry:
                return self._fail_run(run, definition, step_def, seq, attempts_used, started_at, error)

            delay = policy.delay_for(attempts_used)
            self.services.steps.save(
                StepRecord(
                    run_id=run.id,
                    name=step_def.name,
                    seq=seq,
                    status=StepStatus.RETRYING,
                    attempt=attempts_used,
                    input=step_def.params,
                    started_at=started_at,
                    error_class=error.error_class.value,
                    error_message=error.message,
                )
            )
            self.services.events.emit(
                EventType.STEP_RETRY_SCHEDULED,
                run_id=run.id,
                step_name=step_def.name,
                severity=Severity.WARNING,
                message=f"Retrying after {round(delay, 1)} seconds",
                payload={"attempt": attempts_used, "delay": delay, **error.to_dict()},
            )
            if delay <= self.inline_retry_ceiling:
                self.sleeper(delay)
                continue
            run.status = RunStatus.RETRYING
            run.resume_at = self.clock.now() + timedelta(seconds=delay)
            run.error_class = error.error_class.value
            run.error_message = error.message
            self.services.runs.update(run)
            return run

    def _fail_run(
        self,
        run: Run,
        definition: WorkflowDefinition,
        step_def: StepDefinition,
        seq: int,
        attempts_used: int,
        started_at: Any,
        error: SmartOpsError,
    ) -> Run:
        self.services.steps.save(
            StepRecord(
                run_id=run.id,
                name=step_def.name,
                seq=seq,
                status=StepStatus.FAILED,
                attempt=attempts_used,
                input=step_def.params,
                started_at=started_at,
                finished_at=self.clock.now(),
                error_class=error.error_class.value,
                error_message=error.message,
            )
        )
        self.services.events.emit(
            EventType.STEP_FAILED,
            run_id=run.id,
            step_name=step_def.name,
            severity=Severity.ERROR,
            message=f"Step failed: {step_def.title or step_def.name}",
            payload={"attempt": attempts_used, **error.to_dict()},
        )
        run.status = RunStatus.FAILED
        run.finished_at = self.clock.now()
        run.resume_at = None
        run.error_class = error.error_class.value
        run.error_message = error.message
        self.services.runs.update(run)
        self.services.events.emit(
            EventType.RUN_FAILED,
            run_id=run.id,
            step_name=step_def.name,
            severity=Severity.ERROR,
            message="Run stopped due to a step failure",
            payload=error.to_dict(),
        )
        self._open_incident(run, definition, step_def, error)
        return run

    def _open_incident(
        self,
        run: Run,
        definition: WorkflowDefinition,
        step_def: StepDefinition,
        error: SmartOpsError,
    ) -> None:
        incidents = getattr(self.services, "incidents", None)
        if incidents is None:
            return
        signature = f"{definition.key}:{step_def.name}:{error.error_class.value}:{error.code}"
        incident = incidents.open(
            title=f"{definition.title or definition.key} failed at {step_def.name}",
            severity=Severity.ERROR,
            run_id=run.id,
            signature=signature,
        )
        self.services.events.emit(
            EventType.INCIDENT_OPENED,
            run_id=run.id,
            step_name=step_def.name,
            severity=Severity.ERROR,
            message="Incident opened for diagnosis",
            payload={"incident_id": incident.id, "signature": signature},
        )

    def _context_emitter(self, run_id: str, step_name: str) -> Callable[..., Any]:
        def emit(event_type: EventType, **kwargs: Any) -> Any:
            kwargs.setdefault("step_name", step_name)
            return self.services.events.emit(event_type, run_id=run_id, **kwargs)

        return emit
