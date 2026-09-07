"""Open Chrome consistently for checks, recording, replay, and extraction.

Ordinary sites use Playwright's isolated context.  Corporate portals may need
policy-installed extensions, so they can opt into a dedicated persistent Chrome
profile outside the repository.  Keeping that decision here prevents recording
and unattended replay from silently using different browser environments.
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from ...config import BrowserSettings
from ...core.errors import ConfigurationError

logger = logging.getLogger("smartops.browser.session")

# Single-owner guard for a persistent automation profile (5.4): two SmartOps
# processes racing the same on-disk Chrome profile corrupt Chrome's own lock
# state and can each interrupt the other's run. The owner file records who
# currently holds the profile so a second launch can refuse cleanly instead
# of fighting Chrome for the lock.
OWNER_FILE_NAME = "smartops-owner.json"


@dataclass
class BrowserContextSession:
    """The context plus whichever owning object must be closed."""

    context: Any
    browser: Any | None = None
    owner_path: Path | None = None
    owner_pid: int | None = None

    def close(self) -> None:
        try:
            self.context.close()
        finally:
            if self.browser is not None:
                self.browser.close()
            if self.owner_path is not None and self.owner_pid is not None:
                _clear_owner(self.owner_path, self.owner_pid)


def _windows_process_start_time(pid: int) -> float | None:
    """Epoch seconds the Windows process *pid* was created, or None if it is gone."""
    try:
        import ctypes
        from ctypes import wintypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return None
        try:
            creation = wintypes.FILETIME()
            exit_time = wintypes.FILETIME()
            kernel_time = wintypes.FILETIME()
            user_time = wintypes.FILETIME()
            ok = kernel32.GetProcessTimes(
                handle,
                ctypes.byref(creation),
                ctypes.byref(exit_time),
                ctypes.byref(kernel_time),
                ctypes.byref(user_time),
            )
            if not ok:
                return None
            value = (creation.dwHighDateTime << 32) | creation.dwLowDateTime
            if value == 0:
                return None
            # FILETIME: 100-ns intervals since 1601-01-01 -> Unix epoch seconds.
            return (value - 116444736000000000) / 10_000_000
        finally:
            kernel32.CloseHandle(handle)
    except Exception:
        return None


def _default_process_start_time(pid: int) -> float | None:
    """Best-effort process creation time; None when it cannot be read."""
    if os.name == "nt":
        return _windows_process_start_time(pid)
    return None  # no dependency-free equivalent on POSIX; see is_process_alive


def is_process_alive(pid: int, started_at: float | None) -> bool:
    """Whether *pid* is a live process and, when known, still the same one.

    Checking the start time as well as the PID guards against PID reuse: a
    fresh, unrelated process that happens to land on the same PID after a
    reboot must never be mistaken for the profile's previous owner.
    """
    if os.name != "nt":
        # No dependency-free way to read another process's creation time on
        # POSIX here; fall back to a plain liveness probe (no PID-reuse guard).
        try:
            os.kill(pid, 0)
        except (OSError, AttributeError):
            return False
        return True
    current = _default_process_start_time(pid)
    if current is None:
        return False  # OpenProcess failed: the process is gone
    if started_at is None:
        return True
    return abs(current - started_at) < 2.0


def _owner_file_path(profile_root: Path) -> Path:
    return profile_root / OWNER_FILE_NAME


def _read_owner(owner_path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(owner_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) and isinstance(data.get("pid"), int) else None


def _write_owner(owner_path: Path, pid: int, started_at: float | None) -> None:
    payload = {"pid": pid, "started_at": started_at, "acquired_at": time.time()}
    owner_path.write_text(json.dumps(payload), encoding="utf-8")


def _clear_owner(owner_path: Path, pid: int) -> None:
    """Remove the owner file only if it still names this process.

    A stale close (this process took over from a dead owner, then something
    else took over from *this* one before it exited) must not delete the new
    owner's claim.
    """
    owner = _read_owner(owner_path)
    if owner is not None and owner.get("pid") == pid:
        try:
            owner_path.unlink()
        except OSError:
            pass


def acquire_profile_owner(
    profile_root: Path,
    *,
    pid: int | None = None,
    started_at: float | None = None,
    alive_check: Callable[[int, float | None], bool] = is_process_alive,
) -> Path:
    """Claim single ownership of a persistent automation profile.

    Raises ConfigurationError naming the PID when a live owner already holds
    the profile. Recovers automatically from a stale owner file (the process
    is gone, or its start time no longer matches — a reused PID).
    """
    owner_pid = pid if pid is not None else os.getpid()
    owner_started = (
        started_at if started_at is not None else _default_process_start_time(owner_pid)
    )
    owner_path = _owner_file_path(profile_root)
    existing = _read_owner(owner_path)
    if existing is not None:
        existing_pid = existing.get("pid")
        if existing_pid != owner_pid and alive_check(existing_pid, existing.get("started_at")):
            raise ConfigurationError(
                "The automation Chrome profile at "
                f"{profile_root} is already in use by process {existing_pid}. "
                "Close that SmartOps browser session first, or wait for it to finish, "
                "before starting another — unattended runs use one shared profile.",
                details={"profile_root": str(profile_root), "owner_pid": existing_pid},
            )
    _write_owner(owner_path, owner_pid, owner_started)
    return owner_path


def concurrency_warning_message(settings: BrowserSettings) -> str:
    """Warn when the configured concurrency cannot be honoured by one shared profile.

    A persistent automation profile is a single Chrome user-data directory:
    only one process may safely drive it at a time (5.4), so unattended runs
    must configure browser.max_concurrency: 1. This is used by both the doctor
    command and the launch path.
    """
    if settings.user_data_dir.strip() and settings.max_concurrency > 1:
        return (
            f"browser.max_concurrency is {settings.max_concurrency} while a persistent "
            "automation profile is configured; unattended runs must use max_concurrency: 1 "
            "(one shared profile, one owner)."
        )
    return ""


def open_browser_context(
    playwright: Any,
    settings: BrowserSettings,
    *,
    headless: bool | None = None,
    executable_path: str | None = None,
    accept_downloads: bool = False,
    storage_state_path: Path | str | None = None,
) -> BrowserContextSession:
    """Open one Chrome context using the configured isolation model."""
    chosen_headless = settings.headless if headless is None else headless
    chosen_executable = executable_path or settings.executable_path or ""
    common: dict[str, Any] = {"headless": chosen_headless}
    if chosen_executable:
        common["executable_path"] = chosen_executable
    else:
        common["channel"] = "chrome"

    viewport = {
        "width": settings.viewport_width,
        "height": settings.viewport_height,
    }
    configured_dir = settings.user_data_dir.strip()
    if configured_dir:
        profile_root = Path(configured_dir).expanduser()
        if not profile_root.is_absolute():
            raise ConfigurationError(
                "browser.user_data_dir must be an absolute path outside the repository"
            )
        profile_root.mkdir(parents=True, exist_ok=True)

        warning = concurrency_warning_message(settings)
        if warning:
            logger.warning(warning)

        owner_pid = os.getpid()
        owner_started = _default_process_start_time(owner_pid)
        owner_path = acquire_profile_owner(profile_root, pid=owner_pid, started_at=owner_started)

        persistent: dict[str, Any] = {
            **common,
            "accept_downloads": accept_downloads,
            "viewport": viewport,
        }
        # Suppress Chrome's "Restore pages?" bubble after an unclean shutdown
        # (crash, killed process, power loss) so an unattended run never stalls
        # on a dialog with nobody there to dismiss it.
        args: list[str] = ["--hide-crash-restore-bubble"]
        if settings.profile_directory.strip():
            args.append(f"--profile-directory={settings.profile_directory.strip()}")
        if args:
            persistent["args"] = args
        if settings.enable_extensions:
            # Keep all other Playwright defaults; only remove the switch that
            # suppresses enterprise-managed extensions. Do not pass
            # --load-extension: branded Chrome removed it in Chrome 137.
            # Source: https://developer.chrome.com/blog/extension-news-june-2025
            persistent["ignore_default_args"] = ["--disable-extensions"]
        try:
            context = playwright.chromium.launch_persistent_context(
                str(profile_root), **persistent
            )
        except Exception:
            _clear_owner(owner_path, owner_pid)
            raise
        return BrowserContextSession(context=context, owner_path=owner_path, owner_pid=owner_pid)

    browser = playwright.chromium.launch(**common)
    context_options: dict[str, Any] = {
        "accept_downloads": accept_downloads,
        "viewport": viewport,
    }
    if storage_state_path and Path(storage_state_path).exists():
        context_options["storage_state"] = str(storage_state_path)
    context = browser.new_context(**context_options)
    return BrowserContextSession(context=context, browser=browser)
