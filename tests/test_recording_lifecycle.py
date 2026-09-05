from __future__ import annotations
from fastapi.testclient import TestClient
from smartops.api.app import create_app
from smartops.domain.enums import RecordingStatus

def test_recording_catalog_delete_restore_and_version(services) -> None:
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

def test_recording_draft_prefers_network_then_dom_then_relative(services) -> None:
    record = services.recording_manager.create("draft", "local")
    from smartops.domain.models import RecordingStep
    services.recordings.save_step(RecordingStep(record.id, 1, "click", request_ref="request-1"))
    services.recordings.save_step(RecordingStep(record.id, 2, "click", selector="#go"))
    services.recordings.save_step(RecordingStep(record.id, 3, "click", x_ratio=.4, y_ratio=.5))
    record.status = RecordingStatus.COMPLETED; services.recordings.save(record)
    draft = services.recording_manager.draft(record.id).automation_draft
    assert [a["layer"] for a in draft["actions"]] == ["network", "dom", "vision"]
