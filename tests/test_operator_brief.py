"""One page that says where this deployment stands, for whoever works on it next.

Everything in it existed already and was reachable only through the web
interface: the journey stage, the controls a recording registered, the
incidents runs opened. An assistant working in a terminal had to guess at the
state of the system it was about to change, and guessing is how the wrong thing
gets fixed confidently.

These tests hold the two things that make the brief worth trusting: it reports
what is actually there rather than a fixed opinion, and it says plainly when
something is broken instead of leaving a healthy-looking gap.
"""

from __future__ import annotations

import json

import pytest

from smartops.cli import _cmd_brief


class _Args:
    def __init__(self, as_json: bool = True) -> None:
        self.json = as_json


@pytest.fixture
def brief(services, monkeypatch, capsys):
    """Run the command against this test's services rather than a real install."""
    monkeypatch.setattr("smartops.cli._build_services", lambda: services)
    # The command closes what it was given; these services belong to the test.
    monkeypatch.setattr(services, "close", lambda: None)

    def run(as_json: bool = True):
        assert _cmd_brief(_Args(as_json)) == 0
        out = capsys.readouterr().out
        return json.loads(out) if as_json else out

    return run


def test_the_brief_names_the_stage_and_the_next_thing_to_do(services, brief) -> None:
    services.systems.save({
        "key": "portal", "name": "Portal", "auth": {"mode": "none"},
        "reports": [{"key": "daily", "title": "Daily", "url": "https://example.test/daily"}],
    })

    report = brief()

    assert report["stage"], "the brief must say which stage this deployment is at"
    assert report["next_action"], "a stage with no next action leaves the reader stuck"
    assert [stage["key"] for stage in report["stages"]], "the whole journey must be listed"
    assert [system["key"] for system in report["systems"]] == ["portal"]


def test_a_control_that_stopped_resolving_is_named_in_the_brief(services, brief) -> None:
    """The signal worth surfacing: an automation whose button is gone."""
    from smartops.recordings.elements import ElementRepository

    services.systems.save({
        "key": "portal", "name": "Portal", "auth": {"mode": "none"},
        "reports": [{"key": "daily", "title": "Daily", "url": "https://example.test/daily"}],
    })
    path = services.recording_manager.system_elements_path("portal")
    repository = ElementRepository(path)
    gone = repository.remember(
        url="https://example.test/daily", locators=['[id="btnInquiry"]'],
        fingerprint={"name": "Inquiry", "role": "button"},
    )
    ambiguous = repository.remember(
        url="https://example.test/daily", locators=['[id="btnExport"]'],
        fingerprint={"name": "Export", "role": "button"},
    )
    fine = repository.remember(
        url="https://example.test/daily", locators=['[id="btnClear"]'],
        fingerprint={"name": "Clear", "role": "button"},
    )
    repository.record_check(gone.reference, found=0)
    repository.record_check(ambiguous.reference, found=4)
    repository.record_check(fine.reference, found=1)
    repository.save()

    report = brief()

    portal = report["systems"][0]
    assert portal["elements"] == 3
    trouble = {item["reference"]: item["found"] for item in portal["unresolved"]}
    # Both failures are reported, and the one that works is not.
    assert trouble == {gone.reference: 0, ambiguous.reference: 4}


def test_an_incident_opened_with_no_evidence_says_so(services, brief) -> None:
    """A silent gap here is exactly what let the evidence builder go uncalled."""
    from smartops.domain.enums import Severity

    services.incidents.open(
        title="Daily download failed at export",
        severity=Severity.ERROR,
        run_id="run-1",
        signature="daily:export:permanent:none",
    )

    printed = brief(as_json=False)

    assert "Daily download failed at export" in printed
    assert "no evidence was collected" in printed
