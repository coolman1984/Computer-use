"""The single journey, computed from real data, and the gates between its stages.

One place decides what stage the user is at and what they may do next. The web
app renders this instead of inventing its own idea of progress, and the API
enforces it instead of trusting the UI to hide a button — so the two can never
disagree about whether a step is allowed.

The chain, and why each link is a real dependency and not a suggestion:

    1  add a system          nothing can be targeted without one
    2  test the connection   proves the address is reachable before anything is built on it
    3  sign in               a recording of a login-wall is worthless
    4  record the task       the only way the platform learns what to repeat
    5  review the recording  a plan with unreplayable steps must not become an automation
    6  test the automation   approval without proof is how broken things get scheduled
    7  approve it            the explicit human decision to let it run unattended
    8  run it                the first real result
    9  check the result      a downloaded file is not the same as a valid file
    10 schedule it           only ever for something already proven to work
    11 monitor & fix         the loop that keeps it working

Every stage answers the same four questions: what is it for, what does it need,
what proves it is done, and what is the next click.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .core.errors import ErrorClass, SmartOpsError
from .domain.enums import IncidentStatus, ProcessStatus, RunStatus, ValidationStatus
from .sessions import session_is_usable

# Stage keys, in order. The order is the dependency chain.
STAGES = (
    "system",
    "connection",
    "signin",
    "recording",
    "review",
    "test",
    "approval",
    "run",
    "result",
    "schedule",
    "monitor",
)


class StageBlocked(SmartOpsError):
    """Raised when an action is attempted before the stage it depends on is done.

    Carries the blocking stage and the page that fixes it, so the UI can offer a
    button that goes straight there instead of an error the user has to decode.
    """

    def __init__(self, message: str, *, stage: str, action: str, href: str) -> None:
        super().__init__(
            message,
            error_class=ErrorClass.PERMANENT,
            details={"blocked_stage": stage, "fix": {"label": action, "href": href}},
        )
        self.stage = stage


@dataclass
class Stage:
    key: str
    number: int
    title: str
    purpose: str
    done: bool
    detail: str
    action_label: str = ""
    action_href: str = ""
    blocked: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "number": self.number,
            "title": self.title,
            "purpose": self.purpose,
            "done": self.done,
            "blocked": self.blocked,
            "detail": self.detail,
            "action": (
                {"label": self.action_label, "href": self.action_href}
                if self.action_label
                else None
            ),
        }


@dataclass
class Journey:
    stages: list[Stage] = field(default_factory=list)
    current: str = "system"

    def to_dict(self) -> dict[str, Any]:
        return {
            "stages": [s.to_dict() for s in self.stages],
            "current": self.current,
            "complete": all(s.done for s in self.stages),
        }


def _plural(count: int, singular: str, plural: str | None = None) -> str:
    return f"{count} {singular if count == 1 else (plural or singular + 's')}"


def build_journey(services: Any) -> Journey:
    """Compute every stage's real status from the database and the filesystem."""
    systems = services.systems.list()
    processes = services.processes.list(limit=200)
    approved = [p for p in processes if p.status is ProcessStatus.APPROVED]
    tested = [p for p in processes if p.status in (ProcessStatus.TESTED, ProcessStatus.APPROVED)]
    scheduled = [p for p in approved if p.is_scheduled]
    recordings = services.recordings.list(limit=200)
    completed_recordings = [r for r in recordings if r.automation_draft.get("actions")]
    files = services.files.list(limit=200)
    valid_files = [f for f in files if f.validation_status is ValidationStatus.PASSED]
    open_incidents = services.incidents.list(status=IncidentStatus.OPEN, limit=200)
    succeeded_runs = services.runs.list(status=RunStatus.SUCCEEDED, limit=50)
    overdue = overdue_automations(services)

    needs_signin = [
        s
        for s in systems
        if s.auth.mode != "none"
        and not session_is_usable(services.settings.storage.sessions_dir, s.key)
    ]
    connected = [s for s in systems if s.key in services.connection_checks]

    stages = [
        Stage(
            key="system",
            number=1,
            title="Add the system",
            purpose="Tell the platform which site the work happens on.",
            done=bool(systems),
            detail=(
                f"{_plural(len(systems), 'system')} defined."
                if systems
                else "No system yet. Add the site you collect reports from."
            ),
            action_label="Open Systems",
            action_href="systems.html",
        ),
        Stage(
            key="connection",
            number=2,
            title="Test the connection",
            purpose="Prove the address opens from this machine before building on it.",
            done=bool(connected),
            detail=(
                f"{_plural(len(connected), 'system')} tested successfully."
                if connected
                else "Not tested yet. One click confirms the address and the network."
            ),
            action_label="Test now",
            action_href="systems.html",
        ),
        Stage(
            key="signin",
            number=3,
            title="Sign in",
            purpose="Save a session once so later runs never face a login wall.",
            done=bool(systems) and not needs_signin,
            detail=(
                "Every system that needs a sign-in is connected."
                if systems and not needs_signin
                else "Waiting on: " + ", ".join(s.name or s.key for s in needs_signin)
                if needs_signin
                else "Waiting on step 1."
            ),
            action_label="Open Sign-in",
            action_href="credentials.html",
        ),
        Stage(
            key="recording",
            number=4,
            title="Record the task",
            purpose="Do the job once yourself; the platform watches and learns it.",
            done=bool(recordings),
            detail=(
                f"{_plural(len(recordings), 'recording')} captured."
                if recordings
                else "Nothing recorded yet. Record the report you want collected."
            ),
            action_label="Open Recordings",
            action_href="recordings.html",
        ),
        Stage(
            key="review",
            number=5,
            title="Review the recording",
            purpose="Check the captured steps can actually be repeated.",
            done=bool(completed_recordings),
            detail=(
                f"{_plural(len(completed_recordings), 'recording')} reviewed and ready to become an automation."
                if completed_recordings
                else "Open a finished recording and build its automation plan."
            ),
            action_label="Open Recordings",
            action_href="recordings.html",
        ),
        Stage(
            key="test",
            number=6,
            title="Test the automation",
            purpose="Run it once for real, before trusting it with anything.",
            done=bool(tested),
            detail=(
                f"{_plural(len(tested), 'automation')} passed a real test run."
                if tested
                else "No automation has passed a test yet."
            ),
            action_label="Open Automations",
            action_href="processes.html",
        ),
        Stage(
            key="approval",
            number=7,
            title="Approve it",
            purpose="Your explicit go-ahead for it to run without you watching.",
            done=bool(approved),
            detail=(
                f"{_plural(len(approved), 'automation')} approved."
                if approved
                else "Nothing approved yet. Approval is only offered after a test passes."
            ),
            action_label="Open Automations",
            action_href="processes.html",
        ),
        Stage(
            key="run",
            number=8,
            title="Run it",
            purpose="Produce the first real result on demand.",
            done=bool(succeeded_runs),
            detail=(
                f"{_plural(len(succeeded_runs), 'run')} completed successfully."
                if succeeded_runs
                else "No successful run yet."
            ),
            action_label="Open Runs",
            action_href="runs.html",
        ),
        Stage(
            key="result",
            number=9,
            title="Check the result",
            purpose="A downloaded file is not the same as a correct file.",
            done=bool(valid_files),
            detail=(
                f"{_plural(len(valid_files), 'file')} downloaded and validated."
                if valid_files
                else "No validated file yet."
            ),
            action_label="Open Files",
            action_href="files.html",
        ),
        Stage(
            key="schedule",
            number=10,
            title="Put it on a schedule",
            purpose="Make it happen by itself from now on.",
            done=bool(scheduled),
            detail=(
                f"{_plural(len(scheduled), 'automation')} running on a schedule."
                if scheduled
                else "Nothing scheduled yet. Only approved automations can be scheduled."
            ),
            action_label="Open Automations",
            action_href="processes.html",
        ),
        Stage(
            key="monitor",
            number=11,
            title="Watch and fix",
            purpose="Know the moment something breaks, and what to do about it.",
            # This stage is "done" while there is nothing wrong. It is the only
            # stage that can go back to not-done on its own, which is the point:
            # it is the ongoing one, not a box to tick once.
            #
            # "Nothing wrong" is judged on real results, not on a clean log. An
            # automation that quietly stopped producing files raises no incident
            # at all — nothing failed, nothing ran — and would otherwise leave
            # this stage green while the thing it exists for had stopped.
            done=bool(scheduled) and not open_incidents and not overdue,
            detail=(
                f"{_plural(len(open_incidents), 'issue')} need attention."
                if open_incidents
                else f"{_plural(len(overdue), 'automation')} have not produced a result when expected."
                if overdue
                else "Everything is running and producing results."
                if scheduled
                else "Waiting on step 10."
            ),
            action_label="Open Issues",
            action_href="incidents.html",
        ),
    ]

    # Everything after the first unfinished stage is blocked: showing eleven
    # equally-available buttons is what made the old UI feel like a pile of
    # pages instead of one path.
    current = STAGES[-1]
    blocking_found = False
    for stage in stages:
        if blocking_found:
            stage.blocked = True
        elif not stage.done:
            current = stage.key
            blocking_found = True

    return Journey(stages=stages, current=current)


