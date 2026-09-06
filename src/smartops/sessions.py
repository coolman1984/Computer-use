"""Management of saved login sessions (storage_state) per system.

Principle: the platform never sees a password. A human signs in once in a
visible (headed) browser, then we save the session state (cookies + tokens)
to a file outside the repo, and afterwards the automated browser reuses it
instead of signing in again (D020). A session that expires mid-run is
detected and raised as an AuthError (see
adapters/browser/playwright_engine.py and workflows/builtin.py).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Callable

from .config import BrowserSettings
from .core.clock import Clock, SystemClock
from .core.errors import ConfigurationError
from .storage.paths import slug


def session_path(sessions_dir: Path | str, system_key: str) -> Path:
    """Session file path for one system: <sessions_dir>/<slug(system_key)>.json"""
    return Path(sessions_dir) / f"{slug(system_key)}.json"


def session_exists(sessions_dir: Path | str, system_key: str) -> bool:
    """Whether a session file is on disk. Says nothing about whether it works.

    Kept for callers that genuinely mean "is there a file" (housekeeping,
    diagnostics). Anything deciding whether the user is signed in must use
    session_is_usable instead — see why in its docstring.
    """
    return session_path(sessions_dir, system_key).exists()


def read_session_state(sessions_dir: Path | str, system_key: str) -> dict | None:
    """Parse a saved storage_state, or None when it is missing or unreadable."""
    path = session_path(sessions_dir, system_key)
    if not path.exists():
        return None
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return loaded if isinstance(loaded, dict) else None


def session_is_usable(
    sessions_dir: Path | str, system_key: str, *, now: Clock | None = None
) -> bool:
    """Whether the saved session actually carries live credentials.

    The gate used to be "does the file exist". A storage_state written before a
    sign-in completed, or one whose cookies have all since expired, is a valid
    JSON file with nothing in it that would get you through the door — and it
    passed. The user was then allowed to record and schedule against a system
    the platform could not reach, and only found out when an overnight run
    failed.

    Usable means: at least one cookie that has not expired, or at least one
    origin carrying local/session storage (how token-based sign-ins keep state).
    This is a cheap, offline check; proving the session opens a protected page
    is the connection test's job, and costs a browser launch.
    """
    state = read_session_state(sessions_dir, system_key)
    if state is None:
        return False

    clock = now or SystemClock()
    current = clock.now().timestamp()
    for cookie in state.get("cookies") or []:
        if not isinstance(cookie, dict) or not cookie.get("name"):
            continue
        expires = cookie.get("expires")
        # Playwright writes -1 for a session cookie: no expiry, dies with the
        # browser. It is live as far as a saved state is concerned.
        if expires is None or expires in (-1, 0) or float(expires) > current:
            return True

    for origin in state.get("origins") or []:
        if isinstance(origin, dict) and origin.get("localStorage"):
            return True
    return False


def session_state_summary(
    sessions_dir: Path | str, system_key: str, *, now: Clock | None = None
) -> dict:
    """What the sign-in page shows: usable, how old, and why not when it is not."""
    state = read_session_state(sessions_dir, system_key)
    if state is None:
        return {"exists": False, "usable": False, "reason": "No sign-in has been saved yet."}
    usable = session_is_usable(sessions_dir, system_key, now=now)
    return {
        "exists": True,
        "usable": usable,
        "age_hours": session_age_hours(sessions_dir, system_key, now=now),
        "reason": (
            ""
            if usable
            else "The saved sign-in has expired or was never completed. Sign in again."
        ),
    }


def session_age_hours(
    sessions_dir: Path | str, system_key: str, *, now: Clock | None = None
) -> float | None:
    """Session age in hours, or None if the file does not exist."""
    path = session_path(sessions_dir, system_key)
    if not path.exists():
        return None
    clock = now or SystemClock()
    modified = path.stat().st_mtime
    current = clock.now().timestamp()
    return max(0.0, (current - modified) / 3600.0)


def capture_login(
    system_key: str,
    login_url: str,
    *,
    sessions_dir: Path | str,
    browser_settings: BrowserSettings,
    logged_in_selector: str = "",
    executable_path: str | None = None,
    wait_for_enter: Callable[[], None] | None = None,
    timeout_seconds: float = 600.0,
) -> Path:
    """Open a visible browser for manual sign-in, then save storage_state.

    No password is ever entered programmatically; the human signs in inside
    the open window. If a previous session exists (even partially expired) it
    is loaded first, so the user does not have to sign in completely from
    scratch every time.
    """
    if not login_url:
        raise ConfigurationError(
            f"No login_url to sign in to system {system_key}",
            details={"system": system_key},
        )

    from playwright.sync_api import sync_playwright  # deferred import: not needed by every module

    target_path = session_path(sessions_dir, system_key)
    target_path.parent.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as playwright:
        launch_kwargs: dict = {"headless": False}
        chosen = executable_path or browser_settings.executable_path
        if chosen:
            launch_kwargs["executable_path"] = chosen
        else:
            launch_kwargs["channel"] = "chrome"
        browser = playwright.chromium.launch(**launch_kwargs)
        try:
            context_kwargs: dict = {
                "viewport": {
                    "width": browser_settings.viewport_width,
                    "height": browser_settings.viewport_height,
                }
            }
            if target_path.exists():
                context_kwargs["storage_state"] = str(target_path)
            context = browser.new_context(**context_kwargs)
            page = context.new_page()
            page.goto(login_url)

            print(  # noqa: T201 — an intentional interactive message for the operator, not an event log entry
                f"Sign in manually to system {system_key} in the open window, "
                "then come back here and press Enter when you're done."
            )
            if logged_in_selector:
                page.wait_for_selector(logged_in_selector, timeout=timeout_seconds * 1000)
            else:
                (wait_for_enter or (lambda: input()))()

            context.storage_state(path=str(target_path))
        finally:
            browser.close()

    try:
        os.chmod(target_path, 0o600)
    except OSError:
        pass  # Windows or a filesystem without POSIX permissions

    return target_path
