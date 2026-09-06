from __future__ import annotations

from pathlib import Path
import time

from fastapi.testclient import TestClient
import pytest

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


def test_credentials_api_never_accepts_or_returns_a_password(services, tmp_path) -> None:
    (tmp_path / "mes.yaml").write_text(UNATTENDED, encoding="utf-8")
    services.systems = SystemRegistry.load(tmp_path)
    client = TestClient(create_app(services))
    assert client.get("/api/credentials").json()["items"][0]["stored"] is False

    unsupported = client.put(
        "/api/credentials/mes_demo",
        headers={"X-SmartOps-Request": "web"},
        json={"username": "m.labib", "password": "super-secret"},
    )
    assert unsupported.status_code == 405

    # Simulate what the isolated native child process writes directly to the
    # store, then verify the HTTP read side remains password-free.
    services.credentials.put("mes_demo", "m.labib", "super-secret")
    listing = client.get("/api/credentials").json()["items"][0]
    assert listing["stored"] is True and listing["username"] == "m.labib"
    assert "password" not in listing and "super-secret" not in str(listing)

    deleted = client.delete("/api/credentials/mes_demo", headers={"X-SmartOps-Request": "web"})
    assert deleted.status_code == 200 and deleted.json()["stored"] is False


def test_secure_prompt_is_started_from_the_local_ui_without_a_password_payload(
    services, tmp_path
) -> None:
    (tmp_path / "mes.yaml").write_text(UNATTENDED, encoding="utf-8")
    services.systems = SystemRegistry.load(tmp_path)

    class FakePrompt:
        def to_dict(self):
            return {
                "system_key": "mes_demo",
                "status": "waiting",
                "message": "Enter the credential in the separate Windows window.",
                "active": True,
                "saved": False,
                "error": None,
            }

    class FakePromptManager:
        def start(self, system_key: str):
            assert system_key == "mes_demo"
            return FakePrompt()

        def status(self, system_key: str):
            assert system_key == "mes_demo"
            return FakePrompt()

    services.credential_prompts = FakePromptManager()
    client = TestClient(create_app(services))

    denied = client.post("/api/credentials/mes_demo/prompt")
    assert denied.status_code == 403
    started = client.post(
        "/api/credentials/mes_demo/prompt",
        headers={"X-SmartOps-Request": "web"},
    )
    assert started.status_code == 200
    assert started.json()["status"] == "waiting"
    assert "password" not in started.text.lower()
    assert client.get("/api/credentials/mes_demo/prompt").json()["active"] is True


def test_signin_page_uses_a_separate_windows_prompt_instead_of_a_password_field() -> None:
    html = Path("web/credentials.html").read_text(encoding="utf-8")
    assert 'id="password"' not in html
    assert "separate secure Windows window" in html


def test_prompt_manager_launches_an_isolated_process_without_credentials_in_its_command(
    services, tmp_path
) -> None:
    try:
        from smartops.credential_prompt import CredentialPromptManager
    except ImportError:
        pytest.fail("the isolated credential prompt manager is not implemented")

    (tmp_path / "mes.yaml").write_text(UNATTENDED, encoding="utf-8")
    services.systems = SystemRegistry.load(tmp_path)
    launched: list[tuple[list[str], dict]] = []

    class FinishedProcess:
        def wait(self, timeout=None):
            services.credentials.put("mes_demo", "operator", "kept-out-of-command")
            return 0

    def fake_process(command, **kwargs):
        launched.append((list(command), kwargs))
        return FinishedProcess()

    manager = CredentialPromptManager(services, process_factory=fake_process)
    opened = manager.start("mes_demo")
    # A real native window remains active here; the fake child is allowed to
    # finish before this thread is scheduled again.
    assert opened.active or opened.saved
    deadline = time.monotonic() + 3
    while manager.status("mes_demo").active and time.monotonic() < deadline:
        time.sleep(0.01)

    settled = manager.status("mes_demo")
    assert settled.status == "completed" and settled.saved
    command, options = launched[0]
    joined = " ".join(command)
    assert "kept-out-of-command" not in joined
    assert "operator" not in joined
    assert options.get("stdin") is not None
