"""In-memory structural view of one user-shared Chrome tab.

The bridge is evidence, not a second automation engine.  It never receives or
stores input values, cookies, browser storage, network bodies, or downloads.
All data is treated as untrusted and reduced to this module's small contract at
the WebSocket boundary.
"""

from __future__ import annotations

import re
import threading
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from .core.clock import Clock, to_iso

_EXTENSION_ID = re.compile(r"^[a-p]{32}$")
_ALLOWED_TAGS = {"a", "button", "input", "select", "textarea", "summary", "[role]"}
_ALLOWED_TYPES = {
    "button",
    "checkbox",
    "email",
    "number",
    "radio",
    "reset",
    "search",
    "submit",
    "tel",
    "text",
    "url",
}


def valid_extension_origin(origin: str) -> bool:
    prefix = "chrome-extension://"
    return origin.startswith(prefix) and bool(_EXTENSION_ID.fullmatch(origin[len(prefix) :]))


def _text(value: Any, limit: int) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(value.split())[:limit]


def _safe_url(value: Any) -> str:
    raw = _text(value, 2048)
    try:
        parts = urlsplit(raw)
    except ValueError:
        return ""
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        return ""
    host = parts.hostname
    try:
        port = parts.port
    except ValueError:
        return ""
    if port:
        host = f"{host}:{port}"
    return urlunsplit((parts.scheme, host, parts.path[:1024], "", ""))


def _safe_element(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    tag = _text(value.get("tag"), 20).lower()
    element_id = _text(value.get("id"), 100)
    name = _text(value.get("name"), 100)
    role = _text(value.get("role"), 40).lower()
    kind = _text(value.get("type"), 20).lower()
    identity = f"{element_id} {name} {_text(value.get('ariaLabel'), 160)}".lower()
    if kind == "password" or any(word in identity for word in ("password", "passwd")):
        return None
    if tag not in _ALLOWED_TAGS and not role and not (tag in {"div", "span"} and element_id):
        return None
    if tag == "input" and kind not in _ALLOWED_TYPES:
        kind = ""
    bounds = value.get("bounds") if isinstance(value.get("bounds"), dict) else {}
    return {
        "tag": tag,
        "id": element_id,
        "name": name,
        "role": role,
        "type": kind,
        "ariaLabel": _text(value.get("ariaLabel"), 160),
        "text": _text(value.get("text"), 200),
        "isDisabled": value.get("isDisabled") is True,
        "bounds": {
            key: round(float(bounds.get(key, 0)), 1)
            for key in ("x", "y", "width", "height")
            if isinstance(bounds.get(key, 0), (int, float))
        },
    }


def sanitize_snapshot(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("snapshot must be an object")
    frames: list[dict[str, Any]] = []
    raw_frames = value.get("frames") if isinstance(value.get("frames"), list) else []
    for raw_frame in raw_frames[:40]:
        if not isinstance(raw_frame, dict):
            continue
        elements = []
        raw_elements = raw_frame.get("elements")
        if isinstance(raw_elements, list):
            for raw_element in raw_elements[:250]:
                element = _safe_element(raw_element)
                if element is not None:
                    elements.append(element)
        frames.append(
            {
                "frameId": raw_frame.get("frameId")
                if isinstance(raw_frame.get("frameId"), int)
                else None,
                "url": _safe_url(raw_frame.get("url")),
                "title": _text(raw_frame.get("title"), 200),
                "elements": elements,
            }
        )
    tab_id = value.get("tabId")
    return {
        "tabId": tab_id if isinstance(tab_id, int) else None,
        "url": _safe_url(value.get("url")),
        "title": _text(value.get("title"), 200),
        "capturedAt": _text(value.get("capturedAt"), 64),
        "frames": frames,
        "elementCount": sum(len(frame["elements"]) for frame in frames),
    }


class ChromeBridge:
    def __init__(self, clock: Clock) -> None:
        self.clock = clock
        self._lock = threading.Lock()
        self._connection_id = ""
        self._extension_id = ""
        self._connected_at: str | None = None
        self._last_seen_at: str | None = None
        self._snapshot: dict[str, Any] | None = None
        self._error = ""

    def connect(self, connection_id: str, extension_id: str) -> None:
        with self._lock:
            self._connection_id = connection_id
            self._extension_id = extension_id
            self._connected_at = to_iso(self.clock.now())
            self._last_seen_at = self._connected_at
            self._error = ""

    def heartbeat(self, connection_id: str) -> None:
        with self._lock:
            if connection_id == self._connection_id:
                self._last_seen_at = to_iso(self.clock.now())

    def update(self, connection_id: str, snapshot: Any) -> None:
        safe = sanitize_snapshot(snapshot)
        with self._lock:
            if connection_id != self._connection_id:
                return
            self._snapshot = safe
            self._last_seen_at = to_iso(self.clock.now())
            self._error = ""

    def fail(self, connection_id: str, message: Any) -> None:
        with self._lock:
            if connection_id == self._connection_id:
                self._error = _text(message, 300)
                self._last_seen_at = to_iso(self.clock.now())

    def unshare(self, connection_id: str) -> None:
        with self._lock:
            if connection_id == self._connection_id:
                self._snapshot = None
                self._last_seen_at = to_iso(self.clock.now())

    def disconnect(self, connection_id: str) -> None:
        with self._lock:
            if connection_id == self._connection_id:
                self._connection_id = ""

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "isConnected": bool(self._connection_id),
                "isSharing": self._snapshot is not None,
                "extensionId": self._extension_id,
                "connectedAt": self._connected_at,
                "lastSeenAt": self._last_seen_at,
                "error": self._error,
                "snapshot": self._snapshot,
            }
