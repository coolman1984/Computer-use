"""Step 5 tests: persistent-profile single-owner guard, launch flags,
concurrency warning, and the doctor extension check (5.4, 5.5).

No real browser is launched here; the Chromium launcher is a fake, matching
the pattern already used in tests/test_browser_extensions.py.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from smartops.adapters.browser.session import (
    OWNER_FILE_NAME,
    BrowserContextSession,
    acquire_profile_owner,
    concurrency_warning_message,
    is_process_alive,
    open_browser_context,
)
from smartops.checks import extension_provisioning_status
from smartops.config import BrowserSettings
from smartops.core.errors import ConfigurationError


# ---------- owner-file logic with fake PIDs ----------


def test_no_owner_file_is_simply_claimed(tmp_path: Path) -> None:
    owner_path = acquire_profile_owner(tmp_path, pid=111, started_at=1000.0)

    assert owner_path == tmp_path / OWNER_FILE_NAME
    data = json.loads(owner_path.read_text(encoding="utf-8"))
    assert data["pid"] == 111
    assert data["started_at"] == 1000.0


def test_live_owner_refuses_a_second_launch(tmp_path: Path) -> None:
    acquire_profile_owner(tmp_path, pid=111, started_at=1000.0)

    with pytest.raises(ConfigurationError) as excinfo:
        acquire_profile_owner(
            tmp_path,
            pid=222,
            started_at=2000.0,
            alive_check=lambda pid, started_at: True,  # simulate PID 111 still alive
        )

    assert "111" in str(excinfo.value.message)


def test_stale_owner_is_taken_over(tmp_path: Path) -> None:
    acquire_profile_owner(tmp_path, pid=111, started_at=1000.0)

    owner_path = acquire_profile_owner(
        tmp_path,
        pid=222,
        started_at=2000.0,
        alive_check=lambda pid, started_at: False,  # simulate PID 111 gone
    )

    data = json.loads(owner_path.read_text(encoding="utf-8"))
    assert data["pid"] == 222


def test_is_process_alive_recognizes_the_current_process(tmp_path: Path) -> None:
    import os

    pid = os.getpid()
    assert is_process_alive(pid, None) is True


def test_is_process_alive_is_false_for_an_unlikely_pid() -> None:
    # A PID this large should not be assigned on any real machine; the guard
    # must treat it as gone rather than raise.
    assert is_process_alive(999_999_999, None) is False


def test_stale_owner_with_reused_pid_is_not_mistaken_for_the_old_owner(tmp_path: Path) -> None:
    import os

    current_pid = os.getpid()
    # A live PID whose recorded start time no longer matches: the guard must
    # not trust it as the same process (PID reuse after a reboot).
    acquire_profile_owner(tmp_path, pid=current_pid, started_at=1.0)

    owner_path = acquire_profile_owner(
        tmp_path,
        pid=current_pid + 1,
        started_at=2.0,
        alive_check=lambda pid, started_at: False,  # simulates the real start-time mismatch
    )

    data = json.loads(owner_path.read_text(encoding="utf-8"))
    assert data["pid"] == current_pid + 1


def test_clean_close_clears_the_owner_file(tmp_path: Path) -> None:
    owner_path = acquire_profile_owner(tmp_path, pid=333, started_at=500.0)
    session = BrowserContextSession(context=_FakeContext(), owner_path=owner_path, owner_pid=333)

    session.close()

    assert not owner_path.exists()


def test_close_never_removes_a_newer_owners_claim(tmp_path: Path) -> None:
    owner_path = acquire_profile_owner(tmp_path, pid=333, started_at=500.0)
    stale_session = BrowserContextSession(
        context=_FakeContext(), owner_path=owner_path, owner_pid=333
    )
    # A newer process takes over before the stale one's close() runs.
    acquire_profile_owner(
        tmp_path, pid=444, started_at=900.0, alive_check=lambda pid, started_at: False
    )

    stale_session.close()

    data = json.loads(owner_path.read_text(encoding="utf-8"))
    assert data["pid"] == 444


class _FakeContext:
    def close(self) -> None:
        pass


# ---------- launch-argument assertions ----------


class _FakeChromium:
    def __init__(self) -> None:
        self.captured: dict = {}

    def launch_persistent_context(self, user_data_dir, **kwargs):
        self.captured.update(user_data_dir=user_data_dir, **kwargs)
        return _FakeContext()


class _FakePlaywright:
    def __init__(self, chromium: _FakeChromium) -> None:
        self.chromium = chromium


def test_persistent_launch_hides_the_crash_restore_bubble(tmp_path: Path) -> None:
    chromium = _FakeChromium()
    settings = BrowserSettings(user_data_dir=str(tmp_path / "automation-profile"))

    open_browser_context(_FakePlaywright(chromium), settings)

    assert "--hide-crash-restore-bubble" in chromium.captured["args"]


def test_persistent_launch_writes_an_owner_file(tmp_path: Path) -> None:
    chromium = _FakeChromium()
    profile_root = tmp_path / "automation-profile"
    settings = BrowserSettings(user_data_dir=str(profile_root))

    open_browser_context(_FakePlaywright(chromium), settings)

    assert (profile_root / OWNER_FILE_NAME).exists()


def test_isolated_context_needs_no_owner_file(tmp_path: Path) -> None:
    class IsolatedChromium(_FakeChromium):
        def launch(self, **kwargs):
            return _FakeBrowser()

    class _FakeBrowser:
        def new_context(self, **kwargs):
            return _FakeContext()

    isolated = IsolatedChromium()
    settings = BrowserSettings()  # no user_data_dir: isolated context, no profile to own

    session = open_browser_context(_FakePlaywright(isolated), settings)

    assert session.owner_path is None


# ---------- concurrency warning ----------


def test_concurrency_warning_only_fires_for_persistent_profile_above_one(tmp_path: Path) -> None:
    assert concurrency_warning_message(BrowserSettings(max_concurrency=4)) == ""

    persistent = BrowserSettings(user_data_dir=str(tmp_path), max_concurrency=1)
    assert concurrency_warning_message(persistent) == ""

    persistent_and_parallel = BrowserSettings(user_data_dir=str(tmp_path), max_concurrency=4)
    message = concurrency_warning_message(persistent_and_parallel)
    assert "max_concurrency" in message
    assert "1" in message


# ---------- doctor extension check ----------


def test_extension_status_not_configured_without_a_persistent_profile() -> None:
    status = extension_provisioning_status(BrowserSettings())
    assert status.status == "NOT CONFIGURED"


def test_extension_status_missing_when_extensions_folder_is_absent(tmp_path: Path) -> None:
    settings = BrowserSettings(
        user_data_dir=str(tmp_path / "automation-profile"),
        enable_extensions=True,
    )
    status = extension_provisioning_status(settings)
    assert status.status == "MISSING"
    assert "Extensions" in status.path


def test_extension_status_present_when_an_extension_folder_exists(tmp_path: Path) -> None:
    profile_root = tmp_path / "automation-profile"
    extensions_dir = profile_root / "Default" / "Extensions" / "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    extensions_dir.mkdir(parents=True)
    settings = BrowserSettings(user_data_dir=str(profile_root), enable_extensions=True)

    status = extension_provisioning_status(settings)

    assert status.status == "PRESENT"


def test_extension_status_uses_the_configured_profile_directory(tmp_path: Path) -> None:
    profile_root = tmp_path / "automation-profile"
    extensions_dir = profile_root / "Profile 19" / "Extensions" / "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    extensions_dir.mkdir(parents=True)
    settings = BrowserSettings(
        user_data_dir=str(profile_root),
        profile_directory="Profile 19",
        enable_extensions=True,
    )

    status = extension_provisioning_status(settings)

    assert status.status == "PRESENT"
    assert "Profile 19" in status.path
