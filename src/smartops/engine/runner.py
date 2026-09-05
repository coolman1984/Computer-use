"""محرك سير العمل: يشغّل الخطوات كحالة محفوظة، فيمكن استكمال أي تشغيل بعد أي توقف."""

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
from .contracts import StepContext, StepOutcome, StepResult
from .retry import policy_for

# تأخير أقصر من هذا يُنتظر داخل نفس التشغيل، والأطول يُؤجل ويستكمل لاحقًا.
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

    # ---------- إنشاء ----------

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
            message=f"تم إنشاء تشغيل لسير العمل {definition.title or definition.key}",
            payload={"params": run.params, "workflow": definition.key},
        )
        return run

    # ---------- تنفيذ ----------

    def execute(self, run_id: str, *, force: bool = False) -> Run:
        """ينفّذ ما يمكن تنفيذه الآن ثم يعيد حالة التشغيل المحدثة."""
        run = self.services.runs.get(run_id)
        if run is None:
            raise SmartOpsError(f"تشغيل غير موجود: {run_id}", error_class=ErrorClass.PERMANENT)
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

    def drive(self, run_id: str, *, max_cycles: int = 20) -> Run:
        """يستكمل التشغيل حتى ينتهي أو ينتظر وقتًا مستقبليًا. للتطوير والجدولة."""
        run = self.execute(run_id)
        cycles = 1
        while run.status not in TERMINAL_RUN_STATUSES and cycles < max_cycles:
            if run.resume_at and run.resume_at > self.clock.now():
                break
            run = self.execute(run_id)
            cycles += 1
        return run

    # ---------- التفاصيل ----------

    def _execute_locked(self, run: Run, definition: WorkflowDefinition) -> Run:
        first_start = run.started_at is None
        run.status = RunStatus.RUNNING
        run.resume_at = None
        if first_start:
            run.started_at = self.clock.now()
        self.services.runs.update(run)
        self.services.events.emit(
            EventType.RUN_STARTED if first_start else EventType.RUN_RESUMED,
            run_id=run.id,
            message=f"بدء تنفيذ {definition.key}" if first_start else "استكمال التنفيذ",
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
            message="اكتمل التشغيل بنجاح",
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
        """يعيد None لو نجحت الخطوة، أو حالة التشغيل النهائية لو توقف التنفيذ."""
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
                message=f"بدء الخطوة {step_def.title or step_def.name}",
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
            except Exception as exc:  # لا شيء يخرج غير مصنّف
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
                    message=f"نجحت الخطوة {step_def.title or step_def.name}",
                    payload={"attempt": attempt_no, "output_keys": sorted(result.output or {})},
                )
                return None

            if result.outcome is StepOutcome.WAIT:
                # الانتظار المقصود لا يستهلك محاولة.
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
                    message=result.reason or "انتظار مؤقت",
                    payload={"wait_seconds": result.wait_seconds},
                )
                return run

            error = result.error or SmartOpsError("فشل غير موصوف")
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
                message=f"إعادة محاولة بعد {round(delay, 1)} ثانية",
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
            message=f"فشلت الخطوة {step_def.title or step_def.name}",
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
            message="توقف التشغيل بسبب فشل خطوة",
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
            title=f"فشل {definition.title or definition.key} عند {step_def.name}",
            severity=Severity.ERROR,
            run_id=run.id,
            signature=signature,
        )
        self.services.events.emit(
            EventType.INCIDENT_OPENED,
            run_id=run.id,
            step_name=step_def.name,
            severity=Severity.ERROR,
            message="تم فتح حادثة للتشخيص",
            payload={"incident_id": incident.id, "signature": signature},
        )

    def _context_emitter(self, run_id: str, step_name: str) -> Callable[..., Any]:
        def emit(event_type: EventType, **kwargs: Any) -> Any:
            kwargs.setdefault("step_name", step_name)
            return self.services.events.emit(event_type, run_id=run_id, **kwargs)

        return emit
