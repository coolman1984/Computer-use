"""Interactive sign-in driven from the web app instead of the terminal.

The platform still never sees a password: it opens a real, visible Chrome
window, the human signs in there — SSO popup, MFA, whatever the company uses —
and only the resulting session state is saved (D020). What changes here is
*who starts it*. Previously this was `python -m smartops login <system>`, which
put a terminal command in the middle of the journey; now the browser is opened
by a background thread owned by the server, and the web page polls its status.

The thread is the same pattern the recording worker already uses: Playwright's
sync API is not safe to call from the event loop, so the session lives entirely
on its own thread and communicates through a small status object.
"""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import BrowserSettings
from .core.errors import ConfigurationError, PermanentError
from .domain.enums import EventType, Severity
from .sessions import session_path

# How long a sign-in window is allowed to stay open before the platform gives
# up and closes it. Long enough for a slow SSO with MFA, short enough that a
# forgotten window does not hold a browser open all day.
DEFAULT_TIMEOUT_SECONDS = 600.0


@dataclass
class LoginSession:
    """The live state of one sign-in attempt, as the web page sees it."""

    system_key: str
    status: str = "opening"  # opening | waiting | saving | completed | failed | cancelled
    message: str = "Opening a browser window…"
    started_at: datetime | None = None
    finished_at: datetime | None = None
    session_path: str = ""
    error: str | None = None
    _stop: threading.Event = field(default_factory=threading.Event, repr=False)
    _done: threading.Event = field(default_factory=threading.Event, repr=False)
    _thread: threading.Thread | None = field(default=None, repr=False)

    @property
    def active(self) -> bool:
        return self.status in ("opening", "waiting", "saving")

    def to_dict(self) -> dict[str, Any]:
        return {
            "system_key": self.system_key,
            "status": self.status,
            "message": self.message,
            "active": self.active,
            "saved": self.status == "completed",
            "error": self.error,
        }


