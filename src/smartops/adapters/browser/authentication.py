"""One credential-isolated browser login used by extraction, replay, and recording."""
from __future__ import annotations

import inspect
import time
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit

from ...credentials import CredentialStore

# The four states a loaded page can be in as far as sign-in is concerned.
SIGNED_IN = "signed_in"
SIGNED_OUT = "signed_out"
NOTICE_OPEN = "notice_open"
TRANSITIONING = "transitioning"

# Short, explicit budgets. Nothing here may inherit the context default
# (which is minutes) or a single stuck control would consume the whole run.
_TRIAL_CLICK_TIMEOUT_MS = 750
_NOTICE_CLICK_TIMEOUT_MS = 3000
# A Nexacro work frame can hold thousands of nodes under one prefix selector;
# the old 50-match cap could miss the only visible signed-in marker.
_VISIBILITY_SCAN_LIMIT = 2000

# Whether any of the matched elements is rendered *inside the viewport*.
# Playwright's own visibility check only asks for a non-empty box and no
# display:none / visibility:hidden, so an element parked one screen below the
# fold still counts as visible. G-MES mounts its whole signed-in frameset that
# way on the login page (top = frame height), which made the signed-in marker
# "visible" before anyone had signed in. Returns a boolean, never page text.
_ON_SCREEN_JS = """
(elements) => elements.some((el) => {
  if (!el || !el.getBoundingClientRect) return false;
  const r = el.getBoundingClientRect();
  if (!(r.width > 0 && r.height > 0)) return false;
  const s = getComputedStyle(el);
  if (s.display === 'none' || s.visibility === 'hidden') return false;
  const vw = window.innerWidth || document.documentElement.clientWidth;
  const vh = window.innerHeight || document.documentElement.clientHeight;
  return r.bottom > 0 && r.right > 0 && r.top < vh && r.left < vw;
})
"""

# Detection only: returns a boolean, never any page text.
_NOTICE_TITLE_JS = """
() => {
  const visible = (el) => {
    if (!el || !el.getBoundingClientRect) return false;
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 &&
      s.display !== 'none' && s.visibility !== 'hidden';
  };
  return Array.from(document.querySelectorAll('body *')).some((el) =>
    visible(el) && (el.innerText || el.textContent || '').trim() === 'Notice'
  );
}
"""


def _supports_keyword(func: Any, name: str) -> bool:
    """Whether a callable accepts a keyword argument, so doubles stay usable.

    The page/locator doubles used in unit tests implement only the handful of
    methods they need. Feature-detecting instead of assuming keeps production
    code honest against both a real Playwright locator and a fake, and it is
    also how the Playwright >= 1.51 ``filter(visible=...)`` option is used
    without raising the pinned minimum version.
    """
    if not callable(func):
        return False
    try:
        signature = inspect.signature(func)
    except (TypeError, ValueError):
        # A C-level or otherwise unintrospectable callable: assume the real API.
        return True
    parameters = signature.parameters
    if name in parameters:
        return True
    return any(
        parameter.kind is inspect.Parameter.VAR_KEYWORD for parameter in parameters.values()
    )


def _first(locator: Any) -> Any:
    return getattr(locator, "first", locator)


def _visible(locator: Any) -> bool:
    """Return whether at least one matching element is actually visible.

    Every match is considered, not just the first one: the signed-in marker of
    a Nexacro application is frequently the n-th match of a prefix selector.
    """
    try:
        count = locator.count()
        if count < 1:
            return False
        filter_fn = getattr(locator, "filter", None)
        if _supports_keyword(filter_fn, "visible"):
            try:
                return locator.filter(visible=True).count() > 0
            except Exception:
                pass
        if hasattr(locator, "nth"):
            return any(
                locator.nth(index).is_visible()
                for index in range(min(count, _VISIBILITY_SCAN_LIMIT))
            )
        return bool(_first(locator).is_visible())
    except AttributeError:
        # Lightweight port fakes used outside a real browser may only expose
        # count(). Their historical meaning was "present and visible".
        try:
            return locator.count() > 0
        except Exception:
            return False
    except Exception:
        return False


