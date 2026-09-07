"""Read-only instruments for following a browser recording, plus one paragraph
of one-time generic advice for when nothing else is watching.

The old shape of this module sent a CLI agent one generic prompt at the start
of a recording and then told the interface it was "watching" the workflow —
which was never true, because nothing about a fixed paragraph of advice
depends on what is actually happening on screen. What follows instead is a
small set of instruments built directly on what the recorder already knows:
the steps it has written down so far, the screen its worker can look at right
now, what that screen offers, why its screenshots look the way they do, and
which steps still carry no proof they worked. An assistant — the operator's
own `codex` CLI, or anything else that can make an HTTP call — can ask these
as often as it likes and, for the step list, ask for only what is new since
it last asked.

The one-time CLI call below is kept, demoted to a fallback: when nothing else
is watching a recording at all, one paragraph of general advice at the start
beats silence. It is never described as more than that again.

**These instruments are read-only, and they have to stay that way.** Every
method below reads state the recorder or its worker already collected; none
of them may click, type, navigate, or otherwise act on the browser. Exactly
one thing drives the browser during a recording — the worker's own thread,
reacting to a human's input — and a second caller acting on the same page
would not be a second sense, it would be a race the human loses. If a future
instrument needs to *try* something to see what happens, it belongs in a
different phase than this one, not here.

Only generic structure is sent to the CLI agent used for the fallback advice.
Raw page text, selectors, URLs, screenshots, downloaded files, cookies,
usernames, and passwords never enter that request, and the instruments below
never leave this process at all except as the redacted step and screen facts
the recorder already produces.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any

from ..core.ids import new_id
from ..domain.enums import AgentMode, EventType, Severity
from ..domain.models import AgentRun
from ..ports.agents import AgentRequest
from . import instruments


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
    """One optional CLI opinion, and the real instruments a live assistant needs.

    `start()` still launches one ephemeral, analyze-only CLI agent per
    recording for a single paragraph of generic advice. The methods below it
    are what make this class worth calling during a recording that is
    actually running: they read the recorder's own state directly and touch
    nothing in the browser, so an assistant that wants to follow a recording
    as it happens should call those, not wait on this one-time call or trust
    its old claim to be "watching".
    """

    def __init__(self, services: Any) -> None:
        self.services = services
        self._sessions: dict[str, CoachSession] = {}
        self._lock = threading.Lock()

    # ---------- read-only instruments over a live (or finished) recording ----------
    #
    # Nothing below acts on the browser. `recent_steps` and `thin_evidence`
    # read only the steps repository, so they work even after a recording has
    # finished; `look`, `capabilities`, and `diagnose_vision` ask the live
    # worker, so they answer "not running" once the browser is gone rather
    # than reconstructing a state that no longer exists. Do not add a method
    # here that performs an action — that is what turns a spectator into a
    # second pair of hands on the same browser the human is using.

    def recent_steps(self, recording_id: str, *, since_seq: int = 0, limit: int = 50) -> dict[str, Any]:
        """What just happened, only the part the caller has not already seen."""
        return instruments.recent_steps(
            self.services.recordings.steps(recording_id), since_seq=since_seq, limit=limit
        )

    def thin_evidence(self, recording_id: str) -> list[dict[str, Any]]:
        """Which recorded steps still have no proof that they worked."""
        return instruments.thin_evidence(self.services.recordings.steps(recording_id))

    def look(self, recording_id: str, *, capture: bool = True) -> dict[str, Any]:
        """What is on screen right now, straight from the recording worker."""
        return self._ask_worker(recording_id, lambda worker: worker.look(capture=capture))

    def capabilities(self, recording_id: str) -> dict[str, Any]:
        """What the screen currently open in the recorder offers to automate."""
        return self._ask_worker(recording_id, lambda worker: worker.capabilities())

    def diagnose_vision(self, recording_id: str) -> dict[str, Any]:
        """Why this recording's screenshots look the way they do."""
        return self._ask_worker(recording_id, lambda worker: worker.diagnose_vision())

    def _ask_worker(self, recording_id: str, question: Any) -> dict[str, Any]:
        worker = self.services.recording_manager.workers.get(recording_id)
        if worker is None:
            return {"available": False, "reason": "this recording is not running"}
        return question(worker)

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
                session.message = (
                    "The CLI coach returned generic guidance. It is not watching this "
                    "recording; follow it live through the recent-steps, thin-evidence, "
                    "live, vision, and capabilities instruments instead."
                )
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
