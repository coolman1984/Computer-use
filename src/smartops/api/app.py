"""The local HTTP interface. The web UI and chat are built on these endpoints.

Two rules shape this file:

* **The journey is enforced here, not in the browser.** A stage that depends on
  an earlier one refuses with a 409 that names the blocking stage and the page
  that fixes it. Hiding a button in the UI is a suggestion; this is the rule.
* **Nothing long-running blocks a request.** A collection or an automation run
  is queued and executed by the background worker, so the page stays responsive
  and the user watches live events instead of a frozen tab.
"""

from __future__ import annotations

import contextlib
import os
from pathlib import Path
from typing import Any

from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, SecretStr

from ..adapters.notify.local import LocalLogNotifier
from ..checks import check_system
from ..core.errors import SmartOpsError
from ..domain.enums import (
    EventType,
    IncidentStatus,
    ProcessStatus,
    RecordingStatus,
    RunStatus,
    Severity,
    TriggerType,
)
from ..guidance import explain, explain_run
from ..journey import (
    StageBlocked,
    build_journey,
    require_connection_checked,
    require_signed_in,
)
from ..recordings.artifacts import CONTENT_TYPES, preview_path
from ..recordings.converter import describe_plan, review_plan
from ..sessions import session_age_hours, session_exists
from ..services import Services
from ..worker import Worker
from .ws import create_ws_router

# The operations center (static web UI) lives in web/ at the repository root, next to src/.
WEB_DIR = Path(__file__).resolve().parents[3] / "web"

_services: Services | None = None


def get_services() -> Services:
    global _services
    if _services is None:
        _services = Services()
    return _services


class CreateRunRequest(BaseModel):
    workflow: str = Field(..., description="Workflow key")
    params: dict[str, Any] = Field(default_factory=dict)
    start: bool = Field(default=True, description="Start executing immediately")


class CreateRecordingRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    system_key: str = Field(min_length=1, max_length=120)


class DraftRequest(BaseModel):
    report_key: str = Field(default="", max_length=120)


class CredentialRequest(BaseModel):
    username: str = Field(min_length=1, max_length=256)
    password: SecretStr = Field(min_length=1, max_length=1024)


class SystemRequest(BaseModel):
    """A system definition as the web form submits it.

    Loosely typed on purpose: it is handed straight to the same parser the YAML
    loader uses, so there is exactly one place that decides what a valid system
    is and the UI can never accept something the loader would reject.
    """

    key: str = Field(min_length=1, max_length=120, pattern=r"^[A-Za-z0-9_.-]+$")
    name: str = Field(default="", max_length=200)
    auth: dict[str, Any] = Field(default_factory=dict)
    reports: list[dict[str, Any]] = Field(default_factory=list)


class ProcessFromRecordingRequest(BaseModel):
    recording_id: str = Field(min_length=1, max_length=120)
    name: str = Field(default="", max_length=200)
    report_key: str = Field(default="", max_length=120)
    validation_rules: dict[str, Any] | None = None


class ProcessUpdateRequest(BaseModel):
    name: str | None = Field(default=None, max_length=200)
    validation_rules: dict[str, Any] | None = None


class ScheduleRequest(BaseModel):
    daily_at: str = Field(default="", max_length=5)
    every_seconds: float | None = None
    enabled: bool = True