def _visible_on_screen(locator: Any) -> bool:
    """Whether at least one match is visible *and* inside the viewport.

    Used for the signed-in marker only. On a real locator the matched elements
    are measured in the page; a double without ``evaluate_all`` keeps the
    plain visibility meaning so the existing unit doubles stay valid.
    """
    evaluate_all = getattr(locator, "evaluate_all", None)
    if not callable(evaluate_all):
        return _visible(locator)
    try:
        if locator.count() < 1:
            return False
        return bool(evaluate_all(_ON_SCREEN_JS))
    except Exception:
        # Mid-navigation the page refuses queries; that is "not proven", not
        # "signed in", and the caller keeps polling.
        return False


def _login_marker(filters: dict[str, Any]) -> str:
    return filters.get("login_selector") or filters.get("popup_trigger_selector") or ""


def _auth_markers_configured(filters: dict[str, Any]) -> bool:
    """Whether this system describes any sign-in state at all."""
    return bool(_login_marker(filters) or filters.get("logged_in_selector"))


def login_on_top(page: Any, filters: dict[str, Any]) -> bool:
    """Whether the login control exists *and* would receive a pointer event.

    This is the signal that replaces "the login control is visible". G-MES
    completes SSO inside the same document and leaves the login frame mounted
    underneath the application frame: it is covered, not hidden, so
    ``is_visible()`` stays true and a successful login used to be read as a
    sign-out. A trial click runs Playwright's actionability checks — including
    "receives events" — without clicking anything.
    """
    selector = _login_marker(filters)
    if not selector:
        return False
    try:
        locator = page.locator(selector)
        if locator.count() < 1:
            return False
    except Exception:
        return False
    target = _first(locator)
    click = getattr(target, "click", None)
    if not _supports_keyword(click, "trial"):
        # A double without trial clicks keeps the historical meaning.
        return _visible(locator)
    try:
        click(trial=True, timeout=_TRIAL_CLICK_TIMEOUT_MS)
        return True
    except Exception:
        return False


def signed_in_marker(page: Any, filters: dict[str, Any]) -> bool:
    """Whether the configured signed-in marker has a match that is on screen.

    "On screen" rather than "visible": the trace of a failed G-MES replay
    showed 34 "visible" matches of the marker on the *login* page, all of them
    one viewport below the fold where Nexacro parks the application frameset
    until sign-in moves it to the top. Only a match the user could actually see
    counts as evidence of being signed in.
    """
    selector = filters.get("logged_in_selector")
    if not selector:
        return False
    try:
        return _visible_on_screen(page.locator(selector))
    except Exception:
        return False


def ambiguous_auth_state(page: Any, filters: dict[str, Any]) -> bool:
    """Both signals at once: the login control takes clicks *and* the marker is on screen.

    This is the one transitional shape that must never be resolved by guessing.
    Reading it as signed-in used to send replay to step 1 on a login screen and
    report "the site has changed"; reading it as signed-out would submit a
    credential on a page that may already hold a session. It ends the run with
    a message that names the contradiction instead.
    """
    if not filters.get("logged_in_selector"):
        return False
    return signed_in_marker(page, filters) and login_on_top(page, filters)


def notice_open(page: Any, filters: dict[str, Any]) -> bool:
    """Whether a Notice dialog is standing on top of the application."""
    explicit = filters.get("notice_close_selector")
    if explicit:
        try:
            if _visible(page.locator(explicit)):
                return True
        except Exception:
            pass
    evaluate = getattr(page, "evaluate", None)
    if not callable(evaluate):
        return False
    try:
        return bool(evaluate(_NOTICE_TITLE_JS))
    except Exception:
        return False


def classify_auth_state(
    page: Any, filters: dict[str, Any], *, ignore_notice: bool = False
) -> str:
    """Classify one page from independent signals.

    Each signal is gathered on its own and the state is decided from all of
    them together, so that no single ambiguous fact — least of all "a login
    control is present" — can decide the outcome by itself.

    ``ignore_notice`` is used only after a Notice refused to close, to fall
    back on the remaining signals instead of looping forever.
    """
    if not ignore_notice and notice_open(page, filters):
        return NOTICE_OPEN
    on_top = login_on_top(page, filters)
    signed_in = signed_in_marker(page, filters)
    if signed_in and not on_top:
        return SIGNED_IN
    if on_top and not signed_in:
        return SIGNED_OUT
    return TRANSITIONING


