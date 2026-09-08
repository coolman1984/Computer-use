"""The signed-in classifier: a covered login frame is not a sign-out.

G-MES finishes AD SSO inside the same document. It mounts the signed-in frames
and a Notice dialog while leaving the login frame in the DOM, covered rather
than hidden, so ``is_visible()`` on the login control still returns true. The
old rule "a visible login control means signed out" therefore reported a
successful login as an authentication failure. These tests pin the replacement
rule down with page doubles, and once against a real browser on a fixture page
that reproduces the same mechanism.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from smartops.adapters.browser.authentication import (
    NOTICE_OPEN,
    SIGNED_IN,
    SIGNED_OUT,
    TRANSITIONING,
    classify_auth_state,
    ensure_authenticated,
    session_expired,
    state_is_expired,
)
from smartops.credentials import InMemoryCredentialStore

FILTERS = {
    "login_url": "https://portal.test/entry",
    "login_selector": "#login-frame",
    "logged_in_selector": "#app-frame",
    "notice_close_selector": "#notice-close",
    "credential_ref": "portal-test",
    "username_selector": "#userNameInput",
    "password_selector": "#passwordInput",
    "submit_selector": "#submitButton",
}


# ---------- page doubles ----------


class _FakeTimeout(Exception):
    """Stands in for Playwright's TimeoutError in the doubles."""


class _Element:
    def __init__(
        self, *, visible: bool = True, on_top: bool = True, on_screen: bool = True
    ) -> None:
        self.visible = visible
        self.on_top = on_top
        # Inside the viewport. Playwright's is_visible() does not ask this; the
        # classifier's signed-in marker does.
        self.on_screen = on_screen


class _Locator:
    """Behaves like a Playwright locator for the signals the classifier uses."""

    def __init__(self, page: "_Page", selector: str, elements: list[_Element]) -> None:
        self._page = page
        self._selector = selector
        self._elements = elements

    def count(self) -> int:
        return len(self._elements)

    def nth(self, index: int) -> "_Locator":
        return type(self)(self._page, self._selector, self._elements[index : index + 1])

    @property
    def first(self) -> "_Locator":
        return self.nth(0)

    def is_visible(self) -> bool:
        return bool(self._elements) and self._elements[0].visible

    def click(self, *, trial: bool = False, timeout: int | None = None, **_: object) -> None:
        if not self._elements:
            raise _FakeTimeout("no element")
        element = self._elements[0]
        if not element.visible or not element.on_top:
            # Exactly what a trial click reports for a covered control.
            raise _FakeTimeout("element does not receive pointer events")
        if not trial:
            self._page.clicks.append(self._selector)
            self._page.on_click(self._selector)

    def fill(self, value: str) -> None:
        self._page.fills.append(self._selector)


class _MeasuringLocator(_Locator):
    """A real locator's evaluate_all: elements are measured against the viewport."""

    def evaluate_all(self, _script: str):
        return any(element.visible and element.on_screen for element in self._elements)

    def filter(self, *, visible: bool | None = None, **_: object) -> "_MeasuringLocator":
        if visible is None:
            return self
        kept = [element for element in self._elements if element.visible is visible]
        return _MeasuringLocator(self._page, self._selector, kept)


class _FilteringLocator(_Locator):
    """A locator on Playwright >= 1.51, where filter(visible=True) exists."""

    def filter(self, *, visible: bool | None = None, **_: object) -> "_FilteringLocator":
        if visible is None:
            return self
        kept = [element for element in self._elements if element.visible is visible]
        return _FilteringLocator(self._page, self._selector, kept)


class _SimpleLocator:
    """A lightweight double: count() and a bare click(), nothing else."""

    def __init__(self, page: "_Page", selector: str, elements: list[_Element]) -> None:
        self._page = page
        self._selector = selector
        self._elements = elements

    def count(self) -> int:
        return len(self._elements)

    def click(self) -> None:
        self._page.clicks.append(self._selector)
        self._page.on_click(self._selector)


