"""Alerting contract: every channel (UI, email, webhook) follows the same shape."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from ..domain.enums import AlertLevel


@dataclass(frozen=True)
class Alert:
    level: AlertLevel
    title: str
    body: str = ""
    run_id: str | None = None
    incident_id: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)


class NotifierPort(Protocol):
    def send(self, alert: Alert) -> bool: ...
