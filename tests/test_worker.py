"""اختبارات S-04: العامل الخلفي — استطلاع، توازي محدود، ومنع التداخل."""

from __future__ import annotations

import threading
import time


from smartops.core.errors import ConfigurationError
from smartops.domain.enums import RunStatus
from smartops.domain.models import StepDefinition, WorkflowDefinition
from smartops.engine.contracts import StepResult
from smartops.worker import Worker


def _wait_until(predicate, *, timeout: float = 5.0, interval: float = 0.02) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


def test_worker_processes_a_queued_run(services) -> None:
    run = services.runner.create_run("platform.selfcheck")
    worker = Worker(services, poll_interval=0.02, max_concurrency=2)

    dispatched = worker.poll_once()

    assert dispatched == 1
    finished = services.runs.get(run.id)
    assert finished.status is RunStatus.SUCCEEDED


def test_worker_respects_max_concurrency(services) -> None:
    active = {"count": 0, "max_seen": 0}
    lock = threading.Lock()

    def slow_step(ctx):
        with lock:
            active["count"] += 1
            active["max_seen"] = max(active["max_seen"], active["count"])
        time.sleep(0.15)
        with lock:
            active["count"] -= 1
        return StepResult.ok()

    services.step_registry.add("test.slow", slow_step)
    services.workflows.register(
        WorkflowDefinition(
            key="test.slow_flow",
            title="slow",
            steps=(StepDefinition(name="only", uses="test.slow"),),
        )
    )

    run_ids = [services.runner.create_run("test.slow_flow").id for _ in range(5)]
    worker = Worker(services, poll_interval=0.02, max_concurrency=2)

    worker.start()
    try:
        all_done = _wait_until(
            lambda: all(
                services.runs.get(rid).status is RunStatus.SUCCEEDED for rid in run_ids
            ),
            timeout=10.0,
        )
    finally:
        worker.stop()
        worker.join(timeout=5)

    assert all_done, "لازم كل التشغيلات تخلص خلال المهلة"
    assert active["max_seen"] <= 2
    assert active["max_seen"] >= 1  # على الأقل اشتغل تشغيل واحد بالفعل


def test_concurrent_execute_calls_do_not_double_run_same_step(services) -> None:
    """يثبت أن تشغيلين (نداءين متزامنين) على نفس run_id لا يتداخلان أبدًا."""
    calls = {"n": 0}
    barrier = threading.Barrier(2, timeout=3)

    def counting_step(ctx):
        time.sleep(0.05)  # يوسّع فرصة التزامن الحقيقي قبل الإفراج عن القفل
        calls["n"] += 1
        return StepResult.ok()

    services.step_registry.add("test.counting", counting_step)
    services.workflows.register(
        WorkflowDefinition(
            key="test.race",
            title="race",
            steps=(StepDefinition(name="only", uses="test.counting"),),
        )
    )
    run = services.runner.create_run("test.race")

    results: list = []
    results_lock = threading.Lock()

    def call_execute() -> None:
        barrier.wait()
        result = services.runner.execute(run.id)
        with results_lock:
            results.append(result)

    threads = [threading.Thread(target=call_execute) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)

    assert calls["n"] == 1, "خطوة الخطوة لازم تتنفذ مرة واحدة بالظبط رغم النداءين المتزامنين"
    assert any(r.status is RunStatus.SUCCEEDED for r in results)


def test_worker_does_not_dispatch_more_than_max_concurrency_at_once(services) -> None:
    """حتى مع دفعة واحدة من عدة تشغيلات مستحقة، لا يُرسل أكتر من الحد المسموح."""
    for _ in range(5):
        services.runner.create_run("platform.selfcheck")

    worker = Worker(services, poll_interval=0.02, max_concurrency=2)
    dispatched = worker.poll_once()

    assert dispatched == 2


def test_worker_stop_is_clean_and_prompt(services) -> None:
    services.runner.create_run("platform.selfcheck")
    worker = Worker(services, poll_interval=2.0, max_concurrency=1)

    worker.start()
    _wait_until(lambda: worker.is_running(), timeout=1.0)

    started = time.monotonic()
    worker.stop()
    worker.join(timeout=2.0)
    elapsed = time.monotonic() - started

    assert not worker.is_running()
    assert elapsed < 1.5  # الإيقاف ما ينتظرش دورة poll_interval كاملة (2 ثانية)


def test_worker_survives_a_single_run_execute_failure(services) -> None:
    """خطأ غير متوقع أثناء execute تشغيل واحد ما يوقفش العامل عن معالجة الباقي."""
    good_run = services.runner.create_run("platform.selfcheck")
    bad_run = services.runs.create("does.not.exist")  # مسجّل في DB لكن سير عمله غير معرّف

    errors: list[tuple[str, BaseException]] = []
    worker = Worker(
        services,
        poll_interval=0.02,
        max_concurrency=2,
        on_error=lambda run_id, exc: errors.append((run_id, exc)),
    )

    worker.start()
    try:
        ok = _wait_until(
            lambda: services.runs.get(good_run.id).status is RunStatus.SUCCEEDED, timeout=5.0
        )
    finally:
        worker.stop()
        worker.join(timeout=5)

    assert ok
    assert len(errors) == 1
    assert errors[0][0] == bad_run.id
    assert isinstance(errors[0][1], ConfigurationError)
