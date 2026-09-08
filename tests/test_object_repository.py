"""V2 plans refer to one reviewed UI descriptor catalogue."""
from __future__ import annotations

from smartops.domain.models import RecordingStep
from smartops.recordings.converter import build_plan
from smartops.recordings.object_repository import resolve_locator
from smartops.recordings.healing import repair_proposal


def test_compiled_plan_reuses_one_object_for_the_same_control() -> None:
    steps = [
        RecordingStep("r", 1, "click", action="click", selector="#run", locator={"value": "#run"}),
        RecordingStep("r", 2, "click", action="click", selector="#run", locator={"value": "#run"}),
    ]

    plan = build_plan(
        recording_id="r", system_key="portal", report_key="daily", steps=steps,
        start_url="https://portal.example/report",
    )

    first, second = plan["actions"]
    assert plan["plan_version"] == 4
    assert first["object_ref"] == second["object_ref"]
    assert len(plan["object_repository"]["objects"]) == 1


def test_object_reference_is_authoritative_over_legacy_inline_locator() -> None:
    action = {"object_ref": "ui-report", "locator": {"value": "#stale"}}
    repository = {"objects": {"ui-report": {"locator": {"value": "#current"}}}}

    assert resolve_locator(action, repository) == {"value": "#current"}


def test_fallback_repair_is_a_review_proposal_not_an_automatic_edit() -> None:
    proposal = repair_proposal(
        {"seq": 3, "object_ref": "ui-report"}, strategy="anchor", selector="[data-testid=filters]"
    )

    assert proposal == {
        "step": 3,
        "object_ref": "ui-report",
        "strategy": "anchor",
        "candidate_selector": "[data-testid=filters]",
        "status": "review_required",
    }
    assert repair_proposal({"seq": 3}, strategy="primary", selector="#run") is None