def overdue_automations(services: Any) -> list[Any]:
    """Scheduled automations that should have produced a result by now and have not.

    Silence is the failure mode a log cannot show. If the scheduler stops, or a
    run never gets picked up, nothing fails — there is simply no new file, and
    every "did anything go wrong" check based on incidents or run status answers
    no. This asks the only question that matters: when did this automation last
    actually produce a validated file, and is that longer ago than its own
    schedule allows?
    """
    now = services.clock.now()
    overdue: list[Any] = []
    for process in services.processes.scheduled():
        allowed = _expected_gap_seconds(process)
        if allowed is None:
            continue
        last = _last_successful_result_at(services, process)
        # Never yet produced anything: judged from when it was approved, so a
        # freshly scheduled automation is not reported overdue on day one.
        reference = last or process.approved_at or process.created_at
        if reference is None:
            continue
        # One full period of slack: a run that starts on time but takes a while
        # is not late, and neither is a schedule that fires a few minutes off.
        if (now - reference).total_seconds() > allowed * 2:
            overdue.append(process)
    return overdue


def _expected_gap_seconds(process: Any) -> float | None:
    if process.schedule_every_seconds:
        return float(process.schedule_every_seconds)
    if process.schedule_daily_at:
        return 24 * 3600.0
    return None