def session_expired(page: Any, filters: dict[str, Any]) -> bool:
    """Return whether the loaded page is definitely signed out.

    Only an unambiguous ``signed_out`` counts. A covered login frame, an open
    Notice, or a page still mounting are not sign-outs and must never trigger
    a credential submission on their own.
    """
    if not _auth_markers_configured(filters):
        return False
    return classify_auth_state(page, filters) == SIGNED_OUT


def state_is_expired(state: str, page: Any, filters: dict[str, Any]) -> bool:
    """Decide login-or-not from a settled state.

    Fails closed toward the login screen when *both* markers are absent, and
    never toward it while the signed-in marker is present.
    """
    if not _auth_markers_configured(filters):
        return False
    if state == SIGNED_OUT:
        return True
    if state == TRANSITIONING:
        return not signed_in_marker(page, filters)
    return False


def wait_for_auth_surface(
    page: Any, filters: dict[str, Any], timeout_ms: int = 10000
) -> str:
    """Poll until the page has settled into a state that is not transitional.

    ``domcontentloaded`` is too early for Nexacro: the empty shell arrives first
    and the English/SSO controls are rendered several seconds later. Without
    this bounded wait an expired session can be mistaken for a valid one — and,
    since the fix, a half-mounted application for a sign-out.
    """
    if not _auth_markers_configured(filters):
        return classify_auth_state(page, filters)
    state = TRANSITIONING
    # Wall-clock bounded on purpose: a trial click against a covered control
    # costs its own timeout, so counting nominal 250 ms steps would let this
    # loop run several times longer than the budget it was given.
    deadline = time.monotonic() + timeout_ms / 1000
    while True:
        try:
            state = classify_auth_state(page, filters)
            if state != TRANSITIONING:
                return state
            if time.monotonic() >= deadline:
                return state
            page.wait_for_timeout(250)
        except Exception:
            return state


def prevent_debugger_pauses(context: Any, page: Any) -> None:
    """Keep an SSO anti-debug statement from freezing a controlled Chrome tab."""
    try:
        session = context.new_cdp_session(page)
        session.send("Debugger.enable")
        session.send("Debugger.setSkipAllPauses", {"skip": True})
        try:
            session.send("Runtime.runIfWaitingForDebugger")
        except Exception:
            pass
        try:
            session.send("Debugger.resume")
        except Exception:
            pass
    except Exception:
        pass


def find_locator(
    context: Any, preferred_page: Any, selector: str, timeout_ms: int = 30000
) -> tuple[Any, Any]:
    """Find a login control across SSO replacement pages and their frames."""
    waited = 0
    while waited < timeout_ms:
        pages: list[Any] = []
        for candidate in [preferred_page, *reversed(list(getattr(context, "pages", [])))]:
            if candidate not in pages:
                pages.append(candidate)
        for candidate in pages:
            try:
                if getattr(candidate, "is_closed", lambda: False)():
                    continue
            except Exception:
                continue
            scopes = [candidate, *list(getattr(candidate, "frames", []))]
            for scope in scopes:
                try:
                    locator = scope.locator(selector)
                    if locator.count() > 0:
                        return candidate, locator
                except Exception:
                    continue
        try:
            waiting_page = next(
                candidate
                for candidate in pages
                if not getattr(candidate, "is_closed", lambda: False)()
            )
            waiting_page.wait_for_timeout(250)
        except Exception:
            break
        waited += 250
    raise RuntimeError("login control unavailable")


def _click_explicit_close(locator: Any) -> bool:
    """Click a configured Notice close control without ever waiting unbounded.

    Returns True when the dialog was closed. A control that is present but not
    receiving pointer events fails the trial click quickly instead of blocking
    on actionability until the run timeout, which is what turned a Notice into
    a whole-run failure.
    """
    target = _first(locator)
    click = getattr(target, "click", None)
    if click is None:
        return False
    if not _supports_keyword(click, "trial"):
        # A double: keep the historical single, unadorned click.
        click()
        return True
    try:
        click(trial=True, timeout=_TRIAL_CLICK_TIMEOUT_MS)
    except Exception:
        return False
    try:
        click(timeout=_NOTICE_CLICK_TIMEOUT_MS)
    except Exception:
        return False
    return True


