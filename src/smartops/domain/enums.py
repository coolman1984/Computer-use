"""The official statuses and types. Any new status is added here first."""

from __future__ import annotations

from enum import StrEnum


class RunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    WAITING = "waiting"
    RETRYING = "retrying"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


TERMINAL_RUN_STATUSES = frozenset({RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.CANCELLED})
RESUMABLE_RUN_STATUSES = frozenset({RunStatus.QUEUED, RunStatus.WAITING, RunStatus.RETRYING, RunStatus.RUNNING})

# Recorded actions that are not performed against an element: moving to another
# tab or frame, a navigation, a wait, a file that has already arrived, a browser
# dialog the recording already answered. Everything else needs a way to find
# something on the page, and a step of that kind with no locator is a step
# nothing can repeat.
#
# This lives here because three modules have to agree about it — the compiler,
# the review screen's locator editor, and the confidence score — and when they
# each kept their own copy they drifted: two of them had never been told that a
# dialog has no element, so clearing the locators on one was refused with a
# message about finding an element that does not exist.
ACTIONS_WITHOUT_AN_ELEMENT = frozenset({
    "navigate", "switch_page", "switch_frame", "wait_for", "download", "dialog",
})

# Every way a step can be proved to have worked. The authority is the replay
# engine: this is exactly the set `ReplaySession._check_success` knows how to
# check, and a proof outside it is a promise nobody can keep — the run would
# either skip the check or fail on a type it does not recognise.
#
# Here for the same reason as the set above: three places have to agree about
# it — the compiler that proposes a proof, the review screen that lets a person
# choose one, and the observation timeline that offers candidates — and they
# had already drifted. The review screen was refusing two proofs the engine
# checks perfectly well, so a reviewer could not pick the only proof that
# actually works for a multi-choice filter.
#
# "none" is deliberately absent. It is a real recorded value — plenty of steps
# are captured before anyone knows what proves them — but it is not something a
# reviewer may choose, because choosing it means choosing to check nothing.
PROVABLE_SUCCESS_TYPES = frozenset({
    "selector_visible", "selector_hidden", "content_changed",
    "value_equals", "selected_values_are",
    "value_not_empty", "checked_is", "url_changed", "new_page", "page_available",
    "download_started", "network_response", "next_step_actionable",
})


class StepStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    WAITING = "waiting"
    RETRYING = "retrying"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"


class Severity(StrEnum):
    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class AlertLevel(StrEnum):
    GREEN = "green"
    YELLOW = "yellow"
    ORANGE = "orange"
    RED = "red"
    CRITICAL = "critical"


class TriggerType(StrEnum):
    MANUAL = "manual"
    SCHEDULE = "schedule"
    DEPENDENCY = "dependency"
    RETRY = "retry"
    AGENT = "agent"


class EventType(StrEnum):
    RUN_CREATED = "run_created"
    RUN_STARTED = "run_started"
    RUN_WAITING = "run_waiting"
    RUN_RESUMED = "run_resumed"
    RUN_SUCCEEDED = "run_succeeded"
    RUN_FAILED = "run_failed"
    RUN_CANCELLED = "run_cancelled"
    STEP_STARTED = "step_started"
    STEP_SUCCEEDED = "step_succeeded"
    STEP_FAILED = "step_failed"
    STEP_RETRY_SCHEDULED = "step_retry_scheduled"
    FILE_DOWNLOADED = "file_downloaded"
    FILE_VALIDATED = "file_validated"
    FILE_REJECTED = "file_rejected"
    ALERT_RAISED = "alert_raised"
    INCIDENT_OPENED = "incident_opened"
    INCIDENT_CLOSED = "incident_closed"
    AGENT_RUN_STARTED = "agent_run_started"
    AGENT_RUN_FINISHED = "agent_run_finished"
    ESCALATED = "escalated"
    RECORDING_CREATED = "recording_created"
    RECORDING_STARTED = "recording_started"
    RECORDING_PAUSED = "recording_paused"
    RECORDING_RESUMED = "recording_resumed"
    RECORDING_STOPPED = "recording_stopped"
    RECORDING_FAILED = "recording_failed"
    RECORDING_DELETED = "recording_deleted"
    RECORDING_RESTORED = "recording_restored"
    RECORDING_DRAFT_CREATED = "recording_draft_created"
    RECORDING_SANITIZED = "recording_sanitized"
    PROCESS_CREATED = "process_created"
    PROCESS_TEST_STARTED = "process_test_started"
    PROCESS_TESTED = "process_tested"
    PROCESS_TEST_FAILED = "process_test_failed"
    PROCESS_APPROVED = "process_approved"
    PROCESS_RETIRED = "process_retired"
    PROCESS_SCHEDULE_CHANGED = "process_schedule_changed"
    SYSTEM_SAVED = "system_saved"
    SYSTEM_DELETED = "system_deleted"
    SYSTEM_CHECK_PASSED = "system_check_passed"
    SYSTEM_CHECK_FAILED = "system_check_failed"
    LOGIN_STARTED = "login_started"
    LOGIN_SUCCEEDED = "login_succeeded"
    LOGIN_FAILED = "login_failed"


class ProcessStatus(StrEnum):
    """Lifecycle of an automation built from a recording.

    The order is a gate, not a label: a process is only runnable and only
    schedulable once it reaches APPROVED, and it can only reach APPROVED
    after a real test replay actually succeeded (TESTED). That is what stops
    a raw recording from being mistaken for a working automation.
    """

    DRAFT = "draft"
    TESTING = "testing"
    TESTED = "tested"
    TEST_FAILED = "test_failed"
    APPROVED = "approved"
    RETIRED = "retired"


RUNNABLE_PROCESS_STATUSES = frozenset({ProcessStatus.APPROVED})


class RecordingStatus(StrEnum):
    DRAFT = "draft"
    STARTING = "starting"
    RECORDING = "recording"
    PAUSED = "paused"
    STOPPING = "stopping"
    COMPLETED = "completed"
    FAILED = "failed"
    INTERRUPTED = "interrupted"


class ExtractionLayer(StrEnum):
    NETWORK = "network"
    DOM = "dom"
    SELF_HEALING = "self_healing"
    VISION = "vision"
    DESKTOP = "desktop"


class ValidationStatus(StrEnum):
    PENDING = "pending"
    PASSED = "passed"
    FAILED = "failed"


class IncidentStatus(StrEnum):
    OPEN = "open"
    DIAGNOSING = "diagnosing"
    FIXING = "fixing"
    RESOLVED = "resolved"
    ESCALATED = "escalated"


class AgentMode(StrEnum):
    ANALYZE = "analyze"
    EXPERIMENT = "experiment"
    EXECUTE = "execute"
