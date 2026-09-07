from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest


def _no_visible_browser() -> bool:
    """Whether this machine can open a browser window a person could look at.

    A few tests exist precisely to exercise the headed path — the sign-in
    window an operator actually types into. They cannot run where there is no
    display, and until now they did not say so: they failed, every run, with a
    Playwright target-closed error that reads like a broken browser rather than
    a machine without a screen. A suite that reports the same two failures
    forever teaches people to ignore failures.

    Linux without DISPLAY or WAYLAND_DISPLAY has no screen to draw on. Windows
    and macOS always do. The environment variable stays as an explicit override
    for a machine that has a display but should not use it.
    """
    if os.environ.get("SMARTOPS_SKIP_HEADED_TESTS") == "1":
        return True
    if sys.platform.startswith("win") or sys.platform == "darwin":
        return False
    return not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


needs_a_visible_browser = pytest.mark.skipif(
    _no_visible_browser(),
    reason="this test opens a browser window a person would look at, and this machine has no display",
)


def _google_chrome_path() -> str:
    """Where branded Google Chrome is installed, or "" when it is not.

    Distinct from the Chromium every other browser test uses: a handful of
    tests deliberately drive the same browser the operator's automation profile
    runs in, extensions and all, and Chromium is not a stand-in for that.
    """
    configured = os.environ.get("SMARTOPS_TEST_GOOGLE_CHROME_PATH")
    if configured and Path(configured).exists():
        return configured
    candidates = [
        Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
        Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
        Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
        Path("/usr/bin/google-chrome"),
        Path("/usr/bin/google-chrome-stable"),
    ]
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        candidates.append(
            Path(local_app_data) / "Google" / "Chrome" / "Application" / "chrome.exe"
        )
    return next((str(path) for path in candidates if path.exists()), "")


needs_google_chrome = pytest.mark.skipif(
    not _google_chrome_path(),
    reason="this test drives branded Google Chrome, which is not installed on this machine",
)

from smartops.config import AppSettings, BrowserSettings, SafetySettings, Settings, StorageSettings
from smartops.credentials import InMemoryCredentialStore
from smartops.core.clock import FrozenClock
from smartops.services import Services
from smartops.storage.db import Database


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        app=AppSettings(environment="test"),
        storage=StorageSettings(
            sqlite_path=tmp_path / "smartops.db",
            raw_data_dir=tmp_path / "raw",
            incidents_dir=tmp_path / "incidents",
            logs_dir=tmp_path / "logs",
            history_dir=tmp_path / "history",
            sessions_dir=tmp_path / "sessions",
            systems_dir=tmp_path / "systems",
        ),
        browser=BrowserSettings(),
        safety=SafetySettings(),
    )


@pytest.fixture
def clock() -> FrozenClock:
    return FrozenClock()


@pytest.fixture
def slept() -> list[float]:
    return []


@pytest.fixture
def services(settings, clock, slept) -> Services:
    svc = Services(
        settings,
        db=Database(":memory:"),
        clock=clock,
        sleeper=slept.append,
        credential_store=InMemoryCredentialStore(),
    )
    yield svc
    svc.close()