_NOTICE_DOM_CLOSE_JS = """
() => {
  const visible = (el) => {
    if (!el || !el.getBoundingClientRect) return false;
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 &&
      s.display !== 'none' && s.visibility !== 'hidden';
  };
  const all = Array.from(document.querySelectorAll('body *'));
  const titles = all.filter((el) =>
    visible(el) && (el.innerText || el.textContent || '').trim() === 'Notice'
  );
  const selectors = [
    '[aria-label="Close"]', '[title="Close"]',
    '[id$=".closebutton"]', '[id$="closebutton"]',
    '[id*="btnClose"]', '[id*="btn_close"]',
    '[id*="CloseButton"]'
  ];
  for (const title of titles) {
    let scope = title;
    for (let depth = 0; scope && depth < 12; depth += 1, scope = scope.parentElement) {
      const sr = scope.getBoundingClientRect();
      const boundedDialog = sr.width >= 160 && sr.height >= 80 &&
        sr.width < innerWidth * 0.95 && sr.height < innerHeight * 0.95;
      if (!boundedDialog) continue;
      for (const selector of selectors) {
        for (const candidate of scope.querySelectorAll(selector)) {
          if (!visible(candidate)) continue;
          const r = candidate.getBoundingClientRect();
          const inTitleCorner = r.left >= sr.left + sr.width * 0.60 &&
            r.top <= sr.top + Math.min(100, sr.height * 0.30);
          if (r.width <= 90 && r.height <= 90 && inTitleCorner) {
            candidate.click();
            return true;
          }
        }
      }
    }
  }
  return false;
}
"""


def _close_notice_by_dom(page: Any) -> bool:
    """Scoped DOM search for the dialog's own close control."""
    evaluate = getattr(page, "evaluate", None)
    if not callable(evaluate):
        return False
    try:
        return bool(evaluate(_NOTICE_DOM_CLOSE_JS))
    except Exception:
        return False


def _press_escape(page: Any) -> bool:
    """Last resort for a dialog whose close control cannot be clicked."""
    keyboard = getattr(page, "keyboard", None)
    press = getattr(keyboard, "press", None)
    if not callable(press):
        return False
    try:
        press("Escape")
        return True
    except Exception:
        return False


def close_notice(page: Any, filters: dict[str, Any], timeout_ms: int = 30000) -> bool:
    """Close the G-MES Notice dialog through its own DOM close control.

    An explicit selector wins, clicked under an explicit short timeout; when it
    cannot receive the click the scoped DOM search runs, and Escape is pressed
    at most once.  The DOM fallback is deliberately scoped: it first finds a
    visible title whose text is exactly ``Notice``, then looks only inside that
    title's ancestor dialog for a small close-like control. It can never reach
    Chrome's window controls.
    """
    explicit = filters.get("notice_close_selector")
    escape_pressed = False
    deadline = time.monotonic() + timeout_ms / 1000
    first_pass = True
    while first_pass or time.monotonic() < deadline:
        first_pass = False
        try:
            if explicit:
                locator = page.locator(explicit)
                if _visible(locator):
                    if _click_explicit_close(locator):
                        return True
                    if _close_notice_by_dom(page):
                        return True
                    if not escape_pressed:
                        escape_pressed = True
                        if _press_escape(page) and not _visible(page.locator(explicit)):
                            return True
            elif _close_notice_by_dom(page):
                return True
            page.wait_for_timeout(250)
        except Exception:
            return False
    return False


def _open_pages(context: Any, preferred_page: Any) -> list[Any]:
    """Newest tabs first, then the original page if it is still alive."""
    pages: list[Any] = []
    for candidate in [*reversed(list(getattr(context, "pages", []))), preferred_page]:
        if candidate in pages:
            continue
        try:
            if getattr(candidate, "is_closed", lambda: False)():
                continue
        except Exception:
            continue
        pages.append(candidate)
    return pages


def close_notice_in_context(
    context: Any, preferred_page: Any, filters: dict[str, Any], timeout_ms: int
) -> Any | None:
    """Follow G-MES tab handoffs and close Notice wherever it was rendered."""
    deadline = time.monotonic() + timeout_ms / 1000
    first_pass = True
    while first_pass or time.monotonic() < deadline:
        first_pass = False
        pages = _open_pages(context, preferred_page)
        for candidate in pages:
            if close_notice(candidate, filters, timeout_ms=1):
                return candidate
        if not pages:
            return None
        try:
            pages[0].wait_for_timeout(250)
        except Exception:
            pass
    return None


