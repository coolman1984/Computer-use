"""Domain models. Plain dataclasses, to keep the core light and easy to test."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from ..core.clock import to_iso
from .enums import (
    AgentMode,
    EventType,
    IncidentStatus,
    ProcessStatus,
    RunStatus,
    Severity,
    StepStatus,
    TriggerType,
    ValidationStatus,
    RecordingStatus,
)


@dataclass
class Process:
    """An automation the platform can actually re-run.

    This is what a recording becomes once it has been reviewed: an ordered,
    executable plan plus the validation rules its output must satisfy and the
    schedule it runs on. Everything downstream — a manual run, a test run, the
    scheduler — goes through a Process, so "recorded once" and "runs every
    night by itself" are the same object at two points in its life.
    """

    id: str
    name: str
    system_key: str
    report_key: str
    status: ProcessStatus = ProcessStatus.DRAFT
    version: int = 1
    recording_id: str | None = None
    plan: dict[str, Any] = field(default_factory=dict)
    validation_rules: dict[str, Any] = field(default_factory=dict)
    schedule_daily_at: str = ""
    schedule_every_seconds: float | None = None
    schedule_enabled: bool = False
    last_test_run_id: str | None = None
    last_run_id: str | None = None
    error_message: str | None = None
    approved_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @property
    def is_runnable(self) -> bool:
        return self.status is ProcessStatus.APPROVED

    @property
    def is_scheduled(self) -> bool:
        """Only an approved process with a real schedule is ever picked up automatically."""
        return (
            self.is_runnable
            and self.schedule_enabled
            and bool(self.schedule_daily_at or self.schedule_every_seconds)
        )

    def to_run_params(self) -> dict[str, Any]:
        """Params for runner.create_run("process.replay", params=...)."""
        return {
            "process_id": self.id,
            "system": self.system_key,
            "report": self.report_key,
            "plan": self.plan,
            "rules": dict(self.validation_rules),
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "system_key": self.system_key,
            "report_key": self.report_key,
            "status": self.status.value,
            "version": self.version,
            "recording_id": self.recording_id,
            "plan": self.plan,
            "validation_rules": self.validation_rules,
            "schedule": {
                "daily_at": self.schedule_daily_at,
                "every_seconds": self.schedule_every_seconds,
                "enabled": self.schedule_enabled,
            },
            "last_test_run_id": self.last_test_run_id,
            "last_run_id": self.last_run_id,
            "error_message": self.error_message,
            "is_runnable": self.is_runnable,
            "is_scheduled": self.is_scheduled,
            "action_count": len(self.plan.get("actions") or []),
            "approved_at": to_iso(self.approved_at),
            "created_at": to_iso(self.created_at),
            "updated_at": to_iso(self.updated_at),
        }


@dataclass
class Recording:
    id: str
    name: str
    system_key: str
    status: RecordingStatus = RecordingStatus.DRAFT
    version: int = 1
    parent_recording_id: str | None = None
    artifact_dir: str = ""
    worker_pid: int | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    heartbeat_at: datetime | None = None
    error_message: str | None = None
    step_count: int = 0
    download_count: int = 0
    automation_draft: dict[str, Any] = field(default_factory=dict)
    created_at: datetime | None = None
    updated_at: datetime | None = None
    deleted_at: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "name": self.name, "system_key": self.system_key,
            "status": self.status.value, "version": self.version,
            "parent_recording_id": self.parent_recording_id, "started_at": to_iso(self.started_at),
            "finished_at": to_iso(self.finished_at), "heartbeat_at": to_iso(self.heartbeat_at),
            "error_message": self.error_message, "step_count": self.step_count,
            "download_count": self.download_count, "automation_draft": self.automation_draft,
            "created_at": to_iso(self.created_at), "updated_at": to_iso(self.updated_at),
            "deleted_at": to_iso(self.deleted_at),
        }


@dataclass
class RecordingStep:
    """One thing a person did, described completely enough to repeat and to verify.

    A click log was never enough. Repeating a human task needs to know what kind
    of action it was, where it happened (which tab, which frame), how to find the
    element again, what went into it, **and what proves it worked** — because a
    dispatched click is not a result. Two more fields make the sequence safe to
    resume and to retry: where execution can pick up again, and whether repeating
    this particular step could do damage.

    The older `kind`/`selector`/`x_ratio` fields are kept and still populated, so
    recordings made before this contract existed still load and still replay.
    """

    recording_id: str
    seq: int
    kind: str
    occurred_at: datetime | None = None
    page_url_redacted: str = ""
    page_title: str = ""
    selector: str = ""
    target_text_redacted: str = ""
    x_ratio: float | None = None
    y_ratio: float | None = None
    changed_ratio: float | None = None
    request_ref: str = ""
    download_ref: str = ""
    before_image: str = ""
    after_image: str = ""

    # --- the step contract ---
    # What was done: click, fill, select, press, navigate, switch_page,
    # switch_frame, wait_for, download.
    action: str = ""
    # Where: {"page": "main" | "latest" | "page-2", "frame": "" | frame selector}.
    target: dict[str, Any] = field(default_factory=dict)
    # How to find the element again: {"strategy", "value", "fallbacks": [...]}
    # plus x_ratio/y_ratio as the last resort when the page offers no stable name.
    locator: dict[str, Any] = field(default_factory=dict)
    # What goes in: {"value"} for typing and selecting, {"key"} for the keyboard,
    # {"secret_ref"} when the value must come from the credential store instead —
    # a password is never stored here.
    inputs: dict[str, Any] = field(default_factory=dict)
    # What proves it worked: {"type": "selector_visible" | "value_equals" |
    # "url_changed" | "new_page" | "download_started" | ..., "value": ...}.
    success: dict[str, Any] = field(default_factory=dict)
    # How far execution has got once this step succeeds, so a resumed run knows
    # what it may skip.
    checkpoint: str = ""
    # {"max_attempts": n, "safe_to_repeat": bool}. Repeating a submit or a
    # download is not safe: it can double-file a request or double-charge one.
    retry: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "recording_id": self.recording_id, "seq": self.seq, "kind": self.kind,
            "occurred_at": to_iso(self.occurred_at), "page_url_redacted": self.page_url_redacted,
            "page_title": self.page_title, "selector": self.selector,
            "target_text_redacted": self.target_text_redacted, "x_ratio": self.x_ratio,
            "y_ratio": self.y_ratio, "changed_ratio": self.changed_ratio,
            "request_ref": self.request_ref, "download_ref": self.download_ref,
            "before_image": self.before_image, "after_image": self.after_image,
            "action": self.action or self.kind,
            "target": self.target or {"page": "main", "frame": ""},
            "locator": self.locator or ({"strategy": "css", "value": self.selector} if self.selector else {}),
            "inputs": self.inputs or {},
            "success": self.success or {"type": "none"},
            "checkpoint": self.checkpoint,
            "retry": self.retry or {"max_attempts": 1, "safe_to_repeat": False},
        }


@dataclass(frozen=True)
class StepDefinition:
    """One step inside a workflow: a unique name, the registered executor, and its params."""

    name: str
    uses: str
    params: dict[str, Any] = field(default_factory=dict)
    max_attempts: int | None = None
    title: str = ""


@dataclass(frozen=True)
class WorkflowDefinition:
    key: str
    title: str
    steps: tuple[StepDefinition, ...]
    version: int = 1
    description: str = ""

    def step(self, name: str) -> StepDefinition | None:
        return next((s for s in self.steps if s.name == name), None)

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "title": self.title,
            "version": self.version,
            "description": self.description,
            "steps": [
                {"name": s.name, "uses": s.uses, "title": s.title, "params": s.params}
                for s in self.steps
            ],
        }


@dataclass
class Run:
    id: str
    workflow_key: str
    workflow_version: int
    status: RunStatus
    trigger: TriggerType
    params: dict[str, Any] = field(default_factory=dict)
    state: dict[str, Any] = field(default_factory=dict)
    created_at: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    resume_at: datetime | None = None
    error_class: str | None = None
    error_message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "workflow_key": self.workflow_key,
            "workflow_version": self.workflow_version,
            "status": self.status.value,
            "trigger": self.trigger.value,
            "params": self.params,
            "state": self.state,
            "created_at": to_iso(self.created_at),
            "started_at": to_iso(self.started_at),
            "finished_at": to_iso(self.finished_at),
            "resume_at": to_iso(self.resume_at),
            "error_class": self.error_class,
            "error_message": self.error_message,
        }


@dataclass
class StepRecord:
    run_id: str
    name: str
    seq: int
    status: StepStatus
    attempt: int = 0
    input: dict[str, Any] = field(default_factory=dict)
    output: dict[str, Any] = field(default_factory=dict)
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error_class: str | None = None
    error_message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "name": self.name,
            "seq": self.seq,
            "status": self.status.value,
            "attempt": self.attempt,
            "input": self.input,
            "output": self.output,
            "started_at": to_iso(self.started_at),
            "finished_at": to_iso(self.finished_at),
            "error_class": self.error_class,
            "error_message": self.error_message,
        }


@dataclass
class Event:
    id: str
    type: EventType
    severity: Severity
    created_at: datetime
    run_id: str | None = None
    step_name: str | None = None
    message: str = ""
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type.value,
            "severity": self.severity.value,
            "created_at": to_iso(self.created_at),
            "run_id": self.run_id,
            "step_name": self.step_name,
            "message": self.message,
            "payload": self.payload,
        }


@dataclass
class FileArtifact:
    id: str
    run_id: str | None
    system: str
    report: str
    path: str
    original_name: str = ""
    size_bytes: int = 0
    sha256: str = ""
    row_count: int | None = None
    period: str = ""
    validation_status: ValidationStatus = ValidationStatus.PENDING
    validation_details: dict[str, Any] = field(default_factory=dict)
    created_at: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "run_id": self.run_id,
            "system": self.system,
            "report": self.report,
            "path": self.path,
            "original_name": self.original_name,
            "size_bytes": self.size_bytes,
            "sha256": self.sha256,
            "row_count": self.row_count,
            "period": self.period,
            "validation_status": self.validation_status.value,
            "validation_details": self.validation_details,
            "created_at": to_iso(self.created_at),
        }


@dataclass
class Incident:
    id: str
    title: str
    severity: Severity
    status: IncidentStatus = IncidentStatus.OPEN
    run_id: str | None = None
    signature: str = ""
    root_cause: str = ""
    resolution: str = ""
    pack_path: str = ""
    created_at: datetime | None = None
    resolved_at: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "severity": self.severity.value,
            "status": self.status.value,
            "run_id": self.run_id,
            "signature": self.signature,
            "root_cause": self.root_cause,
            "resolution": self.resolution,
            "pack_path": self.pack_path,
            "created_at": to_iso(self.created_at),
            "resolved_at": to_iso(self.resolved_at),
        }


@dataclass
class AgentRun:
    id: str
    agent: str
    model: str
    mode: AgentMode
    reason: str
    incident_id: str | None = None
    run_id: str | None = None
    thinking_level: str = "medium"
    result: str = ""
    tokens_in: int = 0
    tokens_out: int = 0
    escalated_to: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "agent": self.agent,
            "model": self.model,
            "mode": self.mode.value,
            "reason": self.reason,
            "incident_id": self.incident_id,
            "run_id": self.run_id,
            "thinking_level": self.thinking_level,
            "result": self.result,
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
            "escalated_to": self.escalated_to,
            "started_at": to_iso(self.started_at),
            "finished_at": to_iso(self.finished_at),
        }
