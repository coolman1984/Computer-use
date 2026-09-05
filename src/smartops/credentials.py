"""Secure credential storage for unattended local automations.

Passwords are kept in Windows Credential Manager and are never persisted in
SmartOps' database, configuration, logs, or API responses.
"""

from __future__ import annotations

import ctypes
import re
import sys
from dataclasses import dataclass
from typing import Protocol


_KEY_RE = re.compile(r"^[A-Za-z0-9_.-]{1,120}$")
_PREFIX = "SmartOps/"


def validate_credential_key(key: str) -> str:
    if not _KEY_RE.fullmatch(key):
        raise ValueError("Credential key must contain only letters, numbers, dots, dashes, or underscores.")
    return key


@dataclass(frozen=True)
class StoredCredential:
    username: str
    password: str

    def __repr__(self) -> str:
        return f"StoredCredential(username={self.username!r}, password='********')"


class CredentialStore(Protocol):
    def get(self, key: str) -> StoredCredential | None: ...
    def put(self, key: str, username: str, password: str) -> None: ...
    def delete(self, key: str) -> bool: ...


class InMemoryCredentialStore:
    """Test-only store with the same contract as the Windows implementation."""

    def __init__(self) -> None:
        self._items: dict[str, StoredCredential] = {}

    def get(self, key: str) -> StoredCredential | None:
        return self._items.get(validate_credential_key(key))

    def put(self, key: str, username: str, password: str) -> None:
        self._items[validate_credential_key(key)] = StoredCredential(username, password)

    def delete(self, key: str) -> bool:
        return self._items.pop(validate_credential_key(key), None) is not None


class UnavailableCredentialStore:
    def _raise(self) -> None:
        raise RuntimeError("Windows Credential Manager is unavailable on this operating system.")

    def get(self, key: str) -> StoredCredential | None:
        self._raise()

    def put(self, key: str, username: str, password: str) -> None:
        self._raise()

    def delete(self, key: str) -> bool:
        self._raise()


if sys.platform == "win32":
    from ctypes import wintypes

    class _CREDENTIALW(ctypes.Structure):
        _fields_ = [
            ("Flags", wintypes.DWORD),
            ("Type", wintypes.DWORD),
            ("TargetName", wintypes.LPWSTR),
            ("Comment", wintypes.LPWSTR),
            ("LastWritten", wintypes.FILETIME),
            ("CredentialBlobSize", wintypes.DWORD),
            ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
            ("Persist", wintypes.DWORD),
            ("AttributeCount", wintypes.DWORD),
            ("Attributes", ctypes.c_void_p),
            ("TargetAlias", wintypes.LPWSTR),
            ("UserName", wintypes.LPWSTR),
        ]

    _PCREDENTIALW = ctypes.POINTER(_CREDENTIALW)


class WindowsCredentialStore:
    CRED_TYPE_GENERIC = 1
    CRED_PERSIST_LOCAL_MACHINE = 2
    ERROR_NOT_FOUND = 1168

    def __init__(self) -> None:
        if sys.platform != "win32":
            raise RuntimeError("Windows Credential Manager is only available on Windows.")
        self._advapi = ctypes.WinDLL("Advapi32.dll", use_last_error=True)
        self._advapi.CredWriteW.argtypes = [ctypes.POINTER(_CREDENTIALW), wintypes.DWORD]
        self._advapi.CredWriteW.restype = wintypes.BOOL
        self._advapi.CredReadW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(_PCREDENTIALW)]
        self._advapi.CredReadW.restype = wintypes.BOOL
        self._advapi.CredDeleteW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
        self._advapi.CredDeleteW.restype = wintypes.BOOL
        self._advapi.CredFree.argtypes = [ctypes.c_void_p]

    @staticmethod
    def _target(key: str) -> str:
        return _PREFIX + validate_credential_key(key)

    def put(self, key: str, username: str, password: str) -> None:
        if not username or not password:
            raise ValueError("Username and password are required.")
        encoded = password.encode("utf-16-le")
        blob = (ctypes.c_ubyte * len(encoded)).from_buffer_copy(encoded)
        credential = _CREDENTIALW()
        credential.Type = self.CRED_TYPE_GENERIC
        credential.TargetName = self._target(key)
        credential.Comment = "SmartOps unattended login"
        credential.CredentialBlobSize = len(encoded)
        credential.CredentialBlob = ctypes.cast(blob, ctypes.POINTER(ctypes.c_ubyte))
        credential.Persist = self.CRED_PERSIST_LOCAL_MACHINE
        credential.UserName = username
        try:
            if not self._advapi.CredWriteW(ctypes.byref(credential), 0):
                raise OSError(ctypes.get_last_error(), "Could not save credential in Windows Credential Manager.")
        finally:
            ctypes.memset(blob, 0, len(encoded))

    def get(self, key: str) -> StoredCredential | None:
        pointer = _PCREDENTIALW()
        if not self._advapi.CredReadW(self._target(key), self.CRED_TYPE_GENERIC, 0, ctypes.byref(pointer)):
            error = ctypes.get_last_error()
            if error == self.ERROR_NOT_FOUND:
                return None
            raise OSError(error, "Could not read credential from Windows Credential Manager.")
        try:
            item = pointer.contents
            raw = ctypes.string_at(item.CredentialBlob, item.CredentialBlobSize)
            return StoredCredential(item.UserName or "", raw.decode("utf-16-le"))
        finally:
            self._advapi.CredFree(pointer)

    def delete(self, key: str) -> bool:
        if self._advapi.CredDeleteW(self._target(key), self.CRED_TYPE_GENERIC, 0):
            return True
        error = ctypes.get_last_error()
        if error == self.ERROR_NOT_FOUND:
            return False
        raise OSError(error, "Could not delete credential from Windows Credential Manager.")


def default_credential_store() -> CredentialStore:
    return WindowsCredentialStore() if sys.platform == "win32" else UnavailableCredentialStore()
