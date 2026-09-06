"""Connection test for a system: does this definition actually reach the site?

This is the second stage of the journey and it exists to make the first one
falsifiable. Before it, the only way to find out whether a URL or a selector was
right was to run a full collection and read a browser stack trace. A check
opens the page, looks for the two selectors the platform relies on, and answers
in one sentence with a next step — without downloading anything and without
touching the database.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import json
import threading

from .config import BrowserSettings
from .storage.paths import slug
from .workflows.profiles import SystemProfile


@dataclass
class ConnectionCheck:
    """The verdict of one connection test, phrased for a non-technical reader."""

    ok: bool
    reachable: bool
    signed_in: bool | None
    summary: str
    next_step: str = ""
    checked_url: str = ""
    screenshot_path: str = ""
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "reachable": self.reachable,
            "signed_in": self.signed_in,
            "summary": self.summary,
            "next_step": self.next_step,
            "checked_url": self.checked_url,
            "screenshot": bool(self.screenshot_path),
            "details": self.details,
        }


def _target_url(system: SystemProfile) -> str:
    """The page that best proves the system is reachable.

    The login page when there is one — that is where a session-based system is
    entered — otherwise the first report's own page.
    """
    if system.auth.login_url:
        return system.auth.login_url
    return system.reports[0].url if system.reports else ""


def check_system(
    system: SystemProfile,
    *,
    browser_settings: BrowserSettings,
    sessions_dir: Path | str,
    evidence_dir: Path | str | None = None,
    executable_path: str | None = None,
    timeout_seconds: float = 45.0,
) -> ConnectionCheck:
    """Open the system's entry page with the saved session and report what happened."""
    from playwright.sync_api import (  # deferred: not every caller needs Playwright
        TimeoutError as PlaywrightTimeoutError,
        sync_playwright,
    )

    from .sessions import session_path

    url = _target_url(system)
    if not url:
        return ConnectionCheck(
            ok=False,
            reachable=False,
            signed_in=None,
            summary="This system has no address to open yet.",
            next_step="Add a sign-in page address, or a report with its own page address.",
        )

    state_path = session_path(sessions_dir, system.key)
    screenshot = ""
    try:
        with sync_playwright() as playwright:
            launch_kwargs: dict[str, Any] = {"headless": browser_settings.headless}
            chosen = executable_path or browser_settings.executable_path
            if chosen:
                launch_kwargs["executable_path"] = chosen
            else:
                launch_kwargs["channel"] = "chrome"
            browser = playwright.chromium.launch(**launch_kwargs)
            try:
                context_kwargs: dict[str, Any] = {
                    "viewport": {
                        "width": browser_settings.viewport_width,
                        "height": browser_settings.viewport_height,
                    }
                }
                if state_path.exists():
                    context_kwargs["storage_state"] = str(state_path)
                context = browser.new_context(**context_kwargs)
                context.set_default_timeout(timeout_seconds * 1000)
                page = context.new_page()
                page.goto(url, wait_until="domcontentloaded")

                if evidence_dir is not None:
                    try:
                        directory = Path(evidence_dir)
                        directory.mkdir(parents=True, exist_ok=True)
                        target = directory / f"{slug(system.key)}-check.png"
                        page.screenshot(path=str(target), full_page=False)
                        screenshot = str(target)
                    except Exception:
                        pass  # evidence is a bonus; its absence is not a failed check

                signed_in = _signed_in(page, system)
                return _verdict(system, url, signed_in, screenshot)
            finally:
                browser.close()
    except PlaywrightTimeoutError:
        return ConnectionCheck(
            ok=False,
            reachable=False,
            signed_in=None,
            summary="The page did not open in time.",
            next_step=(
                "Check the address is right and that you can open it yourself in a browser "
                "on this machine — a company site often needs the VPN to be connected."
            ),
            checked_url=url,
            screenshot_path=screenshot,
        )
    except Exception as exc:
        # Distinguish "the browser itself is missing" from "the site is
        # unreachable": they look identical here but need opposite actions, and
        # a first-time user hits the first one far more often.
        if "Executable doesn" in str(exc) or "playwright install" in str(exc).lower():
            return ConnectionCheck(
                ok=False,
                reachable=False,
                signed_in=None,
                summary="SmartOps could not find a browser to open the site with.",
                next_step=(
                    "Install Google Chrome on this machine, or point SmartOps at an existing "
                    "browser by setting browser.executable_path in the settings file."
                ),
                checked_url=url,
                details={"error": "browser_missing"},
            )
        return ConnectionCheck(
            ok=False,
            reachable=False,
            signed_in=None,
            summary="The page could not be opened.",
            next_step=(
                "Check the address is right and that this machine can reach the site "
                "(VPN, network, or a typo in the address)."
            ),
            checked_url=url,
            screenshot_path=screenshot,
            details={"error": type(exc).__name__},
        )