def find_application_page(
    context: Any, preferred_page: Any, filters: dict[str, Any], timeout_ms: int = 30000
) -> Any:
    """Return the surviving signed-in portal tab after the SSO handoff."""
    entry = urlsplit(str(filters.get("login_url") or ""))
    deadline = time.monotonic() + timeout_ms / 1000
    first_pass = True
    while first_pass or time.monotonic() < deadline:
        first_pass = False
        for candidate in _open_pages(context, preferred_page):
            try:
                current = urlsplit(candidate.url)
                # Exclude ADFS and the short-lived adsso_index relay tab. G-MES
                # is allowed to replace the exact entry document with another
                # route on the same approved portal host after Notice closes.
                if entry.hostname and current.hostname != entry.hostname:
                    continue
                if current.path.lower().endswith("/adsso_index.html"):
                    continue
                # A Notice standing on the application is still the application;
                # the caller closes it next. A login control left mounted under
                # the signed-in frames is not a reason to reject the tab.
                state = classify_auth_state(candidate, filters)
                if state in (SIGNED_IN, NOTICE_OPEN):
                    return candidate
                # A system with no signed-in marker configured has nothing
                # positive to offer: the login control no longer being on top is
                # the only evidence there is, and it is what the old rule used.
                if state == TRANSITIONING and not filters.get("logged_in_selector"):
                    return candidate
            except Exception:
                continue
        pages = _open_pages(context, preferred_page)
        if not pages:
            break
        try:
            pages[0].wait_for_timeout(250)
        except Exception:
            pass
    raise RuntimeError("signed-in application tab unavailable")


def settle_notice(
    context: Any, page: Any, filters: dict[str, Any], *, timeout_ms: int
) -> tuple[Any, str]:
    """Close an open Notice, then classify again; never assume it worked.

    Returns the page the Notice was closed on (tab handoffs are followed) and
    the state that page is in afterwards.
    """
    state = classify_auth_state(page, filters)
    if state != NOTICE_OPEN:
        return page, state
    notice_page = close_notice_in_context(context, page, filters, timeout_ms=timeout_ms)
    if notice_page is not None:
        page = notice_page
    state = classify_auth_state(page, filters)
    if state == NOTICE_OPEN:
        # The dialog would not go away. Decide on the remaining signals rather
        # than looping, so a stuck Notice cannot mask a real sign-out.
        state = classify_auth_state(page, filters, ignore_notice=True)
    return page, state


