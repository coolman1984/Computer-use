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


# ---------- F-11 tests: /api/systems, /api/alerts, and on-demand collection ----------

SYSTEM_YAML = """
key: erp_demo
name: Demo system
auth:
  mode: session
  login_url: "https://intranet.example.local/login"
reports:
  - key: daily_sales
    title: Daily sales report
    url: "https://intranet.example.local/reports/daily-sales"
    download_selector: "#dl"
    schedule:
      daily_at: "08:00"
"""


@pytest.fixture
def client_with_system(services, tmp_path) -> TestClient:
    from smartops.workflows.profiles import SystemRegistry

    (tmp_path / "erp.yaml").write_text(SYSTEM_YAML, encoding="utf-8")
    services.systems = SystemRegistry.load(tmp_path)
    return TestClient(create_app(services))


def test_list_systems_shows_session_status(client_with_system) -> None:
    body = client_with_system.get("/api/systems").json()
    items = body["items"]

    assert len(items) == 1
    system = items[0]
    assert system["key"] == "erp_demo"
    assert system["auth_mode"] == "session"
    assert system["session_exists"] is False
    assert system["reports"][0]["key"] == "daily_sales"
    assert system["reports"][0]["schedule"]["daily_at"] == "08:00"


def test_list_alerts_returns_empty_when_no_alerts_yet(client) -> None:
    body = client.get("/api/alerts").json()
    assert body["items"] == []


def test_collect_now_unknown_system_returns_404(client_with_system) -> None:
    response = client_with_system.post("/api/systems/does_not_exist/daily_sales/collect")
    assert response.status_code == 404


def test_collect_now_runs_workflow_with_wired_fake_browser(services, tmp_path) -> None:
    from pathlib import Path

    from smartops.domain.enums import ExtractionLayer
    from smartops.ports.browser import ExtractionRequest, ExtractionResult
    from smartops.ports.validation import ValidationReport
    from smartops.workflows.profiles import SystemRegistry

    class FakeBrowser:
        def extract(self, request: ExtractionRequest) -> ExtractionResult:
            target = Path(request.destination_dir) / "daily_sales.csv"
            target.write_bytes(b"date,amount\n2026-01-01,1\n")
            return ExtractionResult(
                ok=True,
                layer_used=ExtractionLayer.NETWORK,
                file_path=target,
                original_name=target.name,
                size_bytes=target.stat().st_size,
            )

        def capture_evidence(self, run_id: str) -> dict:
            return {}

    class FakeValidator:
        def validate(self, path, rules) -> ValidationReport:
            return ValidationReport(passed=True, sha256="x", row_count=1)

    (tmp_path / "erp.yaml").write_text(SYSTEM_YAML, encoding="utf-8")
    services.systems = SystemRegistry.load(tmp_path)
    services.browser = FakeBrowser()
    services.validator = FakeValidator()

    client = TestClient(create_app(services))
    response = client.post("/api/systems/erp_demo/daily_sales/collect")

    assert response.status_code == 201
    assert response.json()["status"] == "succeeded"
