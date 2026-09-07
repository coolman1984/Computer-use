"""Replay has to continue on the page that sign-in proved, and leave it alone.

The recorded G-MES task never got past its first step even after the sign-in
itself started working. Two things in the replay adapter threw the signed-in
state away:

* the page authentication settled on was discarded, because replay never asked
  for it (``on_authenticated_page`` was not passed), and
* the "go back to the recorded starting page" guard compared the whole address.
  A single-document application routes by rewriting the query, so the address
  differed by a route marker and the guard reloaded the document — which
  unmounts the signed-in frames and puts the login screen back.

This test drives a real browser through the Nexacro-like fixture: SSO in a
popup that closes itself, a Notice on top, the application mounted over a login
frame that stays in the DOM, and a route marker added to the address without a
document load. It then asserts that step 1 ran on the adopted page and that the
application document was fetched exactly once for the whole run.
"""

from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlsplit

import pytest

pytest.importorskip("playwright.sync_api")

from smartops.adapters.browser import playwright_engine
from smartops.adapters.browser.playwright_engine import PlaywrightBrowserAdapter
from smartops.adapters.browser.replay import ReplaySession
from smartops.config import BrowserSettings
from smartops.credentials import InMemoryCredentialStore
from smartops.ports.browser import ReplayRequest
from tests.recorded_site import LocalSite


def _resolve_executable_path() -> str | None:
    env_path = os.environ.get("SMARTOPS_TEST_CHROMIUM_PATH") or os.environ.get(
        "PLAYWRIGHT_CHROMIUM_PATH"
    )
    if env_path:
        return env_path
    default = Path("/opt/pw-browsers/chromium")
    return str(default) if default.exists() else None


class _SpySession(ReplaySession):
    """A real replay session that also remembers which page each step ran on."""

    instances: list["_SpySession"] = []

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.adopted: list[object] = []
        self.acted_on: list[object] = []
        _SpySession.instances.append(self)

    def adopt(self, page) -> None:  # only exists once the fix is in place
        self.adopted.append(page)
        super().adopt(page)

    def perform(self, action) -> None:
        self.acted_on.append(self._current)
        super().perform(action)


def _action(seq: int, action: str, **kwargs) -> dict:
    step = {
        "seq": seq,
        "action": action,
        "target": {"page": "main", "frame": ""},
        "locator": {},
        "inputs": {},
        "success": {"type": "none"},
        "retry": {"max_attempts": 1, "safe_to_repeat": True},
    }
    step.update(kwargs)
    return step


@pytest.mark.skipif(
    os.environ.get("SMARTOPS_SKIP_HEADED_TESTS") == "1",
    reason="Browser tests are disabled in this environment",
)
def test_replay_runs_step_one_on_the_adopted_page_without_reloading_it(
    tmp_path, monkeypatch
) -> None:
    from playwright.sync_api import sync_playwright

    store = InMemoryCredentialStore()
    store.put("fixture-portal", "fixture-user", "fixture-secret")
    _SpySession.instances.clear()
    monkeypatch.setattr(playwright_engine, "ReplaySession", _SpySession)

    destination = tmp_path / "out"
    destination.mkdir(parents=True, exist_ok=True)

    with LocalSite() as site:
        start_url = f"{site.base_url}/nexacro_like_login.html"
        app_path = urlsplit(start_url).path
        filters = {
            "login_url": start_url,
            "login_selector": "#login-frame",
            "logged_in_selector": "#app-frame",
            "popup_trigger_selector": "#sso",
            "notice_close_selector": "#notice-close",
            "credential_ref": "fixture-portal",
            "username_selector": "#userNameInput",
            "password_selector": "#passwordInput",
            "submit_selector": "#submitButton",
            "notice_timeout_ms": 15000,
            "notice_probe_timeout_ms": 2000,
            "login_success_timeout_ms": 20000,
        }
        plan = {
            "plan_version": 2,
            "start_url": start_url,
            "actions": [
                _action(
                    1,
                    "click",
                    locator={"strategy": "css", "value": "#open-report"},
                    success={"type": "selector_visible", "value": "#report-ready"},
                ),
                _action(
                    2,
                    "fill",
                    locator={"strategy": "css", "value": "#report-reference"},
                    inputs={"value": "INV-8842"},
                    success={"type": "value_equals", "value": "INV-8842"},
                ),
            ],
            # The fixture produces no workbook on purpose: what is under test is
            # which page the steps run on, not the download ladder. The run's
            # own verdict is therefore "no file", and the step results below are
            # the outcome that matters here.
            "expects_download": False,
            "expected_download_count": 0,
        }
        request = ReplayRequest(
            system="fixture",
            report="worklist",
            destination_dir=destination,
            plan=plan,
            filters=filters,
            timeout_seconds=60.0,
        )

        # Every fetch of the application document, so a reload after sign-in
        # cannot hide. The SSO window uses the same path with ?sso=1 and is not
        # the application document.
        document_loads: list[str] = []

        def note_document(request_event) -> None:
            try:
                if request_event.resource_type != "document":
                    return
                if "sso=1" in request_event.url:
                    return
                if urlsplit(request_event.url).path == app_path:
                    document_loads.append(request_event.url)
            except Exception:
                pass

        with sync_playwright() as playwright:
            try:
                browser = playwright.chromium.launch(
                    headless=True, executable_path=_resolve_executable_path()
                )
            except Exception as exc:  # no browser binary available here
                pytest.skip(f"Chromium is not available ({type(exc).__name__})")
            try:
                context = browser.new_context(
                    viewport={"width": 1024, "height": 768}, accept_downloads=True
                )
                context.on("request", note_document)
                adapter = PlaywrightBrowserAdapter(
                    BrowserSettings(headless=True), credential_store=store
                )
                result = adapter._replay_with_context(context, request, plan, 0.0)
            finally:
                browser.close()

    assert _SpySession.instances, "the replay session was never built"
    session = _SpySession.instances[-1]

    # 1. Both recorded steps ran and proved themselves.
    assert [step["ok"] for step in result.step_results] == [True, True], result.step_results

    # 2. Replay was handed the signed-in page and performed step 1 on it.
    assert session.adopted, "authentication never handed the signed-in page to replay"
    assert session.acted_on[0] is session.adopted[-1], (
        "step 1 ran on a page other than the one the classifier marked signed in"
    )

    # 3. The application document was loaded once — by open(), never again. A
    #    second load here is the reload that used to discard the session.
    assert len(document_loads) == 1, (
        f"the signed-in page was navigated again after sign-in: {document_loads}"
    )
