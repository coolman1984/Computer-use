"""Approval identity: a changed automation must be tested again before it runs."""

from __future__ import annotations

from smartops.domain.enums import ProcessStatus
from smartops.processes.admission import admission_values, current_execution_digest, with_admission


def test_digest_ignores_its_own_admission_metadata(services) -> None:
    process = services.processes.create(
        name="Daily", system_key="missing", report_key="daily",
        plan={"start_url": "https://example.test", "actions": []},
    )
    initial = current_execution_digest(services, process)
    process.plan = with_admission(process.plan, tested_digest=initial, approved_digest=initial)

    assert current_execution_digest(services, process) == initial
    assert admission_values(process.plan)["approved_digest"] == initial


def test_changed_bound_approval_is_unscheduled_before_launch(services) -> None:
    process = services.processes.create(
        name="Daily", system_key="missing", report_key="daily",
        plan={"start_url": "https://example.test", "expects_download": True, "actions": [{
            "seq": 1, "action": "click", "layer": "dom",
            "locator": {"value": "#export"},
            "success": {"type": "download_started"},
            "retry": {"max_attempts": 1, "safe_to_repeat": False},
        }]},
    )
    process.status = ProcessStatus.APPROVED
    process.schedule_enabled = True
    process.validation_rules = {"min_size_bytes": 1}
    digest = current_execution_digest(services, process)
    process.plan = with_admission(process.plan, approved_digest=digest)
    services.processes.save(process)

    process.plan["actions"][0]["locator"]["value"] = "#changed-export"
    services.processes.save(process)

    try:
        services.process_manager.run(process.id)
    except Exception as exc:
        assert "changed" in str(exc).lower()
    else:
        raise AssertionError("changed approved automation was allowed to start")
    settled = services.processes.get(process.id)
    assert settled is not None and settled.status is ProcessStatus.TEST_FAILED
    assert settled.schedule_enabled is False
