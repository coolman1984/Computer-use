from __future__ import annotations

import json
import time

from smartops.domain.models import RecordingStep
from smartops.ports.agents import AgentResponse
from smartops.recordings.coach import RecordingCoach


def test_recording_coach_sends_only_sanitized_structure_to_the_cli_agent(services) -> None:
    seen = []

    class FakeAgent:
        def run(self, request):
            seen.append(request)
            return AgentResponse(
                ok=True,
                summary="Record one complete path and finish only after the file downloads.",
            )

    services.agent_runner = FakeAgent()
    coach = RecordingCoach(services)
    session = coach.start("rec-demo")
    deadline = time.monotonic() + 2
    while coach.status("rec-demo").active and time.monotonic() < deadline:
        time.sleep(0.01)

    settled = coach.status("rec-demo")
    assert session is settled
    assert settled.status == "ready"
    assert seen and seen[0].context == {
        "capture": "browser_actions",
        "goal": "one_complete_download_workflow",
        "privacy": "structure_only",
    }
    serialized = str(seen[0].context).lower()
    assert all(word not in serialized for word in ("username", "password", "cookie", "url", "screenshot"))


def test_legacy_login_username_is_replaced_with_a_credential_reference(services) -> None:
    record = services.recording_manager.create("Legacy task", "portal")
    record.step_count = 1
    record.automation_draft = {
        "actions": [{
            "seq": 1,
            "action": "fill",
            "locator": {"strategy": "css", "value": '[id="userNameInput"]'},
            "inputs": {"value": "old-account-value"},
            "success": {"type": "value_equals", "value": "old-account-value"},
            "label": "old-account-value",
        }]
    }
    services.recordings.save(record)
    services.recordings.save_step(RecordingStep(
        recording_id=record.id,
        seq=1,
        kind="fill",
        action="fill",
        selector='[id="userNameInput"]',
        locator={"strategy": "css", "value": '[id="userNameInput"]'},
        inputs={"value": "old-account-value"},
        success={"type": "value_equals", "value": "old-account-value"},
        target_text_redacted="old-account-value",
    ))
    root = services.settings.storage.recordings_dir / record.id
    root.mkdir(parents=True, exist_ok=True)
    (root / "steps.jsonl").write_text(
        json.dumps(services.recordings.steps(record.id)[0].to_dict()), encoding="utf-8"
    )

    assert services.recording_manager.scrub_legacy_credential_steps() == 1

    repaired = services.recordings.steps(record.id)[0]
    assert repaired.inputs == {"secret_ref": "portal", "secret_field": "username"}
    combined = json.dumps(repaired.to_dict()) + json.dumps(services.recordings.get(record.id).automation_draft)
    combined += (root / "steps.jsonl").read_text(encoding="utf-8")
    assert "old-account-value" not in combined
