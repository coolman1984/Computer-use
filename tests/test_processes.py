"""The automation lifecycle and its gates.

These test the rules the browser journey relies on, directly and without a
browser: a recording only becomes an automation if its plan can actually be
replayed, an automation is only approved after a real passing test, and only an
approved automation can be run or scheduled. The last test closes the loop that
the whole rebuild exists for — a schedule that fires by itself, with no one
touching the platform.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from smartops.checks import ConnectionCheck
from smartops.core.errors import SmartOpsError
from smartops.domain.enums import ExtractionLayer, ProcessStatus, RecordingStatus, TriggerType
from smartops.domain.models import RecordingStep
from smartops.ports.browser import ExtractionResult
from smartops.ports.validation import ValidationReport


class FakeBrowser:
    """A website that always works, so these tests measure the platform's rules."""

    def __init__(self, *, ok: bool = True) -> None:
        self.ok = ok
        self.calls = 0

    def replay(self, request):
        self.calls += 1
        if not self.ok:
            return ExtractionResult(
                ok=False, layer_used=ExtractionLayer.DOM, message="The export button has moved."
            )
        target = Path(request.destination_dir) / "report.csv"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"a,b\n1,2\n")
        return ExtractionResult(
            ok=True, layer_used=ExtractionLayer.DOM, file_path=target,
            original_name=target.name, size_bytes=target.stat().st_size,
        )

    def extract(self, request):
        raise NotImplementedError

    def capture_evidence(self, run_id: str) -> dict:
        return {}


class FakeValidator:
    def validate(self, path, rules) -> ValidationReport:
        return ValidationReport(passed=True, sha256="abc", row_count=1)


@pytest.fixture
def ready(services):
    """A platform standing at the point where a recording can become an automation."""
    services.browser = FakeBrowser()
    services.validator = FakeValidator()
    services.systems.save(
        {
            "key": "erp",
            "name": "Sales ERP",
            "auth": {"mode": "none"},
            "reports": [
                {
                    "key": "daily",
                    "title": "Daily",
                    "url": "https://erp.example.local/daily",
                    "download_selector": "#export",
                }
            ],
        }
    )
    services.connection_checks.record(
        "erp",
        ConnectionCheck(ok=True, reachable=True, signed_in=None, summary="opened"),
        at="2026-01-01T00:00:00Z",
    )
    return services


def _recording(services, *, replayable: bool = True):
    record = services.recording_manager.create("Daily export", "erp")
    if replayable:
        services.recordings.save_step(
            RecordingStep(record.id, 1, "click", selector="#export",
                          page_url_redacted="https://erp.example.local/daily")
        )
    else:
        # A click with no selector and no position: nothing to repeat.
        services.recordings.save_step(
            RecordingStep(record.id, 1, "click", page_url_redacted="https://erp.example.local/daily")
        )
    services.recordings.save_step(RecordingStep(record.id, 2, "download", download_ref="downloads/x.csv"))
    record.status = RecordingStatus.COMPLETED
    services.recordings.save(record)
    services.recording_manager.draft(record.id, "daily")
    return record


# ---------- the review gate ----------


def test_a_recording_that_cannot_be_replayed_is_refused_with_a_reason(ready) -> None:
    record = _recording(ready, replayable=False)

    with pytest.raises(SmartOpsError) as caught:
        ready.process_manager.create_from_recording(record.id)

    # The message has to tell a non-technical user what to do, not name a field.
    assert "record the workflow again" in caught.value.message.lower()


def test_a_recording_with_no_download_is_refused(ready) -> None:
    record = ready.recording_manager.create("No file", "erp")
    ready.recordings.save_step(RecordingStep(record.id, 1, "click", selector="#somewhere"))
    record.status = RecordingStatus.COMPLETED
    ready.recordings.save(record)
    ready.recording_manager.draft(record.id, "daily")

    with pytest.raises(SmartOpsError) as caught:
        ready.process_manager.create_from_recording(record.id)

    assert "no file was downloaded" in caught.value.message.lower()


# ---------- the test and approval gates ----------


def test_an_untested_automation_cannot_be_approved(ready) -> None:
    process = ready.process_manager.create_from_recording(_recording(ready).id)

    with pytest.raises(SmartOpsError) as caught:
        ready.process_manager.approve(process.id)

    assert "test" in caught.value.message.lower()
    assert ready.processes.get(process.id).status is ProcessStatus.DRAFT


