"""حزمة الحادثة: تجميع كل أدلة حادثة واحدة في مجلد واحد + ملخص JSON.

هذا هو "المدخل الأساسي للوكيل الذكي" (انظر MASTER_PLAN.md القسم 15):
الملخص، الخطأ، خطوات التشغيل، الأحداث، الملفات المتوقعة مقابل الفعلية،
وحوادث مشابهة سابقة عبر نفس التوقيع (signature) — قبل استدعاء أي وكيل.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ...core.clock import Clock, SystemClock, to_iso
from ...core.errors import ErrorClass, SmartOpsError
from ...domain.enums import StepStatus


def _extract_error(run: Any, steps: list[Any]) -> dict[str, Any] | None:
    """يستخرج تفاصيل الخطأ: من التشغيل أولًا، وإلا من آخر خطوة فاشلة."""
    if run is not None and run.error_class:
        return {"error_class": run.error_class, "message": run.error_message, "source": "run"}
    for step in reversed(steps):
        if step.status is StepStatus.FAILED and step.error_class:
            return {
                "error_class": step.error_class,
                "message": step.error_message,
                "source": f"step:{step.name}",
            }
    return None


def _expected_files(run: Any) -> dict[str, Any] | None:
    """يستنتج الملف المتوقع من معطيات التشغيل (system/report) إن وُجدت."""
    if run is None:
        return None
    system = run.params.get("system")
    report = run.params.get("report")
    if not system and not report:
        return None
    return {"system": system, "report": report, "period": run.params.get("period", "")}


class IncidentPackBuilder:
    """يبني مجلد أدلة تحت incidents_dir/<incident_id>/ ويحدّث incident.pack_path."""

    def __init__(
        self,
        *,
        incidents: Any,
        runs: Any,
        steps: Any,
        events: Any,  # سجل الأحداث: كائن له timeline(run_id) (مثل services.events)
        files: Any,
        base_dir: Path | str,
        clock: Clock | None = None,
    ) -> None:
        self._incidents = incidents
        self._runs = runs
        self._steps = steps
        self._events = events
        self._files = files
        self._base_dir = Path(base_dir)
        self._clock = clock or SystemClock()

    def build(self, incident_id: str, *, extra_evidence: dict[str, Any] | None = None) -> Path:
        incident = self._incidents.get(incident_id)
        if incident is None:
            raise SmartOpsError(
                f"حادثة غير موجودة: {incident_id}", error_class=ErrorClass.PERMANENT
            )

        run = self._runs.get(incident.run_id) if incident.run_id else None
        steps = self._steps.list(incident.run_id) if incident.run_id else []
        events = self._events.timeline(incident.run_id) if incident.run_id else []
        artifacts = self._files.list(run_id=incident.run_id) if incident.run_id else []
        similar = (
            [i for i in self._incidents.find_by_signature(incident.signature) if i.id != incident.id]
            if incident.signature
            else []
        )

        summary: dict[str, Any] = {
            "generated_at": to_iso(self._clock.now()),
            "incident": incident.to_dict(),
            "run": run.to_dict() if run else None,
            "error": _extract_error(run, steps),
            "steps": [s.to_dict() for s in steps],
            "events": [e.to_dict() for e in events],
            "files": {
                "expected": _expected_files(run),
                "actual": [f.to_dict() for f in artifacts],
            },
            "similar_incidents": [
                {
                    "id": i.id,
                    "title": i.title,
                    "status": i.status.value,
                    "root_cause": i.root_cause,
                    "resolution": i.resolution,
                    "created_at": to_iso(i.created_at),
                }
                for i in similar
            ],
        }
        if extra_evidence:
            summary["evidence"] = extra_evidence

        pack_dir = self._base_dir / incident.id
        pack_dir.mkdir(parents=True, exist_ok=True)
        (pack_dir / "summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
        )

        incident.pack_path = str(pack_dir)
        self._incidents.update(incident)
        return pack_dir