def ensure_authenticated(
    context: Any,
    page: Any,
    *,
    system: str,
    filters: dict[str, Any],
    credential_store: CredentialStore | None,
    session_state_path: Path | None = None,
    manage_tracing: bool = True,
    pause_guard: Callable[[Any, Any], None] = prevent_debugger_pauses,
    on_authenticated_page: Callable[[Any], None] | None = None,
) -> str | None:
    """Use a saved session, or make exactly one credential-backed login attempt.

    Extraction and replay suspend their already-running trace around this call.
    Recording invokes it before installing tracing, screenshots, network capture,
    or page bindings and therefore passes ``manage_tracing=False``.
    """
    notice_probe_timeout_ms = int(filters.get("notice_probe_timeout_ms") or 3000)
    state = wait_for_auth_surface(page, filters)
    # A remembered corporate session can reopen directly underneath a freshly
    # rendered Notice dialog. Clear it before deciding anything, so the state
    # the decision is made on is the application's, not the dialog's.
    if state == NOTICE_OPEN:
        page, state = settle_notice(
            context, page, filters, timeout_ms=notice_probe_timeout_ms
        )
    if state == TRANSITIONING and ambiguous_auth_state(page, filters):
        return (
            f"Could not tell whether {system} is signed in: the login control still "
            "accepts clicks while the signed-in marker is on screen. No credentials "
            "were sent and no step was run; check the system's login and signed-in "
            "selectors."
        )
    if not state_is_expired(state, page, filters):
        if state in (SIGNED_IN, NOTICE_OPEN):
            try:
                page = find_application_page(context, page, filters, timeout_ms=5000)
            except Exception:
                pass
        if on_authenticated_page is not None:
            on_authenticated_page(page)
        return None

    credential_ref = filters.get("credential_ref")
    if not credential_ref:
        return f"Session expired for {system}. Run: python -m smartops login {system}"
    if credential_store is None:
        return f"Secure credentials are unavailable for {system}."

    try:
        credential = credential_store.get(credential_ref)
    except Exception:
        return f"Secure credentials could not be read for {system}."
    if credential is None:
        return f"No secure credentials are stored for {system}."

    if manage_tracing:
        try:
            context.tracing.stop()
        except Exception:
            pass

    password_field = None
    credential_page = page
    stage = "opening the login page"
    page_guard = lambda new_page: pause_guard(context, new_page)
    guard_attached = False
    try:
        context.on("page", page_guard)
        guard_attached = True
    except Exception:
        pass
    try:
        login_url = filters.get("login_url")
        if login_url and not page.url.startswith(login_url):
            page.goto(login_url, wait_until="domcontentloaded")

        stage = "choosing the language"
        language_selector = filters.get("language_selector")
        if language_selector:
            page.locator(language_selector).click()

        stage = "opening the SSO window"
        popup_trigger = filters.get("popup_trigger_selector")
        if popup_trigger:
            with page.expect_popup() as popup_info:
                page.locator(popup_trigger).click()
            credential_page = popup_info.value
            pause_guard(context, credential_page)
            stage = "waiting for the SSO window"
            credential_page.wait_for_load_state("domcontentloaded")

        stage = "filling the saved username"
        credential_page, username_field = find_locator(
            context, credential_page, filters["username_selector"]
        )
        username_field.fill(credential.username)
        stage = "filling the saved password"
        credential_page, password_field = find_locator(
            context, credential_page, filters["password_selector"]
        )
        password_field.fill(credential.password)
        stage = "submitting the SSO form"
        credential_page, submit_button = find_locator(
            context, credential_page, filters["submit_selector"]
        )
        submit_button.click()

        if credential_page is not page:
            stage = "waiting for the SSO window to close"
            if not getattr(credential_page, "is_closed", lambda: False)():
                credential_page.wait_for_event("close")
            try:
                page.bring_to_front()
            except Exception:
                pass

        stage = "closing the notice"
        notice_page = close_notice_in_context(
            context,
            page,
            filters,
            timeout_ms=int(filters.get("notice_timeout_ms") or 30000),
        )

        stage = "following the signed-in G-MES tab"
        page = find_application_page(
            context,
            notice_page or page,
            filters,
            timeout_ms=int(filters.get("login_success_timeout_ms") or 60000),
        )

        stage = "verifying the signed-in page"
        # The old rule here was "wait until the login control is hidden", which
        # a same-document application never satisfies: it covers the login
        # frame instead of removing it. Verification is now the classifier.
        page, auth_state = settle_notice(
            context, page, filters, timeout_ms=notice_probe_timeout_ms
        )
        if auth_state == TRANSITIONING:
            auth_state = wait_for_auth_surface(page, filters)
        if auth_state == NOTICE_OPEN:
            # A second Notice, or one that reappeared: clear it once more and
            # decide on what is underneath.
            page, auth_state = settle_notice(
                context, page, filters, timeout_ms=notice_probe_timeout_ms
            )
        logged_in_selector = filters.get("logged_in_selector")
        if auth_state == SIGNED_OUT:
            return f"Automatic login was rejected for {system}."
        if logged_in_selector and auth_state != SIGNED_IN:
            # A marker that is merely present is not proof: the page must have
            # settled with the marker on screen and the login control no longer
            # taking clicks. Anything less is reported, not assumed.
            return (
                f"Automatic login did not reach the signed-in page for {system} "
                f"(state: {auth_state})."
            )

        if on_authenticated_page is not None:
            on_authenticated_page(page)

        stage = "saving the signed-in session"
        if session_state_path:
            Path(session_state_path).parent.mkdir(parents=True, exist_ok=True)
            context.storage_state(path=str(session_state_path))
        return None
    except Exception as exc:
        return (
            f"Automatic login failed for {system} while {stage} "
            f"({type(exc).__name__})."
        )
    finally:
        if guard_attached:
            try:
                context.remove_listener("page", page_guard)
            except Exception:
                pass
        if password_field is not None:
            try:
                password_field.fill("")
            except Exception:
                pass
        if manage_tracing:
            try:
                context.tracing.start(screenshots=True, snapshots=True)
            except Exception:
                pass
