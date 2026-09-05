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
