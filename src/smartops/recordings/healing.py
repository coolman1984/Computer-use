"""Non-mutating repair proposals generated from proven replay fallbacks.

This module never changes a recording, object descriptor, or approved process.
It records that a reviewed fallback was the one that actually worked, so a
human can promote it in a new recording revision after seeing the evidence.
"""

from __future__ import annotations

from typing import Any

from .redaction import redact_selector


def repair_proposal(action: dict[str, Any], *, strategy: str, selector: str = "") -> dict[str, Any] | None:
    """Return a safe proposal only when a non-primary method was proven live."""
    if strategy == "primary":
        return None
    value = redact_selector(selector)
    return {
        "step": int(action.get("seq") or 0),
        "object_ref": str(action.get("object_ref") or ""),
        "strategy": strategy,
        "candidate_selector": value if value and value != "[redacted]" else "",
        "status": "review_required",
    }
