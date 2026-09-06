"""One credential-isolated browser login used by extraction, replay, and recording."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit

from ...credentials import CredentialStore


def _visible(locator: Any) -> bool:
    """Return whether at least one matching element is actually visible."""
    try:
        count = locator.count()
        if count < 1:
            return False
        if hasattr(locator, "nth"):
            return any(locator.nth(index).is_visible() for index in range(min(count, 50)))
        target = getattr(locator, "first", locator)
        return bool(target.is_visible())
    except AttributeError:
        # Lightweight port fakes used outside a real browser may only expose
        # count(). Their historical meaning was "present and visible".
        try:
            return locator.count() > 0
        except Exception:
            return False
    except Exception:
        return False


def session_expired(page: Any, filters: dict[str, Any]) -> bool:
    """Return whether the loaded page still shows the configured login state."""
    login_selector = filters.get("login_selector") or filters.get("popup_trigger_selector")
    logged_in_selector = filters.get("logged_in_selector")
    # At initial entry the visible login screen always wins. Nexacro may leave
    # a work-frame marker mounted behind it from an earlier session.
    if login_selector and _visible(page.locator(login_selector)):
        return True
    if logged_in_selector and _visible(page.locator(logged_in_selector)):
        return False
    if logged_in_selector and not _visible(page.locator(logged_in_selector)):
        return True
    return False


def wait_for_auth_surface(page: Any, filters: dict[str, Any], timeout_ms: int = 10000) -> None:
    """Let a JavaScript application reveal either its login or signed-in marker.

    ``domcontentloaded`` is too early for Nexacro: the empty shell arrives first
    and the English/SSO controls are rendered several seconds later. Without
    this bounded wait an expired session can be mistaken for a valid one.
    """
    login_selector = filters.get("login_selector") or filters.get("popup_trigger_selector")
    logged_in_selector = filters.get("logged_in_selector")
    if not login_selector and not logged_in_selector:
        return
    waited = 0
    while waited < timeout_ms:
        try:
            if login_selector and _visible(page.locator(login_selector)):
                return
            if logged_in_selector and _visible(page.locator(logged_in_selector)):
                return
            page.wait_for_timeout(250)
        except Exception:
            return
        waited += 250


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


def close_notice(page: Any, filters: dict[str, Any], timeout_ms: int = 30000) -> bool:
    """Close the G-MES Notice dialog through its own DOM close control.

    An explicit selector wins.  Otherwise the fallback is deliberately scoped:
    it first finds a visible title whose text is exactly ``Notice``, then looks
    only inside that title's ancestor dialog for a small close-like control.
    It can never reach Chrome's window controls.
    """
    explicit = filters.get("notice_close_selector")
    waited = 0
    while waited < timeout_ms:
        try:
            if explicit:
                locator = page.locator(explicit)
                if _visible(locator):
                    getattr(locator, "first", locator).click()
                    return True
            else:
                closed = page.evaluate(
                    """
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
                )
                if closed:
                    return True
            page.wait_for_timeout(250)
        except Exception:
            return False
        waited += 250
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
    waited = 0
    while waited < timeout_ms:
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
        waited += 250
    return None


def find_application_page(
    context: Any, preferred_page: Any, filters: dict[str, Any], timeout_ms: int = 30000
) -> Any:
    """Return the surviving signed-in portal tab after the SSO handoff."""
    entry = urlsplit(str(filters.get("login_url") or ""))
    login_selector = filters.get("login_selector") or filters.get("popup_trigger_selector")
    logged_in_selector = filters.get("logged_in_selector")
    waited = 0
    while waited < timeout_ms:
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
                if logged_in_selector and _visible(candidate.locator(logged_in_selector)):
                    return candidate
                if login_selector and not _visible(candidate.locator(login_selector)):
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
        waited += 250
    raise RuntimeError("signed-in application tab unavailable")


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
    wait_for_auth_surface(page, filters)
    if not session_expired(page, filters):
        # A remembered corporate session can reopen directly underneath a
        # freshly rendered Notice dialog.  Probe briefly before capture starts;
        # this is separate from the longer first-login wait below.
        notice_page = close_notice_in_context(
            context,
            page,
            filters,
            timeout_ms=int(filters.get("notice_probe_timeout_ms") or 3000),
        )
        try:
            page = find_application_page(context, notice_page or page, filters, timeout_ms=5000)
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
        logged_in_selector = filters.get("logged_in_selector")
        login_selector = filters.get("login_selector")
        if logged_in_selector:
            page.wait_for_selector(logged_in_selector, state="visible")
        elif login_selector:
            page.wait_for_selector(login_selector, state="hidden")
        if logged_in_selector:
            if not _visible(page.locator(logged_in_selector)):
                return f"Automatic login was rejected for {system}."
        elif session_expired(page, filters):
            return f"Automatic login was rejected for {system}."

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
