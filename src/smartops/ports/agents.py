"""Agent manager contract: run Codex or Claude Code with explicit permission, context, and logging."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from ..domain.enums import AgentMode


@dataclass(frozen=True)
class AgentRequest:
    reason: str
    mode: AgentMode
    agent: str = "claude"
    model: str = "sonnet"
    thinking_level: str = "medium"
    context: dict[str, Any] = field(default_factory=dict)
    incident_id: str | None = None
    run_id: str | None = None
    timeout_seconds: float = 900.0


@dataclass
class AgentResponse:
    ok: bool
    summary: str = ""
    changed_files: list[str] = field(default_factory=list)
    tests_passed: bool | None = None
    tokens_in: int = 0
    tokens_out: int = 0
    should_escalate: bool = False
    raw_output: str = ""


class AgentRunnerPort(Protocol):
    def run(self, request: AgentRequest) -> AgentResponse: ...


@dataclass(frozen=True)
class EscalationStep:
    agent: str
    model: str
    thinking_level: str
    mode: AgentMode


# Default escalation ladder: cheapest first, humans last.
DEFAULT_ESCALATION: tuple[EscalationStep, ...] = (
    EscalationStep("claude", "haiku", "low", AgentMode.ANALYZE),
    EscalationStep("codex", "medium", "medium", AgentMode.ANALYZE),
    EscalationStep("claude", "sonnet", "medium", AgentMode.EXPERIMENT),
    EscalationStep("claude", "opus", "high", AgentMode.EXPERIMENT),
)
