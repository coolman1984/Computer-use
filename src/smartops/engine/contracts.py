"""Step contracts: what a step can see, and what it can return. A step never knows about the database."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Callable, Protocol

from ..core.errors import SmartOpsError


class StepOutcome(StrEnum):
    OK = "ok"
    WAIT = "wait"
    FAIL = "fail"


@dataclass
class StepResult:
    outcome: StepOutcome
    output: dict[str, Any] = field(default_factory=dict)
    wait_seconds: float = 0.0
    reason: str = ""
    error: SmartOpsError | None = None

    @classmethod
    def ok(cls, **output: Any) -> "StepResult":
        return cls(outcome=StepOutcome.OK, output=output)

    @classmethod
    def wait(cls, seconds: float, reason: str = "") -> "StepResult":
        """An intentional pause: waiting for a report to be ready or for an external dependency."""
        return cls(outcome=StepOutcome.WAIT, wait_seconds=seconds, reason=reason)

    @classmethod
    def fail(cls, error: SmartOpsError, reason: str = "") -> "StepResult":
        return cls(outcome=StepOutcome.FAIL, error=error, reason=reason or error.message)


@dataclass
class StepContext:
    """Everything a step needs: its inputs, the run's shared state, and the services."""

    run_id: str
    step_name: str
    attempt: int
    params: dict[str, Any]
    state: dict[str, Any]
    services: Any
    emit: Callable[..., Any]

    def get(self, key: str, default: Any = None) -> Any:
        """Read from the step's own params first, then fall back to the run's shared state."""
        if key in self.params:
            return self.params[key]
        return self.state.get(key, default)


class Step(Protocol):
    def __call__(self, ctx: StepContext) -> StepResult: ...
