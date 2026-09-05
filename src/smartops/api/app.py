"""واجهة HTTP المحلية. الواجهة الرسومية والشات يبنيان فوق هذه النقاط."""

from __future__ import annotations

from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Query
from pydantic import BaseModel, Field

from ..core.errors import SmartOpsError
from ..domain.enums import IncidentStatus, RunStatus, TriggerType
from ..services import Services

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


def create_app(services: Services | None = None) -> FastAPI:
    app = FastAPI(title="SmartOps", version="0.1.0")

    def provide() -> Services:
        return services or get_services()

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "service": "smartops"}

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

    return app
