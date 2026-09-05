from __future__ import annotations

from smartops.core.errors import PermanentError, TransientError
from smartops.domain.enums import EventType, IncidentStatus, RunStatus, StepStatus
from smartops.domain.models import StepDefinition, WorkflowDefinition
from smartops.engine.contracts import StepResult


def _register(services, key, step, workflow_key="test.flow", steps=None):
    services.step_registry.add(key, step)
    definition = WorkflowDefinition(
        key=workflow_key,
        title=workflow_key,
        steps=steps or (StepDefinition(name="only", uses=key),),
    )
    services.workflows.register(definition)
    return definition


def test_selfcheck_workflow_succeeds(services) -> None:
    run = services.runner.create_run("platform.selfcheck")
    run = services.runner.execute(run.id)
    assert run.status is RunStatus.SUCCEEDED
    assert "storage_checked" in run.state
    timeline = [e.type for e in services.events.timeline(run.id)]
    assert timeline[0] is EventType.RUN_CREATED
    assert timeline[-1] is EventType.RUN_SUCCEEDED


def test_transient_failure_retries_then_succeeds(services, slept) -> None:
    calls = {"n": 0}

    def flaky(ctx):
        calls["n"] += 1
        if calls["n"] < 3:
            raise TransientError("الشبكة متقطعة")
        return StepResult.ok(value=calls["n"])

    _register(services, "test.flaky", flaky)
    run = services.runner.create_run("test.flow")
    run = services.runner.execute(run.id)

    assert run.status is RunStatus.SUCCEEDED
    assert calls["n"] == 3
    assert len(slept) == 2  # محاولتان مؤجلتان داخل نفس التشغيل
    step = services.steps.get(run.id, "only")
    assert step.status is StepStatus.SUCCEEDED and step.attempt == 3


def test_permanent_failure_opens_incident(services) -> None:
    def broken(ctx):
        raise PermanentError("تعريف التقرير خاطئ")

    _register(services, "test.broken", broken)
    run = services.runner.create_run("test.flow")
    run = services.runner.execute(run.id)

    assert run.status is RunStatus.FAILED
    assert run.error_class == "permanent"
    incidents = services.incidents.list(status=IncidentStatus.OPEN)
    assert len(incidents) == 1 and incidents[0].run_id == run.id
    assert EventType.INCIDENT_OPENED in [e.type for e in services.events.timeline(run.id)]


def test_retry_budget_is_respected(services) -> None:
    calls = {"n": 0}

    def always_failing(ctx):
        calls["n"] += 1
        raise TransientError("عطل مستمر")

    services.step_registry.add("test.always", always_failing)
    services.workflows.register(
        WorkflowDefinition(
            key="test.budget",
            title="budget",
            steps=(StepDefinition(name="only", uses="test.always", max_attempts=2),),
        )
    )
    run = services.runner.create_run("test.budget")
    run = services.runner.execute(run.id)
    assert run.status is RunStatus.FAILED
    assert calls["n"] == 2


def test_wait_is_resumable_without_replaying_finished_steps(services, clock) -> None:
    counters = {"first": 0, "second": 0}

    def first(ctx):
        counters["first"] += 1
        return StepResult.ok(ready_at="soon")

    def second(ctx):
        counters["second"] += 1
        if counters["second"] == 1:
            return StepResult.wait(600, reason="التقرير لم يجهز بعد")
        return StepResult.ok(done=True)

    services.step_registry.add("test.first", first)
    services.step_registry.add("test.second", second)
    services.workflows.register(
        WorkflowDefinition(
            key="test.wait",
            title="wait",
            steps=(
                StepDefinition(name="first", uses="test.first"),
                StepDefinition(name="second", uses="test.second"),
            ),
        )
    )

    run = services.runner.create_run("test.wait")
    run = services.runner.execute(run.id)
    assert run.status is RunStatus.WAITING and run.resume_at is not None

    # قبل حلول الموعد لا يحدث شيء
    run = services.runner.execute(run.id)
    assert run.status is RunStatus.WAITING
    assert counters == {"first": 1, "second": 1}

    clock.advance(601)
    run = services.runner.execute(run.id)
    assert run.status is RunStatus.SUCCEEDED
    assert counters == {"first": 1, "second": 2}  # الخطوة الناجحة لم تُعَد
    assert run.state["done"] is True


def test_unregistered_workflow_is_rejected(services) -> None:
    import pytest

    from smartops.core.errors import ConfigurationError

    with pytest.raises(ConfigurationError):
        services.runner.create_run("does.not.exist")
