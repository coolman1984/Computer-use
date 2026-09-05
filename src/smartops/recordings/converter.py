from __future__ import annotations
from typing import Any
from ..domain.models import RecordingStep

def build_draft(recording_id: str, system_key: str, steps: list[RecordingStep]) -> dict[str, Any]:
    actions = []
    for step in steps:
        layer = "network" if step.request_ref else "dom" if step.selector else "vision" if step.x_ratio is not None else "manual"
        actions.append({"seq": step.seq, "layer": layer, "kind": step.kind, "selector": step.selector, "x_ratio": step.x_ratio, "y_ratio": step.y_ratio, "download_ref": step.download_ref})
    return {"key": f"recording.{recording_id}", "system_key": system_key, "status": "draft", "requires_review": True, "schedule_enabled": False, "actions": actions,
            "note": "Network first, then DOM, then relative visual fallback. Replay requires explicit review and download validation."}