class _Page:
    """A page double whose DOM is a selector -> elements mapping."""

    locator_class: type = _Locator

    def __init__(
        self,
        elements: dict[str, list[_Element]],
        *,
        notice_title_visible: bool = False,
        url: str = "https://portal.test/entry",
    ) -> None:
        self.elements = elements
        self.notice_title_visible = notice_title_visible
        self.url = url
        self.clicks: list[str] = []
        self.fills: list[str] = []
        self.waits = 0

    def locator(self, selector: str):
        return self.locator_class(self, selector, self.elements.get(selector, []))

    def evaluate(self, script: str, *_args: object) -> bool:
        if "candidate.click()" in script:  # the scoped DOM close fallback
            if self.notice_title_visible:
                self.notice_title_visible = False
                return True
            return False
        return bool(self.notice_title_visible)  # detection only

    def wait_for_timeout(self, _ms: int) -> None:
        self.waits += 1

    def is_closed(self) -> bool:
        return False

    def on_click(self, selector: str) -> None:
        """Closing the Notice takes it out of the DOM, like the real dialog."""
        if selector == FILTERS["notice_close_selector"]:
            self.elements[selector] = []
            self.notice_title_visible = False


class _FilteringPage(_Page):
    locator_class = _FilteringLocator


class _MeasuringPage(_Page):
    locator_class = _MeasuringLocator


class _SimplePage(_Page):
    locator_class = _SimpleLocator

    def evaluate(self, script: str, *_args: object) -> bool:
        raise AttributeError("this double has no page.evaluate")


class _Context:
    def __init__(self, pages: list[object]) -> None:
        self.pages = pages


def _signed_in_dom(*, notice: bool) -> dict[str, list[_Element]]:
    """The G-MES shape: login frame mounted but covered, app marker visible."""
    return {
        "#login-frame": [_Element(visible=True, on_top=False)],
        # Two matches, only the second visible: the marker must be found across
        # all matches, not just the first one.
        "#app-frame": [_Element(visible=False), _Element(visible=True)],
        "#notice-close": [_Element(visible=True)] if notice else [],
    }


# ---------- the states ----------


def test_covered_login_frame_with_notice_is_notice_open_then_signed_in() -> None:
    page = _Page(_signed_in_dom(notice=True), notice_title_visible=True)

    assert classify_auth_state(page, FILTERS) == NOTICE_OPEN
    # The login control is still "visible" in the old sense; that is the whole
    # point of the defect being fixed here.
    assert page.locator("#login-frame").is_visible() is True

    page.locator("#notice-close").first.click()

    assert classify_auth_state(page, FILTERS) == SIGNED_IN
    assert session_expired(page, FILTERS) is False


def test_login_on_top_without_a_signed_in_marker_is_signed_out() -> None:
    page = _Page(
        {
            "#login-frame": [_Element(visible=True, on_top=True)],
            "#app-frame": [],
            "#notice-close": [],
        }
    )

    assert classify_auth_state(page, FILTERS) == SIGNED_OUT
    assert session_expired(page, FILTERS) is True
    assert state_is_expired(SIGNED_OUT, page, FILTERS) is True


def test_ambiguous_login_selector_is_transitional_not_a_credential_target() -> None:
    page = _Page(
        {
            "#login-frame": [_Element(visible=True, on_top=True), _Element(visible=True, on_top=True)],
            "#app-frame": [],
            "#notice-close": [],
        }
    )

    assert classify_auth_state(page, FILTERS) == TRANSITIONING
    assert session_expired(page, FILTERS) is False


def test_both_markers_absent_is_transitioning_and_fails_closed_to_login() -> None:
    page = _Page({"#login-frame": [], "#app-frame": [], "#notice-close": []})

    state = classify_auth_state(page, FILTERS)

    assert state == TRANSITIONING
    # Neither marker present: prefer the login screen over guessing signed in.
    assert state_is_expired(state, page, FILTERS) is True
    # session_expired() itself stays narrow: only an unambiguous sign-out.
    assert session_expired(page, FILTERS) is False


def test_both_markers_present_is_transitioning_but_never_sent_to_login() -> None:
    page = _Page(
        {
            "#login-frame": [_Element(visible=True, on_top=True)],
            "#app-frame": [_Element(visible=True)],
            "#notice-close": [],
        }
    )

    state = classify_auth_state(page, FILTERS)

    assert state == TRANSITIONING
    assert state_is_expired(state, page, FILTERS) is False


