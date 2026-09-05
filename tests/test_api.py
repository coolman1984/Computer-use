from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from smartops.api.app import create_app


@pytest.fixture
def client(services) -> TestClient:
    return TestClient(create_app(services))


def test_health(client) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_root_exposes_registry(client) -> None:
    body = client.get("/").json()
    assert "platform.selfcheck" in body["workflows"]
    assert "core.check_storage" in body["steps"]


def test_create_and_inspect_run(client) -> None:
    created = client.post("/api/runs", json={"workflow": "platform.selfcheck"})
    assert created.status_code == 201
    run_id = created.json()["id"]
    assert created.json()["status"] == "succeeded"

    detail = client.get(f"/api/runs/{run_id}").json()
    assert [s["name"] for s in detail["steps"]] == ["storage", "heartbeat"]

    events = client.get(f"/api/runs/{run_id}/events").json()["items"]
    assert events[0]["type"] == "run_created"
    assert events[-1]["type"] == "run_succeeded"

    listing = client.get("/api/runs", params={"status": "succeeded"}).json()["items"]
    assert run_id in [r["id"] for r in listing]


def test_unknown_workflow_returns_400(client) -> None:
    response = client.post("/api/runs", json={"workflow": "nope"})
    assert response.status_code == 400
    assert response.json()["detail"]["error_class"] == "permanent"


def test_unknown_run_returns_404(client) -> None:
    assert client.get("/api/runs/run_missing").status_code == 404