def test_a_passing_test_unlocks_approval_and_the_test_really_ran(ready) -> None:
    process = ready.process_manager.create_from_recording(_recording(ready).id)

    process, run = ready.process_manager.test(process.id)

    assert ready.browser.calls == 1, "a test must actually drive the browser"
    assert run.status.value == "succeeded"
    assert process.status is ProcessStatus.TESTED
    # And the file it produced is registered as a real result.
    assert [f.report for f in ready.files.list(run_id=run.id)] == ["daily"]

    approved = ready.process_manager.approve(process.id)
    assert approved.status is ProcessStatus.APPROVED and approved.is_runnable


def test_a_failing_test_leaves_the_automation_unapproved(ready) -> None:
    ready.browser = FakeBrowser(ok=False)
    process = ready.process_manager.create_from_recording(_recording(ready).id)

    process, run = ready.process_manager.test(process.id)

    assert run.status.value == "failed"
    assert process.status is ProcessStatus.TEST_FAILED
    with pytest.raises(SmartOpsError):
        ready.process_manager.approve(process.id)
    # A failure also opens an issue, so it cannot be missed.
    assert ready.incidents.list(limit=5)


def test_editing_a_tested_automation_invalidates_its_test(ready) -> None:
    """What was proven to work must stay the same thing that would run."""
    process = ready.process_manager.create_from_recording(_recording(ready).id)
    ready.process_manager.test(process.id)
    assert ready.processes.get(process.id).status is ProcessStatus.TESTED

    edited = ready.process_manager.update(process.id, name="Renamed")

    assert edited.status is ProcessStatus.DRAFT
    assert edited.last_test_run_id is None


# ---------- the run and schedule gates ----------


def test_an_unapproved_automation_cannot_be_run_or_scheduled(ready) -> None:
    process = ready.process_manager.create_from_recording(_recording(ready).id)

    with pytest.raises(SmartOpsError):
        ready.process_manager.run(process.id)
    with pytest.raises(SmartOpsError) as caught:
        ready.process_manager.set_schedule(process.id, daily_at="08:00")

    assert "approved" in caught.value.message.lower()


def test_a_schedule_needs_a_time_or_an_interval(ready) -> None:
    process = _approved(ready)

    with pytest.raises(SmartOpsError) as caught:
        ready.process_manager.set_schedule(process.id, enabled=True)

    assert "when this automation should run" in caught.value.message.lower()


def test_a_bad_daily_time_is_refused_in_the_same_words_as_a_report(ready) -> None:
    """The UI and the YAML loader must agree on what a legal schedule is."""
    process = _approved(ready)

    with pytest.raises(SmartOpsError) as caught:
        ready.process_manager.set_schedule(process.id, daily_at="25:00")

    assert "HH:MM" in caught.value.message


def _approved(services):
    process = services.process_manager.create_from_recording(_recording(services).id)
    services.process_manager.test(process.id)
    return services.process_manager.approve(process.id)


# ---------- the promise: it runs later, by itself ----------


def test_a_scheduled_automation_is_picked_up_and_run_with_no_one_present(ready, clock) -> None:
    """The success criterion of the whole platform, tested directly.

    Approve an automation, give it a schedule, then let only the scheduler and
    the worker act — no HTTP request, no button, no human. A file must appear.
    """
    process = _approved(ready)
    ready.process_manager.set_schedule(process.id, every_seconds=3600)
    files_before = len(ready.files.list(limit=100))

    # Move past the interval and let the scheduler do its pass.
    clock.advance(2 * 3600)
    created = ready.scheduler.tick()

    assert len(created) == 1
    assert created[0].trigger is TriggerType.SCHEDULE
    assert created[0].params["process_id"] == process.id

    # Now the worker, exactly as it runs in the server's background.
    from smartops.worker import Worker

    dispatched = Worker(ready).poll_once()
    assert dispatched == 1

    run = ready.runs.get(created[0].id)
    assert run.status.value == "succeeded"
    assert len(ready.files.list(limit=100)) == files_before + 1


def test_the_scheduler_never_stacks_a_second_copy_of_a_running_automation(ready, clock) -> None:
    process = _approved(ready)
    ready.process_manager.set_schedule(process.id, every_seconds=60)

    clock.advance(5 * 60)
    first = ready.scheduler.tick()
    assert len(first) == 1

    # The first run has not finished; a second pass must not queue another.
    clock.advance(5 * 60)
    assert ready.scheduler.tick() == []


def test_an_unapproved_automation_is_invisible_to_the_scheduler(ready, clock) -> None:
    """Defence in depth: even if a schedule were set some other way, it cannot fire."""
    process = _approved(ready)
    ready.process_manager.set_schedule(process.id, every_seconds=60)
    ready.process_manager.retire(process.id)

    clock.advance(5 * 60)

    assert ready.processes.scheduled() == []
    assert ready.scheduler.tick() == []
