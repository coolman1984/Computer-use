from __future__ import annotations
from fastapi.testclient import TestClient
import pytest
from smartops.api.app import create_app
from smartops.checks import ConnectionCheck
from smartops.core.errors import PermanentError
from smartops.domain.enums import RecordingStatus


def _ready_system(services, key: str = "local") -> None:
    """Bring a system all the way to 'ready to record': defined, tested, signed in.

    Recording is gated on those three stages, so a test that records has to walk
    them the same way a user does — that is the point of the gates.
    """
    services.systems.save(
        {
            "key": key,
            "name": "Local test system",
            "auth": {"mode": "none"},
            "reports": [
                {
                    "key": "daily",
                    "title": "Daily",
                    "url": "https://example.local/report",
                    "download_selector": "#dl",
                }
            ],
        }
    )
    services.connection_checks.record(
        key,
        ConnectionCheck(ok=True, reachable=True, signed_in=None, summary="opened"),
        at="2026-01-01T00:00:00Z",
    )

def test_recording_catalog_delete_restore_and_version(services) -> None:
    _ready_system(services)
    manager = services.recording_manager
    first = manager.create("daily report", "local")
    assert first.status is RecordingStatus.DRAFT
    assert services.recordings.get(first.id).artifact_dir.endswith(first.id)
    first.status = RecordingStatus.COMPLETED
    services.recordings.save(first)
    second = manager.create(first.name, first.system_key, first)
    assert second.version == 2 and second.parent_recording_id == first.id
    # We don't wait for Chrome in this test; we only confirm the trash-can rules.
    second.status = RecordingStatus.FAILED
    services.recordings.save(second)
    manager.delete(second.id)
    assert services.recordings.list() == [first]
    manager.restore(second.id)
    assert {r.id for r in services.recordings.list()} == {first.id, second.id}

def test_recording_api_and_secret_artifacts_are_not_served(services, tmp_path) -> None:
    _ready_system(services)
    client = TestClient(create_app(services))
    response = client.post("/api/recordings", json={"name": "local flow", "system_key": "local"})
    assert response.status_code == 201
    record = response.json()
    detail = client.get(f"/api/recordings/{record['id']}")
    assert detail.status_code == 200 and detail.json()["recording"]["name"] == "local flow"
    artifact_dir = services.recordings.get(record["id"]).artifact_dir
    from pathlib import Path
    root = Path(artifact_dir); (root / "session").mkdir(parents=True); (root / "session" / "storage-state.json").write_text("secret")
    assert client.get(f"/api/recordings/{record['id']}/artifacts/session/storage-state.json").status_code == 404

def test_recording_plan_prefers_a_selector_then_a_relative_click(services) -> None:
    """A plan only ever claims a layer the replay engine can actually execute.

    The earlier version of this labelled a step "network" whenever the recording
    carried a request reference, but nothing could replay a request — so the plan
    described a capability the platform did not have. A step with no selector and
    no position is now honestly reported as 'manual', which is what makes the
    review gate refuse it instead of shipping an automation that cannot run.
    """
    _ready_system(services)
    record = services.recording_manager.create("draft", "local")
    from smartops.domain.models import RecordingStep
    services.recordings.save_step(RecordingStep(record.id, 1, "click", request_ref="request-1"))
    services.recordings.save_step(RecordingStep(record.id, 2, "click", selector="#go"))
    services.recordings.save_step(RecordingStep(record.id, 3, "click", x_ratio=.4, y_ratio=.5))
    record.status = RecordingStatus.COMPLETED; services.recordings.save(record)
    plan = services.recording_manager.draft(record.id).automation_draft
    assert [a["layer"] for a in plan["actions"]] == ["manual", "dom", "visual"]
    # An unreplayable step blocks the plan from becoming an automation.
    assert plan["review"]["ready"] is False


def test_recording_cannot_be_marked_complete_without_a_download(services) -> None:
    _ready_system(services)
    manager = services.recording_manager
    record = manager.create("download report", "local")
    record.status = RecordingStatus.RECORDING
    services.recordings.save(record)

    class FakeWorker:
        def stop(self):
            manager._finished(record.id, None)

    manager.workers[record.id] = FakeWorker()

    with pytest.raises(PermanentError, match="download"):
        manager.stop(record.id)

    settled = manager.stop_incomplete(record.id)
    assert settled.status is RecordingStatus.INTERRUPTED
    assert "without a detected download" in (settled.error_message or "")
