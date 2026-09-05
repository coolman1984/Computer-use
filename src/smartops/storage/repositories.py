"""Data repositories: the only place that knows SQL. The rest of the system deals in models only."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta
from typing import Any

from ..core.clock import Clock, SystemClock, from_iso, to_iso
from ..core.ids import new_id
from ..domain.enums import (
    AgentMode,
    EventType,
    IncidentStatus,
    RunStatus,
    Severity,
    StepStatus,
    TriggerType,
    ValidationStatus,
    RecordingStatus,
)
from ..domain.models import AgentRun, Event, FileArtifact, Incident, Run, StepRecord, Recording, RecordingStep
from .db import Database


def _json(value: Any) -> str:
    return json.dumps(value or {}, ensure_ascii=False, default=str)


def _loads(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


class BaseRepository:
    def __init__(self, db: Database, clock: Clock | None = None) -> None:
        self.db = db
        self.clock = clock or SystemClock()


class RecordingRepository(BaseRepository):
    @staticmethod
    def _recording(row: sqlite3.Row) -> Recording:
        return Recording(
            id=row["id"], name=row["name"], system_key=row["system_key"], version=row["version"],
            parent_recording_id=row["parent_recording_id"], status=RecordingStatus(row["status"]),
            artifact_dir=row["artifact_dir"], worker_pid=row["worker_pid"], started_at=from_iso(row["started_at"]),
            finished_at=from_iso(row["finished_at"]), heartbeat_at=from_iso(row["heartbeat_at"]),
            error_message=row["error_message"], step_count=row["step_count"], download_count=row["download_count"],
            automation_draft=_loads(row["automation_draft"]), created_at=from_iso(row["created_at"]),
            updated_at=from_iso(row["updated_at"]), deleted_at=from_iso(row["deleted_at"]),
        )

    @staticmethod
    def _step(row: sqlite3.Row) -> RecordingStep:
        return RecordingStep(
            recording_id=row["recording_id"], seq=row["seq"], kind=row["kind"],
            occurred_at=from_iso(row["occurred_at"]), page_url_redacted=row["page_url_redacted"],
            page_title=row["page_title"], selector=row["selector"], target_text_redacted=row["target_text_redacted"],
            x_ratio=row["x_ratio"], y_ratio=row["y_ratio"], changed_ratio=row["changed_ratio"],
            request_ref=row["request_ref"], download_ref=row["download_ref"], before_image=row["before_image"], after_image=row["after_image"],
        )

    def create(self, name: str, system_key: str, *, parent: Recording | None = None, artifact_dir: str = "") -> Recording:
        now = self.clock.now()
        recording = Recording(id=new_id("rec"), name=name, system_key=system_key,
            version=(parent.version + 1 if parent else 1), parent_recording_id=(parent.id if parent else None),
            artifact_dir=artifact_dir, created_at=now, updated_at=now)
        with self.db.transaction() as tx:
            tx.execute("INSERT INTO recordings(id,name,system_key,version,parent_recording_id,status,artifact_dir,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (recording.id, recording.name, recording.system_key, recording.version, recording.parent_recording_id,
                 recording.status.value, recording.artifact_dir, to_iso(now), to_iso(now)))
        return recording

    def get(self, recording_id: str, *, include_deleted: bool = True) -> Recording | None:
        sql = "SELECT * FROM recordings WHERE id=?" + ("" if include_deleted else " AND deleted_at IS NULL")
        row = self.db.connection.execute(sql, (recording_id,)).fetchone()
        return self._recording(row) if row else None

    def list(self, *, include_deleted: bool = False, status: RecordingStatus | None = None, limit: int = 100) -> list[Recording]:
        clauses, args = ([] if include_deleted else ["deleted_at IS NULL"], [])
        if status: clauses.append("status=?"); args.append(status.value)
        sql = "SELECT * FROM recordings" + (" WHERE " + " AND ".join(clauses) if clauses else "") + " ORDER BY created_at DESC LIMIT ?"
        args.append(limit)
        return [self._recording(r) for r in self.db.connection.execute(sql, args)]

    def active_for_system(self, system_key: str, except_id: str | None = None) -> Recording | None:
        sql = "SELECT * FROM recordings WHERE system_key=? AND deleted_at IS NULL AND status IN ('starting','recording','paused','stopping')"
        args: list[Any] = [system_key]
        if except_id: sql += " AND id != ?"; args.append(except_id)
        row = self.db.connection.execute(sql, args).fetchone()
        return self._recording(row) if row else None

    def save(self, recording: Recording) -> Recording:
        recording.updated_at = self.clock.now()
        with self.db.transaction() as tx:
            tx.execute("UPDATE recordings SET status=?,artifact_dir=?,worker_pid=?,started_at=?,finished_at=?,heartbeat_at=?,error_message=?,step_count=?,download_count=?,automation_draft=?,updated_at=?,deleted_at=? WHERE id=?",
                (recording.status.value,recording.artifact_dir,recording.worker_pid,to_iso(recording.started_at),to_iso(recording.finished_at),to_iso(recording.heartbeat_at),recording.error_message,recording.step_count,recording.download_count,_json(recording.automation_draft),to_iso(recording.updated_at),to_iso(recording.deleted_at),recording.id))
        return recording

    def save_step(self, step: RecordingStep) -> RecordingStep:
        with self.db.transaction() as tx:
            tx.execute("INSERT INTO recording_steps(recording_id,seq,kind,occurred_at,page_url_redacted,page_title,selector,target_text_redacted,x_ratio,y_ratio,changed_ratio,request_ref,download_ref,before_image,after_image) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(recording_id,seq) DO UPDATE SET kind=excluded.kind,occurred_at=excluded.occurred_at,page_url_redacted=excluded.page_url_redacted,page_title=excluded.page_title,selector=excluded.selector,target_text_redacted=excluded.target_text_redacted,x_ratio=excluded.x_ratio,y_ratio=excluded.y_ratio,changed_ratio=excluded.changed_ratio,request_ref=excluded.request_ref,download_ref=excluded.download_ref,before_image=excluded.before_image,after_image=excluded.after_image",
                (step.recording_id,step.seq,step.kind,to_iso(step.occurred_at),step.page_url_redacted,step.page_title,step.selector,step.target_text_redacted,step.x_ratio,step.y_ratio,step.changed_ratio,step.request_ref,step.download_ref,step.before_image,step.after_image))
        return step

    def steps(self, recording_id: str) -> list[RecordingStep]:
        return [self._step(r) for r in self.db.connection.execute("SELECT * FROM recording_steps WHERE recording_id=? ORDER BY seq", (recording_id,))]

    def purge(self, recording_id: str) -> None:
        """Delete the recording row after maintenance has removed its private files."""
        with self.db.transaction() as tx:
            tx.execute("DELETE FROM recordings WHERE id=? AND deleted_at IS NOT NULL", (recording_id,))


class RunRepository(BaseRepository):
    @staticmethod
    def _row_to_run(row: sqlite3.Row) -> Run:
        return Run(
            id=row["id"],
            workflow_key=row["workflow_key"],
            workflow_version=row["workflow_version"],
            status=RunStatus(row["status"]),
            trigger=TriggerType(row["trigger"]),
            params=_loads(row["params"]),
            state=_loads(row["state"]),
            created_at=from_iso(row["created_at"]),
            started_at=from_iso(row["started_at"]),
            finished_at=from_iso(row["finished_at"]),
            resume_at=from_iso(row["resume_at"]),
            error_class=row["error_class"],
            error_message=row["error_message"],
        )

    def create(
        self,
        workflow_key: str,
        *,
        workflow_version: int = 1,
        params: dict[str, Any] | None = None,
        trigger: TriggerType = TriggerType.MANUAL,
    ) -> Run:
        run = Run(
            id=new_id("run"),
            workflow_key=workflow_key,
            workflow_version=workflow_version,
            status=RunStatus.QUEUED,
            trigger=trigger,
            params=params or {},
            created_at=self.clock.now(),
        )
        with self.db.transaction() as tx:
            tx.execute(
                "INSERT INTO runs (id, workflow_key, workflow_version, status, trigger, params,"
                " state, created_at) VALUES (?,?,?,?,?,?,?,?)",
                (
                    run.id,
                    run.workflow_key,
                    run.workflow_version,
                    run.status.value,
                    run.trigger.value,
                    _json(run.params),
                    _json(run.state),
                    to_iso(run.created_at),
                ),
            )
        return run

    def get(self, run_id: str) -> Run | None:
        row = self.db.connection.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        return self._row_to_run(row) if row else None

    def list(
        self,
        *,
        status: RunStatus | None = None,
        workflow_key: str | None = None,
        limit: int = 50,
    ) -> list[Run]:
        sql = "SELECT * FROM runs"
        clauses: list[str] = []
        args: list[Any] = []
        if status:
            clauses.append("status = ?")
            args.append(status.value)
        if workflow_key:
            clauses.append("workflow_key = ?")
            args.append(workflow_key)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY created_at DESC, id DESC LIMIT ?"
        args.append(limit)
        return [self._row_to_run(row) for row in self.db.connection.execute(sql, args)]

    def due(self, *, now: datetime | None = None, limit: int = 20) -> list[Run]:
        """Runs ready to resume: waiting and now due, or queued."""
        moment = to_iso(now or self.clock.now())
        rows = self.db.connection.execute(
            "SELECT * FROM runs WHERE status = 'queued'"
            " OR (status IN ('waiting','retrying') AND (resume_at IS NULL OR resume_at <= ?))"
            " ORDER BY created_at ASC LIMIT ?",
            (moment, limit),
        )
        return [self._row_to_run(row) for row in rows]

    def update(self, run: Run) -> Run:
        with self.db.transaction() as tx:
            tx.execute(
                "UPDATE runs SET status=?, params=?, state=?, started_at=?, finished_at=?,"
                " resume_at=?, error_class=?, error_message=? WHERE id=?",
                (
                    run.status.value,
                    _json(run.params),
                    _json(run.state),
                    to_iso(run.started_at),
                    to_iso(run.finished_at),
                    to_iso(run.resume_at),
                    run.error_class,
                    run.error_message,
                    run.id,
                ),
            )
        return run

    def claim(self, run_id: str, token: str, *, lease_seconds: int = 900) -> bool:
        """Optimistic lock: stops two workers from driving the same run at once."""
        now = self.clock.now()
        expires = to_iso(now + timedelta(seconds=lease_seconds))
        with self.db.transaction() as tx:
            cursor = tx.execute(
                "UPDATE runs SET lock_token=?, lock_expires_at=? WHERE id=?"
                " AND (lock_token IS NULL OR lock_expires_at IS NULL OR lock_expires_at <= ?)",
                (token, expires, run_id, to_iso(now)),
            )
            return cursor.rowcount == 1

    def release(self, run_id: str, token: str) -> None:
        with self.db.transaction() as tx:
            tx.execute(
                "UPDATE runs SET lock_token=NULL, lock_expires_at=NULL WHERE id=? AND lock_token=?",
                (run_id, token),
            )


class StepRepository(BaseRepository):
    @staticmethod
    def _row_to_step(row: sqlite3.Row) -> StepRecord:
        return StepRecord(
            run_id=row["run_id"],
            name=row["name"],
            seq=row["seq"],
            status=StepStatus(row["status"]),
            attempt=row["attempt"],
            input=_loads(row["input"]),
            output=_loads(row["output"]),
            started_at=from_iso(row["started_at"]),
            finished_at=from_iso(row["finished_at"]),
            error_class=row["error_class"],
            error_message=row["error_message"],
        )

    def get(self, run_id: str, name: str) -> StepRecord | None:
        row = self.db.connection.execute(
            "SELECT * FROM steps WHERE run_id=? AND name=?", (run_id, name)
        ).fetchone()
        return self._row_to_step(row) if row else None

    def list(self, run_id: str) -> list[StepRecord]:
        rows = self.db.connection.execute(
            "SELECT * FROM steps WHERE run_id=? ORDER BY seq ASC", (run_id,)
        )
        return [self._row_to_step(row) for row in rows]

    def save(self, step: StepRecord) -> StepRecord:
        with self.db.transaction() as tx:
            tx.execute(
                "INSERT INTO steps (run_id, name, seq, status, attempt, input, output, started_at,"
                " finished_at, error_class, error_message) VALUES (?,?,?,?,?,?,?,?,?,?,?)"
                " ON CONFLICT(run_id, name) DO UPDATE SET status=excluded.status,"
                " seq=excluded.seq, attempt=excluded.attempt, input=excluded.input,"
                " output=excluded.output, started_at=excluded.started_at,"
                " finished_at=excluded.finished_at, error_class=excluded.error_class,"
                " error_message=excluded.error_message",
                (
                    step.run_id,
                    step.name,
                    step.seq,
                    step.status.value,
                    step.attempt,
                    _json(step.input),
                    _json(step.output),
                    to_iso(step.started_at),
                    to_iso(step.finished_at),
                    step.error_class,
                    step.error_message,
                ),
            )
        return step


class EventRepository(BaseRepository):
    @staticmethod
    def _row_to_event(row: sqlite3.Row) -> Event:
        return Event(
            id=row["id"],
            type=EventType(row["type"]),
            severity=Severity(row["severity"]),
            created_at=from_iso(row["created_at"]),
            run_id=row["run_id"],
            step_name=row["step_name"],
            message=row["message"],
            payload=_loads(row["payload"]),
        )

    def append(self, event: Event) -> Event:
        with self.db.transaction() as tx:
            tx.execute(
                "INSERT INTO events (id, run_id, step_name, type, severity, message, payload,"
                " created_at) VALUES (?,?,?,?,?,?,?,?)",
                (
                    event.id,
                    event.run_id,
                    event.step_name,
                    event.type.value,
                    event.severity.value,
                    event.message,
                    _json(event.payload),
                    to_iso(event.created_at),
                ),
            )
        return event

    def list(
        self,
        *,
        run_id: str | None = None,
        types: list[EventType] | None = None,
        limit: int = 200,
    ) -> list[Event]:
        sql = "SELECT * FROM events"
        clauses: list[str] = []
        args: list[Any] = []
        if run_id:
            clauses.append("run_id = ?")
            args.append(run_id)
        if types:
            clauses.append(f"type IN ({','.join('?' * len(types))})")
            args.extend(t.value for t in types)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY seq ASC LIMIT ?"
        args.append(limit)
        return [self._row_to_event(row) for row in self.db.connection.execute(sql, args)]


class FileRepository(BaseRepository):
    @staticmethod
    def _row_to_file(row: sqlite3.Row) -> FileArtifact:
        return FileArtifact(
            id=row["id"],
            run_id=row["run_id"],
            system=row["system"],
            report=row["report"],
            path=row["path"],
            original_name=row["original_name"],
            size_bytes=row["size_bytes"],
            sha256=row["sha256"],
            row_count=row["row_count"],
            period=row["period"],
            validation_status=ValidationStatus(row["validation_status"]),
            validation_details=_loads(row["validation_details"]),
            created_at=from_iso(row["created_at"]),
        )

    def save(self, artifact: FileArtifact) -> FileArtifact:
        if not artifact.created_at:
            artifact.created_at = self.clock.now()
        with self.db.transaction() as tx:
            tx.execute(
                "INSERT INTO files (id, run_id, system, report, path, original_name, size_bytes,"
                " sha256, row_count, period, validation_status, validation_details, created_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)"
                " ON CONFLICT(id) DO UPDATE SET validation_status=excluded.validation_status,"
                " validation_details=excluded.validation_details, row_count=excluded.row_count,"
                " sha256=excluded.sha256, size_bytes=excluded.size_bytes, path=excluded.path,"
                " original_name=excluded.original_name, period=excluded.period",
                (
                    artifact.id,
                    artifact.run_id,
                    artifact.system,
                    artifact.report,
                    artifact.path,
                    artifact.original_name,
                    artifact.size_bytes,
                    artifact.sha256,
                    artifact.row_count,
                    artifact.period,
                    artifact.validation_status.value,
                    _json(artifact.validation_details),
                    to_iso(artifact.created_at),
                ),
            )
        return artifact

    def find_by_hash(self, sha256: str) -> list[FileArtifact]:
        rows = self.db.connection.execute("SELECT * FROM files WHERE sha256=?", (sha256,))
        return [self._row_to_file(row) for row in rows]

    def list(self, *, run_id: str | None = None, limit: int = 100) -> list[FileArtifact]:
        if run_id:
            rows = self.db.connection.execute(
                "SELECT * FROM files WHERE run_id=? ORDER BY created_at DESC LIMIT ?",
                (run_id, limit),
            )
        else:
            rows = self.db.connection.execute(
                "SELECT * FROM files ORDER BY created_at DESC LIMIT ?", (limit,)
            )
        return [self._row_to_file(row) for row in rows]


class IncidentRepository(BaseRepository):
    @staticmethod
    def _row_to_incident(row: sqlite3.Row) -> Incident:
        return Incident(
            id=row["id"],
            title=row["title"],
            severity=Severity(row["severity"]),
            status=IncidentStatus(row["status"]),
            run_id=row["run_id"],
            signature=row["signature"],
            root_cause=row["root_cause"],
            resolution=row["resolution"],
            pack_path=row["pack_path"],
            created_at=from_iso(row["created_at"]),
            resolved_at=from_iso(row["resolved_at"]),
        )

    def open(
        self,
        *,
        title: str,
        severity: Severity = Severity.ERROR,
        run_id: str | None = None,
        signature: str = "",
    ) -> Incident:
        incident = Incident(
            id=new_id("inc"),
            title=title,
            severity=severity,
            status=IncidentStatus.OPEN,
            run_id=run_id,
            signature=signature,
            created_at=self.clock.now(),
        )
        with self.db.transaction() as tx:
            tx.execute(
                "INSERT INTO incidents (id, run_id, title, severity, status, signature, created_at)"
                " VALUES (?,?,?,?,?,?,?)",
                (
                    incident.id,
                    incident.run_id,
                    incident.title,
                    incident.severity.value,
                    incident.status.value,
                    incident.signature,
                    to_iso(incident.created_at),
                ),
            )
        return incident

    def update(self, incident: Incident) -> Incident:
        with self.db.transaction() as tx:
            tx.execute(
                "UPDATE incidents SET status=?, root_cause=?, resolution=?, pack_path=?,"
                " resolved_at=? WHERE id=?",
                (
                    incident.status.value,
                    incident.root_cause,
                    incident.resolution,
                    incident.pack_path,
                    to_iso(incident.resolved_at),
                    incident.id,
                ),
            )
        return incident

    def get(self, incident_id: str) -> Incident | None:
        row = self.db.connection.execute(
            "SELECT * FROM incidents WHERE id=?", (incident_id,)
        ).fetchone()
        return self._row_to_incident(row) if row else None

    def list(self, *, status: IncidentStatus | None = None, limit: int = 50) -> list[Incident]:
        if status:
            rows = self.db.connection.execute(
                "SELECT * FROM incidents WHERE status=? ORDER BY created_at DESC LIMIT ?",
                (status.value, limit),
            )
        else:
            rows = self.db.connection.execute(
                "SELECT * FROM incidents ORDER BY created_at DESC LIMIT ?", (limit,)
            )
        return [self._row_to_incident(row) for row in rows]

    def find_by_signature(self, signature: str, *, limit: int = 5) -> list[Incident]:
        rows = self.db.connection.execute(
            "SELECT * FROM incidents WHERE signature=? ORDER BY created_at DESC LIMIT ?",
            (signature, limit),
        )
        return [self._row_to_incident(row) for row in rows]


class AgentRunRepository(BaseRepository):
    @staticmethod
    def _row_to_agent_run(row: sqlite3.Row) -> AgentRun:
        return AgentRun(
            id=row["id"],
            agent=row["agent"],
            model=row["model"],
            mode=AgentMode(row["mode"]),
            reason=row["reason"],
            incident_id=row["incident_id"],
            run_id=row["run_id"],
            thinking_level=row["thinking_level"],
            result=row["result"],
            tokens_in=row["tokens_in"],
            tokens_out=row["tokens_out"],
            escalated_to=row["escalated_to"],
            started_at=from_iso(row["started_at"]),
            finished_at=from_iso(row["finished_at"]),
        )

    def save(self, agent_run: AgentRun) -> AgentRun:
        if not agent_run.started_at:
            agent_run.started_at = self.clock.now()
        with self.db.transaction() as tx:
            tx.execute(
                "INSERT INTO agent_runs (id, incident_id, run_id, agent, model, mode,"
                " thinking_level, reason, result, tokens_in, tokens_out, escalated_to, started_at,"
                " finished_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
                " ON CONFLICT(id) DO UPDATE SET result=excluded.result,"
                " tokens_in=excluded.tokens_in, tokens_out=excluded.tokens_out,"
                " escalated_to=excluded.escalated_to, finished_at=excluded.finished_at",
                (
                    agent_run.id,
                    agent_run.incident_id,
                    agent_run.run_id,
                    agent_run.agent,
                    agent_run.model,
                    agent_run.mode.value,
                    agent_run.thinking_level,
                    agent_run.reason,
                    agent_run.result,
                    agent_run.tokens_in,
                    agent_run.tokens_out,
                    agent_run.escalated_to,
                    to_iso(agent_run.started_at),
                    to_iso(agent_run.finished_at),
                ),
            )
        return agent_run

    def list(self, *, incident_id: str | None = None, limit: int = 50) -> list[AgentRun]:
        if incident_id:
            rows = self.db.connection.execute(
                "SELECT * FROM agent_runs WHERE incident_id=? ORDER BY started_at DESC LIMIT ?",
                (incident_id, limit),
            )
        else:
            rows = self.db.connection.execute(
                "SELECT * FROM agent_runs ORDER BY started_at DESC LIMIT ?", (limit,)
            )
        return [self._row_to_agent_run(row) for row in rows]
