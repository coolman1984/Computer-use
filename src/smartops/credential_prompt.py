"""Isolated native Windows prompt for unattended-login credentials.

The web application can ask this manager to open a Windows dialog, but the
username and password never cross HTTP and never appear in this process'
command line.  A short-lived child process owns the dialog and writes the
credential directly to Windows Credential Manager.
"""

from __future__ import annotations

import argparse
import ctypes
import subprocess
import sys
import threading
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Sequence

from .credentials import WindowsCredentialStore, validate_credential_key
from .domain.enums import EventType, Severity


@dataclass
class CredentialPromptSession:
    """Small, secret-free status object returned to the local web UI."""

    system_key: str
    status: str = "opening"  # opening | waiting | completed | cancelled | failed
    message: str = "Opening the separate secure Windows window…"
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error: str | None = None
    _thread: threading.Thread | None = field(default=None, repr=False)

    @property
    def active(self) -> bool:
        return self.status in ("opening", "waiting")

    @property
    def saved(self) -> bool:
        return self.status == "completed"

    def to_dict(self) -> dict[str, Any]:
        return {
            "system_key": self.system_key,
            "status": self.status,
            "message": self.message,
            "active": self.active,
            "saved": self.saved,
            "error": self.error,
        }


class CredentialPromptManager:
    """Launch and monitor one native credential prompt per system."""

    def __init__(
        self,
        services: Any,
        *,
        process_factory: Callable[..., Any] = subprocess.Popen,
    ) -> None:
        self.services = services
        self._process_factory = process_factory
        self._sessions: dict[str, CredentialPromptSession] = {}
        self._lock = threading.Lock()

    def start(self, system_key: str) -> CredentialPromptSession:
        system = self.services.systems.get(system_key)
        if system.auth.mode != "unattended":
            raise ValueError("A saved credential is only used for unattended sign-in.")

        credential_ref = validate_credential_key(system.auth.credential_ref or system.key)
        with self._lock:
            existing = self._sessions.get(system_key)
            if existing is not None and existing.active:
                return existing
            session = CredentialPromptSession(
                system_key=system_key,
                started_at=self.services.clock.now(),
            )
            self._sessions[system_key] = session

        self._emit(EventType.LOGIN_STARTED, system_key, "Secure credential window requested")
        thread = threading.Thread(
            target=self._run,
            args=(session, credential_ref, system.name),
            name=f"credential-prompt-{system_key}",
            daemon=True,
        )
        session._thread = thread
        thread.start()
        return session

    def status(self, system_key: str) -> CredentialPromptSession | None:
        return self._sessions.get(system_key)

    def _run(
        self,
        session: CredentialPromptSession,
        credential_ref: str,
        system_name: str,
    ) -> None:
        session.status = "waiting"
        session.message = "Enter the username and password in the separate secure Windows window."
        command = [
            sys.executable,
            "-m",
            "smartops.credential_prompt",
            "--credential-ref",
            credential_ref,
            "--title",
            f"SmartOps sign-in — {system_name}",
        ]
        options: dict[str, Any] = {
            "stdin": subprocess.DEVNULL,
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
        }
        if sys.platform == "win32":
            options["creationflags"] = subprocess.CREATE_NO_WINDOW

        try:
            process = self._process_factory(command, **options)
            return_code = process.wait()
            if return_code == 0 and self.services.credentials.get(credential_ref) is not None:
                session.status = "completed"
                session.message = "Credential saved securely in Windows Credential Manager."
                self._emit(EventType.LOGIN_SUCCEEDED, session.system_key, "Secure credential saved")
            elif return_code == 2:
                session.status = "cancelled"
                session.message = "Credential entry was cancelled. Nothing was changed."
            else:
                session.status = "failed"
                session.message = "The credential was not saved. Open the secure window and try again."
                session.error = "The secure Windows credential window did not complete successfully."
                self._emit(
                    EventType.LOGIN_FAILED,
                    session.system_key,
                    "Secure credential was not saved",
                    severity=Severity.ERROR,
                )
        except Exception:
            # Never include exception text here: a third-party store or prompt
            # implementation must not be allowed to leak sensitive input into
            # the API, event log, or server console.
            session.status = "failed"
            session.message = "The secure credential window could not be opened."
            session.error = "Native Windows credential prompting is unavailable."
            self._emit(
                EventType.LOGIN_FAILED,
                session.system_key,
                "Secure credential window failed",
                severity=Severity.ERROR,
            )
        finally:
            session.finished_at = self.services.clock.now()

    def _emit(
        self,
        event_type: EventType,
        system_key: str,
        message: str,
        *,
        severity: Severity = Severity.INFO,
    ) -> None:
        self.services.events.emit(
            event_type,
            severity=severity,
            step_name=f"credentials:{system_key}",
            message=message,
            payload={"system_key": system_key, "source": "native_credential_prompt"},
        )


