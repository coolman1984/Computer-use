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


# ---------- the read-only instruments that replaced "watching" ----------
#
# None of these drive a browser: they read exactly what a recording has
# already written down, so they are tested here as plain state, with no
# worker and no real Chrome anywhere in sight.


def _save_step(services, recording_id: str, seq: int, *, success_type: str = "none", text: str = "") -> None:
    services.recordings.save_step(RecordingStep(
        recording_id=recording_id,
        seq=seq,
        kind="click",
        action="click",
        target={"page": "main", "frame": ""},
        success={"type": success_type},
        target_text_redacted=text,
    ))


def test_recent_steps_returns_only_what_is_new_since_the_caller_last_asked(services) -> None:
    record = services.recording_manager.create("Steps demo", "portal")
    for seq in range(1, 6):
        _save_step(services, record.id, seq)

    coach = RecordingCoach(services)
    first = coach.recent_steps(record.id, since_seq=0, limit=3)
    assert [step["seq"] for step in first["steps"]] == [1, 2, 3]
    assert first["more_available"] is True
    assert first["next_since_seq"] == 3
    assert first["total_steps"] == 5

    rest = coach.recent_steps(record.id, since_seq=first["next_since_seq"])
    assert [step["seq"] for step in rest["steps"]] == [4, 5]
    assert rest["more_available"] is False

    nothing_new = coach.recent_steps(record.id, since_seq=rest["next_since_seq"])
    assert nothing_new["steps"] == []
    assert nothing_new["next_since_seq"] == rest["next_since_seq"]


def test_thin_evidence_lists_only_steps_with_no_proof_of_success(services) -> None:
    record = services.recording_manager.create("Thin demo", "portal")
    _save_step(services, record.id, 1, success_type="value_equals")
    _save_step(services, record.id, 2, success_type="none", text="Search")
    _save_step(services, record.id, 3, success_type="selector_visible")

    weak = RecordingCoach(services).thin_evidence(record.id)

    assert [item["seq"] for item in weak] == [2]
    assert weak[0]["action"] == "click"
    assert weak[0]["target_text_redacted"] == "Search"


def test_live_screen_instruments_report_unavailable_without_a_running_worker(services) -> None:
    record = services.recording_manager.create("No worker demo", "portal")
    coach = RecordingCoach(services)

    for instrument in (coach.look, coach.capabilities, coach.diagnose_vision):
        assert instrument(record.id) == {
            "available": False,
            "reason": "this recording is not running",
        }
