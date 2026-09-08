"""Safe, ordered evidence emitted while a person records a browser task.

This file is deliberately separate from the database-backed recording steps.
Steps are the compact plan a reviewer sees. Observations are the diagnostic
timeline that explains why a step exists and what changed around it.
"""
from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path
from typing import Any

_SAFE_TAGS = frozenset({
    "a", "button", "input", "label", "option", "select", "textarea",
    "div", "span", "li", "td", "th", "tr", "table", "iframe",
})
_SAFE_ROLES = frozenset({
    "button", "checkbox", "combobox", "grid", "gridcell", "link", "menuitem",
    "option", "radio", "row", "tab", "textbox", "tree", "treeitem",
})
_SAFE_ACTIONS = frozenset({
    "click", "fill", "select", "check", "press", "navigate", "switch_page",
    "switch_frame", "wait_for", "download",
})
_SAFE_SUCCESS_TYPES = frozenset({
    "none", "download_started", "network_response", "new_page", "page_available",
    "selector_visible", "value_equals", "value_not_empty", "checked_is", "url_matches",
})
_SAFE_RESOURCE_TYPES = frozenset({
    "document", "stylesheet", "image", "media", "font", "script", "texttrack",
    "xhr", "fetch", "eventsource", "websocket", "manifest", "other",
})
_SAFE_PAGE = re.compile(r"^(?:main|latest|page-[1-9][0-9]*)$")
_SAFE_ERROR = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,80}$")


class ObservationBus:
    """Append safe facts to one monotonic, per-recording timeline.

    The worker is the sole producer today, but the lock keeps this safe when a
    future read-only sensor publishes from a browser callback. Data is
    allowlisted here as a second line of defence: a caller must not be able to
    accidentally put an element selector, a filename, text, or a field value
    into an evidence timeline.
    """

    def __init__(self, artifact_dir: Path, recording_id: str) -> None:
        self.path = artifact_dir / "observations" / "timeline.jsonl"
        self.recording_id = recording_id
        self._sequence = 0
        self._lock = threading.Lock()

    def emit(self, kind: str, *, source: str, data: dict[str, Any] | None = None) -> int:
        """Write one JSON-safe event and return its recording-local sequence."""
        with self._lock:
            self._sequence += 1
            event = {
                "recording_id": self.recording_id,
                "sequence": self._sequence,
                "timestamp_monotonic_ns": time.monotonic_ns(),
                "kind": kind,
                "source": source,
                "data": _safe_data(data),
            }
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n")
            return self._sequence


def safe_state(value: Any) -> dict[str, Any]:
    """Keep only tiny structural state; never accept text, values, or markup."""
    if not isinstance(value, dict):
        return {}
    state: dict[str, Any] = {}
    for key in ("visible", "enabled", "checked", "selected"):
        if key not in value:
            continue
        item = value.get(key)
        if isinstance(item, bool):
            state[key] = item
    if type(value.get("count")) is int and value["count"] >= 0:
        state["count"] = value["count"]
    tag = value.get("tag")
    if isinstance(tag, str) and tag.lower() in _SAFE_TAGS:
        state["tag"] = tag.lower()
    role = value.get("role")
    if isinstance(role, str) and role.lower() in _SAFE_ROLES:
        state["role"] = role.lower()
    return state


def _safe_data(value: dict[str, Any] | None) -> dict[str, Any]:
    """Return the tiny, structural evidence contract accepted by the journal."""
    if not isinstance(value, dict):
        return {}
    safe: dict[str, Any] = {}
    action = value.get("action")
    if isinstance(action, str) and action in _SAFE_ACTIONS:
        safe["action"] = action
    page = value.get("page")
    if isinstance(page, str) and _SAFE_PAGE.fullmatch(page):
        safe["page"] = page
    success_type = value.get("success_type")
    if isinstance(success_type, str) and success_type in _SAFE_SUCCESS_TYPES:
        safe["success_type"] = success_type
    resource_type = value.get("resource_type")
    if isinstance(resource_type, str) and resource_type in _SAFE_RESOURCE_TYPES:
        safe["resource_type"] = resource_type
    method = value.get("method")
    if isinstance(method, str) and method.upper() in {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}:
        safe["method"] = method.upper()
    error_type = value.get("error_type")
    if isinstance(error_type, str) and _SAFE_ERROR.fullmatch(error_type):
        safe["error_type"] = error_type
    for key in ("download_count", "network_request_count", "size_bytes"):
        number = value.get(key)
        if type(number) is int and 0 <= number <= 2**63 - 1:
            safe[key] = number
    for key in ("before", "after"):
        state = safe_state(value.get(key))
        if state:
            safe[key] = state
    return safe