def _prompt_and_store(credential_ref: str, title: str) -> int:
    """Run CredUI and save its result. Return 2 when the user cancels."""
    if sys.platform != "win32":
        return 1

    from ctypes import wintypes

    # SmartOps may be started by an agent on an isolated WinSta0 desktop.
    # Attach this new thread before creating any UI so CredUI appears on the
    # operator's physical desktop instead of an invisible automation desktop.
    user32 = ctypes.WinDLL("user32.dll", use_last_error=True)
    user32.OpenDesktopW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.BOOL,
        wintypes.DWORD,
    ]
    user32.OpenDesktopW.restype = wintypes.HANDLE
    user32.SetThreadDesktop.argtypes = [wintypes.HANDLE]
    user32.SetThreadDesktop.restype = wintypes.BOOL
    user32.CloseDesktop.argtypes = [wintypes.HANDLE]
    user32.CloseDesktop.restype = wintypes.BOOL
    desktop = user32.OpenDesktopW("default", 0, False, 0x10000000)  # GENERIC_ALL
    if not desktop:
        return 1
    if not user32.SetThreadDesktop(desktop):
        user32.CloseDesktop(desktop)
        return 1

    class CREDUI_INFOW(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("hwndParent", wintypes.HWND),
            ("pszMessageText", wintypes.LPCWSTR),
            ("pszCaptionText", wintypes.LPCWSTR),
            ("hbmBanner", wintypes.HBITMAP),
        ]

    credui = ctypes.WinDLL("Credui.dll", use_last_error=True)
    prompt = credui.CredUIPromptForCredentialsW
    prompt.argtypes = [
        ctypes.POINTER(CREDUI_INFOW),
        wintypes.LPCWSTR,
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.LPWSTR,
        wintypes.ULONG,
        wintypes.LPWSTR,
        wintypes.ULONG,
        ctypes.POINTER(wintypes.BOOL),
        wintypes.DWORD,
    ]
    prompt.restype = wintypes.DWORD

    username = ctypes.create_unicode_buffer(513)
    password = ctypes.create_unicode_buffer(1025)
    save = wintypes.BOOL(False)
    info = CREDUI_INFOW(
        cbSize=ctypes.sizeof(CREDUI_INFOW),
        hwndParent=None,
        pszMessageText=(
            "Enter the account used by this automation. SmartOps stores it directly "
            "in Windows Credential Manager."
        ),
        pszCaptionText=title[:128],
        hbmBanner=None,
    )
    flags = 0x00000002 | 0x00000008 | 0x00000080 | 0x00040000
    try:
        result = prompt(
            ctypes.byref(info),
            f"SmartOps/{credential_ref}",
            None,
            0,
            username,
            len(username),
            password,
            len(password),
            ctypes.byref(save),
            flags,
        )
        if result == 1223:  # ERROR_CANCELLED
            return 2
        if result != 0:
            return 1
        if not username.value or not password.value:
            return 1
        WindowsCredentialStore().put(credential_ref, username.value, password.value)
        return 0
    finally:
        ctypes.memset(ctypes.addressof(username), 0, ctypes.sizeof(username))
        ctypes.memset(ctypes.addressof(password), 0, ctypes.sizeof(password))
        user32.CloseDesktop(desktop)


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="SmartOps secure Windows credential prompt")
    parser.add_argument("--credential-ref", required=True)
    parser.add_argument("--title", default="SmartOps secure sign-in")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        credential_ref = validate_credential_key(args.credential_ref)
        return _prompt_and_store(credential_ref, str(args.title))
    except Exception:
        # This process is deliberately silent. The parent reports only a
        # generic failure and no credential-related value can reach stdout.
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
