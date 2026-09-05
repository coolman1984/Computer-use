"""S-10 tests: building the incident pack (summary + steps + events + files + similar incidents)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from smartops.adapters.incidents.pack import IncidentPackBuilder
from smartops.core.errors import PermanentError, SmartOpsError
from smartops.domain.enums import IncidentStatus, RunStatus
from smartops.domain.models import FileArtifact, StepDefinition, WorkflowDefinition


def _builder(services, tmp_path: Path) -> IncidentPackBuilder:
    return IncidentPackBuilder(
        incidents=services.incidents,
        runs=services.runs,
        steps=services.steps,
        events=services.events,
        files=services.files,
        base_dir=tmp_path / "incidents",
        clock=services.clock,
    )


def _register_broken_workflow(services, key: str = "test.broken_flow") -> None:
    def broken(ctx):
        raise PermanentError("Bad report definition", code="bad_definition")

    services.step_registry.add(f"{key}.step", broken)
    services.workflows.register(
        WorkflowDefinition(
            key=key,
            title="Broken workflow",
            steps=(StepDefinition(name="only", uses=f"{key}.step"),),
        )
    )


def test_build_pack_for_failed_run(services, tmp_path: Path) -> None:
    _register_broken_workflow(services)
    run = services.runner.create_run("test.broken_flow")
    run = services.runner.execute(run.id)
    assert run.status is RunStatus.FAILED

    incident = services.incidents.list(status=IncidentStatus.OPEN)[0]
    pack_dir = _builder(services, tmp_path).build(incident.id)

    assert pack_dir.exists()
    summary_path = pack_dir / "summary.json"
    assert summary_path.exists()

    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    assert summary["incident"]["id"] == incident.id
    assert summary["run"]["id"] == run.id
    assert summary["error"]["error_class"] == "permanent"
    assert summary["error"]["message"] == "Bad report definition"
    assert [s["name"] for s in summary["steps"]] == ["only"]
    assert summary["steps"][0]["status"] == "failed"
    assert any(e["type"] == "incident_opened" for e in summary["events"])
    assert summary["files"]["actual"] == []
    assert summary["similar_incidents"] == []


def test_pack_path_is_saved_on_incident(services, tmp_path: Path) -> None:
    _register_broken_workflow(services)
    run = services.runner.create_run("test.broken_flow")
    services.runner.execute(run.id)
    incident = services.incidents.list(status=IncidentStatus.OPEN)[0]

    pack_dir = _builder(services, tmp_path).build(incident.id)

    reloaded = services.incidents.get(incident.id)
    assert reloaded.pack_path == str(pack_dir)


def test_similar_incidents_found_by_signature(services, tmp_path: Path) -> None:
    _register_broken_workflow(services)

    run1 = services.runner.create_run("test.broken_flow")
    services.runner.execute(run1.id)
    run2 = services.runner.create_run("test.broken_flow")
    services.runner.execute(run2.id)

    incidents = services.incidents.list(status=IncidentStatus.OPEN)
    assert len(incidents) == 2
    # Newest first in list(), so run2's incident is [0] and run1's is [1]
    newest, oldest = incidents[0], incidents[1]
    assert newest.signature == oldest.signature

    summary = json.loads(
        (_builder(services, tmp_path).build(newest.id) / "summary.json").read_text(encoding="utf-8")
    )
    assert [s["id"] for s in summary["similar_incidents"]] == [oldest.id]


def test_expected_vs_actual_files(services, tmp_path: Path) -> None:
    from smartops.core.ids import new_id

    _register_broken_workflow(services)
    run = services.runner.create_run(
        "test.broken_flow", params={"system": "erp", "report": "daily_sales"}
    )
    services.runner.execute(run.id)
    incident = services.incidents.list(status=IncidentStatus.OPEN)[0]

    services.files.save(
        FileArtifact(
            id=new_id("file"),
            run_id=run.id,
            system="erp",
            report="daily_sales",
            path=str(tmp_path / "daily_sales.csv"),
            size_bytes=10,
        )
    )

    summary = json.loads(
        (_builder(services, tmp_path).build(incident.id) / "summary.json").read_text(encoding="utf-8")
    )
    assert summary["files"]["expected"] == {"system": "erp", "report": "daily_sales", "period": ""}
    assert len(summary["files"]["actual"]) == 1
    assert summary["files"]["actual"][0]["system"] == "erp"


def test_extra_evidence_is_attached_when_given(services, tmp_path: Path) -> None:
    _register_broken_workflow(services)
    run = services.runner.create_run("test.broken_flow")
    services.runner.execute(run.id)
    incident = services.incidents.list(status=IncidentStatus.OPEN)[0]

    summary_path = _builder(services, tmp_path).build(
        incident.id, extra_evidence={"screenshot_base64": "abc"}
    ) / "summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    assert summary["evidence"] == {"screenshot_base64": "abc"}


def test_missing_incident_raises_clear_error(services, tmp_path: Path) -> None:
    with pytest.raises(SmartOpsError, match="not found"):
        _builder(services, tmp_path).build("inc_does_not_exist")