def test_marker_parked_below_the_fold_is_not_signed_in() -> None:
    """The G-MES login page, as its replay trace showed it.

    Nexacro mounts the whole signed-in frameset on the login page, one viewport
    below the fold. Every match of the marker is "visible" to Playwright, none
    is on screen, and the login control takes clicks: that is a sign-out.
    """
    page = _MeasuringPage(
        {
            "#login-frame": [_Element(visible=True, on_top=True)],
            "#app-frame": [_Element(visible=True, on_screen=False) for _ in range(34)],
            "#notice-close": [],
        }
    )

    # The old meaning of "visible" still says yes; that was the trap.
    assert page.locator("#app-frame").filter(visible=True).count() == 34

    state = classify_auth_state(page, FILTERS)

    assert state == SIGNED_OUT
    assert session_expired(page, FILTERS) is True
    assert state_is_expired(state, page, FILTERS) is True


def test_marker_moved_on_screen_over_the_login_frame_is_signed_in() -> None:
    """After sign-in the frameset moves to the top and covers the login frame."""
    page = _MeasuringPage(
        {
            "#login-frame": [_Element(visible=True, on_top=False)],
            "#app-frame": [_Element(visible=True, on_screen=True)],
            "#notice-close": [],
        }
    )

    assert classify_auth_state(page, FILTERS) == SIGNED_IN


def test_ensure_authenticated_stops_on_the_ambiguous_state_without_credentials() -> None:
    """Login control clickable *and* marker on screen: report it, do nothing else.

    Before this rule the page was taken for signed in, replay ran step 1 on a
    login screen, and the run was reported as "the site has probably changed".
    """
    page = _MeasuringPage(
        {
            "#login-frame": [_Element(visible=True, on_top=True)],
            "#app-frame": [_Element(visible=True, on_screen=True)],
            "#notice-close": [],
            "#userNameInput": [_Element()],
            "#passwordInput": [_Element()],
            "#submitButton": [_Element()],
        }
    )
    adopted: list[object] = []
    store = InMemoryCredentialStore()
    store.put(FILTERS["credential_ref"], "not-a-real-user", "not-a-real-secret")

    message = ensure_authenticated(
        _Context([page]),
        page,
        system="portal",
        filters={**FILTERS, "notice_probe_timeout_ms": 50},
        credential_store=store,
        manage_tracing=False,
        on_authenticated_page=adopted.append,
    )

    assert message is not None
    assert "Could not tell whether portal is signed in" in message
    assert adopted == []
    assert page.fills == []
    assert FILTERS["submit_selector"] not in page.clicks


def test_notice_is_detected_from_its_title_when_no_selector_is_configured() -> None:
    filters = {key: value for key, value in FILTERS.items() if key != "notice_close_selector"}
    page = _Page(_signed_in_dom(notice=False), notice_title_visible=True)

    assert classify_auth_state(page, filters) == NOTICE_OPEN
    assert classify_auth_state(page, filters, ignore_notice=True) == SIGNED_IN


def test_visible_marker_is_found_through_filter_visible_when_available() -> None:
    page = _FilteringPage(_signed_in_dom(notice=False))

    assert classify_auth_state(page, FILTERS) == SIGNED_IN


# ---------- doubles without trial clicks ----------


def test_double_without_trial_click_falls_back_to_visibility() -> None:
    signed_out = _SimplePage(
        {"#login-frame": [_Element()], "#app-frame": [], "#notice-close": []}
    )
    signed_in = _SimplePage(
        {"#login-frame": [], "#app-frame": [_Element()], "#notice-close": []}
    )

    assert classify_auth_state(signed_out, FILTERS) == SIGNED_OUT
    assert classify_auth_state(signed_in, FILTERS) == SIGNED_IN


# ---------- the caller ----------


