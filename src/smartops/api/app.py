"""واجهة HTTP المحلية. الواجهة الرسومية والشات يبنيان فوق هذه النقاط."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, SecretStr

from ..adapters.notify.local import LocalLogNotifier
from ..core.errors import SmartOpsError
from ..domain.enums import IncidentStatus, RunStatus, TriggerType, RecordingStatus
from ..recordings.artifacts import preview_path
from ..sessions import session_age_hours, session_exists
from ..services import Services
from .ws import create_ws_router

# غرفة القيادة (واجهة الويب الثابتة) تعيش في web/ بجذر المستودع، بجانب src/.
WEB_DIR = Path(__file__).resolve().parents[3] / "web"

_services: Services | None = None


def get_services() -> Services:
    global _services
    if _services is None:
        _services = Services()
    return _services


class CreateRunRequest(BaseModel):
    workflow: str = Field(..., description="مفتاح سير العمل")
    params: dict[str, Any] = Field(default_factory=dict)
    start: bool = Field(default=True, description="ابدأ التنفيذ فورًا")


class CreateRecordingRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    system_key: str = Field(min_length=1, max_length=120)


class CredentialRequest(BaseModel):
    username: str = Field(min_length=1, max_length=256)
    password: SecretStr = Field(min_length=1, max_length=1024)


def create_app(services: Services | None = None) -> FastAPI:
    app = FastAPI(title="SmartOps", version="0.1.0")

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        if request.url.path.startswith("/api/credentials"):
            response.headers["Cache-Control"] = "no-store"
        return response

    def provide() -> Services:
        return services or get_services()

    app.include_router(create_ws_router(provide))

    if WEB_DIR.is_dir():
        # غرفة القيادة: /app/index.html ولوحاتها. لا تأثير على أي نقطة API قائمة.
        app.mount("/app", StaticFiles(directory=WEB_DIR, html=True), name="web")

    @app.get("/health")
    def health(svc: Services = Depends(provide)) -> dict[str, Any]:
        return {"status": "ok", "service": "smartops", "recorder": svc.recording_recovery.health()}

    @app.get("/")
    def root(svc: Services = Depends(provide)) -> dict[str, Any]:
        return {
            "name": svc.settings.app.name,
            "environment": svc.settings.app.environment,
            "status": "core-ready",
            "workflows": [w.key for w in svc.workflows.list()],
            "steps": svc.step_registry.keys(),
        }

    @app.get("/api/workflows")
    def list_workflows(svc: Services = Depends(provide)) -> dict[str, Any]:
        return {"items": [w.to_dict() for w in svc.workflows.list()]}

    @app.post("/api/runs", status_code=201)
    def create_run(body: CreateRunRequest, svc: Services = Depends(provide)) -> dict[str, Any]:
        try:
            run = svc.runner.create_run(
                body.workflow, params=body.params, trigger=TriggerType.MANUAL
            )
        except SmartOpsError as exc:
            raise HTTPException(status_code=400, detail=exc.to_dict()) from exc
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
            raise HTTPException(status_code=404, detail="تشغيل غير موجود")
        return {"run": run.to_dict(), "steps": [s.to_dict() for s in svc.steps.list(run_id)]}

    @app.post("/api/runs/{run_id}/start")
    def start_run(run_id: str, svc: Services = Depends(provide)) -> dict[str, Any]:
        if svc.runs.get(run_id) is None:
            raise HTTPException(status_code=404, detail="تشغيل غير موجود")
        return svc.runner.drive(run_id).to_dict()

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

    @app.get("/api/incidents")
    def list_incidents(
        status: IncidentStatus | None = None,
        limit: int = Query(default=50, le=200),
        svc: Services = Depends(provide),
    ) -> dict[str, Any]:
        return {"items": [i.to_dict() for i in svc.incidents.list(status=status, limit=limit)]}

    @app.get("/api/files")
    def list_files(
        run_id: str | None = None,
        limit: int = Query(default=100, le=500),
        svc: Services = Depends(provide),
    ) -> dict[str, Any]:
        return {"items": [f.to_dict() for f in svc.files.list(run_id=run_id, limit=limit)]}

    @app.get("/api/systems")
    def list_systems(svc: Services = Depends(provide)) -> dict[str, Any]:
        items = []
        for system in svc.systems.list():
            items.append(
                {
                    "key": system.key,
                    "name": system.name,
                    "auth_mode": system.auth.mode,
                    "session_exists": (
                        session_exists(svc.settings.storage.sessions_dir, system.key)
                        if system.auth.mode in ("session", "unattended")
                        else None
                    ),
                    "session_age_hours": (
                        session_age_hours(svc.settings.storage.sessions_dir, system.key)
                        if system.auth.mode in ("session", "unattended")
                        else None
                    ),
                    "reports": [
                        {
                            "key": report.key,
                            "title": report.title,
                            "schedule": {
                                "daily_at": report.schedule.daily_at,
                                "every_seconds": report.schedule.every_seconds,
                                "enabled": report.schedule.enabled,
                            },
                        }
                        for report in system.reports
                    ],
                }
            )
        return {"items": items}

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
            items.append({"system_key": system.key, "credential_ref": ref, "stored": stored, "username": username})
        return {"items": items}

    def _credential_system(system_key: str, svc: Services):
        try:
            return svc.systems.get(system_key)
        except SmartOpsError as exc:
            raise HTTPException(status_code=404, detail=exc.to_dict()) from exc

    def _require_local_ui(request: Request) -> None:
        # Browser mutations must originate from the shipped UI. Missing header is
        # intentionally rejected to reduce accidental script/CSRF use.
        if request.headers.get("X-SmartOps-Request") != "web":
            raise HTTPException(status_code=403, detail="Credential changes must come from the SmartOps UI.")

    @app.put("/api/credentials/{system_key}")
    def save_credential(system_key: str, body: CredentialRequest, request: Request, svc: Services = Depends(provide)) -> dict[str, Any]:
        _require_local_ui(request)
        system = _credential_system(system_key, svc)
        if system.auth.mode != "unattended":
            raise HTTPException(status_code=400, detail="System authentication mode must be unattended.")
        ref = system.auth.credential_ref or system.key
        try:
            svc.credentials.put(ref, body.username, body.password.get_secret_value())
        except (ValueError, OSError, RuntimeError) as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return {"system_key": system_key, "credential_ref": ref, "stored": True, "username": body.username}

    @app.delete("/api/credentials/{system_key}")
    def delete_credential(system_key: str, request: Request, svc: Services = Depends(provide)) -> dict[str, Any]:
        _require_local_ui(request)
        system = _credential_system(system_key, svc)
        ref = system.auth.credential_ref or system.key
        try:
            deleted = svc.credentials.delete(ref)
        except (ValueError, OSError, RuntimeError) as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return {"system_key": system_key, "credential_ref": ref, "stored": False, "deleted": deleted}

    @app.get("/api/recordings")
    def list_recordings(
        include_deleted: bool = False, status: RecordingStatus | None = None,
        limit: int = Query(default=100, le=500), svc: Services = Depends(provide),
    ) -> dict[str, Any]:
        return {"items": [r.to_dict() for r in svc.recordings.list(include_deleted=include_deleted, status=status, limit=limit)]}

    @app.post("/api/recordings", status_code=201)
    def create_recording(body: CreateRecordingRequest, svc: Services = Depends(provide)) -> dict[str, Any]:
        try: return svc.recording_manager.create(body.name, body.system_key).to_dict()
        except SmartOpsError as exc: raise HTTPException(status_code=400, detail=exc.to_dict()) from exc

    @app.get("/api/recordings/{recording_id}")
    def recording_detail(recording_id: str, svc: Services = Depends(provide)) -> dict[str, Any]:
        record = svc.recordings.get(recording_id)
        if not record: raise HTTPException(status_code=404, detail="التسجيل غير موجود")
        return {"recording": record.to_dict(), "steps": [s.to_dict() for s in svc.recordings.steps(recording_id)]}

    def _recording_control(action: str, recording_id: str, svc: Services) -> dict[str, Any]:
        try: return getattr(svc.recording_manager, action)(recording_id).to_dict()
        except SmartOpsError as exc: raise HTTPException(status_code=400, detail=exc.to_dict()) from exc

    @app.post("/api/recordings/{recording_id}/start")
    def start_recording(recording_id: str, svc: Services = Depends(provide)) -> dict[str, Any]: return _recording_control("start", recording_id, svc)
    @app.post("/api/recordings/{recording_id}/pause")
    def pause_recording(recording_id: str, svc: Services = Depends(provide)) -> dict[str, Any]: return _recording_control("pause", recording_id, svc)
    @app.post("/api/recordings/{recording_id}/resume")
    def resume_recording(recording_id: str, svc: Services = Depends(provide)) -> dict[str, Any]: return _recording_control("resume", recording_id, svc)
    @app.post("/api/recordings/{recording_id}/stop")
    def stop_recording(recording_id: str, svc: Services = Depends(provide)) -> dict[str, Any]: return _recording_control("stop", recording_id, svc)
    @app.post("/api/recordings/{recording_id}/rerecord")
    def rerecord(recording_id: str, svc: Services = Depends(provide)) -> dict[str, Any]: return _recording_control("rerecord", recording_id, svc)
    @app.post("/api/recordings/{recording_id}/delete")
    def delete_recording(recording_id: str, svc: Services = Depends(provide)) -> dict[str, Any]: return _recording_control("delete", recording_id, svc)
    @app.post("/api/recordings/{recording_id}/restore")
    def restore_recording(recording_id: str, svc: Services = Depends(provide)) -> dict[str, Any]: return _recording_control("restore", recording_id, svc)
    @app.post("/api/recordings/{recording_id}/draft")
    def recording_draft(recording_id: str, svc: Services = Depends(provide)) -> dict[str, Any]: return _recording_control("draft", recording_id, svc)

    @app.get("/api/recordings/{recording_id}/artifacts/{name:path}")
    def recording_artifact(recording_id: str, name: str, svc: Services = Depends(provide)) -> Response:
        record=svc.recordings.get(recording_id)
        if not record: raise HTTPException(status_code=404, detail="التسجيل غير موجود")
        path=preview_path(Path(record.artifact_dir), name)
        if not path: raise HTTPException(status_code=404, detail="هذا الملف غير متاح للعرض")
        media="application/json" if path.suffix == ".json" else "image/png"
        return Response(path.read_bytes(), media_type=media)

    @app.get("/api/alerts")
    def list_alerts(limit: int = Query(default=100, le=1000), svc: Services = Depends(provide)) -> dict[str, Any]:
        notifier = LocalLogNotifier(svc.settings.storage.logs_dir / "alerts.jsonl")
        records = notifier.read_all()
        return {"items": list(reversed(records))[:limit]}

    @app.post("/api/systems/{system_key}/{report_key}/collect", status_code=201)
    def collect_now(system_key: str, report_key: str, svc: Services = Depends(provide)) -> dict[str, Any]:
        try:
            params = svc.systems.run_params(system_key, report_key)
        except SmartOpsError as exc:
            raise HTTPException(status_code=404, detail=exc.to_dict()) from exc
        run = svc.runner.create_run("collect.report", params=params, trigger=TriggerType.MANUAL)
        run = svc.runner.drive(run.id)
        return run.to_dict()

    return app
