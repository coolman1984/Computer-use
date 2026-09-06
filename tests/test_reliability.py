"""Reliability of the journey: results, sessions, recovery, and supervision.

Each test here started life as a proof that a specific defect existed. They are
the regression wall for the failures that matter most, because every one of them
is silent — the platform reported success while losing a file, accepting an
empty session, or leaving work stranded.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from smartops.checks import ConnectionCheck
from smartops.domain.enums import ExtractionLayer, ProcessStatus, RecordingStatus, RunStatus
from smartops.domain.models import RecordingStep
from smartops.ports.browser import ExtractionResult
from smartops.ports.validation import ValidationRules


# ---------- helpers ----------


class CountingBrowser:
    """Writes a file with a fixed name every time, the way a real export does.

    A real site serves "daily_sales.csv" on every download; the platform is what
    has to keep two runs' copies apart. `fresh_each_time` decides whether the
    source has new data (the normal case) or is handing back the identical file
    again (the stale-source case the duplicate check exists for).
    """

    def __init__(self, payload: bytes = b"date,amount\n2026-01-01,1\n", *, fresh_each_time: bool = True) -> None:
        self.payload = payload
        self.fresh_each_time = fresh_each_time
        self.written: list[Path] = []

    def replay(self, request):
        target = Path(request.destination_dir) / "daily_sales.csv"
        target.parent.mkdir(parents=True, exist_ok=True)
        body = self.payload
        if self.fresh_each_time:
            body = self.payload + f"row-{len(self.written)}\n".encode()
        target.write_bytes(body)
        self.written.append(target)
        return ExtractionResult(
            ok=True, layer_used=ExtractionLayer.DOM, file_path=target,
            original_name=target.name, size_bytes=target.stat().st_size,
        )

    extract = replay

    def capture_evidence(self, run_id: str) -> dict:
        return {}


def _system(services, key="erp", mode="none"):
    auth = {"mode": mode}
    if mode != "none":
        auth.update({"login_url": "https://erp.example.local/login", "logged_in_selector": "#u"})
    services.systems.save({
        "key": key, "name": "ERP", "auth": auth,
        "reports": [{"key": "daily", "title": "Daily",
                     "url": "https://erp.example.local/daily", "download_selector": "#export"}],
    })
    services.connection_checks.record(
        key, ConnectionCheck(ok=True, reachable=True, signed_in=True, summary="ok"), at="2026-01-01T00:00:00Z"
    )


def _approved_process(services, key="erp", *, safe_to_repeat: bool = False):
    record = services.recording_manager.create("Daily export", key)
    services.recordings.save_step(RecordingStep(
        record.id, 1, "click", selector="#export",
        page_url_redacted="https://erp.example.local/daily",
        retry={"max_attempts": 3 if safe_to_repeat else 1,
               "safe_to_repeat": safe_to_repeat},
    ))
    services.recordings.save_step(RecordingStep(record.id, 2, "download", download_ref="downloads/x.csv"))
    record.status = RecordingStatus.COMPLETED
    services.recordings.save(record)
    services.recording_manager.draft(record.id, "daily")
    process = services.process_manager.create_from_recording(record.id)
    services.process_manager.test(process.id)
    return services.process_manager.approve(process.id)


# ---------- 1. results are never overwritten ----------


def test_two_runs_on_the_same_day_keep_both_files(services) -> None:
    """The defect: output was addressed by date, so the second run of the day
    silently wrote over the first one's file while both database rows still
    pointed at that one path. A whole day's earlier result was gone.
    """
    services.browser = CountingBrowser()
    _system(services)
    process = _approved_process(services)

    first = services.runner.drive(services.process_manager.run(process.id).id)
    second = services.runner.drive(services.process_manager.run(process.id).id)
    assert first.status is RunStatus.SUCCEEDED and second.status is RunStatus.SUCCEEDED

    paths = {}
    for run in (first, second):
        artifacts = services.files.list(run_id=run.id)
        assert len(artifacts) == 1, "each run registers exactly its own file"
        paths[run.id] = Path(artifacts[0].path)

    assert len(set(paths.values())) == 2, "two runs must not share one path on disk"
    for run_id, path in paths.items():
        assert path.is_file(), f"{path} was overwritten or removed by the other run"
    # Both files still hold their own run's content, not the last writer's.
    assert paths[first.id].read_bytes() != paths[second.id].read_bytes()


def test_a_run_directory_carries_its_run_id(services) -> None:
    """Given a file, an operator must be able to tell which run produced it."""
    services.browser = CountingBrowser()
    _system(services)
    process = _approved_process(services)
    run = services.runner.drive(services.process_manager.run(process.id).id)

    artifact = [f for f in services.files.list(run_id=run.id)][0]
    assert run.id in Path(artifact.path).parts or run.id in str(artifact.path)


# ---------- 2. validation catches what actually goes wrong ----------


def test_an_error_page_saved_as_a_csv_is_rejected(services, tmp_path) -> None:
    """The defect: a portal that answers a download with an HTML session-expired
    page produced a file with a .csv name, a healthy size and a unique hash — so
    it passed every check and was reported as a valid result.
    """
    page = tmp_path / "daily_sales.csv"
    page.write_bytes(
        b"<!DOCTYPE html><html><head><title>Session expired</title></head>"
        b"<body><h1>Please sign in again</h1></body></html>"
    )

    report = services.validator.validate(page, ValidationRules(min_size_bytes=1))

    assert not report.passed
    assert any("web page" in f.lower() or "html" in f.lower() for f in report.failures)


def test_a_csv_with_a_header_and_no_rows_is_rejected_when_rows_are_required(services, tmp_path) -> None:
    empty = tmp_path / "daily.csv"
    empty.write_bytes(b"date,amount\n")

    report = services.validator.validate(empty, ValidationRules(min_rows=1))

    assert not report.passed


def test_a_corrupt_excel_file_is_rejected(services, tmp_path) -> None:
    broken = tmp_path / "daily.xlsx"
    broken.write_bytes(b"this is not a zip archive at all")

    report = services.validator.validate(broken, ValidationRules(min_size_bytes=1))

    assert not report.passed


def test_the_same_file_downloaded_twice_is_reported_as_a_repeat(services, tmp_path) -> None:
    """A source system that has not refreshed yet hands back yesterday's file.
    That is not a new result, and treating it as one corrupts the history.
    """
    services.browser = CountingBrowser(fresh_each_time=False)
    _system(services)
    process = _approved_process(services)

    services.runner.drive(services.process_manager.run(process.id).id)
    second = services.runner.drive(services.process_manager.run(process.id).id)

    assert second.status is RunStatus.FAILED
    assert "duplicate" in (second.error_message or "").lower() or "identical" in (second.error_message or "").lower()


# ---------- 3. a session file is not a session ----------


def test_an_empty_session_file_does_not_count_as_signed_in(services) -> None:
    """The defect: the gate checked only that a file existed. An empty or expired
    storage-state passed it, so the platform let the user record and schedule
    against a system it could not actually reach.
    """
    from smartops.sessions import session_is_usable, session_path

    _system(services, mode="session")
    path = session_path(services.settings.storage.sessions_dir, "erp")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"cookies": [], "origins": []}), encoding="utf-8")

    assert not session_is_usable(services.settings.storage.sessions_dir, "erp")


def test_a_session_with_a_live_cookie_counts_as_signed_in(services) -> None:
    from smartops.sessions import session_is_usable, session_path

    _system(services, mode="session")
    path = session_path(services.settings.storage.sessions_dir, "erp")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "cookies": [{"name": "sid", "value": "abc", "domain": "erp.example.local",
                     "path": "/", "expires": 4102444800}],
        "origins": [],
    }), encoding="utf-8")

    assert session_is_usable(services.settings.storage.sessions_dir, "erp")


def test_a_session_whose_cookies_have_all_expired_is_rejected(services) -> None:
    from smartops.sessions import session_is_usable, session_path

    _system(services, mode="session")
    path = session_path(services.settings.storage.sessions_dir, "erp")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "cookies": [{"name": "sid", "value": "abc", "domain": "erp.example.local",
                     "path": "/", "expires": 1000000}],  # long past
        "origins": [],
    }), encoding="utf-8")

    assert not session_is_usable(services.settings.storage.sessions_dir, "erp")


def test_the_signin_gate_uses_session_validity_not_file_presence(services) -> None:
    from smartops.core.errors import SmartOpsError
    from smartops.journey import require_signed_in
    from smartops.sessions import session_path

    _system(services, mode="session")
    path = session_path(services.settings.storage.sessions_dir, "erp")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"cookies": [], "origins": []}), encoding="utf-8")

    with pytest.raises(SmartOpsError):
        require_signed_in(services, "erp")


# ---------- 4. retrying actually retries ----------


def test_retrying_a_failed_run_makes_a_real_new_attempt(services) -> None:
    """The defect: retry called drive() on the failed run, but execute() returns
    immediately for any terminal run — so the button did nothing at all, forever,
    with no error to explain it.
    """
    class Flaky:
        """A site that is down until the test says otherwise.

        `failing` stays on for every attempt, including the engine's own inline
        retries — otherwise the engine rescues the run and there is no failed run
        left to retry, which is the thing under test here.
        """

        def __init__(self) -> None:
            self.failing = False
            self.served = 0

        def replay(self, request):
            if self.failing:
                return ExtractionResult(ok=False, layer_used=ExtractionLayer.DOM,
                                        message="The export button was not there.")
            self.served += 1
            target = Path(request.destination_dir) / "daily_sales.csv"
            target.parent.mkdir(parents=True, exist_ok=True)
            # Genuinely new data each time, so the duplicate check (rightly) does
            # not fire and this test measures the retry, not the validator.
            target.write_bytes(f"date,amount\n2026-01-01,{self.served}\n".encode())
            return ExtractionResult(ok=True, layer_used=ExtractionLayer.DOM, file_path=target,
                                    original_name=target.name, size_bytes=target.stat().st_size)

        extract = replay

        def capture_evidence(self, run_id: str) -> dict:
            return {}

    browser = Flaky()
    services.browser = browser
    _system(services)
    # This synthetic task only downloads a report and has no external side
    # effect, so the fixture declares it repeatable. Unsafe plans are covered by
    # the separate refusal test and must never reach this retry path.
    process = _approved_process(services, safe_to_repeat=True)

    browser.failing = True
    failed = services.runner.drive(services.process_manager.run(process.id).id)
    assert failed.status is RunStatus.FAILED

    browser.failing = False  # the site is back
    retried = services.runner.retry(failed.id)

    assert retried.id != failed.id, "a retry must be a new attempt, not the same record"
    assert retried.params.get("retry_of") == failed.id, "the new attempt must point back"
    driven = services.runner.drive(retried.id)
    assert driven.status is RunStatus.SUCCEEDED
    assert services.files.list(run_id=retried.id), "the retry must produce its own file"
    # The failed attempt stays in the history as its own record.
    assert services.runs.get(failed.id).status is RunStatus.FAILED


def test_retrying_a_successful_run_is_refused(services) -> None:
    from smartops.core.errors import SmartOpsError

    services.browser = CountingBrowser()
    _system(services)
    process = _approved_process(services)
    run = services.runner.drive(services.process_manager.run(process.id).id)

    with pytest.raises(SmartOpsError):
        services.runner.retry(run.id)


# ---------- 5. nothing is stranded by a restart ----------


def test_a_run_left_running_by_a_crash_is_recovered(services) -> None:
    """The defect: due() only ever returned queued/waiting/retrying runs, so a
    run interrupted mid-execution stayed RUNNING forever — never finished, never
    failed, never picked up again, and counted as "active" on the overview.
    """
    services.browser = CountingBrowser()
    _system(services)
    process = _approved_process(services, safe_to_repeat=True)
    run = services.process_manager.run(process.id)

    # Simulate the crash: marked running, lock long expired, never finished.
    run.status = RunStatus.RUNNING
    run.started_at = services.clock.now()
    services.runs.update(run)
    services.runs.claim(run.id, "dead-worker", lease_seconds=-1)

    recovered = services.recovery.recover_stranded_runs()

    assert run.id in [r.id for r in recovered]
    assert services.runs.get(run.id).status is RunStatus.QUEUED
    # And it now actually runs to completion.
    assert services.runner.drive(run.id).status is RunStatus.SUCCEEDED


def test_a_run_still_held_by_a_live_worker_is_left_alone(services) -> None:
    """Recovery must never yank a run out from under a worker that is still on it."""
    services.browser = CountingBrowser()
    _system(services)
    process = _approved_process(services)
    run = services.process_manager.run(process.id)
    run.status = RunStatus.RUNNING
    services.runs.update(run)
    services.runs.claim(run.id, "live-worker", lease_seconds=900)

    assert services.recovery.recover_stranded_runs() == []
    assert services.runs.get(run.id).status is RunStatus.RUNNING


def test_an_automation_left_testing_by_a_restart_is_settled(services) -> None:
    """The defect: a test started in the app and interrupted by a restart left the
    automation stuck at 'testing' with no way out — its buttons all gone.
    """
    services.browser = CountingBrowser()
    _system(services)
    process = _approved_process(services)
    process.status = ProcessStatus.TESTING
    services.processes.save(process)

    services.recovery.recover_stranded_processes()

    settled = services.processes.get(process.id)
    assert settled.status is not ProcessStatus.TESTING


def test_recovery_runs_on_startup(services) -> None:
    """It has to happen by itself; a user will not run a repair command."""
    assert hasattr(services, "recovery")
    # Services wires recovery and calls it during construction, so a restart is
    # enough to clear stranded work.
    assert services.recovery.ran_at_startup is True


# ---------- 6. no duplicate or overlapping execution ----------


def test_the_same_automation_cannot_run_twice_at_once(services) -> None:
    """Two overlapping runs against one system fight over the same session and can
    both half-download the same report.
    """
    from smartops.core.errors import SmartOpsError

    services.browser = CountingBrowser()
    _system(services)
    process = _approved_process(services)
    services.process_manager.run(process.id)

    with pytest.raises(SmartOpsError) as caught:
        services.process_manager.run(process.id)

    assert "already" in caught.value.message.lower()


# ---------- 7. the platform watches itself on real results ----------


def test_an_automation_that_stopped_producing_results_is_reported(services) -> None:
    """The defect this guards: silence looks like health.

    If the scheduler stops, or runs are never picked up, nothing fails — there is
    simply no new file. Every check based on incidents or run status answers
    "all clear", and the one thing the user cares about (a fresh report each
    morning) has quietly stopped.
    """
    from smartops.journey import build_journey, overdue_automations

    services.browser = CountingBrowser()
    _system(services)
    process = _approved_process(services)
    services.process_manager.set_schedule(process.id, every_seconds=3600)

    # Right after approval nothing is overdue: it has not had time yet.
    assert overdue_automations(services) == []

    # Now let more than two full periods pass with no result produced.
    services.clock.advance(3 * 3600)

    overdue = overdue_automations(services)
    assert [p.id for p in overdue] == [process.id]

    journey = build_journey(services)
    monitor = next(s for s in journey.stages if s.key == "monitor")
    assert monitor.done is False
    assert "not produced a result" in monitor.detail


def test_an_automation_producing_results_on_time_is_not_reported(services) -> None:
    from smartops.journey import overdue_automations

    services.browser = CountingBrowser()
    _system(services)
    process = _approved_process(services)
    services.process_manager.set_schedule(process.id, every_seconds=3600)

    services.clock.advance(3 * 3600)
    services.runner.drive(services.process_manager.run(process.id).id)

    assert overdue_automations(services) == []


def test_the_worker_survives_a_failing_poll(services) -> None:
    """A single bad cycle must not end the loop and take every schedule with it."""
    from smartops.worker import Worker

    class ExplodingScheduler:
        def __init__(self) -> None:
            self.ticks = 0

        def tick(self):
            self.ticks += 1
            raise RuntimeError("the database was momentarily locked")

    scheduler = ExplodingScheduler()
    worker = Worker(services, scheduler=scheduler, poll_interval=0.01)

    # poll_once surfaces scheduler failures without ending anything.
    worker.poll_once()
    worker.poll_once()

    assert scheduler.ticks == 2, "a failed tick must not stop later cycles"


def test_worker_health_distinguishes_running_from_working(services) -> None:
    """is_running() is true for a wedged thread too; health must mean more."""
    from smartops.worker import Worker

    worker = Worker(services, poll_interval=0.01)
    assert not worker.is_healthy(), "a worker that was never started is not healthy"

    worker.start()
    try:
        deadline = __import__("time").time() + 3
        while worker.seconds_since_last_poll() is None and __import__("time").time() < deadline:
            __import__("time").sleep(0.02)
        assert worker.is_healthy()
        assert worker.seconds_since_last_poll() is not None
    finally:
        worker.stop()
        worker.join(timeout=5)
