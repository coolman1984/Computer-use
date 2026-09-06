"""Recording lifecycle: create, start, pause, resume, stop, re-record, and
convert a completed recording into a reviewable automation draft."""
from __future__ import annotations
import json, os
from pathlib import Path
from typing import Any
from ..core.errors import ConcurrencyError, PermanentError
from ..domain.enums import EventType, RecordingStatus, Severity
from ..domain.models import Recording, RecordingStep
from ..sessions import session_path
from .converter import build_plan, review_plan

_PROOF_TYPES = {
    "selector_visible", "selector_hidden", "value_equals", "value_not_empty",
    "checked_is", "url_changed", "new_page", "page_available", "download_started",
}
_NO_ELEMENT_ACTIONS = {"navigate", "switch_page", "switch_frame", "wait_for"}
from .worker import PlaywrightRecordingWorker

_ACTIVE = {RecordingStatus.STARTING, RecordingStatus.RECORDING, RecordingStatus.PAUSED, RecordingStatus.STOPPING}

def _default_report_key(name: str) -> str:
    """A stable, file-system-safe report key derived from the recording's name.

    A recorded automation still needs a report key: it is what names the folder
    its files land in and what ties every run of it together in history.
    """
    parts = ["".join(c for c in word if c.isalnum()) for word in name.strip().lower().split()]
    cleaned = "_".join(part for part in parts if part)
    return cleaned or "recorded_report"

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
        worker = PlaywrightRecordingWorker(record.id, Path(record.artifact_dir), url, lambda item: self._step(record.id, item), lambda: self._heartbeat(record.id), lambda error: self._finished(record.id, error), self.services.settings.browser.executable_path, session_path(self.services.settings.storage.sessions_dir, record.system_key), self.services.settings.browser.record_headless)
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
    def draft(self, recording_id: str, report_key: str = "") -> Recording:
        """Build the executable replay plan for a completed recording.

        The plan is stored on the recording as the reviewable artifact; turning
        it into something runnable and schedulable is ProcessManager's job. The
        review verdict is stored alongside it so the UI can say plainly whether
        this recording is fit to become an automation.
        """
        record=self._required(recording_id)
        if record.status != RecordingStatus.COMPLETED: raise PermanentError("Complete and review the recording before creating a draft")
        # Where the recording actually began, not the system's sign-in page.
        # Handing the login URL to the plan made every replay start somewhere the
        # recorded steps do not exist, and the first step failed as "the element
        # is no longer on the page" — a change to the site that never happened.
        # build_plan reads the first captured page URL; the system URL is only
        # the fallback for a recording that never reached a real page.
        plan=build_plan(recording_id=record.id, system_key=record.system_key,
                        report_key=report_key or _default_report_key(record.name),
                        steps=self.services.recordings.steps(record.id),
                        start_url="") or {}
        if not plan.get("start_url"):
            plan["start_url"] = self._system_url(record.system_key)
        record.automation_draft={**plan, "review": review_plan(plan)}
        self.services.recordings.save(record); self._emit(EventType.RECORDING_DRAFT_CREATED, record, "Reviewable automation draft created"); return record

    def update_draft_action(
        self, recording_id: str, seq: int, changes: dict[str, Any]
    ) -> tuple[Recording, dict[str, Any]]:
        """Apply the small, safe edits offered by the review screen.

        The action type and its page/frame are facts captured from the human
        session, so this endpoint never invents or relocates them. A reviewer can
        reorder real locator candidates, correct a non-secret input, choose
        observable success evidence, and make retries stricter. Secret values
        and an escalation from unsafe to repeatable are rejected in the backend.
        """
        record = self._required(recording_id)
        if record.status is not RecordingStatus.COMPLETED:
            raise PermanentError("Finish the recording before editing its review plan.")
        plan = dict(record.automation_draft or {})
        actions = [dict(item) for item in (plan.get("actions") or [])]
        match = next((item for item in actions if int(item.get("seq", 0)) == seq), None)
        if match is None:
            raise PermanentError("The recorded step was not found. Build the plan again.")

        allowed = {"locator_candidates", "inputs", "success", "wait_timeout_seconds", "retry"}
        unknown = set(changes) - allowed
        if unknown:
            raise PermanentError(f"These review fields cannot be changed: {', '.join(sorted(unknown))}")
        self._edit_locator(match, changes)
        self._edit_inputs(match, changes)
        self._edit_success(match, changes)
        self._edit_wait(match, changes)
        self._edit_retry(match, changes)

        plan["actions"] = actions
        plan["review"] = review_plan(plan)
        record.automation_draft = plan
        self.services.recordings.save(record)
        self._emit(EventType.RECORDING_DRAFT_CREATED, record, f"Recorded step {seq} updated during review")
        return record, match

    @staticmethod
    def _edit_locator(action: dict[str, Any], changes: dict[str, Any]) -> None:
        if "locator_candidates" not in changes:
            return
        values = changes["locator_candidates"]
        if not isinstance(values, list) or any(not isinstance(v, str) for v in values):
            raise PermanentError("Element locators must be a list of text selectors.")
        cleaned: list[str] = []
        for value in values:
            value = value.strip()
            if not value or value == "[redacted]" or len(value) > 500:
                raise PermanentError("Each element locator must be a real, non-redacted selector.")
            if value not in cleaned:
                cleaned.append(value)
        if not cleaned and (action.get("action") or "click") not in _NO_ELEMENT_ACTIONS:
            raise PermanentError("This step needs at least one real way to find its element.")
        action["locator"] = {
            "strategy": "css",
            "value": cleaned[0] if cleaned else "",
            "fallbacks": cleaned[1:],
        }
        action["selector"] = action["locator"]["value"]
        if cleaned:
            action["layer"] = "dom"
            action["confidence"] = "high"

    @staticmethod
    def _edit_inputs(action: dict[str, Any], changes: dict[str, Any]) -> None:
        if "inputs" not in changes:
            return
        current = action.get("inputs") or {}
        if current.get("secret_ref"):
            raise PermanentError(
                "Secret inputs are never editable or stored in a review plan. Update the saved credential instead."
            )
        supplied = changes["inputs"]
        if not isinstance(supplied, dict):
            raise PermanentError("Step inputs must be a structured value.")
        kind = action.get("action") or "click"
        allowed_by_kind = {
            "fill": {"value"}, "select": {"value"}, "check": {"checked"},
            "press": {"key"}, "navigate": {"url"}, "wait_for": {"seconds"},
            "click": set(), "switch_page": set(), "switch_frame": set(),
        }
        allowed = allowed_by_kind.get(kind, set())
        if set(supplied) - allowed:
            raise PermanentError("That input does not belong to this kind of step.")
        lowered = " ".join(supplied).lower()
        if any(word in lowered for word in ("password", "passwd", "token", "secret", "otp", "cookie")):
            raise PermanentError("Secrets cannot be stored inside an automation plan.")
        if "seconds" in supplied:
            seconds = float(supplied["seconds"])
            if seconds < 0 or seconds > 300:
                raise PermanentError("A wait must be between 0 and 300 seconds.")
            supplied = {"seconds": seconds}
        for value in supplied.values():
            if isinstance(value, str) and len(value) > 2000:
                raise PermanentError("A reviewed input is too long to store safely.")
        action["inputs"] = dict(supplied)

    @staticmethod
    def _edit_success(action: dict[str, Any], changes: dict[str, Any]) -> None:
        if "success" not in changes:
            return
        proof = changes["success"]
        if not isinstance(proof, dict) or proof.get("type") not in _PROOF_TYPES:
            raise PermanentError("Choose a supported, observable proof of success.")
        kind = proof["type"]
        if kind in {"selector_visible", "selector_hidden", "value_equals", "url_changed"}:
            value = proof.get("value")
            if not isinstance(value, str) or not value.strip() or len(value) > 1000:
                raise PermanentError("This proof needs a real expected value or selector.")
        action_kind = action.get("action") or "click"
        inputs = action.get("inputs") or {}
        if inputs.get("secret_ref") and kind != "value_not_empty":
            raise PermanentError("A secret field can only be checked as filled; its value is never stored.")
        if action_kind in {"fill", "select"} and not inputs.get("secret_ref"):
            if kind != "value_equals" or proof.get("value") != inputs.get("value"):
                raise PermanentError("A field must be proved by checking the same value entered into it.")
        if action_kind == "check":
            if kind != "checked_is" or bool(proof.get("value")) != bool(inputs.get("checked")):
                raise PermanentError("A checkbox must be proved in the state selected for it.")
        action["success"] = dict(proof)

    @staticmethod
    def _edit_wait(action: dict[str, Any], changes: dict[str, Any]) -> None:
        if "wait_timeout_seconds" not in changes:
            return
        seconds = float(changes["wait_timeout_seconds"])
        if seconds < 1 or seconds > 300:
            raise PermanentError("The success wait must be between 1 and 300 seconds.")
        action["wait_timeout_seconds"] = seconds

    @staticmethod
    def _edit_retry(action: dict[str, Any], changes: dict[str, Any]) -> None:
        if "retry" not in changes:
            return
        supplied = changes["retry"]
        if not isinstance(supplied, dict) or set(supplied) - {"max_attempts", "safe_to_repeat"}:
            raise PermanentError("Retry settings are not valid.")
        old_safe = bool((action.get("retry") or {}).get("safe_to_repeat", False))
        new_safe = bool(supplied.get("safe_to_repeat", old_safe))
        if new_safe and not old_safe:
            raise PermanentError(
                "A step recorded as unsafe cannot be made repeatable by editing. Record it again if that fact is wrong."
            )
        attempts = int(supplied.get("max_attempts", 1))
        if attempts < 1 or attempts > 5:
            raise PermanentError("Retry attempts must be between 1 and 5.")
        if not new_safe and attempts != 1:
            raise PermanentError("An unsafe step can have only one attempt.")
        action["retry"] = {"max_attempts": attempts, "safe_to_repeat": new_safe}
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
        self._resolve_secret_ref(record, data)
        step=RecordingStep(recording_id=recording_id, seq=record.step_count+1, occurred_at=self.services.clock.now(), **data); self.services.recordings.save_step(step)
        record.step_count += 1; record.download_count += int(step.kind=="download"); self.services.recordings.save(record)
        root=Path(record.artifact_dir); root.mkdir(parents=True, exist_ok=True)
        with (root/"steps.jsonl").open("a", encoding="utf-8") as out: out.write(json.dumps(step.to_dict(), ensure_ascii=False)+"\n")
    @staticmethod
    def _resolve_secret_ref(record: Recording, data: dict[str, Any]) -> None:
        """Name the credential a sensitive field must be filled from at run time.

        The recorder sees a password box and records that a secret goes here,
        never what it was. It has no business knowing how credentials are named,
        so the system this recording belongs to is attached here — that key is
        what the credential store is asked for during the run.
        """
        inputs = data.get("inputs")
        if isinstance(inputs, dict) and inputs.get("secret_field") and not inputs.get("secret_ref"):
            inputs["secret_ref"] = record.system_key

    def _finished(self, recording_id: str, error: str | None) -> None:
        record=self.services.recordings.get(recording_id)
        if not record: return
        record.status=RecordingStatus.FAILED if error else RecordingStatus.COMPLETED; record.error_message=error; record.finished_at=self.services.clock.now(); record.worker_pid=None; self.services.recordings.save(record)
        Path(record.artifact_dir).mkdir(parents=True, exist_ok=True)
        (Path(record.artifact_dir)/"manifest.json").write_text(json.dumps(record.to_dict(), ensure_ascii=False),encoding="utf-8")
        self._emit(EventType.RECORDING_FAILED if error else EventType.RECORDING_STOPPED, record, "Recording failed" if error else "Recording completed", Severity.ERROR if error else Severity.INFO); self.workers.pop(recording_id, None)
