"""Regression proofs for review editing, multi-file results, and production guards."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from smartops.api.app import create_app
from smartops.config import BrowserSettings, Settings
from smartops.checks import ConnectionCheck
from smartops.core.errors import SmartOpsError
from smartops.domain.enums import ExtractionLayer, ProcessStatus, RecordingStatus, RunStatus
from smartops.domain.models import RecordingStep
from smartops.ports.browser import ExtractionResult
from smartops.recordings.converter import review_plan
from smartops.recordings.worker import PlaywrightRecordingWorker
from smartops.adapters.browser.replay import ReplaySession, StepFailed
from smartops.services import Services
from smartops.sessions import session_is_usable, session_path


def _system(services) -> None:
    services.systems.save({
        "key": "erp",
        "name": "ERP",
        "auth": {"mode": "none"},
        "reports": [{
            "key": "daily",
            "title": "Daily",
            "url": "https://erp.example.local/report",
            "download_selector": "#export",
            "validation_rules": {"expected_extensions": [".csv"], "min_rows": 1},
        }],
    })


def _reviewable_recording(services, *, secret: bool = False):
    _system(services)
    record = services.recording_manager.create("Daily export", "erp")
    inputs = ({"secret_ref": "erp", "secret_field": "password"}
              if secret else {"value": "North"})
    services.recordings.save_step(RecordingStep(
        recording_id=record.id,
        seq=1,
        kind="fill",
        action="fill",
        page_url_redacted="https://erp.example.local/report",
        page_title="Daily report",
        target={"page": "main", "frame": "#report-frame"},
        locator={"strategy": "css", "value": "#region", "fallbacks": ["[name=region]"]},
        inputs=inputs,
        success={"type": "value_not_empty"} if secret else {"type": "value_equals", "value": "North"},
        checkpoint="after-step-1",
        retry={"max_attempts": 3, "safe_to_repeat": True},
    ))
    services.recordings.save_step(RecordingStep(
        recording_id=record.id,
        seq=2,
        kind="click",
        action="click",
        page_url_redacted="https://erp.example.local/report",
        target={"page": "main", "frame": "#report-frame"},
        locator={"strategy": "css", "value": "#export", "fallbacks": []},
        success={"type": "download_started"},
        checkpoint="after-step-2",
        retry={"max_attempts": 1, "safe_to_repeat": False},
    ))
    for seq, name in ((3, "summary.csv"), (4, "details.csv")):
        services.recordings.save_step(RecordingStep(
            recording_id=record.id,
            seq=seq,
            kind="download",
            action="download",
            inputs={"file_name": name},
            success={"type": "download_started"},
            retry={"max_attempts": 1, "safe_to_repeat": False},
        ))
    record.status = RecordingStatus.COMPLETED
    record.step_count = 4
    record.download_count = 2
    services.recordings.save(record)
    return services.recording_manager.draft(record.id, "daily")


def test_unproven_steps_block_review_instead_of_becoming_an_approved_guess() -> None:
    plan = {
        "start_url": "https://example.test",
        "expects_download": True,
        "expected_download_count": 1,
        "actions": [{
            "seq": 1,
            "action": "click",
            "layer": "dom",
            "locator": {"value": "#submit"},
            "success": {"type": "none"},
            "retry": {"max_attempts": 1, "safe_to_repeat": False},
        }],
    }

    verdict = review_plan(plan)

    assert verdict["ready"] is False
    assert verdict["unproven_action_count"] == 1


def test_screen_position_fallback_is_not_approvable_as_reliable() -> None:
    plan = {
        "start_url": "https://example.test",
        "expects_download": True,
        "expected_download_count": 1,
        "actions": [{
            "seq": 1, "action": "click", "layer": "visual",
            "locator": {"x_ratio": .5, "y_ratio": .5},
            "success": {"type": "download_started"},
            "retry": {"max_attempts": 1, "safe_to_repeat": False},
        }],
    }

    verdict = review_plan(plan)

    assert verdict["ready"] is False
    assert any("record" in problem.lower() for problem in verdict["problems"])


class _Download:
    suggested_filename = "report.csv"

    def __init__(self, body: bytes) -> None:
        self.body = body

    def save_as(self, path: str) -> None:
        Path(path).write_bytes(self.body)


def test_recorder_keeps_two_downloads_with_the_same_suggested_name(tmp_path) -> None:
    worker = PlaywrightRecordingWorker(
        "rec", tmp_path, "https://example.test", lambda _: None,
        lambda: None, lambda _: None,
    )
    page = object()

    worker._finish_download((_Download(b"first"), page, "https://example.test"))
    worker._finish_download((_Download(b"second"), page, "https://example.test"))

    files = sorted((tmp_path / "downloads").glob("report*.csv"))
    assert len(files) == 2
    assert {path.read_bytes() for path in files} == {b"first", b"second"}


def test_replay_never_overwrites_duplicate_downloads_left_by_an_interruption(tmp_path) -> None:
    """Resuming into a run directory must preserve every previously saved file."""
    from smartops.adapters.browser.replay import ReplaySession

    destination = tmp_path / "downloads"
    destination.mkdir()
    (destination / "report.csv").write_text("old", encoding="utf-8")
    (destination / "report-2.csv").write_text("older", encoding="utf-8")
    session = ReplaySession(object(), artifact_dir=tmp_path)
    session._pending_downloads = [_Download(b"new")]

    session.collect_downloads(destination)

    assert (destination / "report.csv").read_text(encoding="utf-8") == "old"
    assert (destination / "report-2.csv").read_text(encoding="utf-8") == "older"
    assert (destination / "report-3.csv").read_bytes() == b"new"


class _ProofLocator:
    def __init__(self) -> None:
        self.first = self
        self.timeout = None

    def wait_for(self, *, state, timeout) -> None:
        self.timeout = timeout


class _ProofScope:
    def __init__(self, locator) -> None:
        self.item = locator

    def locator(self, selector):
        return self.item


def test_step_specific_wait_is_used_for_selector_proof(tmp_path) -> None:
    locator = _ProofLocator()
    session = ReplaySession(object(), artifact_dir=tmp_path, evidence_timeout_ms=15000)
    session._scope = lambda action: _ProofScope(locator)

    session.perform({
        "seq": 1, "action": "wait_for", "inputs": {"seconds": 0},
        "success": {"type": "selector_visible", "value": "#done"},
        "wait_timeout_seconds": 2,
        "retry": {"max_attempts": 1, "safe_to_repeat": True},
    })

    assert locator.timeout == 2000


class _MissingElement:
    first = None

    def __init__(self) -> None:
        self.first = self

    def element_handle(self, timeout):
        return None


class _SinglePage:
    def __init__(self) -> None:
        self.main_frame = object()
        self.frames = [self.main_frame]

    def is_closed(self):
        return False

    def locator(self, selector):
        return _MissingElement()


def test_replay_never_falls_back_to_the_wrong_tab_or_frame(tmp_path) -> None:
    page = _SinglePage()
    session = ReplaySession(object(), artifact_dir=tmp_path)
    session._pages, session._current = [page], page

    with pytest.raises(StepFailed, match="tab"):
        session._scope({"seq": 4, "target": {"page": "page-1", "frame": ""}})
    with pytest.raises(StepFailed, match="frame"):
        session._scope({"seq": 5, "target": {"page": "main", "frame": "#missing"}})


def test_broken_selector_never_silently_becomes_a_screen_position_click(tmp_path) -> None:
    page = _SinglePage()
    page.viewport_size = {"width": 1000, "height": 800}
    session = ReplaySession(object(), artifact_dir=tmp_path)
    session._pages, session._current = [page], page

    fallback = session._position_locator({
        "layer": "dom", "locator": {"x_ratio": .5, "y_ratio": .5},
    }, page)

    assert fallback is None


def test_new_tab_proof_requires_a_tab_created_by_this_step(tmp_path) -> None:
    page = _SinglePage()
    session = ReplaySession(object(), artifact_dir=tmp_path, evidence_timeout_ms=1)
    session._pages, session._current = [page], page

    with pytest.raises(StepFailed, match="new tab"):
        session.perform({
            "seq": 1, "action": "wait_for", "inputs": {"seconds": 0},
            "success": {"type": "new_page"},
            "retry": {"max_attempts": 1, "safe_to_repeat": True},
        })


def test_reviewed_action_can_be_safely_edited_and_rechecked_from_the_api(services) -> None:
    record = _reviewable_recording(services)
    client = TestClient(create_app(services))

    response = client.patch(
        f"/api/recordings/{record.id}/draft/actions/1",
        json={
            "locator_candidates": ["[data-testid=region]", "#region"],
            "inputs": {"value": "South"},
            "success": {"type": "value_equals", "value": "South"},
            "wait_timeout_seconds": 20,
            "retry": {"max_attempts": 2, "safe_to_repeat": True},
        },
    )

    assert response.status_code == 200, response.text
    action = response.json()["action"]
    assert action["locator"]["value"] == "[data-testid=region]"
    assert action["locator"]["fallbacks"] == ["#region"]
    assert action["inputs"] == {"value": "South"}
    assert action["wait_timeout_seconds"] == 20
    assert response.json()["review"]["ready"] is True


def test_review_edit_refuses_to_put_a_secret_inside_the_plan(services) -> None:
    record = _reviewable_recording(services, secret=True)
    client = TestClient(create_app(services))

    response = client.patch(
        f"/api/recordings/{record.id}/draft/actions/1",
        json={"inputs": {"value": "do-not-store-this-password"}},
    )

    assert response.status_code == 400
    persisted = services.recordings.get(record.id).automation_draft["actions"][0]
    assert "do-not-store-this-password" not in str(persisted)


class TwoFileBrowser:
    def extract(self, request):
        paths = []
        for name, body in (("summary.csv", b"id,value\n1,10\n"),
                           ("details.csv", b"id,value\n1,10\n2,20\n")):
            path = Path(request.destination_dir) / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(body)
            paths.append(path)
        return ExtractionResult(ok=True, layer_used=ExtractionLayer.DOM, file_paths=paths)

    replay = extract

    def capture_evidence(self, run_id: str) -> dict:
        return {}


def test_collect_contract_registers_validates_and_archives_every_file(services) -> None:
    _system(services)
    services.browser = TwoFileBrowser()
    params = services.systems.run_params("erp", "daily")

    run = services.runner.drive(services.runner.create_run("collect.report", params=params).id)

    assert run.status is RunStatus.SUCCEEDED
    assert len(run.state["file_paths"]) == 2
    assert run.state["validated_count"] == 2
    files = services.files.list(run_id=run.id)
    assert len(files) == 2
    assert all(item.validation_status.value == "passed" for item in files)
    assert len(list(services.settings.storage.history_dir.rglob("*.parquet"))) == 2


def test_invalid_member_of_a_file_group_is_never_archived(services) -> None:
    """History is trusted input; failed output must not reach it."""
    from smartops.ports.validation import ValidationReport

    class OneInvalidFileValidator:
        def validate(self, path, rules):
            if path.name == "details.csv":
                return ValidationReport(passed=False, failures=["bad columns"])
            return ValidationReport(passed=True, sha256="good", row_count=1)

    _system(services)
    services.browser = TwoFileBrowser()
    services.validator = OneInvalidFileValidator()

    run = services.runner.drive(
        services.runner.create_run("collect.report", params=services.systems.run_params("erp", "daily")).id
    )

    assert run.status is RunStatus.FAILED
    assert not list(services.settings.storage.history_dir.rglob("*.parquet"))


def test_singular_file_alias_can_never_disagree_with_the_primary_group(tmp_path) -> None:
    first, second, stale = (tmp_path / name for name in ("first.csv", "second.csv", "stale.csv"))

    result = ExtractionResult(
        ok=True, layer_used=ExtractionLayer.DOM,
        file_paths=[first, second], file_path=stale,
    )

    assert result.file_path == first


def test_record_headless_requires_an_explicit_development_permission(settings) -> None:
    from smartops.services import Services

    unsafe = replace(settings, browser=replace(settings.browser, record_headless=True))
    with pytest.raises(SmartOpsError, match="development"):
        Services(unsafe)


def test_a_text_value_named_false_cannot_unlock_development_features(settings) -> None:
    unsafe = replace(
        settings,
        browser=replace(settings.browser, record_headless=True),
        safety=replace(settings.safety, allow_development_features="false"),
    )

    with pytest.raises(SmartOpsError, match="development"):
        Services(unsafe)


def test_worker_disable_env_is_guarded_even_when_app_builds_its_own_services(
    monkeypatch, settings
) -> None:
    monkeypatch.setenv("SMARTOPS_DISABLE_WORKER", "1")
    monkeypatch.setattr("smartops.api.app.load_settings", lambda: settings, raising=False)

    with pytest.raises(SmartOpsError, match="development"):
        create_app()


def test_internal_replay_workflow_cannot_bypass_approval_through_generic_api(services) -> None:
    client = TestClient(create_app(services))

    response = client.post("/api/runs", json={
        "workflow": "process.replay",
        "params": {"system": "erp", "report": "daily", "plan": {"actions": []}},
        "start": False,
    })

    assert response.status_code == 403


def test_legacy_yaml_schedule_cannot_bypass_the_approval_gate(services) -> None:
    services.systems.save({
        "key": "legacy", "name": "Legacy", "auth": {"mode": "none"},
        "reports": [{
            "key": "daily", "title": "Daily", "url": "https://example.test/report",
            "download_selector": "#export", "schedule": {"every_seconds": 60},
        }],
    })

    created = services.scheduler.tick()

    assert created == []
    assert services.runs.list(workflow_key="collect.report") == []


def test_preexisting_approved_process_with_unproven_steps_cannot_run(services) -> None:
    _system(services)
    process = services.processes.create(
        name="Old unsafe plan", system_key="erp", report_key="daily",
        recording_id=None,
        plan={
            "start_url": "https://erp.example.local/report", "expects_download": True,
            "expected_download_count": 1,
            "actions": [{
                "seq": 1, "action": "click", "layer": "dom",
                "locator": {"value": "#export"}, "success": {"type": "none"},
                "retry": {"max_attempts": 1, "safe_to_repeat": False},
            }],
        },
        validation_rules={"expected_extensions": [".csv"]}, version=1,
    )
    process.status = ProcessStatus.APPROVED
    services.processes.save(process)

    with pytest.raises(SmartOpsError, match="proof"):
        services.process_manager.run(process.id)


def test_queued_legacy_replay_cannot_bypass_review_when_started(services) -> None:
    """A stored run from before the new API gate is still stopped by the engine."""
    run = services.runner.create_run("process.replay", params={
        "system": "erp", "report": "daily",
        "plan": {
            "start_url": "https://erp.example.local/report", "expects_download": True,
            "expected_download_count": 1,
            "actions": [{
                "seq": 1, "action": "click", "locator": {"value": "#export"},
                "success": {"type": "none"},
                "retry": {"max_attempts": 1, "safe_to_repeat": False},
            }],
        },
    })

    completed = services.runner.drive(run.id)

    assert completed.status is RunStatus.FAILED
    assert "proof" in (completed.error_message or "").lower()


def test_two_simultaneous_run_requests_create_only_one_process_run(services) -> None:
    """The manual button and scheduler must not each launch the same process."""
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    process = services.processes.create(
        name="Concurrent", system_key="erp", report_key="daily", plan={
            "start_url": "https://erp.example.local/report", "expects_download": True,
            "expected_download_count": 1,
            "actions": [{
                "seq": 1, "action": "click", "layer": "dom",
                "locator": {"value": "#export"}, "success": {"type": "download_started"},
                "retry": {"max_attempts": 1, "safe_to_repeat": False},
            }],
        },
    )
    process.status = ProcessStatus.APPROVED
    services.processes.save(process)
    start = Barrier(2)

    def launch():
        start.wait()
        try:
            return services.process_manager.run(process.id).id
        except SmartOpsError:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        ids = list(pool.map(lambda _: launch(), range(2)))

    assert len([item for item in ids if item]) == 1
    assert len(services.runs.list(workflow_key="process.replay")) == 1


def test_failed_unsafe_task_cannot_be_blindly_retried(services) -> None:
    _system(services)
    run = services.runner.create_run("process.replay", params={
        "system": "erp",
        "report": "daily",
        "plan": {"actions": [{
            "seq": 1,
            "action": "press",
            "success": {"type": "selector_visible", "value": "#done"},
            "retry": {"max_attempts": 1, "safe_to_repeat": False},
        }]},
    })
    run.status = RunStatus.FAILED
    services.runs.update(run)

    with pytest.raises(SmartOpsError, match="unsafe"):
        services.runner.retry(run.id)


def test_crashed_unsafe_task_is_failed_for_review_not_replayed(services) -> None:
    _system(services)
    run = services.runner.create_run("process.replay", params={
        "system": "erp",
        "report": "daily",
        "plan": {"actions": [{
            "seq": 1,
            "action": "press",
            "success": {"type": "selector_visible", "value": "#done"},
            "retry": {"max_attempts": 1, "safe_to_repeat": False},
        }]},
    })
    run.status = RunStatus.RUNNING
    services.runs.update(run)
    services.runs.claim(run.id, "dead-worker", lease_seconds=-1)

    services.recovery.recover_stranded_runs()

    recovered = services.runs.get(run.id)
    assert recovered.status is RunStatus.FAILED
    assert "unsafe" in (recovered.error_message or "").lower()


class RestartableTwoFileBrowser:
    def replay(self, request):
        paths = []
        for index in (1, 2):
            target = Path(request.destination_dir) / f"report-{index}.csv"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(f"id,value\n{index},{request.run_id}\n", encoding="utf-8")
            paths.append(target)
        return ExtractionResult(ok=True, layer_used=ExtractionLayer.DOM, file_paths=paths)

    extract = replay

    def capture_evidence(self, run_id: str) -> dict:
        return {}


class OneFileBrowser(RestartableTwoFileBrowser):
    def replay(self, request):
        target = Path(request.destination_dir) / "only-one.csv"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("id,value\n1,one\n", encoding="utf-8")
        return ExtractionResult(ok=True, layer_used=ExtractionLayer.DOM, file_paths=[target])


def test_replay_contract_rejects_one_missing_file_even_if_adapter_claims_success(services) -> None:
    _system(services)
    services.browser = OneFileBrowser()
    run = services.runner.create_run("process.replay", params={
        "system": "erp", "report": "daily",
        "plan": {
            "start_url": "https://erp.example.local/report", "expects_download": True,
            "expected_download_count": 2,
            "actions": [{
                "seq": 1, "action": "click", "locator": {"value": "#export"},
                "success": {"type": "download_started"},
                "retry": {"max_attempts": 1, "safe_to_repeat": False},
            }],
        },
        "rules": {"expected_extensions": [".csv"], "min_rows": 1},
    })

    result = services.runner.drive(run.id)

    assert result.status is RunStatus.FAILED
    assert "2" in (result.error_message or "")
    assert services.files.list(run_id=run.id) == []


def test_replay_contract_rejects_a_missing_third_file(services) -> None:
    _system(services)
    services.browser = RestartableTwoFileBrowser()
    run = services.runner.create_run("process.replay", params={
        "system": "erp", "report": "daily",
        "plan": {
            "start_url": "https://erp.example.local/report", "expects_download": True,
            "expected_download_count": 3,
            "actions": [{
                "seq": 1, "action": "click", "layer": "dom",
                "locator": {"value": "#export"}, "success": {"type": "download_started"},
                "retry": {"max_attempts": 1, "safe_to_repeat": False},
            }],
        },
        "rules": {"expected_extensions": [".csv"], "min_rows": 1},
    })

    completed = services.runner.drive(run.id)

    assert completed.status is RunStatus.FAILED
    assert "3 files" in (completed.error_message or "")
    assert services.files.list(run_id=run.id) == []


def test_stored_legacy_collection_cannot_resume_in_production(services) -> None:
    services.settings = replace(
        services.settings, app=replace(services.settings.app, environment="production")
    )
    run = services.runner.create_run("collect.report", params={"system": "erp", "report": "daily"})

    completed = services.runner.drive(run.id)

    assert completed.status is RunStatus.FAILED
    assert "legacy direct collection" in (completed.error_message or "").lower()


def _persisted_full_recording(svc: Services):
    record = svc.recording_manager.create("Full report", "portal")
    common = {"page_url_redacted": "https://portal.example.test/report"}
    steps = [
        RecordingStep(record.id, 1, "fill", action="fill", page_title="Report",
                      target={"page": "main", "frame": ""},
                      locator={"strategy": "css", "value": "#query", "fallbacks": []},
                      inputs={"value": "North"}, success={"type": "value_equals", "value": "North"},
                      retry={"max_attempts": 3, "safe_to_repeat": True}, **common),
        RecordingStep(record.id, 2, "select", action="select",
                      locator={"strategy": "css", "value": "#period", "fallbacks": []},
                      inputs={"value": "monthly"}, success={"type": "value_equals", "value": "monthly"},
                      retry={"max_attempts": 3, "safe_to_repeat": True}, **common),
        RecordingStep(record.id, 3, "press", action="press",
                      locator={"strategy": "css", "value": "#query", "fallbacks": []},
                      inputs={"key": "Tab"}, success={"type": "selector_visible", "value": "#preview"},
                      retry={"max_attempts": 1, "safe_to_repeat": False}, **common),
        RecordingStep(record.id, 4, "click", action="click",
                      locator={"strategy": "css", "value": "#open", "fallbacks": []},
                      success={"type": "new_page"},
                      retry={"max_attempts": 1, "safe_to_repeat": False}, **common),
        RecordingStep(record.id, 5, "switch_page", action="switch_page",
                      target={"page": "page-1", "frame": ""}, success={"type": "page_available"},
                      retry={"max_attempts": 3, "safe_to_repeat": True}, **common),
        RecordingStep(record.id, 6, "switch_frame", action="switch_frame",
                      target={"page": "page-1", "frame": "#report-frame"},
                      success={"type": "selector_visible", "value": "#export"},
                      retry={"max_attempts": 3, "safe_to_repeat": True}, **common),
        RecordingStep(record.id, 7, "click", action="click",
                      target={"page": "page-1", "frame": "#report-frame"},
                      locator={"strategy": "css", "value": "#export", "fallbacks": []},
                      success={"type": "download_started"},
                      retry={"max_attempts": 1, "safe_to_repeat": False}, **common),
        RecordingStep(record.id, 8, "download", action="download", inputs={"file_name": "report.csv"}),
        RecordingStep(record.id, 9, "download", action="download", inputs={"file_name": "report-2.csv"}),
    ]
    for step in steps:
        svc.recordings.save_step(step)
    record.status, record.step_count, record.download_count = RecordingStatus.COMPLETED, len(steps), 2
    svc.recordings.save(record)
    return record


def test_approved_multifile_journey_survives_process_restart(settings: Settings) -> None:
    first = Services(settings)
    try:
        client = TestClient(create_app(first))
        added = client.put("/api/systems/portal", json={
            "key": "portal", "name": "Portal",
            "auth": {"mode": "session", "login_url": "https://portal.example.test/login",
                     "logged_in_selector": "#account"},
            "reports": [{"key": "full", "title": "Full",
                         "url": "https://portal.example.test/report", "download_selector": "#export"}],
        })
        assert added.status_code == 200, added.text
        first.connection_checks.record(
            "portal", ConnectionCheck(True, True, True, "Connected"), at=first.clock.now().isoformat()
        )
        state = session_path(settings.storage.sessions_dir, "portal")
        state.parent.mkdir(parents=True, exist_ok=True)
        state.write_text(
            '{"cookies":[{"name":"session","value":"opaque","expires":-1}],"origins":[]}',
            encoding="utf-8",
        )
        first.browser = RestartableTwoFileBrowser()
        record = _persisted_full_recording(first)
        assert client.post(f"/api/recordings/{record.id}/draft", json={"report_key": "full"}).status_code == 200
        edited = client.patch(f"/api/recordings/{record.id}/draft/actions/1", json={
            "locator_candidates": ["[data-testid=query]", "#query"],
            "inputs": {"value": "South"}, "success": {"type": "value_equals", "value": "South"},
            "wait_timeout_seconds": 25, "retry": {"max_attempts": 2, "safe_to_repeat": True},
        })
        assert edited.status_code == 200, edited.text
        made = client.post("/api/processes", json={
            "recording_id": record.id, "name": "Full report", "report_key": "full",
        })
        assert made.status_code == 201, made.text
        process_id = made.json()["id"]
        tested = client.post(f"/api/processes/{process_id}/test")
        assert tested.status_code == 201 and tested.json()["run"]["status"] == "succeeded", tested.text
        assert len(first.files.list(run_id=tested.json()["run"]["id"])) == 2
        assert client.post(f"/api/processes/{process_id}/approve").status_code == 200
        ran = client.post(f"/api/processes/{process_id}/run")
        assert ran.status_code == 201 and ran.json()["status"] == "succeeded", ran.text
        assert len(first.files.list(run_id=ran.json()["id"])) == 2
        assert client.put(f"/api/processes/{process_id}/schedule", json={
            "every_seconds": 3600, "enabled": True,
        }).status_code == 200
    finally:
        first.close()

    restarted = Services(settings)
    try:
        restarted.browser = RestartableTwoFileBrowser()
        process = restarted.processes.get(process_id)
        assert process is not None and process.is_runnable and process.is_scheduled
        assert restarted.recordings.get(record.id).automation_draft["actions"][0]["inputs"] == {"value": "South"}
        assert session_is_usable(settings.storage.sessions_dir, "portal", now=restarted.clock)
        assert restarted.connection_checks.get("portal") is not None
        rerun = restarted.runner.drive(restarted.process_manager.run(process_id).id)
        assert rerun.status is RunStatus.SUCCEEDED
        assert len(restarted.files.list(run_id=rerun.id)) == 2
        assert rerun.state["validated_count"] == 2
        assert len(rerun.state["file_paths"]) == 2
    finally:
        restarted.close()
