from __future__ import annotations

from fastapi.testclient import TestClient

from smartops.api.app import create_app
from smartops.credentials import InMemoryCredentialStore
from smartops.workflows.profiles import SystemRegistry


UNATTENDED = """
key: mes_demo
name: MES demo
auth:
  mode: unattended
  login_url: https://example.invalid/login
  logged_in_selector: '#user-menu'
  login_selector: '#login-form'
  username_selector: '#username'
  password_selector: '#password'
  submit_selector: 'button[type=submit]'
reports:
  - key: daily
    url: https://example.invalid/report
    download_selector: '#download'
"""


def test_credential_store_round_trip_and_masked_repr() -> None:
    store = InMemoryCredentialStore()
    store.put("mes_demo", "m.labib", "super-secret")
    item = store.get("mes_demo")
    assert item and item.username == "m.labib" and item.password == "super-secret"
    assert "super-secret" not in repr(item)
    assert store.delete("mes_demo") is True
    assert store.get("mes_demo") is None


def test_credentials_api_never_returns_password(services, tmp_path) -> None:
    (tmp_path / "mes.yaml").write_text(UNATTENDED, encoding="utf-8")
    services.systems = SystemRegistry.load(tmp_path)
    client = TestClient(create_app(services))
    assert client.get("/api/credentials").json()["items"][0]["stored"] is False

    denied = client.put("/api/credentials/mes_demo", json={"username": "u", "password": "p"})
    assert denied.status_code == 403
    saved = client.put(
        "/api/credentials/mes_demo",
        headers={"X-SmartOps-Request": "web"},
        json={"username": "m.labib", "password": "super-secret"},
    )
    assert saved.status_code == 200
    assert saved.json() == {"system_key": "mes_demo", "credential_ref": "mes_demo", "stored": True, "username": "m.labib"}
    listing = client.get("/api/credentials").json()["items"][0]
    assert listing["stored"] is True and listing["username"] == "m.labib"
    assert "password" not in listing and "super-secret" not in saved.text

    deleted = client.delete("/api/credentials/mes_demo", headers={"X-SmartOps-Request": "web"})
    assert deleted.status_code == 200 and deleted.json()["stored"] is False