def _signed_in(page: Any, system: SystemProfile) -> bool | None:
    """Whether the saved session still works: None when the system defines no way to tell."""
    logged_in_selector = system.auth.logged_in_selector
    login_selector = system.auth.login_selector
    if not logged_in_selector and not login_selector:
        return None
    if logged_in_selector and page.locator(logged_in_selector).count() > 0:
        return True
    if login_selector and page.locator(login_selector).count() > 0:
        return False
    # A "signed in" marker that is absent means not signed in; a "sign-in form"
    # marker that is absent means signed in. Only one of the two is set here.
    return not logged_in_selector


def _verdict(
    system: SystemProfile, url: str, signed_in: bool | None, screenshot: str
) -> ConnectionCheck:
    if system.auth.mode == "none":
        return ConnectionCheck(
            ok=True,
            reachable=True,
            signed_in=None,
            summary="The site opened successfully. This system needs no sign-in.",
            next_step="Record the workflow for the report you want to collect.",
            checked_url=url,
            screenshot_path=screenshot,
        )
    if signed_in is None:
        return ConnectionCheck(
            ok=True,
            reachable=True,
            signed_in=None,
            summary=(
                "The site opened, but the system has no way to tell whether you are signed in."
            ),
            next_step=(
                "Add a 'signed-in marker' to the system so the platform can detect an expired "
                "session by itself instead of failing mid-run."
            ),
            checked_url=url,
            screenshot_path=screenshot,
        )
    if signed_in:
        return ConnectionCheck(
            ok=True,
            reachable=True,
            signed_in=True,
            summary="The site opened and you are signed in.",
            next_step="Record the workflow for the report you want to collect.",
            checked_url=url,
            screenshot_path=screenshot,
        )
    return ConnectionCheck(
        ok=True,
        reachable=True,
        signed_in=False,
        summary="The site opened, but you are not signed in yet.",
        next_step="Sign in from the Sign-in page, then test again.",
        checked_url=url,
        screenshot_path=screenshot,
    )


class ConnectionCheckStore:
    """Remembers the last connection test per system, across restarts.

    A stage that a restart silently un-does is not a stage. This is deliberately
    a small JSON file rather than a database table: it is cached evidence about
    the outside world, not platform state, and it is cheap to throw away and
    re-test if it is ever lost.
    """

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()
        self._cache: dict[str, dict[str, Any]] | None = None

    def _load(self) -> dict[str, dict[str, Any]]:
        if self._cache is None:
            try:
                loaded = json.loads(self.path.read_text(encoding="utf-8"))
                self._cache = loaded if isinstance(loaded, dict) else {}
            except (OSError, json.JSONDecodeError):
                self._cache = {}
        return self._cache

    def record(self, system_key: str, check: ConnectionCheck, *, at: str = "") -> None:
        """Store a *passing* check; a failure clears any earlier pass.

        Storing failures as "checked" would let a system that used to work keep
        a green tick after it stopped working.
        """
        with self._lock:
            data = self._load()
            if check.ok:
                data[system_key] = {**check.to_dict(), "checked_at": at}
            else:
                data.pop(system_key, None)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def forget(self, system_key: str) -> None:
        with self._lock:
            data = self._load()
            if data.pop(system_key, None) is not None:
                self.path.write_text(
                    json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
                )

    def get(self, system_key: str) -> dict[str, Any] | None:
        return self._load().get(system_key)

    def __contains__(self, system_key: object) -> bool:
        return isinstance(system_key, str) and system_key in self._load()