class LoginManager:
    """Opens sign-in windows and saves the sessions they produce."""

    def __init__(self, services: Any, *, executable_path: str | None = None) -> None:
        self.services = services
        self.sessions: dict[str, LoginSession] = {}
        self._lock = threading.Lock()
        self._executable_path = executable_path

    # ---------- public API ----------

    def start(self, system_key: str) -> LoginSession:
        """Open a visible browser at the system's sign-in page.

        Returns immediately; the caller polls status(). Starting a second
        sign-in for the same system while one is open is a no-op rather than an
        error, so an impatient double-click does not open two windows.
        """
        system = self.services.systems.get(system_key)
        if system.auth.mode == "none":
            raise PermanentError(
                f"System '{system.name}' does not need a sign-in.",
                details={"system": system_key},
            )
        if not system.auth.login_url:
            raise ConfigurationError(
                f"System '{system.name}' has no sign-in page address to open.",
                details={"system": system_key},
            )

        with self._lock:
            existing = self.sessions.get(system_key)
            if existing is not None and existing.active:
                return existing
            session = LoginSession(system_key=system_key, started_at=self.services.clock.now())
            self.sessions[system_key] = session

        self._emit(EventType.LOGIN_STARTED, system_key, "Sign-in window opened")
        thread = threading.Thread(
            target=self._run,
            args=(session, system),
            name=f"login-{system_key}",
            daemon=True,
        )
        session._thread = thread
        thread.start()
        return session

    def finish(self, system_key: str) -> LoginSession:
        """The user says they are signed in: save the session and close the window."""
        session = self._required(system_key)
        if not session.active:
            return session
        session.status = "saving"
        session.message = "Saving your session…"
        session._stop.set()
        # Bounded wait: the browser thread saves the state and exits. If it
        # somehow does not, the status stays "saving" and says so rather than
        # this call hanging the HTTP request.
        session._done.wait(timeout=30)
        return session

    def cancel(self, system_key: str) -> LoginSession:
        session = self._required(system_key)
        if session.active:
            session.status = "cancelled"
            session.message = "Sign-in cancelled."
            session._stop.set()
            session._done.wait(timeout=15)
        return session

    def status(self, system_key: str) -> LoginSession | None:
        return self.sessions.get(system_key)

    # ---------- the browser thread ----------

    def _run(self, session: LoginSession, system: Any) -> None:
        target = session_path(self.services.settings.storage.sessions_dir, system.key)
        target.parent.mkdir(parents=True, exist_ok=True)
        settings: BrowserSettings = self.services.settings.browser
        try:
            from playwright.sync_api import sync_playwright

            with sync_playwright() as playwright:
                launch_kwargs: dict[str, Any] = {"headless": False}
                if self._executable_path:
                    launch_kwargs["executable_path"] = self._executable_path
                browser = playwright.chromium.launch(**launch_kwargs)
                try:
                    context_kwargs: dict[str, Any] = {
                        "viewport": {
                            "width": settings.viewport_width,
                            "height": settings.viewport_height,
                        }
                    }
                    # Reuse a partially valid session so the user does not have
                    # to start from a cold sign-in every single time.
                    if target.exists():
                        context_kwargs["storage_state"] = str(target)
                    context = browser.new_context(**context_kwargs)
                    page = context.new_page()
                    page.goto(system.auth.login_url, wait_until="domcontentloaded")
                    session.status = "waiting"
                    session.message = (
                        "Sign in inside the browser window that just opened, then come back "
                        "here and choose 'I have signed in'."
                    )
                    self._wait_for_login(session, page, system)
                    if session.status == "cancelled":
                        return
                    session.status = "saving"
                    context.storage_state(path=str(target))
                    self._harden(target)
                    session.session_path = str(target)
                    session.status = "completed"
                    session.message = "Your session was saved. This system is connected."
                    self._emit(EventType.LOGIN_SUCCEEDED, system.key, "Sign-in session saved")
                finally:
                    browser.close()
        except Exception as exc:
            session.status = "failed"
            session.error = type(exc).__name__
            session.message = (
                "The sign-in window could not be opened or the session could not be saved. "
                "Make sure Google Chrome is installed and that you are working at the machine "
                "itself, not through a remote session without a screen."
            )
            self._emit(
                EventType.LOGIN_FAILED,
                system.key,
                "Sign-in failed",
                severity=Severity.WARNING,
            )
        finally:
            session.finished_at = self.services.clock.now()
            session._done.set()

    def _wait_for_login(self, session: LoginSession, page: Any, system: Any) -> None:
        """Wait until the sign-in is done, whichever way that happens first.

        Two ways out: the system's signed-in marker appears (we detect success
        ourselves and the user does not have to confirm anything), or the user
        presses the confirm button in the web app. Polling the marker rather
        than blocking on wait_for_selector is what keeps the confirm button
        responsive at the same time.
        """
        marker = system.auth.logged_in_selector
        deadline = DEFAULT_TIMEOUT_SECONDS
        elapsed = 0.0
        while elapsed < deadline:
            if session._stop.wait(1.0):
                return  # the user confirmed, or cancelled
            elapsed += 1.0
            if not marker:
                continue
            try:
                if page.locator(marker).count() > 0:
                    return
            except Exception:
                # Mid-navigation the page can refuse queries; that is normal
                # during a sign-in redirect chain and not a failure.
                continue
        session.status = "failed"
        session.message = "The sign-in window was open too long and was closed."

    # ---------- helpers ----------

    @staticmethod
    def _harden(path: Path) -> None:
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass  # Windows, or a filesystem without POSIX permissions

    def _required(self, system_key: str) -> LoginSession:
        session = self.sessions.get(system_key)
        if session is None:
            raise PermanentError(
                "No sign-in is in progress for this system. Start one first.",
                details={"system": system_key},
            )
        return session

    def _emit(
        self, event: EventType, system_key: str, message: str, severity: Severity = Severity.INFO
    ) -> None:
        self.services.events.emit(
            event, severity=severity, message=message, payload={"system": system_key}
        )