def _last_successful_result_at(services: Any, process: Any) -> Any:
    """When this automation last produced a file that passed its checks."""
    for artifact in services.files.list(limit=500):
        if (
            artifact.system == process.system_key
            and artifact.report == process.report_key
            and artifact.validation_status is ValidationStatus.PASSED
        ):
            return artifact.created_at
    return None


# ---------- gates enforced by the API ----------


def require_system(services: Any, system_key: str) -> Any:
    """The system must exist. Every later stage hangs off this one."""
    return services.systems.get(system_key)


def require_signed_in(services: Any, system_key: str) -> None:
    """Block work that would only capture or replay a login screen."""
    system = require_system(services, system_key)
    if system.auth.mode == "none":
        return
    if system.auth.mode == "unattended":
        # Unattended systems sign themselves in at run time from the stored
        # credential, so the gate is the credential, not a saved session.
        ref = system.auth.credential_ref or system.key
        try:
            stored = services.credentials.get(ref) is not None
        except Exception:
            stored = False
        if not stored:
            raise StageBlocked(
                f"Save the username and password for '{system.name}' before this step, "
                "so the platform can sign in by itself.",
                stage="signin",
                action="Open Sign-in",
                href="credentials.html",
            )
        return
    if not session_is_usable(services.settings.storage.sessions_dir, system_key):
        # Deliberately not "is there a file": an incomplete or expired saved
        # session is exactly the case that used to pass this gate and then fail
        # overnight with nobody watching.
        raise StageBlocked(
            f"Sign in to '{system.name}' first. There is no working saved sign-in, so this "
            "would only reach the login page.",
            stage="signin",
            action="Sign in now",
            href="credentials.html",
        )


def require_connection_checked(services: Any, system_key: str) -> None:
    """Block a recording against a system whose address has never opened.

    A recording is the most expensive step to redo — it needs a human to sit
    through it — so it is worth refusing one against an address that has not
    been shown to work.
    """
    system = require_system(services, system_key)
    if system_key not in services.connection_checks:
        raise StageBlocked(
            f"Test the connection to '{system.name}' first, so you do not record against "
            "an address that cannot be opened.",
            stage="connection",
            action="Test the connection",
            href="systems.html",
        )
