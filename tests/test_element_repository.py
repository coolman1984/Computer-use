"""One description per control, so it is repaired once instead of twelve times.

Every step used to carry its own private copy of how to find its element.
Twelve steps touching the same Inquiry button held twelve locator lists, and a
site that renamed that button broke twelve steps that had to be repaired
twelve times — each one a separate chance to get it slightly wrong.

These tests hold the two properties that make the shared repository worth
having: the same control met twice is one entry, and repairing that entry
reaches every step that names it. And the one property that keeps it safe: a
plan whose repository is missing behaves exactly as it did before.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from smartops.recordings.elements import (
    ElementRepository,
    element_key,
    merge,
    screen_key,
)
from tests.recorded_site import LocalSite


def _chromium_path() -> str | None:
    env_path = os.environ.get("SMARTOPS_TEST_CHROMIUM_PATH") or os.environ.get(
        "PLAYWRIGHT_CHROMIUM_PATH"
    )
    if env_path:
        return env_path
    default = Path("/opt/pw-browsers/chromium")
    return str(default) if default.exists() else None


@pytest.fixture
def site():
    with LocalSite() as running:
        yield running


# ---------- the identity rules ----------


def test_the_same_screen_visited_with_different_filters_is_one_screen() -> None:
    """Otherwise every visit invents a new screen and nothing ever accumulates."""
    monday = screen_key("https://portal.example/report/daily?period=2026-09-07&run=a1")
    tuesday = screen_key("https://portal.example/report/daily?period=2026-09-08&run=b2")

    assert monday == tuesday == "portal.example/report/daily"


def test_a_control_that_gains_a_label_is_still_the_same_control() -> None:
    """A key that changed whenever anything changed would defeat the point."""
    before = element_key(['[id="btnInquiry"]'], {"name": "Inquiry", "role": "button"})
    after = element_key(
        ['[id="btnInquiry"]', 'role=button[name="Inquiry"]'],
        {"name": "Inquiry", "role": "button", "anchor": "Reports"},
    )

    assert before == after


def test_meeting_a_control_again_updates_it_instead_of_duplicating_it() -> None:
    repository = ElementRepository()

    first = repository.remember(
        url="https://portal.example/report/daily?period=1",
        locators=['[id="btnInquiry"]'],
        fingerprint={"name": "Inquiry", "role": "button"},
    )
    second = repository.remember(
        url="https://portal.example/report/daily?period=2",
        locators=['[id="btnInquiry"]', 'role=button[name="Inquiry"]'],
        fingerprint={"name": "Inquiry", "role": "button"},
    )

    assert first.reference == second.reference
    assert len(repository) == 1
    assert second.used_by == 2
    # The later sighting's extra locator is kept, behind the one the first
    # recording actually proved.
    assert second.locators == ['[id="btnInquiry"]', 'role=button[name="Inquiry"]']


# ---------- the point of the whole thing ----------


def test_repairing_one_element_repairs_every_step_that_names_it(tmp_path) -> None:
    repository = ElementRepository(tmp_path / "elements.json")
    element = repository.remember(
        url="https://portal.example/report/daily",
        locators=['[id="btnInquiry"]'],
        fingerprint={"name": "Inquiry", "role": "button"},
    )
    # Nine steps in one automation press this button.
    for _ in range(8):
        repository.remember(
            url="https://portal.example/report/daily",
            locators=['[id="btnInquiry"]'],
            fingerprint={"name": "Inquiry", "role": "button"},
        )
    assert repository.get(element.reference).used_by == 9

    repository.repair(element.reference, ['role=button[name="Search"]'])

    # One edit; every step that names this element now finds it the new way.
    assert repository.locators_for(element.reference) == ['role=button[name="Search"]']
    # And the stale verdict is dropped: it described the locators that are gone.
    assert repository.get(element.reference).last_check == {}


def test_a_repair_survives_being_written_and_read_back(tmp_path) -> None:
    path = tmp_path / "elements.json"
    repository = ElementRepository(path)
    element = repository.remember(
        url="https://portal.example/report/daily",
        locators=['[id="btnInquiry"]'],
        fingerprint={"name": "Inquiry", "role": "button"},
    )
    repository.repair(element.reference, ['role=button[name="Search"]'])
    repository.save()

    reopened = ElementRepository(path).load()

    assert reopened.locators_for(element.reference) == ['role=button[name="Search"]']
    assert json.loads(path.read_text(encoding="utf-8"))["version"] == 1


def test_a_repository_written_by_a_newer_smartops_is_left_alone(tmp_path) -> None:
    """Rewriting a shape we do not understand would destroy it."""
    path = tmp_path / "elements.json"
    path.write_text(json.dumps({"version": 99, "screens": {"a": {}}}), encoding="utf-8")

    repository = ElementRepository(path).load()

    assert len(repository) == 0  # nothing adopted, and nothing overwritten


def test_a_second_recording_improves_the_first_ones_descriptions(tmp_path) -> None:
    shared = ElementRepository(tmp_path / "shared.json")
    first = ElementRepository()
    first.remember(url="https://portal.example/daily", locators=['[id="btn"]'],
                   fingerprint={"name": "Inquiry", "role": "button"})
    second = ElementRepository()
    second.remember(url="https://portal.example/daily",
                    locators=['[id="btn"]', 'role=button[name="Inquiry"]'],
                    fingerprint={"name": "Inquiry", "role": "button"})

    merge(shared, first)
    merge(shared, second)

    assert len(shared) == 1
    only = next(iter(shared))
    assert only.locators == ['[id="btn"]', 'role=button[name="Inquiry"]']


# ---------- what the recorder actually produces ----------


@pytest.fixture
def recorded(services, site):
    from smartops.adapters.browser.playwright_engine import PlaywrightBrowserAdapter

    services.settings.browser.__dict__["executable_path"] = _chromium_path() or ""
    services.browser = PlaywrightBrowserAdapter(
        services.settings.browser, credential_store=services.credentials
    )
    services.systems.save({
        "key": "portal", "name": "Portal", "auth": {"mode": "none"},
        "reports": [{"key": "daily", "title": "Daily",
                     "url": f"{site.base_url}/torture/stale_grid.html"}],
    })
    return services


def test_a_recording_names_its_controls_and_checks_them_before_the_browser_closes(
    recorded, site
) -> None:
    """The step keeps its own locators; the reference travels beside them."""
    from tests.recording_harness import capture_with_recorder

    def press_inquiry_twice(page) -> None:
        page.click("#btnInquiry")
        page.wait_for_timeout(700)
        page.click("#btnInquiry")
        page.wait_for_timeout(600)

    steps = capture_with_recorder(
        recorded, start_url=f"{site.base_url}/torture/stale_grid.html",
        script=press_inquiry_twice, executable_path=_chromium_path(),
    )
    record = recorded.recordings.list(limit=1)[0]
    written = json.loads((Path(record.artifact_dir) / "elements.json").read_text(encoding="utf-8"))

    clicks = [step for step in steps if step["action"] in {"click", "pointer_click"}]
    assert clicks, [step["action"] for step in steps]
    reference = clicks[0]["inputs"]["_element"]
    assert "#" in reference  # screen and control together
    # The step still carries its own way of finding the button.
    assert clicks[0]["locator"]["value"]

    # Both presses of the same button are one control, used twice.
    screen, key = reference.split("#", 1)
    described = written["screens"][screen][key]
    assert described["used_by"] == len(clicks)
    # And it was asked for before the browser closed, and answered uniquely.
    assert described["last_check"]["resolves"] is True
    assert described["last_check"]["found"] == 1


def test_a_misclick_can_be_undone_while_the_recording_is_still_running(
    recorded, site
) -> None:
    """Until now the only remedy was to throw the whole recording away.

    That is why long tasks stopped being recorded: one stray click near the end
    meant starting over. The mis-click is removed and the next action carries on
    from the step before it.
    """
    from smartops.domain.enums import RecordingStatus

    manager = recorded.recording_manager
    record = manager.create("Task with a stray click", "portal")
    record.status = RecordingStatus.RECORDING
    recorded.recordings.save(record)

    for action in ("click", "fill", "click"):
        manager._step(record.id, {"kind": action, "action": action, "inputs": {}})
    assert [step.action for step in recorded.recordings.steps(record.id)] == [
        "click", "fill", "click"
    ]

    manager.undo_last_step(record.id)

    kept = recorded.recordings.steps(record.id)
    assert [step.action for step in kept] == ["click", "fill"]
    assert recorded.recordings.get(record.id).step_count == 2
    # The file mirror agrees with the database, rather than still holding the
    # step that was taken back.
    lines = (Path(record.artifact_dir) / "steps.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2

    # The next action takes the number the undone step gave up, so nothing that
    # already refers to an earlier step has its identity changed underneath it.
    manager._step(record.id, {"kind": "download", "action": "download", "inputs": {}})
    assert [step.seq for step in recorded.recordings.steps(record.id)] == [1, 2, 3]


def test_a_finished_recording_cannot_have_its_steps_undone(recorded) -> None:
    """Undo is a recording-time repair, not an edit of a finished recording."""
    from smartops.core.errors import PermanentError
    from smartops.domain.enums import RecordingStatus

    manager = recorded.recording_manager
    record = manager.create("Finished task", "portal")
    record.status = RecordingStatus.RECORDING
    recorded.recordings.save(record)
    manager._step(record.id, {"kind": "click", "action": "click", "inputs": {}})
    record.status = RecordingStatus.COMPLETED
    recorded.recordings.save(record)

    with pytest.raises(PermanentError, match="running recording"):
        manager.undo_last_step(record.id)