def create_app(services: Services | None = None) -> FastAPI:
    def provide() -> Services:
        return services or get_services()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        """The server, the background worker, and the scheduler start as one system.

        Without this, a schedule set in the app would simply never fire unless
        someone also ran `python -m smartops work` in a second terminal — exactly
        the kind of hidden manual step this platform exists to remove. Tests and
        one-off tooling opt out with SMARTOPS_DISABLE_WORKER=1.
        """
        app.state.worker = None
        if os.getenv("SMARTOPS_DISABLE_WORKER") != "1":
            svc = provide()
            worker = Worker(svc, scheduler=svc.scheduler)
            worker.start()
            app.state.worker = worker
        try:
            yield
        finally:
            worker = getattr(app.state, "worker", None)
            if worker is not None:
                worker.stop()
                worker.join(timeout=10)

    app = FastAPI(title="SmartOps", version="0.2.0", lifespan=lifespan)

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        if request.url.path.startswith("/api/credentials"):
            response.headers["Cache-Control"] = "no-store"
        return response

    app.include_router(create_ws_router(provide))

    if WEB_DIR.is_dir():
        # Operations center: /app/index.html and its pages.
        app.mount("/app", StaticFiles(directory=WEB_DIR, html=True), name="web")

    # ---------- shared error translation ----------

    def _fail(exc: SmartOpsError, status: int = 400) -> HTTPException:
        """Every refused action leaves here with a plain-language explanation attached."""
        body = exc.to_dict()
        body["guidance"] = explain(
            exc.error_class.value, message=exc.message, details=exc.details
        )
        return HTTPException(status_code=status, detail=body)

    def _blocked(exc: StageBlocked) -> HTTPException:
        # 409, not 400: the request is well-formed, the journey is simply not
        # there yet. The UI uses this to render a "do this first" prompt rather
        # than a validation error.
        return _fail(exc, status=409)

    # ---------- health and shape ----------

    @app.get("/health")
    def health(svc: Services = Depends(provide)) -> dict[str, Any]:
        worker = getattr(app.state, "worker", None)
        return {
            "status": "ok",
            "service": "smartops",
            "recorder": svc.recording_recovery.health(),
            # A user reading "automatic runs: on" is how they know the platform
            # will still work when they are not looking at it.
            "automatic_runs": bool(worker is not None and worker.is_running()),
        }

    @app.get("/")
    def root(svc: Services = Depends(provide)) -> dict[str, Any]:
        return {
            "name": svc.settings.app.name,
            "environment": svc.settings.app.environment,
            "status": "core-ready",
            "workflows": [w.key for w in svc.workflows.list()],
            "steps": svc.step_registry.keys(),
        }

    @app.get("/api/journey")
    def journey(svc: Services = Depends(provide)) -> dict[str, Any]:
        """The one ordered path, with each stage's real status and next action."""
        return build_journey(svc).to_dict()

    @app.get("/api/workflows")
    def list_workflows(svc: Services = Depends(provide)) -> dict[str, Any]:
        return {"items": [w.to_dict() for w in svc.workflows.list()]}

    # ---------- runs ----------

    @app.post("/api/runs", status_code=201)
    def create_run(body: CreateRunRequest, svc: Services = Depends(provide)) -> dict[str, Any]:
        try:
            run = svc.runner.create_run(
                body.workflow, params=body.params, trigger=TriggerType.MANUAL
            )
        except SmartOpsError as exc:
            raise _fail(exc) from exc
        if body.start:
            run = svc.runner.drive(run.id)
        return run.to_dict()

    @app.get("/api/runs")
    def list_runs(
        status: RunStatus | None = None,
        workflow: str | None = None,
        limit: int = Query(default=50, le=200),
        svc: Services = Depends(provide),
    ) -> dict[str, Any]:
        runs = svc.runs.list(status=status, workflow_key=workflow, limit=limit)
        return {"items": [r.to_dict() for r in runs]}

    @app.get("/api/runs/{run_id}")
    def get_run(run_id: str, svc: Services = Depends(provide)) -> dict[str, Any]:
        run = svc.runs.get(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="Run not found")
        return {
            "run": run.to_dict(),
            "steps": [s.to_dict() for s in svc.steps.list(run_id)],
            "files": [f.to_dict() for f in svc.files.list(run_id=run_id)],
            "guidance": explain_run(run),
        }

    @app.post("/api/runs/{run_id}/start")
    def start_run(run_id: str, svc: Services = Depends(provide)) -> dict[str, Any]:
        if svc.runs.get(run_id) is None:
            raise HTTPException(status_code=404, detail="Run not found")
        run = svc.runner.drive(run_id)
        _settle_owning_process(svc, run)
        return run.to_dict()

    @app.get("/api/runs/{run_id}/events")
    def run_events(
        run_id: str,
        limit: int = Query(default=500, le=2000),
        svc: Services = Depends(provide),
    ) -> dict[str, Any]:
        return {"items": [e.to_dict() for e in svc.events.timeline(run_id, limit=limit)]}

    @app.get("/api/events")
    def recent_events(
        limit: int = Query(default=200, le=2000), svc: Services = Depends(provide)
    ) -> dict[str, Any]:
        return {"items": [e.to_dict() for e in svc.events.recent(limit=limit)]}

    # ---------- incidents and files ----------

    @app.get("/api/incidents")
    def list_incidents(
        status: IncidentStatus | None = None,
        limit: int = Query(default=50, le=200),
        svc: Services = Depends(provide),
    ) -> dict[str, Any]:
        items = []
        for incident in svc.incidents.list(status=status, limit=limit):
            entry = incident.to_dict()
            run = svc.runs.get(incident.run_id) if incident.run_id else None
            # An issue without an explanation is just an alarm. Attach the same
            # what-happened / what-to-do the run page shows.
            entry["guidance"] = explain_run(run) if run else None
            items.append(entry)
        return {"items": items}

    @app.post("/api/incidents/{incident_id}/resolve")
    def resolve_incident(incident_id: str, svc: Services = Depends(provide)) -> dict[str, Any]:
        """Mark an issue handled, so the monitoring stage can go green again."""
        incident = svc.incidents.get(incident_id)
        if incident is None:
            raise HTTPException(status_code=404, detail="Issue not found")
        incident.status = IncidentStatus.RESOLVED
        incident.resolved_at = svc.clock.now()
        return svc.incidents.update(incident).to_dict()

    @app.get("/api/files")
    def list_files(
        run_id: str | None = None,
        limit: int = Query(default=100, le=500),
        svc: Services = Depends(provide),
    ) -> dict[str, Any]:
        items = []
        for artifact in svc.files.list(run_id=run_id, limit=limit):
            entry = artifact.to_dict()
            # Whether the file is still on disk is part of "check the result":
            # a database row for a file someone moved or deleted is not a result.
            entry["exists"] = Path(artifact.path).exists()
            items.append(entry)
        return {"items": items}

    @app.get("/api/files/{file_id}/download")
    def download_file(file_id: str, svc: Services = Depends(provide)) -> Response:
        """Hand the user the actual file, so verifying a result never means opening a folder."""
        matches = [f for f in svc.files.list(limit=1000) if f.id == file_id]
        if not matches:
            raise HTTPException(status_code=404, detail="File not found")
        path = Path(matches[0].path)
        if not path.is_file():
            raise HTTPException(
                status_code=404,
                detail="This file is no longer on disk. Run the automation again to produce a new one.",
            )
        name = matches[0].original_name or path.name
        return Response(
            path.read_bytes(),
            media_type="application/octet-stream",
            headers={"Content-Disposition": f'attachment; filename="{name}"'},
        )

    @app.get("/api/alerts")
    def list_alerts(
        limit: int = Query(default=100, le=1000), svc: Services = Depends(provide)
    ) -> dict[str, Any]:
        notifier = LocalLogNotifier(svc.settings.storage.logs_dir / "alerts.jsonl")
        return {"items": list(reversed(notifier.read_all()))[:limit]}

    # ---------- stage 1: systems ----------

    def _system_entry(svc: Services, system: Any) -> dict[str, Any]:
        needs_session = system.auth.mode in ("session", "unattended")
        check = svc.connection_checks.get(system.key)
        return {
            "key": system.key,
            "name": system.name,
            "auth_mode": system.auth.mode,
            "login_url": system.auth.login_url,
            "logged_in_selector": system.auth.logged_in_selector,
            "login_selector": system.auth.login_selector,
            "session_exists": (
                session_exists(svc.settings.storage.sessions_dir, system.key)
                if needs_session
                else None
            ),
            "session_age_hours": (
                session_age_hours(svc.settings.storage.sessions_dir, system.key)
                if needs_session
                else None
            ),
            "connection_checked": check is not None,
            "connection_check": check,
            "reports": [
                {
                    "key": report.key,
                    "title": report.title,
                    "url": report.url,
                    "download_selector": report.download_selector,
                    "direct_download_url": report.direct_download_url,
                    "wait_selector": report.wait_selector,
                    "period": report.period,
                    "schedule": {
                        "daily_at": report.schedule.daily_at,
                        "every_seconds": report.schedule.every_seconds,
                        "enabled": report.schedule.enabled,
                    },
                }
                for report in system.reports
            ],
        }

    @app.get("/api/systems")
    def list_systems(svc: Services = Depends(provide)) -> dict[str, Any]:
        # The directory is part of the answer: the most common cause of "no
        # systems" is the server reading an unexpected directory, and showing it
        # makes that self-diagnosing instead of a guess.
        return {
            "items": [_system_entry(svc, s) for s in svc.systems.list()],
            "directory": str(svc.settings.storage.systems_dir),
        }

    @app.get("/api/systems/{system_key}")
    def get_system(system_key: str, svc: Services = Depends(provide)) -> dict[str, Any]:
        try:
            return _system_entry(svc, svc.systems.get(system_key))
        except SmartOpsError as exc:
            raise _fail(exc, status=404) from exc

    @app.put("/api/systems/{system_key}", status_code=200)
    def save_system(
        system_key: str, body: SystemRequest, svc: Services = Depends(provide)
    ) -> dict[str, Any]:
        """Create or update a system from the app, with no restart and no YAML editing."""
        if body.key != system_key:
            raise HTTPException(
                status_code=400, detail="The system key in the address and the body must match."
            )
        try:
            system = svc.systems.save(
                {
                    "key": body.key,
                    "name": body.name or body.key,
                    "auth": body.auth,
                    "reports": body.reports,
                }
            )
        except SmartOpsError as exc:
            raise _fail(exc) from exc
        except OSError as exc:
            raise HTTPException(
                status_code=503,
                detail=f"The system could not be written to {svc.settings.storage.systems_dir}: {exc}",
            ) from exc
        # Changing a system's addresses or markers invalidates the old
        # connection test — it proved something about a definition that no
        # longer exists.
        svc.connection_checks.forget(system_key)
        svc.events.emit(
            EventType.SYSTEM_SAVED,
            message=f"System '{system.name}' saved",
            payload={"system": system.key},
        )
        return _system_entry(svc, system)

    @app.delete("/api/systems/{system_key}")
    def delete_system(system_key: str, svc: Services = Depends(provide)) -> dict[str, Any]:
        processes = svc.processes.list(system_key=system_key, limit=1)
        if processes:
            raise HTTPException(
                status_code=409,
                detail=(
                    "This system still has automations built on it. Retire them first, "
                    "so nothing is left pointing at a system that no longer exists."
                ),
            )
        removed = svc.systems.delete(system_key)
        svc.connection_checks.forget(system_key)
        if removed:
            svc.events.emit(
                EventType.SYSTEM_DELETED,
                message=f"System '{system_key}' deleted",
                payload={"system": system_key},
            )
        return {"key": system_key, "deleted": removed}

    # ---------- stage 2: connection test ----------

    @app.post("/api/systems/{system_key}/check")
    def check_connection(system_key: str, svc: Services = Depends(provide)) -> dict[str, Any]:
        """Open the system's page and report, in one sentence, whether it worked."""
        try:
            system = svc.systems.get(system_key)
        except SmartOpsError as exc:
            raise _fail(exc, status=404) from exc
        result = check_system(
            system,
            browser_settings=svc.settings.browser,
            sessions_dir=svc.settings.storage.sessions_dir,
            evidence_dir=Path(svc.settings.storage.incidents_dir) / "checks",
        )
        svc.connection_checks.record(system_key, result, at=svc.clock.now().isoformat())
        svc.events.emit(
            EventType.SYSTEM_CHECK_PASSED if result.ok else EventType.SYSTEM_CHECK_FAILED,
            severity=Severity.INFO if result.ok else Severity.WARNING,
            message=result.summary,
            payload={"system": system_key, "signed_in": result.signed_in},
        )
        return result.to_dict()

    # ---------- stage 3: sign-in ----------

    @app.get("/api/signin")
    def signin_overview(svc: Services = Depends(provide)) -> dict[str, Any]:
        """Every system's sign-in state and the one action that advances it."""
        items = []
        for system in svc.systems.list():
            session = svc.login_manager.status(system.key)
            ref = system.auth.credential_ref or system.key
            stored = False
            username = ""
            if system.auth.mode == "unattended":
                try:
                    credential = svc.credentials.get(ref)
                    stored, username = credential is not None, (
                        credential.username if credential else ""
                    )
                except Exception:
                    stored, username = False, ""
            items.append(
                {
                    "system_key": system.key,
                    "name": system.name,
                    "auth_mode": system.auth.mode,
                    "credential_ref": ref,
                    "credential_stored": stored,
                    "username": username,
                    "session_exists": session_exists(
                        svc.settings.storage.sessions_dir, system.key
                    ),
                    "session_age_hours": session_age_hours(
                        svc.settings.storage.sessions_dir, system.key
                    ),
                    "login_in_progress": session.to_dict() if session else None,
                }
            )
        return {"items": items}

    @app.post("/api/systems/{system_key}/login", status_code=202)
    def start_login(system_key: str, svc: Services = Depends(provide)) -> dict[str, Any]:
        """Open a real browser window for the user to sign in — from the app, not a terminal."""
        try:
            return svc.login_manager.start(system_key).to_dict()
        except SmartOpsError as exc:
            raise _fail(exc) from exc

    @app.get("/api/systems/{system_key}/login")
    def login_status(system_key: str, svc: Services = Depends(provide)) -> dict[str, Any]:
        session = svc.login_manager.status(system_key)
        if session is None:
            return {"system_key": system_key, "status": "idle", "active": False, "saved": False}
        return session.to_dict()

    @app.post("/api/systems/{system_key}/login/finish")
    def finish_login(system_key: str, svc: Services = Depends(provide)) -> dict[str, Any]:
        try:
            return svc.login_manager.finish(system_key).to_dict()
        except SmartOpsError as exc:
            raise _fail(exc) from exc

    @app.post("/api/systems/{system_key}/login/cancel")
    def cancel_login(system_key: str, svc: Services = Depends(provide)) -> dict[str, Any]:
        try:
            return svc.login_manager.cancel(system_key).to_dict()
        except SmartOpsError as exc:
            raise _fail(exc) from exc

    # ---------- stage 3b: stored credentials (unattended systems) ----------

    @app.get("/api/credentials")
    def list_credentials(svc: Services = Depends(provide)) -> dict[str, Any]:
        items = []
        for system in svc.systems.list():
            if system.auth.mode != "unattended":
                continue
            ref = system.auth.credential_ref or system.key
            try:
                credential = svc.credentials.get(ref)
                stored = credential is not None
                username = credential.username if credential else ""
            except Exception:
                stored, username = False, ""
            items.append(
                {
                    "system_key": system.key,
                    "credential_ref": ref,
                    "stored": stored,
                    "username": username,
                }
            )
        return {"items": items}

    def _credential_system(system_key: str, svc: Services):
        try:
            return svc.systems.get(system_key)
        except SmartOpsError as exc:
            raise _fail(exc, status=404) from exc

    def _require_local_ui(request: Request) -> None:
        # Browser mutations must originate from the shipped UI. Missing header is
        # intentionally rejected to reduce accidental script/CSRF use.
        if request.headers.get("X-SmartOps-Request") != "web":
            raise HTTPException(
                status_code=403, detail="Credential changes must come from the SmartOps UI."
            )

    @app.put("/api/credentials/{system_key}")
    def save_credential(
        system_key: str,
        body: CredentialRequest,
        request: Request,
        svc: Services = Depends(provide),
    ) -> dict[str, Any]:
        _require_local_ui(request)
        system = _credential_system(system_key, svc)
        if system.auth.mode != "unattended":
            raise HTTPException(
                status_code=400, detail="System authentication mode must be unattended."
            )
        ref = system.auth.credential_ref or system.key
        try:
            svc.credentials.put(ref, body.username, body.password.get_secret_value())
        except (ValueError, OSError, RuntimeError) as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return {
            "system_key": system_key,
            "credential_ref": ref,
            "stored": True,
            "username": body.username,
        }

    @app.delete("/api/credentials/{system_key}")
    def delete_credential(
        system_key: str, request: Request, svc: Services = Depends(provide)
    ) -> dict[str, Any]:
        _require_local_ui(request)
        system = _credential_system(system_key, svc)
        ref = system.auth.credential_ref or system.key
        try:
            deleted = svc.credentials.delete(ref)
        except (ValueError, OSError, RuntimeError) as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return {"system_key": system_key, "credential_ref": ref, "stored": False, "deleted": deleted}

    # ---------- stage 4: recordings ----------

    @app.get("/api/recordings")
    def list_recordings(
        include_deleted: bool = False,
        status: RecordingStatus | None = None,
        limit: int = Query(default=100, le=500),
        svc: Services = Depends(provide),
    ) -> dict[str, Any]:
        return {
            "items": [
                r.to_dict()
                for r in svc.recordings.list(
                    include_deleted=include_deleted, status=status, limit=limit
                )
            ]
        }

    @app.post("/api/recordings", status_code=201)
    def create_recording(
        body: CreateRecordingRequest, svc: Services = Depends(provide)
    ) -> dict[str, Any]:
        # Recording is the one stage that costs the user real time, so both
        # earlier stages are enforced before a window is ever opened.
        try:
            require_connection_checked(svc, body.system_key)
            require_signed_in(svc, body.system_key)
        except StageBlocked as exc:
            raise _blocked(exc) from exc
        except SmartOpsError as exc:
            raise _fail(exc, status=404) from exc
        try:
            return svc.recording_manager.create(body.name, body.system_key).to_dict()
        except SmartOpsError as exc:
            raise _fail(exc) from exc

    @app.get("/api/recordings/{recording_id}")
    def recording_detail(recording_id: str, svc: Services = Depends(provide)) -> dict[str, Any]:
        record = svc.recordings.get(recording_id)
        if not record:
            raise HTTPException(status_code=404, detail="Recording not found")
        plan = record.automation_draft or {}
        return {
            "recording": record.to_dict(),
            "steps": [s.to_dict() for s in svc.recordings.steps(recording_id)],
            # The plan in plain sentences, plus the verdict on whether it is fit
            # to become an automation. This is stage 5 (review) in one payload.
            "plan_summary": describe_plan(plan) if plan.get("actions") else [],
            "review": plan.get("review") or (review_plan(plan) if plan else None),
            "processes": [
                p.to_dict()
                for p in svc.processes.list(limit=200)
                if p.recording_id == recording_id
            ],
        }

    def _recording_control(action: str, recording_id: str, svc: Services) -> dict[str, Any]:
        try:
            return getattr(svc.recording_manager, action)(recording_id).to_dict()
        except SmartOpsError as exc:
            raise _fail(exc) from exc

    @app.post("/api/recordings/{recording_id}/start")
    def start_recording(recording_id: str, svc: Services = Depends(provide)) -> dict[str, Any]:
        return _recording_control("start", recording_id, svc)

    @app.post("/api/recordings/{recording_id}/pause")
    def pause_recording(recording_id: str, svc: Services = Depends(provide)) -> dict[str, Any]:
        return _recording_control("pause", recording_id, svc)

    @app.post("/api/recordings/{recording_id}/resume")
    def resume_recording(recording_id: str, svc: Services = Depends(provide)) -> dict[str, Any]:
        return _recording_control("resume", recording_id, svc)

    @app.post("/api/recordings/{recording_id}/stop")
    def stop_recording(recording_id: str, svc: Services = Depends(provide)) -> dict[str, Any]:
        return _recording_control("stop", recording_id, svc)

    @app.post("/api/recordings/{recording_id}/rerecord")
    def rerecord(recording_id: str, svc: Services = Depends(provide)) -> dict[str, Any]:
        return _recording_control("rerecord", recording_id, svc)

    @app.post("/api/recordings/{recording_id}/delete")
    def delete_recording(recording_id: str, svc: Services = Depends(provide)) -> dict[str, Any]:
        return _recording_control("delete", recording_id, svc)

    @app.post("/api/recordings/{recording_id}/restore")
    def restore_recording(recording_id: str, svc: Services = Depends(provide)) -> dict[str, Any]:
        return _recording_control("restore", recording_id, svc)

    # ---------- stage 5: review ----------

    @app.post("/api/recordings/{recording_id}/draft")
    def recording_draft(
        recording_id: str, body: DraftRequest | None = None, svc: Services = Depends(provide)
    ) -> dict[str, Any]:
        """Build the executable plan from the captured steps, and review it."""
        try:
            record = svc.recording_manager.draft(
                recording_id, (body.report_key if body else "") or ""
            )
        except SmartOpsError as exc:
            raise _fail(exc) from exc
        plan = record.automation_draft or {}
        return {
            "recording": record.to_dict(),
            "plan_summary": describe_plan(plan),
            "review": plan.get("review"),
        }

    @app.get("/api/recordings/{recording_id}/artifacts/{name:path}")
    def recording_artifact(
        recording_id: str, name: str, svc: Services = Depends(provide)
    ) -> Response:
        record = svc.recordings.get(recording_id)
        # Soft-deleted recordings are in the trash, not gone — but their private
        # artifacts stop being servable until the recording is restored.
        if not record or record.deleted_at:
            raise HTTPException(status_code=404, detail="Recording not found")
        path = preview_path(Path(record.artifact_dir), name)
        if not path:
            raise HTTPException(status_code=404, detail="This file is not available for preview")
        return Response(path.read_bytes(), media_type=CONTENT_TYPES[path.suffix.lower()])

    # ---------- stages 6-10: automations ----------

    @app.get("/api/processes")
    def list_processes(
        system_key: str | None = None,
        status: ProcessStatus | None = None,
        limit: int = Query(default=100, le=500),
        svc: Services = Depends(provide),
    ) -> dict[str, Any]:
        return {
            "items": [
                p.to_dict()
                for p in svc.processes.list(system_key=system_key, status=status, limit=limit)
            ]
        }

    @app.post("/api/processes", status_code=201)
    def create_process(
        body: ProcessFromRecordingRequest, svc: Services = Depends(provide)
    ) -> dict[str, Any]:
        """Turn a reviewed recording into an automation that can be tested."""
        try:
            process = svc.process_manager.create_from_recording(
                body.recording_id,
                name=body.name,
                report_key=body.report_key,
                validation_rules=body.validation_rules,
            )
        except SmartOpsError as exc:
            raise _fail(exc) from exc
        return process.to_dict()

    @app.get("/api/processes/{process_id}")
    def process_detail(process_id: str, svc: Services = Depends(provide)) -> dict[str, Any]:
        process = svc.processes.get(process_id)
        if process is None:
            raise HTTPException(status_code=404, detail="Automation not found")
        test_run = svc.runs.get(process.last_test_run_id) if process.last_test_run_id else None
        last_run = svc.runs.get(process.last_run_id) if process.last_run_id else None
        return {
            "process": process.to_dict(),
            "plan_summary": describe_plan(process.plan),
            "test_run": test_run.to_dict() if test_run else None,
            "last_run": last_run.to_dict() if last_run else None,
            "guidance": explain_run(test_run) if test_run else None,
            "files": [
                f.to_dict()
                for f in svc.files.list(limit=200)
                if f.system == process.system_key and f.report == process.report_key
            ][:20],
        }

    @app.patch("/api/processes/{process_id}")
    def update_process(
        process_id: str, body: ProcessUpdateRequest, svc: Services = Depends(provide)
    ) -> dict[str, Any]:
        try:
            return svc.process_manager.update(
                process_id, name=body.name, validation_rules=body.validation_rules
            ).to_dict()
        except SmartOpsError as exc:
            raise _fail(exc) from exc

    @app.post("/api/processes/{process_id}/test", status_code=201)
    def test_process(process_id: str, svc: Services = Depends(provide)) -> dict[str, Any]:
        """Stage 6: prove it works, for real, before anything may be approved."""
        try:
            process = svc.process_manager.require(process_id)
            require_signed_in(svc, process.system_key)
            process, run = svc.process_manager.test(process_id)
        except StageBlocked as exc:
            raise _blocked(exc) from exc
        except SmartOpsError as exc:
            raise _fail(exc) from exc
        return {
            "process": process.to_dict(),
            "run": run.to_dict(),
            "guidance": explain_run(run),
        }

    @app.post("/api/processes/{process_id}/approve")
    def approve_process(process_id: str, svc: Services = Depends(provide)) -> dict[str, Any]:
        """Stage 7: the explicit human decision. Refused unless a test passed."""
        try:
            return svc.process_manager.approve(process_id).to_dict()
        except SmartOpsError as exc:
            raise _fail(exc) from exc

    @app.post("/api/processes/{process_id}/run", status_code=201)
    def run_process(process_id: str, svc: Services = Depends(provide)) -> dict[str, Any]:
        """Stage 8: queue an approved automation; the worker runs it in the background."""
        try:
            process = svc.process_manager.require(process_id)
            require_signed_in(svc, process.system_key)
            run = svc.process_manager.run(process_id)
        except StageBlocked as exc:
            raise _blocked(exc) from exc
        except SmartOpsError as exc:
            raise _fail(exc) from exc
        return run.to_dict()

    @app.put("/api/processes/{process_id}/schedule")
    def schedule_process(
        process_id: str, body: ScheduleRequest, svc: Services = Depends(provide)
    ) -> dict[str, Any]:
        """Stage 10: only ever available for something already approved."""
        try:
            return svc.process_manager.set_schedule(
                process_id,
                daily_at=body.daily_at,
                every_seconds=body.every_seconds,
                enabled=body.enabled,
            ).to_dict()
        except SmartOpsError as exc:
            raise _fail(exc) from exc

    @app.post("/api/processes/{process_id}/retire")
    def retire_process(process_id: str, svc: Services = Depends(provide)) -> dict[str, Any]:
        try:
            return svc.process_manager.retire(process_id).to_dict()
        except SmartOpsError as exc:
            raise _fail(exc) from exc

    # ---------- YAML-defined report collection (unchanged path, now queued) ----------

    @app.post("/api/systems/{system_key}/{report_key}/collect", status_code=201)
    def collect_now(
        system_key: str, report_key: str, svc: Services = Depends(provide)
    ) -> dict[str, Any]:
        try:
            require_signed_in(svc, system_key)
            params = svc.systems.run_params(system_key, report_key)
        except StageBlocked as exc:
            raise _blocked(exc) from exc
        except SmartOpsError as exc:
            raise _fail(exc, status=404) from exc
        run = svc.runner.create_run("collect.report", params=params, trigger=TriggerType.MANUAL)
        # Queued, not driven inline: a real download takes minutes and must not
        # hold an HTTP request open. The worker picks it up on its next poll and
        # the page follows it live over the event stream.
        if getattr(app.state, "worker", None) is None:
            run = svc.runner.drive(run.id)
        return run.to_dict()

    return app


def _settle_owning_process(svc: Services, run: Any) -> None:
    """Keep an automation's status in step with a run driven from the API."""
    process_id = (run.params or {}).get("process_id")
    if not process_id:
        return
    with contextlib.suppress(SmartOpsError):
        svc.process_manager.settle_test(process_id, run.id)
