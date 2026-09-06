from __future__ import annotations

from contextlib import contextmanager

from smartops.adapters.browser.playwright_engine import PlaywrightBrowserAdapter
from smartops.config import BrowserSettings
from smartops.credentials import InMemoryCredentialStore
from smartops.ports.browser import ExtractionRequest


def test_popup_login_clicks_the_main_page_then_fills_only_the_sso_popup(tmp_path) -> None:
    calls: list[tuple[str, str]] = []
    state = {"signed_out": True}
    store = InMemoryCredentialStore()
    store.put("mes-demo", "demo-operator", "demo-secret-not-real")

    class Locator:
        def __init__(self, page_name: str, selector: str):
            self.page_name, self.selector = page_name, selector

        def count(self):
            if self.selector == "#login-form":
                return 1 if state["signed_out"] else 0
            if self.selector == "#dashboard":
                return 0 if state["signed_out"] else 1
            return 1

        def click(self):
            calls.append((self.page_name, f"click:{self.selector}"))
            if self.selector == "#submitButton":
                state["signed_out"] = False

        def fill(self, value: str):
            calls.append((self.page_name, f"fill:{self.selector}:{value}"))

    class Popup:
        def locator(self, selector):
            return Locator("popup", selector)

        def wait_for_load_state(self, *args, **kwargs):
            calls.append(("popup", "loaded"))

        def wait_for_event(self, event, **kwargs):
            calls.append(("popup", f"wait:{event}"))

    popup = Popup()

    class MainPage:
        url = "https://example.invalid/login"

        def locator(self, selector):
            return Locator("main", selector)

        def goto(self, *args, **kwargs):
            calls.append(("main", "goto"))

        @contextmanager
        def expect_popup(self):
            class Info:
                value = popup

            yield Info()

        def wait_for_selector(self, selector, **kwargs):
            calls.append(("main", f"wait:{selector}"))

    class Tracing:
        def stop(self):
            calls.append(("context", "trace:stop"))

        def start(self, **kwargs):
            calls.append(("context", "trace:start"))

    class Context:
        tracing = Tracing()

        def new_cdp_session(self, page):
            raise RuntimeError("not needed by this test double")

    adapter = PlaywrightBrowserAdapter(
        BrowserSettings(headless=True), credential_store=store
    )
    request = ExtractionRequest(
        system="mes",
        report="plan",
        destination_dir=tmp_path,
    )
    filters = {
        "login_url": "https://example.invalid/login",
        "login_selector": "#login-form",
        "logged_in_selector": "#dashboard",
        "credential_ref": "mes-demo",
        "language_selector": "#english",
        "popup_trigger_selector": "#sso",
        "username_selector": "#userNameInput",
        "password_selector": "#passwordInput",
        "submit_selector": "#submitButton",
        "notice_close_selector": "#close-notice",
    }

    result = adapter._ensure_authenticated(Context(), MainPage(), request, filters)

    assert result is None
    assert ("main", "click:#english") in calls
    assert ("main", "click:#sso") in calls
    assert ("popup", "fill:#userNameInput:demo-operator") in calls
    assert ("popup", "fill:#passwordInput:demo-secret-not-real") in calls
    assert ("popup", "click:#submitButton") in calls
    assert ("main", "click:#close-notice") in calls
    assert calls.index(("main", "click:#sso")) < calls.index(
        ("popup", "fill:#userNameInput:demo-operator")
    )
