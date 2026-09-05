"""Evaluate download slowness against fixed thresholds (F-07): a simple alert
with no state and no external connection — the starting point for the full
early-warning rules engine later (P5).
"""

from __future__ import annotations

from ...domain.enums import AlertLevel


def evaluate_latency(
    duration_seconds: float,
    *,
    warn_after_seconds: float | None,
    critical_after_seconds: float | None,
) -> AlertLevel | None:
    """Return the appropriate alert level, or None if the download is within normal range.

    Critical takes priority over warning if both thresholds are exceeded. A None threshold is ignored.
    """
    if critical_after_seconds is not None and duration_seconds >= critical_after_seconds:
        return AlertLevel.RED
    if warn_after_seconds is not None and duration_seconds >= warn_after_seconds:
        return AlertLevel.YELLOW
    return None
