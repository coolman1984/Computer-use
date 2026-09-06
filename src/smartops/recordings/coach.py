"""A read-only CLI agent that prepares and guides browser recordings.

Only generic structure is sent to the agent. Raw page text, selectors, URLs,
screenshots, downloaded files, cookies, usernames, and passwords never enter
the coach request.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any

from ..core.ids import new_id
from ..domain.enums import AgentMode, EventType, Severity
from ..domain.models import AgentRun
from ..ports.agents import AgentRequest


_BASE_ADVICE = [
    "Perform one complete path from the starting screen to the final download.",
    "Wait for each screen to finish changing before the next action.",
    "Do not repeat the sign-in credential entry; SmartOps handles it separately.",
    "Choose Finish and save only after the expected file has downloaded.",
]


@dataclass
class CoachSession:
    recording_id: str
    status: str = "analyzing"  # analyzing | ready | unavailable | failed
    message: str = "Starting the read-only Recording Coach…"
    advice: list[str] = field(default_factory=lambda: list(_BASE_ADVICE))
    error: str | None = None
    _thread: threading.Thread | None = field(default=None, repr=False)

    @property
    def active(self) -> bool:
        return self.status == "analyzing"

    def to_dict(self) -> dict[str, Any]:
        return {
            "recording_id": self.recording_id,
            "status": self.status,
            "message": self.message,
            "advice": list(self.advice),
            "active": self.active,
            "error": self.error,
        }


class RecordingCoach:
    """Launch one ephemeral, analyze-only CLI agent per recording attempt."""

    def __init__(self, services: Any) -> None:
        self.services = services
        self._sessions: dict[str, CoachSession] = {}
        self._lock = threading.Lock()

    def start(self, recording_id: str) -> CoachSession:
        with self._lock:
            existing = self._sessions.get(recording_id)
            if existing is not None and existing.active:
                return existing
            session = CoachSession(recording_id=recording_id)
            self._sessions[recording_id] = session

        if self.services.agent_runner is None:
            session.status = "unavailable"
            session.message = "Recording Coach is off; the built-in guidance is still active."
            return session

        self.services.events.emit(
            EventType.AGENT_RUN_STARTED,
            message="Read-only Recording Coach started",
            step_name=f"recording-coach:{recording_id}",
            payload={"recording_id": recording_id, "mode": "analyze", "privacy": "structure_only"},
        )
        thread = threading.Thread(
            target=self._run,
            args=(session,),
            name=f"recording-coach-{recording_id}",
            daemon=True,
        )
        session._thread = thread
        thread.start()
        return session

    def status(self, recording_id: str) -> CoachSession | None:
        return self._sessions.get(recording_id)

    def _run(self, session: CoachSession) -> None:
        agent_run = AgentRun(
            id=new_id("agent"),
            agent="codex",
            model="configured-default",
            mode=AgentMode.ANALYZE,
            reason="Prepare safe guidance for a new browser recording",
            started_at=self.services.clock.now(),
        )
        self.services.agent_runs.save(agent_run)
        try:
            response = self.services.agent_runner.run(
                AgentRequest(
                    reason=(
                        "Act as a browser Recording Coach. Give one concise paragraph of practical "
                        "guidance for recording a complete, reliable download workflow. Do not use "
                        "tools, inspect files, request credentials, or discuss any company-specific data."
                    ),
                    mode=AgentMode.ANALYZE,
                    agent="codex",
                    model="configured-default",
                    thinking_level="low",
                    context={
                        "capture": "browser_actions",
                        "goal": "one_complete_download_workflow",
                        "privacy": "structure_only",
                    },
                    timeout_seconds=120,
                )
            )
            if response.ok:
                concise = " ".join(response.summary.split())[:800]
                if concise:
                    session.advice.insert(0, concise)
                session.status = "ready"
                session.message = "Recording Coach is ready and watching the workflow structure."
            else:
                session.status = "failed"
                session.message = "The CLI coach could not answer; the built-in guidance remains active."
                session.error = "The read-only CLI agent did not complete."
            agent_run.result = session.message
            agent_run.tokens_in = response.tokens_in
            agent_run.tokens_out = response.tokens_out
        except Exception:
            session.status = "failed"
            session.message = "The CLI coach could not start; the built-in guidance remains active."
            session.error = "The read-only CLI agent is unavailable."
            agent_run.result = session.message
        finally:
            agent_run.finished_at = self.services.clock.now()
            self.services.agent_runs.save(agent_run)
            self.services.events.emit(
                EventType.AGENT_RUN_FINISHED,
                severity=Severity.INFO if session.status == "ready" else Severity.WARNING,
                message=session.message,
                step_name=f"recording-coach:{session.recording_id}",
                payload={
                    "recording_id": session.recording_id,
                    "status": session.status,
                    "privacy": "structure_only",
                },
            )
