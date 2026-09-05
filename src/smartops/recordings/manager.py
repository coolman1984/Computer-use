"""Recording lifecycle: create, start, pause, resume, stop, re-record, and
convert a completed recording into a reviewable automation draft."""
from __future__ import annotations
import json, os
from pathlib import Path
from typing import Any
from ..core.errors import ConcurrencyError, PermanentError
from ..domain.enums import EventType, RecordingStatus, Severity
from ..domain.models import Recording, RecordingStep
from .converter import build_draft
from .worker import PlaywrightRecordingWorker

_ACTIVE = {RecordingStatus.STARTING, RecordingStatus.RECORDING, RecordingStatus.PAUSED, RecordingStatus.STOPPING}

class RecordingManager:
    def __init__(self, services: Any) -> None:
        self.services, self.workers = services, {}
    def _emit(self, event: EventType, record: Recording, message: str, severity: Severity = Severity.INFO) -> None:
        self.services.events.emit(event, severity=severity, message=message, payload={"recording_id": record.id, "status": record.status.value})
    def create(self, name: str, system_key: str, parent: Recording | None = None) -> Recording:
        if not name.strip() or not system_key.strip(): raise PermanentError("Recording name and system are both required")
        record = self.services.recordings.create(name.strip(), system_key.strip(), parent=parent)
        record.artifact_dir = str((self.services.settings.storage.recordings_dir / record.id).resolve())
        self.services.recordings.save(record); self._emit(EventType.RECORDING_CREATED, record, "New recording created")
        return record
    def start(self, recording_id: str) -> Recording:
        record = self._required(recording_id)
        if record.status in {RecordingStatus.RECORDING, RecordingStatus.PAUSED, RecordingStatus.STARTING}: return record
        if record.status not in {RecordingStatus.DRAFT, RecordingStatus.INTERRUPTED, RecordingStatus.FAILED}: raise PermanentError("This recording cannot be started in its current state")
        other = self.services.recordings.active_for_system(record.system_key, record.id)
        if other: raise ConcurrencyError("Another recording is already active for this system")
        record.status, record.error_message, record.started_at = RecordingStatus.STARTING, None, self.services.clock.now(); self.services.recordings.save(record); self._emit(EventType.RECORDING_STARTED, record, "Opening Google Chrome for recording")
        url = self._system_url(record.system_key)
        worker = PlaywrightRecordingWorker(record.id, Path(record.artifact_dir), url, lambda item: self._step(record.id, item), lambda: self._heartbeat(record.id), lambda error: self._finished(record.id, error))
        self.workers[record.id] = worker
        try:
            worker.start()
        except Exception as exc:
            record.status, record.error_message = RecordingStatus.FAILED, type(exc).__name__; self.services.recordings.save(record); self._emit(EventType.RECORDING_FAILED, record, "Could not launch the recorder", Severity.ERROR)
            self.workers.pop(record.id, None)
            return record
        # worker.start() only spawns the capture thread and returns immediately;
        # that thread can already have hit an instant failure (Chrome missing,
        # no desktop session) and called _finished -> FAILED before we get
        # here. Re-read instead of blindly stamping RECORDING over a stale
        # local object, or we'd resurrect a dead recording as "recording".
        current = self._required(record.id)
        if current.status == RecordingStatus.STARTING:
            current.status, current.worker_pid, current.heartbeat_at = RecordingStatus.RECORDING, os.getpid(), self.services.clock.now()
            self.services.recordings.save(current)
        return current
    def pause(self, recording_id: str) -> Recording:
        record=self._required(recording_id)
        if record.status == RecordingStatus.PAUSED: return record
        if record.status != RecordingStatus.RECORDING: raise PermanentError("Pause is only available while recording")
        worker=self.workers.get(record.id)
        if not worker: raise PermanentError("The recorder worker is not connected")
        worker.pause(); record.status=RecordingStatus.PAUSED; self.services.recordings.save(record); self._emit(EventType.RECORDING_PAUSED, record, "Step capture paused"); return record
    def resume(self, recording_id: str) -> Recording:
        record=self._required(recording_id)
        if record.status == RecordingStatus.RECORDING: return record
        if record.status != RecordingStatus.PAUSED: raise PermanentError("Resume is only available after a pause")
        worker=self.workers.get(record.id)
        if not worker: raise PermanentError("The recorder worker is not connected")
        worker.resume(); record.status=RecordingStatus.RECORDING; self.services.recordings.save(record); self._emit(EventType.RECORDING_RESUMED, record, "Step capture resumed"); return record
    def stop(self, recording_id: str) -> Recording:
        record=self._required(recording_id)
        if record.status == RecordingStatus.COMPLETED: return record
        if record.status not in {RecordingStatus.RECORDING, RecordingStatus.PAUSED, RecordingStatus.STOPPING}: raise PermanentError("There is no active recording to stop")
        record.status=RecordingStatus.STOPPING; self.services.recordings.save(record)
        worker=self.workers.get(record.id)
        if worker:
            worker.stop()
        else:
            # No in-process worker to ask for a clean stop (e.g. this manager
            # was restarted while the DB still says active). We cannot know
            # what, if anything, was captured — reporting COMPLETED here
            # would claim a successful recording that may not exist.
            record.status, record.error_message = RecordingStatus.INTERRUPTED, "No recorder worker was connected; record again"
            self.services.recordings.save(record)
            self._emit(EventType.RECORDING_FAILED, record, "No recorder worker was connected", Severity.WARNING)
        return self._required(record.id)
    def rerecord(self, recording_id: str) -> Recording:
        original=self._required(recording_id)
        if original.status in _ACTIVE: raise PermanentError("Stop the current recording before re-recording")
        return self.start(self.create(original.name, original.system_key, original).id)
    def delete(self, recording_id: str) -> Recording:
        record=self._required(recording_id)
        if record.status in _ACTIVE: raise PermanentError("An active recording cannot be deleted")
        record.deleted_at=self.services.clock.now(); self.services.recordings.save(record); self._emit(EventType.RECORDING_DELETED, record, "Recording moved to the trash"); return record
    def restore(self, recording_id: str) -> Recording:
        record=self._required(recording_id)
        record.deleted_at=None; self.services.recordings.save(record); self._emit(EventType.RECORDING_RESTORED, record, "Recording restored"); return record
    def draft(self, recording_id: str) -> Recording:
        record=self._required(recording_id)
        if record.status != RecordingStatus.COMPLETED: raise PermanentError("Complete and review the recording before creating a draft")
        record.automation_draft=build_draft(record.id, record.system_key, self.services.recordings.steps(record.id)); self.services.recordings.save(record); self._emit(EventType.RECORDING_DRAFT_CREATED, record, "Reviewable automation draft created"); return record
    def recover(self, stale_seconds: int = 90) -> int:
        now=self.services.clock.now(); count=0
        for record in self.services.recordings.list(limit=1000):
            if record.status in _ACTIVE and (not record.heartbeat_at or (now-record.heartbeat_at).total_seconds()>stale_seconds):
                record.status=RecordingStatus.INTERRUPTED; record.error_message="The recorder worker stopped responding; you can record again"; self.services.recordings.save(record); self._emit(EventType.RECORDING_FAILED, record, "The recorder worker stopped responding", Severity.WARNING); count+=1
        return count
    def _required(self, recording_id: str) -> Recording:
        record=self.services.recordings.get(recording_id)
        if not record: raise PermanentError("Recording not found")
        return record
    def _system_url(self, key: str) -> str:
        try:
            system=self.services.systems.get(key)
            return system.auth.login_url or (system.reports[0].url if system.reports else "about:blank")
        except Exception: return "about:blank"
    def _heartbeat(self, recording_id: str) -> None:
        record=self.services.recordings.get(recording_id)
        if record and record.status in _ACTIVE: record.heartbeat_at=self.services.clock.now(); self.services.recordings.save(record)
    def _step(self, recording_id: str, data: dict[str, Any]) -> None:
        record=self.services.recordings.get(recording_id)
        if not record or record.status == RecordingStatus.PAUSED: return
        step=RecordingStep(recording_id=recording_id, seq=record.step_count+1, occurred_at=self.services.clock.now(), **data); self.services.recordings.save_step(step)
        record.step_count += 1; record.download_count += int(step.kind=="download"); self.services.recordings.save(record)
        root=Path(record.artifact_dir); root.mkdir(parents=True, exist_ok=True)
        with (root/"steps.jsonl").open("a", encoding="utf-8") as out: out.write(json.dumps(step.to_dict(), ensure_ascii=False)+"\n")
    def _finished(self, recording_id: str, error: str | None) -> None:
        record=self.services.recordings.get(recording_id)
        if not record: return
        record.status=RecordingStatus.FAILED if error else RecordingStatus.COMPLETED; record.error_message=error; record.finished_at=self.services.clock.now(); record.worker_pid=None; self.services.recordings.save(record)
        Path(record.artifact_dir).mkdir(parents=True, exist_ok=True)
        (Path(record.artifact_dir)/"manifest.json").write_text(json.dumps(record.to_dict(), ensure_ascii=False),encoding="utf-8")
        self._emit(EventType.RECORDING_FAILED if error else EventType.RECORDING_STOPPED, record, "Recording failed" if error else "Recording completed", Severity.ERROR if error else Severity.INFO); self.workers.pop(recording_id, None)