def test_ensure_authenticated_accepts_the_covered_login_frame() -> None:
    """The exact case that used to fail "while following the signed-in tab"."""
    page = _Page(_signed_in_dom(notice=True), notice_title_visible=True)
    context = _Context([page])
    adopted: list[object] = []
    store = InMemoryCredentialStore()
    store.put(FILTERS["credential_ref"], "not-a-real-user", "not-a-real-secret")

    message = ensure_authenticated(
        context,
        page,
        system="portal",
        filters=FILTERS,
        credential_store=store,
        manage_tracing=False,
        on_authenticated_page=adopted.append,
    )

    assert message is None
    assert adopted == [page]
    # The Notice was closed, and no credential was ever submitted.
    assert FILTERS["notice_close_selector"] in page.clicks
    assert FILTERS["submit_selector"] not in page.clicks
    assert classify_auth_state(page, FILTERS) == SIGNED_IN


def test_ensure_authenticated_still_reports_a_real_sign_out() -> None:
    page = _Page(
        {
            "#login-frame": [_Element(visible=True, on_top=True)],
            "#app-frame": [],
            "#notice-close": [],
        }
    )
    filters = {key: value for key, value in FILTERS.items() if key != "credential_ref"}

    message = ensure_authenticated(
        context=_Context([page]),
        page=page,
        system="portal",
        filters=filters,
        credential_store=None,
        manage_tracing=False,
    )

    assert message is not None
    assert "Session expired" in message


# ---------- one real browser against the Nexacro-like fixture ----------


def _resolve_executable_path() -> str | None:
    env_path = os.environ.get("SMARTOPS_TEST_CHROMIUM_PATH") or os.environ.get(
        "PLAYWRIGHT_CHROMIUM_PATH"
    )
    if env_path:
        return env_path
    default = Path("/opt/pw-browsers/chromium")
    return str(default) if default.exists() else None


@pytest.mark.skipif(
    os.environ.get("SMARTOPS_SKIP_HEADED_TESTS") == "1",
    reason="Browser tests are disabled in this environment",
)
def test_real_browser_same_document_sso_ends_signed_in() -> None:
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from tests.recorded_site import LocalSite

    store = InMemoryCredentialStore()
    store.put("fixture-portal", "fixture-user", "fixture-secret")

    with LocalSite() as site:
        login_url = f"{site.base_url}/nexacro_like_login.html"
        filters = {
            "login_url": login_url,
            "login_selector": "#login-frame",
            "logged_in_selector": "#app-frame",
            "popup_trigger_selector": "#sso",
            "notice_close_selector": "#notice-close",
            "credential_ref": "fixture-portal",
            "username_selector": "#userNameInput",
            "password_selector": "#passwordInput",
            "submit_selector": "#submitButton",
            # The configured budgets must reach the adapter (they used to be
            # dropped by the profile parser).
            "notice_timeout_ms": 15000,
            "notice_probe_timeout_ms": 2000,
            "login_success_timeout_ms": 20000,
        }
        with sync_playwright() as playwright:
            try:
                browser = playwright.chromium.launch(
                    headless=True, executable_path=_resolve_executable_path()
                )
            except Exception as exc:  # no browser binary available here
                pytest.skip(f"Chromium is not available ({type(exc).__name__})")
            try:
                context = browser.new_context(viewport={"width": 1024, "height": 768})
                page = context.new_page()
                page.goto(login_url, wait_until="domcontentloaded")

                # The application frame is already mounted, full size, parked
                # one viewport below the fold — exactly what the G-MES login
                # page does. Playwright calls it visible; it is not on screen.
                assert page.locator("#app-frame").is_visible() is True
                assert classify_auth_state(page, filters) == SIGNED_OUT

                adopted: list[object] = []
                message = ensure_authenticated(
                    context,
                    page,
                    system="fixture",
                    filters=filters,
                    credential_store=store,
                    manage_tracing=False,
                    on_authenticated_page=adopted.append,
                )

                assert message is None, message
                assert adopted, "the authenticated page was never handed back"
                signed_in_page = adopted[0]
                # The login frame is still mounted and still "visible": only the
                # classifier's independent signals tell signed-in from signed-out.
                assert signed_in_page.locator("#login-frame").is_visible() is True
                assert classify_auth_state(signed_in_page, filters) == SIGNED_IN
            finally:
                browser.close()
